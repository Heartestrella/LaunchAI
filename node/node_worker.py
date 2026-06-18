"""
node_worker.py
~~~~~~~~~~~~~~
节点图执行引擎 —— 标准接口。

设计要点
========
1. **NodeValue**：节点端口之间流动的数据包装。统一以"文件路径"为基础载体，
   并附带类型标签（audio/image/video/text/file 等）。后续新增的节点都只跟
   NodeValue 打交道，不关心上下游具体是什么。

2. **NodeExecutor**：每种节点类型对应一个 Executor 子类。
   - 在 ``execute(ctx, inputs, params) -> dict[port_name, NodeValue]`` 中实现逻辑。
   - 通过 ``@register("def_id")`` 装饰器注册到全局表。
   - 可以同步调用 ``QThread`` 形式的旧 Worker（如 ``DemucsWorker``）然后阻塞等待。

3. **GraphWorker**：``QThread`` 子类，沿用 LaunchAI 现有 worker 的信号风格
   ``progress / output / finished / error``。按拓扑顺序逐个节点执行。

4. **ExecutionContext**：贯穿一次运行，提供日志钩子、临时目录、取消标志、
   以及"上一个进度区间"换算（保证节点内进度 0-100 映射到全局百分比）。

后续扩展只需要再写一个 NodeExecutor 子类 + ``@register("xxx")``，节点编辑器
那一侧零改动即可。
"""

from __future__ import annotations

import os
import shutil
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

from PyQt6.QtCore import QThread, Qt, pyqtSignal

from node.node_graph import NodeGraph, NodeInstance, Connection


# ══════════════════════════════════════════════════════════════════════
#  端口数据：NodeValue
# ══════════════════════════════════════════════════════════════════════

@dataclass
class NodeValue:
    """端口之间流动的数据包装。

    暂以"文件路径 + 类型"为主要载体，足以覆盖 demucs/whisper/realesrgan 等
    全部基于文件 IO 的工具。后续要塞二进制 / numpy 数组时再扩 ``payload``。
    """
    type:    str                    # "audio" / "image" / "video" / "text" / "file"
    path:    str | None = None      # 文件路径（最常用）
    payload: Any = None             # 备用载荷（如 numpy / bytes）
    meta:    dict = field(default_factory=dict)

    def as_path(self) -> str:
        if self.path:
            return self.path
        raise ValueError(f"NodeValue[{self.type}] 没有文件路径")


# ══════════════════════════════════════════════════════════════════════
#  执行上下文
# ══════════════════════════════════════════════════════════════════════

class ExecutionContext:
    """一次图执行的共享上下文。"""

    def __init__(self, log_fn: Callable[[str], None],
                 progress_fn: Callable[[int, str], None],
                 cancelled_fn: Callable[[], bool]):
        self._log = log_fn
        self._progress = progress_fn
        self._cancelled = cancelled_fn

        # 当前节点的全局百分比区间（GraphWorker 在执行前设置）
        self.cur_lo = 0
        self.cur_hi = 100

    def log(self, msg: str):
        self._log(msg)

    def sub_progress(self, percent: int, text: str = ""):
        """节点内部 0-100 进度 → 映射到全局百分比。"""
        p = max(0, min(100, percent))
        g = int(self.cur_lo + (self.cur_hi - self.cur_lo) * p / 100)
        self._progress(g, text)

    @property
    def cancelled(self) -> bool:
        return self._cancelled()


# ══════════════════════════════════════════════════════════════════════
#  Executor 基类 + 注册表
# ══════════════════════════════════════════════════════════════════════

class NodeExecutor:
    """所有节点执行器的基类。"""

    def execute(self,
                ctx: ExecutionContext,
                inputs: dict[str, NodeValue],
                params: dict[str, Any]) -> dict[str, NodeValue]:
        """实现节点逻辑。

        Args:
            ctx:    执行上下文
            inputs: ``{port_name: NodeValue}``，未连接的端口键不存在
            params: 节点参数（已合并默认值）

        Returns:
            ``{port_name: NodeValue}``，键须与 NodeDef.outputs 对应
        """
        raise NotImplementedError


_REGISTRY: dict[str, NodeExecutor] = {}


def register(def_id: str):
    """将一个 NodeExecutor 子类注册到 def_id。"""
    def deco(cls):
        _REGISTRY[def_id] = cls()
        return cls
    return deco


def get_executor(def_id: str) -> NodeExecutor | None:
    return _REGISTRY.get(def_id)


# ══════════════════════════════════════════════════════════════════════
#  GraphWorker：拓扑执行整张图
# ══════════════════════════════════════════════════════════════════════

class GraphWorker(QThread):
    """按拓扑序执行 NodeGraph，沿用项目统一的 worker 信号风格。"""

    progress = pyqtSignal(int, str)   # 全局百分比, 状态文字
    output   = pyqtSignal(str)        # HTML 日志行
    finished = pyqtSignal(dict)       # {iid: {port: NodeValue}}
    error    = pyqtSignal(str)

    def __init__(self, graph: NodeGraph, parent=None):
        super().__init__(parent)
        self.graph = graph
        self._cancelled = False
        self._results: dict[str, dict[str, NodeValue]] = {}

    # ── 外部接口 ──────────────────────────────────────────────────────
    def cancel(self):
        self._cancelled = True

    # ── HTML 日志辅助 ─────────────────────────────────────────────────
    @staticmethod
    def _html(text: str, color: str | None = None, bold: bool = False) -> str:
        if not color and not bold:
            return text
        style = []
        if color:
            style.append(f"color:{color}")
        if bold:
            style.append("font-weight:bold")
        return f'<span style="{";".join(style)}">{text}</span>'

    def _emit_log(self, text: str):
        self.output.emit(text)

    # ── 主流程 ────────────────────────────────────────────────────────
    def run(self):
        try:
            order = self.graph.topological_order()
            if order is None:
                self.error.emit("检测到循环依赖，无法执行")
                return
            if not order:
                self.error.emit("图为空")
                return

            executable = [iid for iid in order
                          if get_executor(self.graph.nodes[iid].def_id) is not None]
            total = len(executable) or 1

            self._emit_log(self._html(
                f"开始执行 {total} 个可执行节点（共 {len(order)} 个）",
                "#4CAF50", bold=True))

            for step, iid in enumerate(order):
                if self._cancelled:
                    self._emit_log(self._html("用户取消了执行", "#FF9800"))
                    self.error.emit("用户取消了执行")
                    return

                node = self.graph.nodes[iid]
                executor = get_executor(node.def_id)
                if executor is None:
                    self._emit_log(self._html(
                        f"跳过 {node.title}（无 executor 实现）", "#888888"))
                    continue

                # 收集已连接的输入
                inputs = self._collect_inputs(node)

                # 全局进度区间分配
                done_idx = sum(
                    1 for j in order[:step]
                    if get_executor(self.graph.nodes[j].def_id) is not None)
                ctx = ExecutionContext(
                    log_fn=self._emit_log,
                    progress_fn=self.progress.emit,
                    cancelled_fn=lambda: self._cancelled,
                )
                ctx.cur_lo = int(done_idx / total * 100)
                ctx.cur_hi = int((done_idx + 1) / total * 100)

                self._emit_log(self._html(
                    f"▶ [{step + 1}/{len(order)}] {node.title}",
                    "#60CDFF", bold=True))
                self.progress.emit(ctx.cur_lo, node.title)

                try:
                    outputs = executor.execute(ctx, inputs, dict(node.params))
                except Exception as exc:
                    self.error.emit(f"{node.title} 执行失败: {exc}")
                    return

                if not isinstance(outputs, dict):
                    outputs = {}
                self._results[iid] = outputs

            self.progress.emit(100, "完成")
            self._emit_log(self._html("✓ 全部节点执行完成", "#4CAF50", bold=True))
            self.finished.emit(self._results)

        except Exception as exc:
            self.error.emit(f"执行过程中发生异常: {exc}")

    # ── 内部 ──────────────────────────────────────────────────────────
    def _collect_inputs(self, node: NodeInstance) -> dict[str, NodeValue]:
        result: dict[str, NodeValue] = {}
        nd = node.definition
        if nd is None:
            return result
        for port in nd.inputs:
            # 找到连到本端口的上游
            for conn in self.graph.connections.values():
                if conn.dst_iid == node.iid and conn.dst_port == port.name:
                    src_outs = self._results.get(conn.src_iid, {})
                    val = src_outs.get(conn.src_port)
                    if val is not None:
                        result[port.name] = val
                    break
        return result


# ══════════════════════════════════════════════════════════════════════
#  Qt 工具：把基于 QThread 的旧 Worker 同步等待
# ══════════════════════════════════════════════════════════════════════

def run_qthread_blocking(worker: QThread,
                         on_output: Callable[[str], None] | None = None,
                         on_progress: Callable[[int, str], None] | None = None,
                         on_cancelled: Callable[[], bool] | None = None) -> tuple[Any, str | None]:
    """同步运行一个 LaunchAI 风格的 QThread Worker。

    通过 ``QThread.wait()`` 轮询而非 QEventLoop，避免跨线程 ``quit()`` 的时序问题。
    上层 ``GraphWorker`` 本身就是后台线程，阻塞它没有副作用。

    Returns:
        ``(finished_payload, error_msg)`` —— 二选一，另一个为 None
    """
    result: dict[str, Any] = {"ok": None, "err": None}

    def _on_finished(payload):
        result["ok"] = payload

    def _on_error(msg):
        result["err"] = msg

    # 必须 DirectConnection：调用方阻塞在 wait() 上不跑事件循环，
    # 若是 QueuedConnection 信号永远到不了。
    direct = Qt.ConnectionType.DirectConnection
    if hasattr(worker, "finished"):
        worker.finished.connect(_on_finished, direct)
    if hasattr(worker, "error"):
        worker.error.connect(_on_error, direct)
    if on_output is not None and hasattr(worker, "output"):
        worker.output.connect(on_output, direct)
    if on_progress is not None and hasattr(worker, "progress"):
        worker.progress.connect(on_progress, direct)

    worker.start()

    # wait(timeout_ms) 是线程安全的；超时 → 检查取消标志 → 继续等
    while not worker.wait(200):
        if on_cancelled is not None and on_cancelled():
            if hasattr(worker, "cancel"):
                worker.cancel()
            worker.wait()   # 等彻底退出
            break

    return result["ok"], result["err"]


# ══════════════════════════════════════════════════════════════════════
#  内置 Executor：基础节点
# ══════════════════════════════════════════════════════════════════════

def _infer_type_from_ext(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    if ext in (".mp3", ".wav", ".flac", ".m4a", ".ogg", ".aac"):
        return "audio"
    if ext in (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff"):
        return "image"
    if ext in (".mp4", ".mov", ".mkv", ".avi", ".webm"):
        return "video"
    if ext in (".txt", ".srt", ".json", ".vtt", ".md"):
        return "text"
    return "file"


@register("file_input")
class FileInputExec(NodeExecutor):
    def execute(self, ctx, inputs, params):
        path = (params.get("path") or "").strip()
        if not path:
            # 空路径不抛错 —— 常见于 demo / 用户没填完的节点；
            # 输出端口不出值，下游若真需要会在自己那一步报缺输入。
            ctx.log(GraphWorker._html(
                "  · 未指定文件路径，跳过此节点", "#FF9800"))
            return {}
        if not os.path.exists(path):
            raise RuntimeError(f"文件不存在: {path}")
        ctx.log(GraphWorker._html(f"  · 读取 {path}", "#CCCCCC"))
        return {"file_out": NodeValue(type=_infer_type_from_ext(path), path=path)}


# 类型 → 默认扩展名（用于随机文件名场景）
_TYPE_DEFAULT_EXT = {
    "audio": ".wav",
    "image": ".png",
    "video": ".mp4",
    "text":  ".txt",
    "file":  "",
}


@register("file_output")
class FileOutputExec(NodeExecutor):
    """文件保存节点。

    文件名优先级：用户设定 > 输入文件文件名 > 随机生成（扩展名按数据类型推测）
    """

    def execute(self, ctx, inputs, params):
        src = inputs.get("file_in")
        if src is None or not src.path:
            ctx.log(GraphWorker._html("  · 未接入文件，跳过保存", "#FF9800"))
            return {}

        directory = (params.get("directory") or "./output").strip()
        os.makedirs(directory, exist_ok=True)

        filename = (params.get("filename") or "").strip()
        if not filename:
            # 次优：用上游文件名
            base = os.path.basename(src.path.rstrip("/\\"))
            if base:
                filename = base
        if not filename:
            # 最后兜底：随机文件名 + 按类型推测扩展名
            ext = os.path.splitext(src.path)[1] if src.path else ""
            if not ext:
                ext = _TYPE_DEFAULT_EXT.get(src.type, "")
            filename = f"node_output_{uuid.uuid4().hex[:8]}{ext}"
            ctx.log(GraphWorker._html(
                f"  · 未指定文件名，已随机生成: {filename}", "#888888"))

        dst = os.path.join(directory, filename)

        if os.path.abspath(dst) == os.path.abspath(src.path):
            ctx.log(GraphWorker._html(f"  · 源与目标相同，跳过: {dst}", "#888888"))
        else:
            shutil.copy2(src.path, dst)
            ctx.log(GraphWorker._html(f"  · 已保存 → {dst}", "#4CAF50"))
        return {}


@register("preview")
class PreviewExec(NodeExecutor):
    """preview 节点只是 UI 上的展示，执行时把上游文件直通即可。"""

    def execute(self, ctx, inputs, params):
        src = inputs.get("input")
        if src and src.path:
            ctx.log(GraphWorker._html(f"  · 预览源: {src.path}", "#CCCCCC"))
        return {}


# ══════════════════════════════════════════════════════════════════════
#  内置 Executor：Demucs
# ══════════════════════════════════════════════════════════════════════

@register("demucs")
class DemucsExec(NodeExecutor):
    """调用现有 DemucsWorker，把 5 个 stem 输出映射到节点端口。

    输入端口: audio_in (audio)
    输出端口: vocals / drums / bass / other / mix
    """

    # demucs CLI 输出文件名 → 节点端口名
    _STEM_TO_PORT = {
        "vocals": "vocals",
        "drums":  "drums",
        "bass":   "bass",
        "other":  "other",
    }

    def execute(self, ctx, inputs, params):
        from workers.demucs_worker import DemucsWorker

        audio_in = inputs.get("audio_in")
        if audio_in is None or not audio_in.path:
            raise RuntimeError("demucs 缺少音频输入（audio_in 未连接）")

        output_dir = (params.get("output")
                      or os.path.abspath("./separated"))
        os.makedirs(output_dir, exist_ok=True)

        model = params.get("model", "htdemucs")
        fmt = params.get("format", "wav")

        # htdemucs 这类 Transformer 模型最大 segment 7.8s；7 对所有模型都安全
        segment = int(params.get("segment", 7) or 7)
        if segment > 7:
            segment = 7

        worker_params = {
            "input":   audio_in.path,
            "output":  output_dir,
            "model":   model,
            "device":  params.get("device", "cuda"),
            "shifts":  int(params.get("shifts", 1) or 1),
            "segment": segment,
            "overlap": float(params.get("overlap", 0.25) or 0.25),
            "format":  fmt,
            # 全部 4 个 stem 都要，给下游 5 个端口全输出
            "tracks":  {"vocals": True, "drums": True,
                        "bass": True,  "other": True},
        }

        worker = DemucsWorker(worker_params)

        def _fwd_output(line: str):
            ctx.log(line)

        def _fwd_progress(percent: int, status: str):
            ctx.sub_progress(percent, status)

        sep_dir, err = run_qthread_blocking(
            worker,
            on_output=_fwd_output,
            on_progress=_fwd_progress,
            on_cancelled=lambda: ctx.cancelled,
        )
        # 取消优先：避免给上层抛"输出目录无效"这类假错
        if ctx.cancelled:
            return {}
        if err:
            raise RuntimeError(err)
        if not sep_dir or not os.path.isdir(sep_dir):
            raise RuntimeError(f"demucs 输出目录无效: {sep_dir}")

        # 收集 stem 文件 —— 文件名形如 vocals.wav / drums.mp3
        outputs: dict[str, NodeValue] = {}
        for stem, port in self._STEM_TO_PORT.items():
            # 优先按指定格式找，找不到再退回常见格式
            candidates = [f"{stem}.{fmt}", f"{stem}.wav",
                          f"{stem}.flac", f"{stem}.mp3"]
            for name in candidates:
                full = os.path.join(sep_dir, name)
                if os.path.isfile(full):
                    outputs[port] = NodeValue(type="audio", path=full)
                    break
            if port not in outputs:
                ctx.log(GraphWorker._html(
                    f"  · 未找到 {stem} 输出文件", "#FF9800"))

        # mix 端口 —— demucs 不直接产出，但语义上等价于原始混音
        outputs["mix"] = NodeValue(type="audio", path=audio_in.path)

        ctx.log(GraphWorker._html(
            f"  · demucs 完成，输出目录 {sep_dir}", "#4CAF50"))
        return outputs


# ══════════════════════════════════════════════════════════════════════
#  内置 Executor：Whisper
# ══════════════════════════════════════════════════════════════════════

@register("whisper")
class WhisperExec(NodeExecutor):
    """调用现有 WhisperWorker，把转录产物映射到节点端口。

    输入端口: audio_in (audio)
    输出端口: transcript (转录文本) / srt (字幕文件) / json (时间戳JSON)
    """

    # 端口名 → (NodeValue.type, 候选扩展名列表)
    # 第一个为首选格式，找不到再依次退回（与 DemucsExec._STEM_TO_PORT 用法一致）
    _PORT_SPEC = {
        "transcript": ("text", [".txt", ".vtt", ".srt"]),
        "srt":        ("file", [".srt", ".vtt"]),
        "json":       ("text", [".json"]),
    }

    def execute(self, ctx, inputs, params):
        from workers.whisper_worker import WhisperWorker

        audio_in = inputs.get("audio_in")
        if audio_in is None or not audio_in.path:
            raise RuntimeError("whisper 缺少音频输入（audio_in 未连接）")

        output_dir = (params.get("output")
                      or os.path.abspath("./transcripts"))
        os.makedirs(output_dir, exist_ok=True)

        # "auto" 在 UI 侧表示"自动检测"；worker 透传给 whisper CLI 时需为 None
        language = params.get("language", "auto")
        if language in ("auto", "", None):
            language = None

        worker_params = {
            "input":         [audio_in.path],
            "output":        output_dir,
            "model":         params.get("model", "large-v3"),
            "device":        params.get("device", "cpu"),
            "language":      language,
            "task":          params.get("task", "transcribe"),
            # all → 让 worker 同时产出 txt/srt/vtt/json，便于多端口取用
            "output_format": "all",
        }

        worker = WhisperWorker(worker_params)

        def _fwd_output(line: str):
            ctx.log(line)

        def _fwd_progress(percent: int, status: str):
            ctx.sub_progress(percent, status)

        out_dir, err = run_qthread_blocking(
            worker,
            on_output=_fwd_output,
            on_progress=_fwd_progress,
            on_cancelled=lambda: ctx.cancelled,
        )
        # 取消优先：避免给上层抛"输出目录无效"这类假错
        if ctx.cancelled:
            return {}
        if err:
            raise RuntimeError(err)
        if not out_dir or not os.path.isdir(out_dir):
            raise RuntimeError(f"whisper 输出目录无效: {out_dir}")

        # whisper CLI 用输入文件的 stem 命名所有产物：<stem>.txt / .srt / .json …
        stem = os.path.splitext(os.path.basename(audio_in.path))[0]

        outputs: dict[str, NodeValue] = {}
        for port, (vtype, candidates) in self._PORT_SPEC.items():
            for ext in candidates:
                full = os.path.join(out_dir, stem + ext)
                if os.path.isfile(full):
                    outputs[port] = NodeValue(type=vtype, path=full)
                    break
            if port not in outputs:
                ctx.log(GraphWorker._html(
                    f"  · 未找到 {port} 端口对应的输出文件", "#FF9800"))

        ctx.log(GraphWorker._html(
            f"  · whisper 完成，输出目录 {out_dir}", "#4CAF50"))
        return outputs
