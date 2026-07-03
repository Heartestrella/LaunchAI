"""统一的「AI 工具底层包 / 仓库是否就绪」检查。

LLM 聊天的 _tool_xxx 入口、节点编辑器各 *Exec.execute 入口在启动真正的
worker 之前调用 ``check(tool_id)``:
  - 返回 None      → 就绪,可以放心启动 worker
  - 返回 str       → 中文错误,直接显示在工具卡片 / 抛 RuntimeError
                     给节点引擎,而不再让 worker 的 subprocess 抛出
                     `ModuleNotFoundError` 整页 stack trace 污染对话

不直接 import 工具底层包 —— 那样 settings 路径上偶尔 import 这个模块时
就会被一票重依赖卡住。检查走两条:
  1) pip 包 —— 用 ``PipWorker.is_package_installed`` (importlib find_spec,
     不需要真 import,O(ms))
  2) 仓库形式安装 (Applio / GPT-SoVITS) —— 先看 ``configs/config.json::installed.<name>``
     flag,flag 是 pip_worker 跑完整条安装流水线才写入,可靠但不完备;
     flag 缺失时回退到 ``_git_projects/<pkg>_<fork>`` 克隆目录是否存在,
     与 subpage_switch_pages._repo_tool_installed 的判定保持一致 ——
     避免"页面里能用、LLM 却说没装"的错配。

如果新加了工具,在 _TOOL_REQUIREMENTS 里添一行即可。
"""

import os

from utils.configer import get_field
from logger import debug


# tool_id -> (pkg, friendly_page)
# pkg 以 "flag:" 开头时走 installed.<x> config flag
_TOOL_REQUIREMENTS = {
    # LLM 聊天里的 tool name
    "demucs_separate":    ("demucs",          "音频分离 - Demucs"),
    "whisper_transcribe": ("openai-whisper",  "语音识别 - Whisper"),
    "rvc_convert":        ("flag:Applio",     "AI 变声 - RVC"),
    "gptsovits_tts":      ("flag:GPT-SoVITS", "语音合成 - GPT-SoVITS"),
    "yolo_detect":        ("ultralytics",     "目标检测 - YOLO"),
    "musicgen_compose":   ("audiocraft",      "音乐生成 - Audiocraft"),
    "audiogen_create":    ("audiocraft",      "音乐生成 - Audiocraft"),
    # 节点编辑器里的 Exec id
    "node.demucs":        ("demucs",          "音频分离 - Demucs"),
    "node.whisper":       ("openai-whisper",  "语音识别 - Whisper"),
    "node.rvc":           ("flag:Applio",     "AI 变声 - RVC"),
    "node.gptsovits":     ("flag:GPT-SoVITS", "语音合成 - GPT-SoVITS"),
    "node.audiocraft":    ("audiocraft",      "音乐生成 - Audiocraft"),
}


def _repo_flag_or_clone_present(name: str) -> tuple[bool, str]:
    """仓库式工具的就绪判定,与 subpage_switch_pages._repo_tool_installed 一致。

    先看 installed.<name> flag;flag 未写时回退看 _git_projects/<name>_<fork>
    克隆目录是否存在。返回 (ok, 用于日志的判定原因)。
    """
    if bool(get_field(f"installed.{name}", False)):
        return True, "flag=True"
    # 延迟 import,避免 utils 早期模块被 pip_worker 的重依赖拖住
    from workers.pip_worker import GIT_PROJECTS_ROOT, fork_map
    fork = fork_map.get(name)
    if not fork:
        return False, "flag=False & fork_map 无映射"
    clone_dir = os.path.join(GIT_PROJECTS_ROOT, f"{name}_{fork}")
    if os.path.isdir(clone_dir):
        return True, f"flag=False, 回退到克隆目录: {clone_dir}"
    return False, f"flag=False, 克隆目录不存在: {clone_dir}"


def check(tool_id: str):
    """工具底层包 / 仓库是否就绪。

    Returns:
        None: 已就绪
        str:  中文友好错误,可直接展示给用户 / 喂回 LLM
    """
    req = _TOOL_REQUIREMENTS.get(tool_id)
    if not req:
        debug(f"tool_ready.check({tool_id}) → 未登记, 放行")
        return None  # 未登记的工具不强制检查 (e.g. list_dir / mix_audio)
    pkg, page = req
    if pkg.startswith("flag:"):
        name = pkg[5:]
        ok, reason = _repo_flag_or_clone_present(name)
        display = name
        debug(f"tool_ready.check({tool_id}) → repo:{name} ok={ok} ({reason})")
    else:
        from workers.pip_worker import PipWorker
        ok = PipWorker.is_package_installed(pkg)
        display = pkg
        debug(f"tool_ready.check({tool_id}) → pip:{pkg} ok={ok}")
    if ok:
        return None
    return (f"{display} 尚未安装,请到导航栏「{page}」页完成安装后再试。"
            "(懒启动开启时首次进入该页会显示「正在加载…」,稍等即可看到安装入口)")
