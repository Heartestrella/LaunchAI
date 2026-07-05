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

    如果 configs/config.json.proxy.enabled 为 true，还会把
    HTTP_PROXY/HTTPS_PROXY/ALL_PROXY/NO_PROXY(大小写各一份)注入。
    apply_proxy_env() 已在 app 启动时把这些写进当前进程的 os.environ,
    这里再显式合并一次是防御措施:防止调用方在启动前构造 env、或代理开关
    被临时改动后 os.environ 里的键被清理但父环境残留。
    """
    env = os.environ.copy()
    if torch_home:
        env["TORCH_HOME"] = torch_home
    if hf_home:
        env["HF_HOME"] = hf_home
        env["HUGGINGFACE_HUB_CACHE"] = hf_home
    px = _proxy_env_dict()
    if px:
        env.update(px)
    else:
        # 代理被关掉时,把父环境残留的键也剔除(否则子进程还会走系统代理)
        for k in _PROXY_ENV_KEYS:
            env.pop(k, None)
    return env


# 代理相关环境变量键 —— urllib/requests/curl 各自读的键集合。都写一遍,
# 大小写各一份避免第三方库大小写敏感差异导致漏读(requests 优先小写,urllib 都读)。
_PROXY_ENV_KEYS = (
    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
    "http_proxy", "https_proxy", "all_proxy", "no_proxy",
)


def _proxy_env_dict() -> dict:
    """按 configs/config.json.proxy 生成一份准备写入 env 的代理键值对。
    未启用/未填 URL 就返回 {},让调用方按"关闭"路径处理。"""
    # 延迟 import 避免在 configer 加载完前调用出问题
    from utils.configer import get_field
    cfg = get_field("proxy", {}) or {}
    if not cfg.get("enabled"):
        return {}
    url = (cfg.get("url") or "").strip()
    if not url:
        return {}
    no_proxy = (cfg.get("no_proxy") or
                "127.0.0.1,localhost,0.0.0.0,::1").strip()
    d = {}
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
              "http_proxy", "https_proxy", "all_proxy"):
        d[k] = url
    d["NO_PROXY"] = no_proxy
    d["no_proxy"] = no_proxy
    return d


def apply_proxy_env() -> None:
    """按当前 config 把代理写入 os.environ,并重装 urllib 默认 opener。

    - 启用: 8 个大小写变体都写,同时 urllib.install_opener 装上 ProxyHandler
      让 in-process 的 urlopen (LLM chat 的流式请求)立即走代理
    - 禁用: 把 _PROXY_ENV_KEYS 全部从 os.environ 剔除(否则残留会让子进程仍
      走系统代理),urllib 也换成空 ProxyHandler,显式绕过环境变量

    settings 卡切开关时调这一下即可,不需要重启。
    """
    active = _proxy_env_dict()
    if active:
        for k, v in active.items():
            os.environ[k] = v
    else:
        for k in _PROXY_ENV_KEYS:
            os.environ.pop(k, None)
    # 立即让 urllib 默认 opener 拿到新配置。urlopen(req) 走的是 install_opener
    # 设置的 opener;若不显式重装,Python 只会在第一次调用时 snapshot 一次 env。
    try:
        import urllib.request
        if active:
            handler = urllib.request.ProxyHandler(
                {"http": active["HTTP_PROXY"],
                 "https": active["HTTPS_PROXY"]})
        else:
            # 空 dict 表示"显式不用代理",而不是"从 env 读"
            handler = urllib.request.ProxyHandler({})
        urllib.request.install_opener(urllib.request.build_opener(handler))
    except Exception:
        # 装 opener 失败不影响子进程路径,不硬中断
        pass


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
