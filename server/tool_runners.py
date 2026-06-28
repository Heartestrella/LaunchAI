"""server/tool_runners.py
~~~~~~~~~~~~~~~~~~~~~~~~~
把 8 个工具的调用归一成一个 ``run_tool(tool, params, on_event) -> dict``。

**复用现有 worker 逻辑,不另写一套**:
  - 6 个子进程 / runner 工具(demucs / whisper / realesrgan / rvc / gptsovits /
    audiocraft):调各 worker 的 ``build_argv()`` 拼命令、``subprocess.Popen`` 跑、
    ``parse_percent()`` 解析进度 —— 全程不碰 Qt。
  - 2 个进程内工具(yolo / iopaint):直接实例化现有 QThread worker,把它的 Qt 信号
    连到回调,**同步**调用 ``worker.run()``(不 ``start()``、不需要事件循环;与 GUI
    一样在后台线程里跑模型)。

事件回调 ``on_event(kind, payload)``:
    kind="progress"  payload={"percent": int, "text": str}
    kind="log"       payload={"line": str}

返回 ``{"outputs": [绝对文件路径...], "elapsed": float}``。
出错抛 ``ToolError``(HTTP 层转 4xx/5xx)。

集中工具注册:新增工具只动这里 + 对应 worker。
"""
from __future__ import annotations

import os
import sys
import glob
import time
import subprocess

from utils import paths as _paths


class ToolError(Exception):
    """工具调用失败。code 给 HTTP 层决定状态码(400 入参错 / 503 未安装 / 500 跑挂)。"""
    def __init__(self, message: str, code: int = 500):
        super().__init__(message)
        self.code = code


# ── 工具注册表(元数据,供 /v1/models 列出;不在此 import worker) ──────────
# primary_input: /v1/invoke 顶层 input 字段映射到的参数名。
# params: 参数名 -> 默认值(给客户端看该传什么;None 表示必填 / 无默认)。
TOOLS: dict[str, dict] = {
    "demucs": {
        "display": "音频分离 - Demucs", "category": "audio", "primary_input": "input",
        "params": {"input": None, "model": "htdemucs", "device": "cuda",
                   "shifts": 1, "segment": 10, "overlap": 0.25, "format": "wav",
                   "two_stems": None,
                   "tracks": {"vocals": True, "drums": True, "bass": True, "other": True}},
    },
    "whisper": {
        "display": "语音识别 - Whisper", "category": "audio", "primary_input": "input",
        "params": {"input": None, "model": "small", "device": "cpu", "language": None,
                   "task": "transcribe", "output_format": "all", "beam_size": 5,
                   "best_of": 1, "temperature": 0.0, "word_timestamps": False,
                   "condition_on_previous_text": True},
    },
    "realesrgan": {
        "display": "图像超分 - Real-ESRGAN", "category": "image", "primary_input": "input",
        "params": {"input": None, "model": "realesrgan-x4plus", "scale": 4, "tile": 0,
                   "gpu_id": "auto", "tta": False, "fmt": "png", "threads": "1:2:2"},
    },
    "rvc": {
        "display": "AI 变声 - RVC", "category": "audio", "primary_input": "input",
        "params": {"input": None, "model_path": None, "index_path": "", "device": "cuda:0",
                   "f0_method": "rmvpe", "transpose": 0, "index_rate": 0.75,
                   "filter_radius": 3, "resample_sr": 0, "rms_mix_rate": 0.25,
                   "protect": 0.33, "split_infer": False, "format": "wav"},
    },
    "gptsovits": {
        "display": "语音合成 - GPT-SoVITS", "category": "audio", "primary_input": "target_text",
        "params": {"target_text": None, "target_language": "中文",
                   "gpt_model": None, "sovits_model": None, "ref_audio": None,
                   "aux_ref_audios": [], "ref_text": None, "ref_language": "中文",
                   "output": None, "how_to_cut": "不切", "top_k": 15, "top_p": 1.0,
                   "temperature": 1.0, "speed": 1.0, "device": "cuda:0"},
    },
    "audiocraft": {
        "display": "音乐生成 - Audiocraft", "category": "audio", "primary_input": "prompt",
        "params": {"prompt": None, "task": "musicgen", "model": "small", "device": "cuda",
                   "melody": "", "output_format": "wav", "duration": 8.0, "top_k": 250,
                   "top_p": 0.0, "temperature": 1.0, "cfg_coef": 3.0},
    },
    "yolo": {
        "display": "目标检测 - YOLO", "category": "image", "primary_input": "files",
        "params": {"files": None, "weights": "yolov8n.pt", "imgsz": 640, "conf": 0.25,
                   "iou": 0.45, "max_det": 300, "device": "auto", "fp16": True,
                   "tta": False, "agnostic": False, "classes": None,
                   "save_mode": "图片+TXT(YOLO)", "draw_boxes": True,
                   "draw_label": True, "line_w": 2},
    },
    "iopaint": {
        "display": "图像修复 - IOPaint", "category": "image", "primary_input": "image",
        "params": {"image": None, "mask": None, "model": "lama", "device": "cpu",
                   "hd_strategy": "CROP", "hd_strategy_crop_trigger_size": 800,
                   "hd_strategy_crop_margin": 128, "hd_strategy_resize_limit": 1280,
                   "ldm_steps": 20, "out_format": "png"},
    },
}

AUDIO_EXTS = (".wav", ".flac", ".mp3", ".m4a", ".ogg", ".opus")
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".bmp")


# ── 通用助手 ────────────────────────────────────────────────────────────
def _as_list(x) -> list:
    if x is None:
        return []
    return x if isinstance(x, list) else [x]


def _list_files(d: str) -> list:
    if not d or not os.path.isdir(d):
        return []
    out = []
    for root, _dirs, files in os.walk(d):
        for f in files:
            out.append(os.path.join(root, f))
    return out


def _new_files(dirs, t0: float, exts=None) -> list:
    """收集 dirs 下 mtime >= t0 的文件(即本次运行新建/覆写的产物)。"""
    seen, out = set(), []
    for d in dirs:
        for fp in _list_files(d):
            if fp in seen:
                continue
            seen.add(fp)
            try:
                if os.path.getmtime(fp) >= t0 - 2:
                    if exts is None or fp.lower().endswith(tuple(exts)):
                        out.append(fp)
            except OSError:
                pass
    return sorted(out)


def _run_subprocess(argv, on_event, parse_percent, env=None) -> int:
    """跑子进程、逐行解析进度/日志。返回 returncode。"""
    proc = subprocess.Popen(
        argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        bufsize=1, encoding="utf-8", errors="replace", env=env)
    for raw in iter(proc.stdout.readline, ""):
        line = raw.rstrip()
        if not line:
            continue
        pct = parse_percent(line) if parse_percent else None
        if pct is not None:
            on_event("progress", {"percent": int(pct), "text": line})
        else:
            on_event("log", {"line": line})
    proc.wait()
    return proc.returncode


def _drive_qt_worker(worker, on_event, outputs: list):
    """实例化好的 QThread worker:连信号→回调,同步 run()。outputs 收 file_done 路径。"""
    err = {}
    worker.progress.connect(
        lambda pct, txt: on_event("progress", {"percent": int(pct), "text": txt}))
    if hasattr(worker, "log_line"):
        worker.log_line.connect(lambda line: on_event("log", {"line": line}))
    if hasattr(worker, "output"):
        worker.output.connect(lambda line: on_event("log", {"line": line}))
    worker.file_done.connect(lambda _inp, out: outputs.append(out))
    worker.error.connect(lambda m: err.setdefault("msg", m))
    worker.run()   # 同步,跑在当前(uvicorn)线程
    if "msg" in err:
        raise ToolError(err["msg"], code=500)


# ── 逐工具 runner ───────────────────────────────────────────────────────
def _run_demucs(params, on_event, t0):
    from workers.demucs_worker import DemucsWorker
    inp = params.get("input")
    if not inp or not os.path.exists(inp):
        raise ToolError(f"输入文件不存在: {inp}", 400)
    env = _paths.subprocess_env(torch_home=_paths.model_dir("demucs"))
    rc = _run_subprocess(DemucsWorker.build_argv(params), on_event,
                         DemucsWorker.parse_percent, env=env)
    if rc != 0:
        raise ToolError(f"demucs 退出码 {rc}", 500)
    return _new_files([DemucsWorker.result_dir(params)], t0, AUDIO_EXTS)


def _run_whisper(params, on_event, t0):
    from workers.whisper_worker import WhisperWorker
    files = WhisperWorker.file_list(params)
    if not files:
        raise ToolError("未指定输入文件", 400)
    out_dir = params.get("output") or _paths.output_dir("whisper")
    env = _paths.subprocess_env(torch_home=_paths.model_dir("whisper"))
    for fp in files:
        if not os.path.exists(fp):
            raise ToolError(f"输入文件不存在: {fp}", 400)
        rc = _run_subprocess(WhisperWorker.build_argv(params, fp), on_event,
                             WhisperWorker.parse_percent, env=env)
        if rc != 0:
            raise ToolError(f"whisper 退出码 {rc}", 500)
    return _new_files([out_dir], t0)


def _run_realesrgan(params, on_event, t0):
    from workers.realesrgan_worker import RealESRGANWorker, DEFAULT_EXE
    inp = params.get("input")
    if not inp or not os.path.exists(inp):
        raise ToolError(f"输入路径不存在: {inp}", 400)
    if not os.path.isfile(params.get("exe_path", DEFAULT_EXE)):
        raise ToolError("找不到 realesrgan-ncnn-vulkan 可执行文件", 503)
    argv, out_path = RealESRGANWorker.build_argv(params)
    rc = _run_subprocess(argv, on_event, RealESRGANWorker.parse_percent)
    if rc != 0:
        raise ToolError(f"realesrgan 退出码 {rc}", 500)
    if os.path.isfile(out_path):
        return [out_path]
    return _new_files([out_path], t0, IMAGE_EXTS)


def _run_rvc(params, on_event, t0):
    from workers.rvc_worker import RVCInferWorker
    files = RVCInferWorker.file_list(params)
    if not files:
        raise ToolError("未指定输入音频", 400)
    mp = params.get("model_path")
    if not mp or not os.path.exists(mp):
        raise ToolError(f"模型文件不存在: {mp}", 400)
    out_dir = params.get("output") or _paths.output_dir("rvc")
    os.makedirs(out_dir, exist_ok=True)
    outs = []
    for fp in files:
        if not os.path.exists(fp):
            raise ToolError(f"输入文件不存在: {fp}", 400)
        op = RVCInferWorker.output_path(params, fp)
        rc = _run_subprocess(RVCInferWorker.build_argv(params, fp, op), on_event,
                             RVCInferWorker.parse_percent)
        if rc != 0:
            raise ToolError(f"rvc 退出码 {rc} ({fp})", 500)
        outs.append(op)
    return outs


def _run_gptsovits(params, on_event, t0):
    from workers.gptsovits_worker import GPTSoVITSInferWorker
    for key in ("gpt_model", "sovits_model", "ref_audio"):
        v = params.get(key)
        if not v or not os.path.exists(v):
            raise ToolError(f"{key} 不存在: {v}", 400)
    if not (params.get("target_text") or "").strip():
        raise ToolError("target_text 为空", 400)
    out = params.get("output")
    if not out:
        out = os.path.join(_paths.output_dir("gptsovits"),
                           f"gptsovits_{int(t0)}.wav")
        params["output"] = out
    os.makedirs(os.path.dirname(out), exist_ok=True)
    rc = _run_subprocess(GPTSoVITSInferWorker.build_argv(params), on_event, None)
    if rc != 0:
        raise ToolError(f"gptsovits 退出码 {rc}", 500)
    return [out] if os.path.exists(out) else _new_files(
        [_paths.output_dir("gptsovits")], t0, AUDIO_EXTS)


def _run_audiocraft(params, on_event, t0):
    from workers.audiocraft_worker import AudiocraftWorker
    prompts = AudiocraftWorker.prompt_list(params)
    if not prompts:
        raise ToolError("请至少填写一条提示词(prompt)", 400)
    out_dir = params.get("output") or _paths.output_dir("audiocraft")
    os.makedirs(out_dir, exist_ok=True)
    env = _paths.subprocess_env(hf_home=_paths.model_dir("audiocraft"))
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env["PYTHONUNBUFFERED"] = "1"
    rc = _run_subprocess(AudiocraftWorker.build_argv(params, prompts, out_dir),
                         on_event, None, env=env)
    if rc != 0:
        raise ToolError(f"audiocraft 退出码 {rc}", 500)
    return _new_files([out_dir], t0, AUDIO_EXTS)


def _run_yolo(params, on_event, t0):
    from workers.yolo_worker import YoloWorker
    files = _as_list(params.get("files") or params.get("input"))
    files = [f for f in files if f]
    if not files:
        raise ToolError("未指定输入图片(files)", 400)
    for f in files:
        if not os.path.exists(f):
            raise ToolError(f"输入文件不存在: {f}", 400)
    outputs = []
    worker = YoloWorker(files, params)
    _drive_qt_worker(worker, on_event, outputs)
    return outputs


def _run_iopaint(params, on_event, t0):
    from workers.iopaint_worker import IOPaintWorker
    img_p = params.get("image")
    mask_p = params.get("mask")
    if not img_p or not os.path.exists(img_p):
        raise ToolError(f"图片不存在: {img_p}", 400)
    if not mask_p or not os.path.exists(mask_p):
        raise ToolError(f"掩码不存在: {mask_p}", 400)
    try:
        import cv2
    except ImportError as e:
        raise ToolError(f"未安装 IOPaint 依赖(opencv): {e}", 503)
    img_bgr = cv2.imread(img_p, cv2.IMREAD_COLOR)
    if img_bgr is None:
        raise ToolError(f"无法读取图片: {img_p}", 400)
    image_rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)
    mask_gray = cv2.imread(mask_p, cv2.IMREAD_GRAYSCALE)
    if mask_gray is None:
        raise ToolError(f"无法读取掩码: {mask_p}", 400)
    iparams = {k: v for k, v in params.items() if k not in ("image", "mask")}
    iparams.setdefault("out_dir", _paths.output_dir("iopaint"))
    outputs = []
    worker = IOPaintWorker(image_rgb, mask_gray, img_p, iparams)
    _drive_qt_worker(worker, on_event, outputs)
    return outputs


_RUNNERS = {
    "demucs": _run_demucs,
    "whisper": _run_whisper,
    "realesrgan": _run_realesrgan,
    "rvc": _run_rvc,
    "gptsovits": _run_gptsovits,
    "audiocraft": _run_audiocraft,
    "yolo": _run_yolo,
    "iopaint": _run_iopaint,
}


def run_tool(tool: str, params: dict, on_event=None) -> dict:
    """跑一个工具,归一返回 {"outputs":[文件...], "elapsed":秒}。

    on_event(kind, payload) 可为 None(非流式时丢弃事件)。
    """
    if tool not in _RUNNERS:
        raise ToolError(f"未知工具: {tool}", 404)
    sink = on_event or (lambda *_a, **_k: None)
    t0 = time.time()
    outputs = _RUNNERS[tool](dict(params or {}), sink, t0)
    return {"outputs": outputs, "elapsed": round(time.time() - t0, 2)}
