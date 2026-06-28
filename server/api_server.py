"""server/api_server.py
~~~~~~~~~~~~~~~~~~~~~~~
LaunchAI 的 HTTP API:用 FastAPI 把 8 个工具开放成接口,跑在 Qt 进程内的后台
守护线程里(uvicorn)。请求格式参考 LLM 调用(顶层 ``model`` 选工具、``parameters``
带参数、``stream`` 流式)。实际执行复用 ``server.tool_runners.run_tool``。

端点(全部需 ``Authorization: Bearer <key>``,除 /healthz):
  GET  /healthz                     —— 探活,无需鉴权
  GET  /v1/models                   —— 列出 8 个工具及其参数 schema(对齐 LLM models)
  GET  /v1/models/{tool}            —— 单个工具
  POST /v1/invoke                   —— JSON、本地路径;stream:true → SSE
  POST /v1/tools/{tool}/upload      —— multipart 上传输入文件后执行
  GET  /v1/files?path=<abs>         —— 下载产出(路径白名单限定在 paths.root() 内)

生命周期:``ApiServerManager`` 单例,start()/stop()/is_running()。设置页与 app.py 用它。
"""
import os
import json
import time
import queue
import asyncio
import threading
from urllib.parse import quote

from utils import paths as _paths
from utils.configer import get_field
from logger import info, warning, error

from server.tool_runners import TOOLS, run_tool, ToolError


# ── 鉴权 / 工具函数 ─────────────────────────────────────────────────────
def _configured_key() -> str:
    return (get_field("api_server.api_key", "") or "").strip()


def _file_url(abs_path: str) -> str:
    return f"/v1/files?path={quote(abs_path)}"


def _within_root(abs_path: str) -> bool:
    """abs_path 是否在 paths.root() 之内(防目录穿越)。"""
    try:
        root = os.path.realpath(_paths.root())
        target = os.path.realpath(abs_path)
        return os.path.commonpath([root, target]) == root
    except Exception:
        return False


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


# 上传文件如何映射到各工具的参数(primary 文件型入参)
def _apply_uploads(tool: str, saved: list, params: dict):
    if not saved:
        return
    if tool in ("demucs", "realesrgan"):
        params["input"] = saved[0]
    elif tool in ("whisper", "rvc"):
        params["input"] = saved
    elif tool == "yolo":
        params["files"] = saved
    elif tool == "iopaint":
        params["image"] = saved[0]
        if len(saved) > 1:
            params["mask"] = saved[1]
    elif tool == "gptsovits":
        params["ref_audio"] = saved[0]      # 目标文本仍走 parameters.target_text
    elif tool == "audiocraft":
        params["melody"] = saved[0]         # 提示词仍走 parameters.prompt


def create_app():
    from fastapi import (FastAPI, Header, HTTPException, Request, UploadFile,
                         File, Form, Depends)
    from fastapi.responses import JSONResponse, StreamingResponse, FileResponse
    from starlette.concurrency import run_in_threadpool

    app = FastAPI(title="LaunchAI API", version="1.0")

    def require_api_key(authorization: str = Header(None)):
        key = _configured_key()
        if not key:
            raise HTTPException(503, "服务未配置 API Key")
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(401, "缺少 Authorization: Bearer <key>")
        if authorization.split(" ", 1)[1].strip() != key:
            raise HTTPException(401, "API Key 无效")
        return True

    def _tool_meta(tool: str) -> dict:
        m = TOOLS[tool]
        return {"id": tool, "object": "tool", "category": m["category"],
                "display": m["display"], "primary_input": m["primary_input"],
                "parameters": m["params"]}

    # ── 探活 ────────────────────────────────────────────────────────────
    @app.get("/healthz")
    def healthz():
        return {"status": "ok", "tools": list(TOOLS.keys())}

    # ── 列工具 ──────────────────────────────────────────────────────────
    @app.get("/v1/models")
    def list_models(_=Depends(require_api_key)):
        return {"object": "list", "data": [_tool_meta(t) for t in TOOLS]}

    @app.get("/v1/models/{tool}")
    def get_model(tool: str, _=Depends(require_api_key)):
        if tool not in TOOLS:
            raise HTTPException(404, f"未知工具: {tool}")
        return _tool_meta(tool)

    # ── 调用核(JSON / 本地路径) ────────────────────────────────────────
    def _merge_params(tool: str, body_input, parameters: dict) -> dict:
        params = dict(parameters or {})
        if body_input is not None:
            params[TOOLS[tool]["primary_input"]] = body_input
        return params

    def _run_blocking(tool: str, params: dict) -> dict:
        logs: list = []

        def on_event(kind, payload):
            if kind == "log":
                logs.append(payload.get("line", ""))
            elif kind == "progress":
                logs.append(payload.get("text", ""))
        result = run_tool(tool, params, on_event)
        return {"tool": tool, "status": "succeeded",
                "outputs": result["outputs"],
                "output_urls": [_file_url(p) for p in result["outputs"]],
                "elapsed": result["elapsed"],
                "logs": logs[-40:]}

    def _stream(tool: str, params: dict):
        q: queue.Queue = queue.Queue()
        DONE = object()
        holder: dict = {}

        def on_event(kind, payload):
            q.put((kind, payload))

        def work():
            try:
                holder["result"] = run_tool(tool, params, on_event)
            except ToolError as e:
                holder["error"] = (e.code, str(e))
            except Exception as e:   # noqa: BLE001
                holder["error"] = (500, str(e))
            finally:
                q.put(DONE)

        async def gen():
            threading.Thread(target=work, daemon=True).start()
            loop = asyncio.get_event_loop()
            while True:
                item = await loop.run_in_executor(None, q.get)
                if item is DONE:
                    break
                kind, payload = item
                yield _sse(kind, payload)
            if "error" in holder:
                code, msg = holder["error"]
                yield _sse("error", {"code": code, "message": msg})
            else:
                r = holder["result"]
                yield _sse("result", {
                    "status": "succeeded", "tool": tool,
                    "outputs": r["outputs"],
                    "output_urls": [_file_url(p) for p in r["outputs"]],
                    "elapsed": r["elapsed"]})
            yield "data: [DONE]\n\n"

        return StreamingResponse(gen(), media_type="text/event-stream")

    @app.post("/v1/invoke")
    async def invoke(request: Request, _=Depends(require_api_key)):
        body = await request.json()
        tool = body.get("model") or body.get("tool")
        if tool not in TOOLS:
            raise HTTPException(404, f"未知工具(model): {tool}")
        params = _merge_params(tool, body.get("input"), body.get("parameters"))
        if body.get("stream"):
            return _stream(tool, params)
        try:
            return await run_in_threadpool(_run_blocking, tool, params)
        except ToolError as e:
            raise HTTPException(e.code, str(e))

    # ── 便捷:直接 POST /v1/tools/{tool}(JSON,等价 invoke) ──────────────
    @app.post("/v1/tools/{tool}")
    async def invoke_tool(tool: str, request: Request,
                          _=Depends(require_api_key)):
        if tool not in TOOLS:
            raise HTTPException(404, f"未知工具: {tool}")
        body = await request.json()
        # parameters 优先;否则把除保留键外的 body 当参数(便捷直传)
        parameters = body.get("parameters")
        if parameters is None:
            parameters = {k: v for k, v in body.items()
                          if k not in ("model", "tool", "input", "stream", "parameters")}
        params = _merge_params(tool, body.get("input"), parameters)
        if body.get("stream"):
            return _stream(tool, params)
        try:
            return await run_in_threadpool(_run_blocking, tool, params)
        except ToolError as e:
            raise HTTPException(e.code, str(e))

    # ── multipart 上传执行 ──────────────────────────────────────────────
    @app.post("/v1/tools/{tool}/upload")
    async def upload_invoke(tool: str,
                            files: list[UploadFile] = File(...),
                            parameters: str = Form("{}"),
                            _=Depends(require_api_key)):
        if tool not in TOOLS:
            raise HTTPException(404, f"未知工具: {tool}")
        try:
            params = json.loads(parameters or "{}")
            if not isinstance(params, dict):
                raise ValueError
        except ValueError:
            raise HTTPException(400, "parameters 不是合法 JSON 对象")

        up_dir = os.path.join(_paths.output_dir("node"), "_api_uploads",
                              f"{int(time.time()*1000)}")
        os.makedirs(up_dir, exist_ok=True)
        saved = []
        for uf in files:
            dest = os.path.join(up_dir, os.path.basename(uf.filename or "upload.bin"))
            with open(dest, "wb") as fh:
                fh.write(await uf.read())
            saved.append(dest)
        _apply_uploads(tool, saved, params)
        try:
            return await run_in_threadpool(_run_blocking, tool, params)
        except ToolError as e:
            raise HTTPException(e.code, str(e))

    # ── 下载产出 ────────────────────────────────────────────────────────
    @app.get("/v1/files")
    def download(path: str, _=Depends(require_api_key)):
        if not path or not os.path.isfile(path):
            raise HTTPException(404, "文件不存在")
        if not _within_root(path):
            raise HTTPException(403, "禁止访问 data 根目录以外的文件")
        return FileResponse(path, filename=os.path.basename(path))

    return app


# ── 生命周期管理 ────────────────────────────────────────────────────────
class ApiServerManager:
    """uvicorn 跑在守护线程里的单例。设置页 / app.py 通过它启停。"""

    _instance: "ApiServerManager | None" = None

    def __init__(self):
        self._server = None       # uvicorn.Server
        self._thread = None       # threading.Thread
        self._host = "127.0.0.1"
        self._port = 8765

    @classmethod
    def instance(cls) -> "ApiServerManager":
        if cls._instance is None:
            cls._instance = ApiServerManager()
        return cls._instance

    def is_running(self) -> bool:
        return (self._thread is not None and self._thread.is_alive()
                and self._server is not None and getattr(self._server, "started", False))

    @property
    def base_url(self) -> str:
        host = "127.0.0.1" if self._host in ("0.0.0.0", "") else self._host
        return f"http://{host}:{self._port}"

    def start(self, host: str, port: int):
        """启动服务;已在跑则直接返回。绑定失败 / 超时抛 RuntimeError。"""
        if self.is_running():
            return
        if not _configured_key():
            raise RuntimeError("未设置 API Key,拒绝启动(强制鉴权)")
        import uvicorn

        self._host, self._port = host, int(port)
        app = create_app()
        config = uvicorn.Config(app, host=host, port=int(port),
                                log_level="info", access_log=False)
        self._server = uvicorn.Server(config)
        # 非主线程不能装信号处理器(uvicorn 0.49 已自带保护,这里再兜一层)
        self._server.install_signal_handlers = lambda: None

        err_holder: dict = {}

        def _serve():
            try:
                self._server.run()
            except Exception as e:   # noqa: BLE001
                err_holder["err"] = e
                error(f"[api] uvicorn 退出: {e}")

        self._thread = threading.Thread(target=_serve, daemon=True,
                                        name="LaunchAI-API")
        self._thread.start()

        # 等待最多 ~5s 确认绑定成功
        deadline = time.time() + 5
        while time.time() < deadline:
            if getattr(self._server, "started", False):
                info(f"[api] 服务已启动 {self.base_url}")
                return
            if "err" in err_holder:
                raise RuntimeError(f"启动失败: {err_holder['err']}")
            if not self._thread.is_alive():
                raise RuntimeError(f"启动失败: {err_holder.get('err', '线程提前退出')}")
            time.sleep(0.1)
        raise RuntimeError("启动超时(端口可能被占用)")

    def stop(self):
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=5)
        info("[api] 服务已停止")
        self._server = None
        self._thread = None
