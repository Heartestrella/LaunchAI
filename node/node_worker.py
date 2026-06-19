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


@register("text_input")
class TextInputExec(NodeExecutor):
    """文本输入节点。

    把参数 ``text`` 中的内容落到磁盘文件 输出 ``NodeValue(type="text", path=...)``。
    走文件而非 payload 字段是为了与现有 NodeValue 的 path 约定保持一致
    下游 (gptsovits / whisper 的下游再接) 都按 path 读 不用特判 payload。
    """

    def execute(self, ctx, inputs, params):
        text = (params.get("text") or "").strip()
        if not text:
            # 空文本不抛错 —— 与 file_input 空路径行为一致 下游缺输入会自报
            ctx.log(GraphWorker._html(
                "  · 未输入文本，跳过此节点", "#FF9800"))
            return {}

        out_dir = os.path.abspath("./output/_text_inputs")
        os.makedirs(out_dir, exist_ok=True)
        out_path = os.path.join(
            out_dir, f"text_{uuid.uuid4().hex[:8]}.txt")
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(text)
        ctx.log(GraphWorker._html(
            f"  · 写入文本 ({len(text)} 字) → {out_path}", "#CCCCCC"))
        return {"text_out": NodeValue(type="text", path=out_path)}


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


# ══════════════════════════════════════════════════════════════════════
#  内置 Executor：GPT-SoVITS .list 数据源
# ══════════════════════════════════════════════════════════════════════

@register("sovits_list_input")
class SovitsListInputExec(NodeExecutor):
    """解析 .list 数据集 取第 ``entry_index`` 条 输出 (音频, 文本)。

    用于一次性驱动下游 gptsovits 节点的 ``ref_audio`` + ``ref_text``。
    跟 subpage_gptsovits 里的 ListEntryPickerDialog 是同一份解析逻辑
    (utils.sovits_list.parse_sovits_list_file) 字面值保持一致。

    输出端口:
        audio_out (audio)  该条目对应的音频文件
        text_out  (text)   该条目对应的文本（写到 ./output/_text_inputs/）
    日志中会打印总条数 / 当前条目语种 让用户决定 gptsovits 节点的
    ref_language 设成什么；MVP 阶段不通过端口传递语种字段。
    """

    def execute(self, ctx, inputs, params):
        from utils.sovits_list import parse_sovits_list_file

        list_path = (params.get("list_path") or "").strip()
        if not list_path:
            ctx.log(GraphWorker._html(
                "  · 未指定 .list 路径 跳过此节点", "#FF9800"))
            return {}
        if not os.path.isfile(list_path):
            raise RuntimeError(f".list 文件不存在: {list_path}")

        try:
            entries = parse_sovits_list_file(list_path)
        except Exception as exc:
            raise RuntimeError(f".list 解析失败: {exc}") from exc

        if not entries:
            raise RuntimeError(
                f".list 文件中没有可识别的条目（字段格式不符）: {list_path}")

        # 可选：.list 原路径匹配不到时 用 audio_dir 按 basename 兜底
        # 与 remap_entries_audio_root 的区别: 这里只补救"原路径找不到"的条目
        # 原路径能用就保持不动 避免 audio_dir 里有同名但内容不同的文件覆盖
        audio_dir = (params.get("audio_dir") or "").strip()
        if audio_dir:
            if not os.path.isdir(audio_dir):
                raise RuntimeError(f"audio_dir 不是有效目录: {audio_dir}")
            missing_entries = [e for e in entries if not e.get("exists")]
            if missing_entries:
                # 一次性建立 basename → 绝对路径索引 避免对每条都 walk
                name_index: dict[str, str] = {}
                for dirpath, _, filenames in os.walk(audio_dir):
                    for fn in filenames:
                        name_index.setdefault(
                            fn, os.path.join(dirpath, fn))
                recovered = 0
                for e in missing_entries:
                    base = os.path.basename(e["audio"])
                    flat = os.path.join(audio_dir, base)
                    if os.path.isfile(flat):
                        new_path = flat
                    elif base in name_index:
                        new_path = name_index[base]
                    else:
                        continue
                    e["audio"] = os.path.normpath(new_path)
                    e["exists"] = True
                    recovered += 1
                ctx.log(GraphWorker._html(
                    f"  · audio_dir 兜底: 缺失 {len(missing_entries)} 条 "
                    f"恢复 {recovered} 条", "#CCCCCC"))

        total   = len(entries)
        usable  = [i for i, e in enumerate(entries) if e.get("exists")]
        missing = total - len(usable)

        ctx.log(GraphWorker._html(
            f"  · 共 {total} 条 可用 {len(usable)} 条 缺失 {missing} 条",
            "#CCCCCC"))

        idx_raw = params.get("entry_index", 0)
        try:
            idx = int(idx_raw)
        except (TypeError, ValueError):
            raise RuntimeError(f"entry_index 不是整数: {idx_raw!r}")

        # entry_index = -1 自动取首个可用 与 subpage "全选可用" 首条做主参考语义对齐
        if idx < 0:
            if not usable:
                hint = ("（可填 audio_dir 参数按 basename 重新匹配）"
                        if not audio_dir else "")
                raise RuntimeError(
                    f".list 中没有任何可用条目（{total} 条音频全部缺失）{hint}")
            idx = usable[0]
            ctx.log(GraphWorker._html(
                f"  · entry_index=-1 自动选首个可用条目 → 第 {idx} 条",
                "#CCCCCC"))
        elif idx >= total:
            raise RuntimeError(
                f"entry_index={idx} 越界（共 {total} 条 0..{total - 1}）")

        entry = entries[idx]
        if not entry.get("exists"):
            # 主动给出可用条目的索引提示 + audio_dir 补救建议
            hint_parts = []
            if usable:
                preview = usable[:5]
                more = f" 等共 {len(usable)} 条" if len(usable) > 5 else ""
                hint_parts.append(f"可用条目索引: {preview}{more}")
            if not audio_dir:
                hint_parts.append(
                    "或填写 audio_dir 参数指向本机音频目录按 basename 重新匹配")
            hint = f"（{' ; '.join(hint_parts)}）" if hint_parts else ""
            raise RuntimeError(
                f"第 {idx} 条音频不存在: {entry['audio']}{hint}")

        # 文本落盘 复用 text_input 节点的输出目录约定
        out_dir = os.path.abspath("./output/_text_inputs")
        os.makedirs(out_dir, exist_ok=True)
        text_path = os.path.join(
            out_dir, f"sovits_list_{uuid.uuid4().hex[:8]}.txt")
        with open(text_path, "w", encoding="utf-8") as f:
            f.write(entry["text"])

        ctx.log(GraphWorker._html(
            f"  · 选中第 {idx} 条 [{entry['speaker'] or '?'} · "
            f"{entry['lang'] or '?'}] {entry['audio']}", "#4CAF50"))
        ctx.log(GraphWorker._html(
            f"  · 文本 ({len(entry['text'])} 字) → {text_path}", "#CCCCCC"))

        return {
            "audio_out": NodeValue(type="audio", path=entry["audio"]),
            "text_out":  NodeValue(type="text",  path=text_path),
        }


# ══════════════════════════════════════════════════════════════════════
#  内置 Executor：GPT-SoVITS
# ══════════════════════════════════════════════════════════════════════

@register("gptsovits")
class GPTSoVITSExec(NodeExecutor):
    """调用现有 GPTSoVITSInferWorker。

    与 GPT-SoVITS 子页 UI 完全对齐：所有"用户在 UI 里要选/填的东西"都做成
    输入端口 上游通常用 ``file_input`` (file 端口可桥接到 file/audio) 和
    ``text_input`` (text 端口) 当源；端口未接时回退到同名 params。

    输入端口（优先于同名参数）:
        gpt_model    (file)   GPT .ckpt 路径
        sovits_model (file)   SoVITS .pth 路径
        ref_audio    (audio)  参考 wav (3~10s)
        ref_text     (text)   参考文本文件 内容会被 open() 读出
        target_text  (text)   目标文本文件 同上
    输出端口:
        audio_out    (audio)  合成后的 wav/flac
    """

    @staticmethod
    def _resolve_path(inputs: dict, port: str,
                      params: dict, key: str) -> str:
        """文件类输入：端口优先 参数兜底。"""
        v = inputs.get(port)
        if v is not None and v.path:
            return v.path
        return (params.get(key) or "").strip()

    @staticmethod
    def _resolve_text(inputs: dict, port: str,
                      params: dict, key: str,
                      ctx: ExecutionContext) -> str:
        """文本类输入：端口接通时读 NodeValue.path 文件 失败回退到参数。"""
        v = inputs.get(port)
        if v is not None and v.path and os.path.isfile(v.path):
            try:
                with open(v.path, "r", encoding="utf-8") as f:
                    txt = f.read().strip()
                if txt:
                    ctx.log(GraphWorker._html(
                        f"  · 从 {port} 端口读取 {len(txt)} 字", "#CCCCCC"))
                    return txt
            except Exception as exc:
                ctx.log(GraphWorker._html(
                    f"  · 读取 {port} 端口失败，回退到参数: {exc}",
                    "#FF9800"))
        return (params.get(key) or "").strip()

    def execute(self, ctx, inputs, params):
        from workers.gptsovits_worker import GPTSoVITSInferWorker

        # ── 必填：模型 + 参考音频 ──────────────────────────────────────
        gpt_path = self._resolve_path(inputs, "gpt_model", params, "gpt_model")
        if not gpt_path:
            raise RuntimeError(
                "gptsovits 缺少 GPT 模型（gpt_model 端口未连接且参数也未填）")

        sovits_path = self._resolve_path(
            inputs, "sovits_model", params, "sovits_model")
        if not sovits_path:
            raise RuntimeError(
                "gptsovits 缺少 SoVITS 模型（sovits_model 端口未连接且参数也未填）")

        ref_audio = inputs.get("ref_audio")
        if ref_audio is None or not ref_audio.path:
            raise RuntimeError("gptsovits 缺少参考音频（ref_audio 未连接）")

        ref_text = self._resolve_text(
            inputs, "ref_text", params, "ref_text", ctx)
        if not ref_text:
            raise RuntimeError(
                "gptsovits 缺少参考文本（ref_text 端口未连接且参数也未填）")

        target_text = self._resolve_text(
            inputs, "target_text", params, "target_text", ctx)
        if not target_text:
            raise RuntimeError(
                "gptsovits 缺少目标文本（target_text 端口未连接且参数也未填）")

        # ── 输出格式 + 路径 ───────────────────────────────────────────
        fmt = str(params.get("format", "wav")).lower()
        if fmt not in ("wav", "flac"):
            fmt = "wav"

        output_dir = (params.get("output")
                      or os.path.abspath("./output"))
        os.makedirs(output_dir, exist_ok=True)
        output_path = os.path.join(
            output_dir, f"gptsovits_{uuid.uuid4().hex[:8]}.{fmt}")

        worker_params = {
            "gpt_model":       gpt_path,
            "sovits_model":    sovits_path,
            "ref_audio":       ref_audio.path,
            "ref_text":        ref_text,
            "ref_language":    params.get("ref_language", "中文"),
            "target_text":     target_text,
            "target_language": params.get("target_language", "中文"),
            "output":          output_path,
            "how_to_cut":      params.get("how_to_cut", "不切"),
            "top_k":           int(params.get("top_k", 15) or 15),
            "top_p":           float(params.get("top_p", 1.0) or 1.0),
            "temperature":     float(params.get("temperature", 1.0) or 1.0),
            "speed":           float(params.get("speed", 1.0) or 1.0),
            "device":          params.get("device", "cuda:0"),
        }

        worker = GPTSoVITSInferWorker(worker_params)

        def _fwd_output(line: str):
            ctx.log(line)

        def _fwd_progress(percent: int, status: str):
            ctx.sub_progress(percent, status)

        out_path, err = run_qthread_blocking(
            worker,
            on_output=_fwd_output,
            on_progress=_fwd_progress,
            on_cancelled=lambda: ctx.cancelled,
        )
        # 取消优先：避免给上层抛"输出文件无效"这类假错
        if ctx.cancelled:
            return {}
        if err:
            raise RuntimeError(err)
        if not out_path or not os.path.isfile(out_path):
            raise RuntimeError(f"gptsovits 输出文件无效: {out_path}")

        ctx.log(GraphWorker._html(
            f"  · gptsovits 完成，输出 {out_path}", "#4CAF50"))
        return {"audio_out": NodeValue(type="audio", path=out_path)}


# ══════════════════════════════════════════════════════════════════════
#  内置 Executor：Real-ESRGAN 图像超分
# ══════════════════════════════════════════════════════════════════════

@register("realesrgan")
class RealESRGANExec(NodeExecutor):
    """调用 RealESRGANWorker（realesrgan-ncnn-vulkan）执行图像超分。

    输入端口: image_in  (image)
    输出端口: image_out (image)
    """

    @staticmethod
    def _normalize_gpu_id(raw) -> str:
        """属性面板里的设备值（cpu / cuda:N）转成 ncnn-vulkan 的 -g 参数。

        - "cpu"      → "-1"   （worker 内部识别 -1 后不传 -g，走 CPU 路径）
        - "cuda:N"   → "N"
        - "auto"/"" → "auto"  （worker 不传 -g，让 ncnn 自动选第一张 GPU）
        - 其它纯数字 / "-1" 直接透传
        """
        s = str(raw).strip().lower()
        if s in ("", "auto"):
            return "auto"
        if s == "cpu":
            return "-1"
        if s.startswith("cuda:"):
            return s.split(":", 1)[1] or "auto"
        return s

    def execute(self, ctx, inputs, params):
        from workers.realesrgan_worker import RealESRGANWorker, DEFAULT_EXE

        image_in = inputs.get("image_in")
        if image_in is None or not image_in.path:
            raise RuntimeError(
                "real-esrgan 缺少图像输入（image_in 未连接）")

        if not os.path.isfile(DEFAULT_EXE):
            raise RuntimeError(
                f"找不到 realesrgan-ncnn-vulkan.exe:\n{DEFAULT_EXE}\n"
                "请确认 resource/realesrgan-ncnn-vulkan/ 目录完整。")

        output_dir = (params.get("output")
                      or os.path.abspath("./esrgan_results"))
        os.makedirs(output_dir, exist_ok=True)

        worker_params = {
            "exe_path":   DEFAULT_EXE,
            "input":      image_in.path,
            "output_dir": output_dir,
            "model":      params.get("model", "realesrgan-x4plus"),
            "scale":      int(params.get("scale", 4) or 4),
            "tile":       int(params.get("tile", 0) or 0),
            "gpu_id":     self._normalize_gpu_id(params.get("gpu_id", "auto")),
            "fmt":        str(params.get("fmt", "png")).lower(),
            "tta":        bool(params.get("tta", False)),
        }

        worker = RealESRGANWorker(worker_params)

        def _fwd_output(line: str):
            ctx.log(line)

        def _fwd_progress(percent: int, status: str):
            ctx.sub_progress(percent, status)

        # RealESRGANWorker.finished 是 pyqtSignal(str, float)
        # run_qthread_blocking 内部的 _on_finished(payload) 只接 payload 一个参数，
        # PyQt 允许槽函数参数比信号少 —— 末尾的 elapsed 会被丢弃，payload 即输出路径。
        out_path, err = run_qthread_blocking(
            worker,
            on_output=_fwd_output,
            on_progress=_fwd_progress,
            on_cancelled=lambda: ctx.cancelled,
        )
        if ctx.cancelled:
            return {}
        if err:
            raise RuntimeError(err)
        if not out_path or not os.path.isfile(out_path):
            raise RuntimeError(f"real-esrgan 输出文件无效: {out_path}")

        ctx.log(GraphWorker._html(
            f"  · real-esrgan 完成，输出 {out_path}", "#4CAF50"))
        return {"image_out": NodeValue(type="image", path=out_path)}
