"""
utils/sovits_list.py
~~~~~~~~~~~~~~~~~~~~
GPT-SoVITS .list 数据集解析 —— 纯 IO 逻辑 无 Qt 依赖

抽到 utils 是为了让 widgets/subpage 和 node 编辑器同时复用
否则 node_worker 反过来 import subpage 会拖入 PyQt 顶层依赖
"""

import os


# .list 文件的 lang 字段（小写）→ UI 中文字面值
# 字面值必须与 inference_webui.dict_language_v2 一致 否则推理 KeyError
LIST_LANG_MAP = {
    "zh":      "中文",
    "en":      "英文",
    "ja":      "日文",
    "ko":      "韩文",
    "yue":     "粤语",
    "all_zh":  "中文",
    "all_en":  "英文",
    "all_ja":  "日文",
    "all_ko":  "韩文",
    "all_yue": "粤语",
}


def parse_sovits_list_file(list_path: str) -> list[dict]:
    """解析 GPT-SoVITS 数据集 .list 文件

    每行格式 ``vocal_path|speaker_name|language|text``

    - 字段不足 4 段或纯注释/空行会被跳过
    - 路径若为相对路径 按 .list 所在目录解析
    - 音频是否存在只作为 ``exists`` 字段保留 不过滤
      让调用方决定怎么提示缺文件的条目

    Returns:
        ``[{"audio": abs_path, "speaker": str, "lang": cn_name,
            "text": str, "exists": bool}, ...]``
    """
    entries: list[dict] = []
    base_dir = os.path.dirname(os.path.abspath(list_path))
    try:
        with open(list_path, "r", encoding="utf-8") as f:
            raw_lines = f.readlines()
    except UnicodeDecodeError:
        # 极少数 .list 是 GBK 编码（早期 Windows 工具产出）
        with open(list_path, "r", encoding="gbk", errors="ignore") as f:
            raw_lines = f.readlines()

    for line in raw_lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("|")
        if len(parts) < 4:
            continue
        audio_raw, speaker, lang_code, text = (
            parts[0], parts[1], parts[2], "|".join(parts[3:]))

        audio_path = audio_raw.strip().replace("\\", "/")
        if not os.path.isabs(audio_path):
            audio_path = os.path.normpath(
                os.path.join(base_dir, audio_path))

        entries.append({
            "audio":   audio_path,
            "speaker": speaker.strip(),
            "lang":    LIST_LANG_MAP.get(lang_code.strip().lower(), ""),
            "text":    text.strip(),
            "exists":  os.path.isfile(audio_path),
        })
    return entries


def remap_entries_audio_root(entries: list[dict],
                             audio_root: str) -> list[dict]:
    """按用户指定的音频根目录重新匹配 entries 中的音频路径

    适用场景: .list 由别的机器产出 写死了训练机的绝对路径
    本机的音频在另一个目录 用户提供新根目录后按文件名重映射

    匹配策略 (依次尝试):
        1) ``<audio_root>/<basename(原路径)>``       —— flat 目录最常见
        2) 递归扫描 ``audio_root`` 取首个同名文件      —— 子目录场景
        3) 都找不到则保留原路径 ``exists`` 设 False

    Returns:
        新的 entries 列表 (原列表不动) ``audio`` 和 ``exists`` 字段已更新
    """
    if not audio_root or not os.path.isdir(audio_root):
        return entries

    # 预扫一次 建 basename → 绝对路径索引 避免每条都 walk
    # 同名重复时取首个 (字典序由 os.walk 决定 不保证稳定)
    name_index: dict[str, str] = {}
    for dirpath, _, filenames in os.walk(audio_root):
        for fn in filenames:
            name_index.setdefault(fn, os.path.join(dirpath, fn))

    remapped: list[dict] = []
    for e in entries:
        base = os.path.basename(e["audio"])
        flat = os.path.join(audio_root, base)
        if os.path.isfile(flat):
            new_path = flat
        elif base in name_index:
            new_path = name_index[base]
        else:
            new_path = e["audio"]   # 保留原值 让 UI 仍能显示缺失

        new_e = dict(e)
        new_e["audio"] = os.path.normpath(new_path)
        new_e["exists"] = os.path.isfile(new_path)
        remapped.append(new_e)
    return remapped
