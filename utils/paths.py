"""
utils/paths.py
~~~~~~~~~~~~~~
统一的输出目录 / 模型下载目录解析。

约定布局（root 由 config.json 的 paths.root 控制，默认 <project_root>/data/）:

  <root>/
    outputs/
      demucs/              demucs_node/
      whisper/             whisper_node/
      realesrgan/          realesrgan_node/
      yolo/                yolo_node/
      rvc/                 rvc_node/
      gptsovits/           gptsovits_node/
      iopaint/
      node/
        file_output/  text_inputs/  format_convert/  image_resize/  audio_merge/
    models/
      whisper/   demucs/   yolo/   iopaint/

不使用 utils.atool.resource_path，因为它走 _MEIPASS / cwd，对可写目录不合适；
这里基于 __file__ 解析项目根，与 utils.configer 一致。
"""

from __future__ import annotations

import os
from typing import Optional

from utils.configer import get_field, update_global_config


# 工具 -> outputs/ 下的子目录名。集中此处，新增工具只动这里。
_OUTPUT_TOOL_DIRS: dict[str, str] = {
    "demucs":         "demucs",
    "demucs_node":    "demucs_node",
    "whisper":        "whisper",
    "whisper_node":   "whisper_node",
    "realesrgan":     "realesrgan",
    "realesrgan_node": "realesrgan_node",
    "yolo":           "yolo",
    "yolo_node":      "yolo_node",
    "rvc":            "rvc",
    "rvc_node":       "rvc_node",
    "gptsovits":      "gptsovits",
    "gptsovits_node": "gptsovits_node",
    "iopaint":        "iopaint",
    "audiocraft":     "audiocraft",
    "node":           "node",
}

# 工具 -> models/ 下的子目录名
_MODEL_TOOL_DIRS: dict[str, str] = {
    "whisper":    "whisper",
    "demucs":     "demucs",
    "yolo":       "yolo",
    "iopaint":    "iopaint",
    "audiocraft": "audiocraft",
    # ── 用户自抓的角色/语音模型 由「找模型」页面写入 ──
    # RVC 模型一般成对出现 .pth + .index 直接放 data/models/rvc/<name>/
    "rvc":        "rvc",
    # GPT-SoVITS 一份 .ckpt + 一份 .pth(SoVITS) + 可选参考音频/.list
    # 放 data/models/gptsovits/<name>/
    "gptsovits":  "gptsovits",
}


def project_root() -> str:
    """项目根目录（utils/ 的上一级），与 utils.configer 保持一致。"""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _default_root() -> str:
    return os.path.join(project_root(), "data")


def root() -> str:
    """读取归一化根目录的绝对路径（来自 config 或默认）。"""
    r = get_field("paths.root", None) or _default_root()
    return os.path.abspath(r)


def output_dir(tool: str, sub: Optional[str] = None) -> str:
    """
    返回 <root>/outputs/<tool>[/<sub>] 的绝对路径，并保证目录存在。
    tool 必须是 _OUTPUT_TOOL_DIRS 的 key。
    """
    if tool not in _OUTPUT_TOOL_DIRS:
        raise KeyError(f"unknown output tool: {tool!r}")
    parts = [root(), "outputs", _OUTPUT_TOOL_DIRS[tool]]
    if sub:
        parts.append(sub)
    path = os.path.join(*parts)
    os.makedirs(path, exist_ok=True)
    return path


def model_dir(tool: str) -> str:
    """返回 <root>/models/<tool> 的绝对路径，并保证目录存在。"""
    if tool not in _MODEL_TOOL_DIRS:
        raise KeyError(f"unknown model tool: {tool!r}")
    path = os.path.join(root(), "models", _MODEL_TOOL_DIRS[tool])
    os.makedirs(path, exist_ok=True)
    return path


def subprocess_env(*, torch_home: Optional[str] = None,
                   hf_home: Optional[str] = None) -> dict:
    """
    返回 os.environ.copy() 后注入指定缓存环境变量的字典，
    供 subprocess.Popen(..., env=...) 使用。
    """
    env = os.environ.copy()
    if torch_home:
        env["TORCH_HOME"] = torch_home
    if hf_home:
        env["HF_HOME"] = hf_home
        env["HUGGINGFACE_HUB_CACHE"] = hf_home
    return env


def apply_inproc_env(*, xdg: Optional[str] = None,
                     hf: Optional[str] = None,
                     torch: Optional[str] = None) -> None:
    """
    给同进程环境（即将 import 第三方库前）写入缓存目录环境变量。
    用于 IOPaint 等在主进程内 import 的库。
    """
    if xdg:
        os.environ["XDG_CACHE_HOME"] = xdg
    if hf:
        os.environ["HF_HOME"] = hf
        os.environ["HUGGINGFACE_HUB_CACHE"] = hf
    if torch:
        os.environ["TORCH_HOME"] = torch


def ensure_defaults() -> str:
    """
    首启时：如果 config 中没有 paths.root，写入默认值；
    并保证 outputs/ models/ 全套子目录在磁盘上存在。
    返回当前 root 的绝对路径。
    """
    configured = get_field("paths.root", None)
    if not configured:
        update_global_config({"paths": {"root": _default_root()}}, merge=True)

    r = root()
    os.makedirs(os.path.join(r, "outputs"), exist_ok=True)
    os.makedirs(os.path.join(r, "models"), exist_ok=True)
    for name in _OUTPUT_TOOL_DIRS.values():
        os.makedirs(os.path.join(r, "outputs", name), exist_ok=True)
    for name in _MODEL_TOOL_DIRS.values():
        os.makedirs(os.path.join(r, "models", name), exist_ok=True)
    return r
