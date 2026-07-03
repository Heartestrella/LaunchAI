# widgets/subpage/subpage_llm_chat.py
# LaunchAI 内置 LLM 聊天页 —— OpenAI 兼容协议 / 流式 / 文件附件 / 配置持久化
#
# 这个页面与 `tests/llm_demo.py` 共享 UI 主体；改样式请改这里，
# tests/llm_demo.py 只是个独立运行壳子用于 UI 迭代。

import os
import re
import sys
import json
import html as _htmllib
import mimetypes
import urllib.request
import urllib.error
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import Qt, QThread, pyqtSignal, QSize, QTimer
from PyQt6.QtGui import QKeyEvent, QTextCursor
from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QFileDialog,
    QSizePolicy, QFrame,
)

from qfluentwidgets import (
    TitleLabel, BodyLabel, CaptionLabel, StrongBodyLabel,
    PrimaryPushButton, PushButton, TransparentToolButton,
    LineEdit, PasswordLineEdit, TextEdit, ComboBox, EditableComboBox,
    CardWidget, SmoothScrollArea,
    IconWidget, InfoBar, InfoBarPosition, FluentIcon as FIF,
)

from utils.configer import get_field, get_config_manager


# ============================================================================
# FIF 安全解析 —— 不同 qfluentwidgets 版本里 FIF 枚举差异不小，
# 直接用 FIF.XXX 容易 AttributeError。用 _fic 包一层。
# ============================================================================
def _fic(*candidates, default_name: str = "APPLICATION"):
    """按优先级返回第一个真实存在的 FIF.<name>；都没有时回落到 APPLICATION。"""
    for name in candidates:
        ic = getattr(FIF, name, None)
        if ic is not None:
            return ic
    return getattr(FIF, default_name, FIF.HOME)


ICON_HEADER = _fic("CHAT", "MESSAGE", "EDIT")
ICON_BOT = _fic("ROBOT", "IOT", "APPLICATION")
ICON_SEND = _fic("SEND_FILL", "SEND", "AIRPLANE")
ICON_CLEAR = _fic("BROOM", "DELETE", "REMOVE")
ICON_ATTACH = _fic("LINK", "ATTACHE", "DOCUMENT")
ICON_COPY = _fic("COPY", "PASTE", "DOCUMENT")
ICON_DOC = _fic("DOCUMENT", "PASTE")
ICON_FOLDER = _fic("FOLDER", "FOLDER_ADD", "DOCUMENT")
ICON_CLOSE = _fic("CLOSE", "CANCEL_MEDIUM", "REMOVE")
ICON_CHEV_R = _fic("RIGHT_ARROW", "CHEVRON_RIGHT", "MENU")
ICON_CHEV_D = _fic("DOWN", "ARROW_DOWN", "MENU")
ICON_SYNC = _fic("SYNC", "UPDATE", "ROTATE", "HISTORY")


# ============================================================================
# 设计 token —— 整文件统一从这里取色，避免 rgba(180,180,180,180) 这种临时灰
# ============================================================================
BRAND = "#0078D4"
INK_PRIMARY = "#EAEAEA"
INK_SECONDARY = "#B0B0B0"
INK_TERTIARY = "#7E7E7E"

HAIRLINE = "rgba(255, 255, 255, 0.08)"
USER_HAIRLINE = "rgba(0, 120, 212, 0.55)"
USER_SURFACE = "rgba(0, 120, 212, 0.07)"
ASSISTANT_AVATAR = "rgba(255, 255, 255, 0.06)"

CHIP_BG = "rgba(255, 255, 255, 0.06)"
ATTACH_USER_BG = "rgba(0, 120, 212, 0.18)"

PILL_BG_OK = "rgba(70, 200, 140, 0.14)"
PILL_FG_OK = "#7BD7B0"
PILL_BG_WARN = "rgba(220, 150, 50, 0.16)"
PILL_FG_WARN = "#E6B97A"

INPUT_MIN_H = 36
INPUT_MAX_H = 140


# ============================================================================
# System Prompt —— 写死，每次请求自动拼到 messages[0]
# 由“职责段” + “工具调用协议段”拼成，后者从 TOOL_REGISTRY 自动渲染，
# 新增工具只动 TOOL_REGISTRY 即可同步进 prompt。
# ============================================================================
SYSTEM_PROMPT_BASE = """你是 LaunchAI（奇点）内置的智能助手。
你的职责：
1. 用清晰、中肯的中文回答用户关于本地 AI 工具（Whisper / Demucs / Real-ESRGAN / YOLO / GPT-SoVITS / RVC / IOPaint / AudioCraft）的使用问题。
2. 当用户上传文件 / 目录时，结合内容进行分析、摘要、改写或答疑；遇到目录附件请用 list_dir / read_file 自行探查里面有什么。
3. 涉及命令、参数、错误日志时，优先给出可直接复制运行的结果，并用 ``` 代码块包裹。
4. 不要编造模型名称、文件路径或参数；不确定时如实说明。
5. 回答简洁，不需要客套，不要重复用户提问。
"""


# GPT-SoVITS 推理用得到的中文枚举:必须与 subpage_gptsovits.LANGUAGES /
# CUT_METHODS 字面值完全一致 —— 它们最终会作为 dict key 在 inference_webui 里
# 查表,写错会 KeyError 或静默不切。这里复制一份避免在 chat 模块里依赖
# subpage_gptsovits(后者 import 了大量 fluent 控件)。
_SOVITS_LANGS = ["中文", "英文", "日文", "粤语", "韩文",
                 "中英混合", "日英混合", "粤英混合", "韩英混合",
                 "多语种混合", "多语种混合(粤语)"]
_SOVITS_CUTS = ["不切", "凑四句一切", "凑50字一切", "按中文句号。切",
                "按英文句号.切", "按标点符号切"]


# Real-ESRGAN 实际可用模型名 —— 必须扫盘,因为不同发行版的命名差异巨大
# (旧版 realesrgan-x4plus,新版 realesrgan-plus-x4)。SYSTEM_PROMPT 是
# 模块加载时一次性渲染的,这里也只在 import 时扫一次,够用了。
def _scan_realesrgan_models() -> list[str]:
    """读 resource/realesrgan-ncnn-vulkan/models/*.param,返回模型名 stem。
    过滤掉含 'wdn' 的版本 —— 那是 Clip 层模型,ncnn-vulkan 跑不了。
    扫不到时返回历史命名列表作兜底,避免 TOOL_REGISTRY 渲染出空 enum。"""
    candidates = [
        os.path.join(os.getcwd(), "resource",
                     "realesrgan-ncnn-vulkan", "models"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     "..", "..", "resource",
                     "realesrgan-ncnn-vulkan", "models"),
    ]
    for d in candidates:
        if not os.path.isdir(d):
            continue
        try:
            out = []
            for fn in sorted(os.listdir(d)):
                if not fn.lower().endswith(".param"):
                    continue
                stem = os.path.splitext(fn)[0]
                if "wdn" in stem.lower():
                    continue
                out.append(stem)
            if out:
                return out
        except OSError:
            continue
    # 兜底 —— 仅出现在装包但没拷模型的状态
    return ["realesrgan-plus-x4", "realesrgan-plus-anime-x4",
            "realesr-animevideov3-x4"]


_REALESRGAN_MODELS = _scan_realesrgan_models()
# 默认模型:优先选含 "plus" 且不含 "anime" 的通用 4× 模型(同时兼容新旧命名),
# 否则取列表首个。与 node/node_registry.py 的策略保持一致。
_REALESRGAN_DEFAULT_MODEL = next(
    (m for m in _REALESRGAN_MODELS
     if "plus" in m.lower() and "anime" not in m.lower()),
    _REALESRGAN_MODELS[0],
)


# 工具表 —— 新增工具时在这里加一项，prompt 与执行调度都会同步生效。
# 字段:
#   summary  : 一行简介，写给 LLM 看
#   params   : {name: {type, required, default?, enum?, desc}}
#   returns  : 成功返回值的描述
TOOL_REGISTRY: dict[str, dict] = {
    "list_dir": {
        "summary": (
            "列出某个目录里的文件和子目录,用来探查用户给的素材 / 数据集文件夹里有什么。"
            "默认只列一层;给 recursive=true 时最多展开 2 层。"
            "返回的每个条目都带绝对路径,后续工具调用可直接复制使用,不要再做手工拼接。"
        ),
        "params": {
            "path": {
                "type": "string",
                "required": True,
                "desc": "要列出内容的目录绝对路径(通常来自用户消息里的"
                        " `--- 目录: ...` 附件标记)",
            },
            "recursive": {
                "type": "boolean",
                "required": False,
                "default": False,
                "desc": "是否递归子目录,默认 false;true 时最多展开 2 层并最多列 200 条",
            },
        },
        "returns": (
            "JSON 字符串:{path, count, truncated, entries:[{name,type,size,path}]}。"
            "type 是 file 或 dir;size 单位字节,目录为 null;path 是该条目的绝对路径。"
        ),
    },
    "read_file": {
        "summary": (
            "读取一个本地文本文件的内容,用来查看 SoVITS 的 .list 转写清单 / "
            ".txt 转写 / .json 配置等。"
            "不要用它读模型权重(.ckpt/.pth)、音频或其它二进制文件。"
        ),
        "params": {
            "path": {
                "type": "string",
                "required": True,
                "desc": "要读取的文件绝对路径",
            },
            "max_bytes": {
                "type": "number",
                "required": False,
                "default": 65536,
                "desc": "最多读取多少字节,默认 64 KB,上限 1 MB;超出会自动截断",
            },
        },
        "returns": (
            "首行是元信息 `[path: ..., size: ..., returned_bytes: ..., truncated: ...]`,"
            "之后是文件文本内容(UTF-8 解码,errors=replace)"
        ),
    },
    "demucs_separate": {
        "summary": (
            "调用本地 Demucs 把音频分离;两种模式根据 two_stems 切换:"
            "\n  - 省略 two_stems(默认):4 轨全分,产出 vocals.wav / drums.wav / bass.wav / other.wav"
            "\n  - two_stems=\"vocals\"(最常用):2 轨分,产出 vocals.wav(人声) + no_vocals.wav(其余三轨混合后的纯伴奏)"
            "\n  - two_stems=\"drums/bass/other\":同理,产出 <stem>.wav + no_<stem>.wav"
            "\n\n做翻唱 / 换声场景几乎都用 two_stems=\"vocals\":先拿到 vocals.wav 喂 RVC 或 GPT-SoVITS 换音色,"
            "拿到处理后的新人声后用 mix_audio 把新人声和原 no_vocals.wav 伴奏混回去得到成品。"
        ),
        "params": {
            "input": {
                "type": "string",
                "required": True,
                "desc": "待分离音频的本地绝对路径,支持 .mp3 / .wav / .flac / .m4a 等",
            },
            "model": {
                "type": "string",
                "required": False,
                "default": "htdemucs",
                "enum": ["htdemucs", "htdemucs_ft", "mdx_extra", "mdx"],
                "desc": "Demucs 模型名,默认 htdemucs",
            },
            "device": {
                "type": "string",
                "required": False,
                "default": "cuda",
                "enum": ["cuda", "cpu"],
                "desc": "推理设备,默认 cuda;无独立显卡或显存不够填 cpu",
            },
            "two_stems": {
                "type": "string",
                "required": False,
                "default": None,
                "enum": ["vocals", "drums", "bass", "other"],
                "desc": "2 轨分离:产出 <stem>.wav + no_<stem>.wav 两文件。"
                        "翻唱 / 换声场景填 \"vocals\";想 4 轨全分就省略",
            },
            "format": {
                "type": "string",
                "required": False,
                "default": "wav",
                "enum": ["wav", "mp3", "flac"],
                "desc": "输出格式,默认 wav",
            },
        },
        "returns": (
            "成功时返回**分离结果子目录**的绝对路径(形如 <output_root>/<model>/<input_stem>/),"
            "其下分模式包含:"
            "\n  - 默认 4 轨:vocals.wav / drums.wav / bass.wav / other.wav"
            "\n  - two_stems=vocals:vocals.wav + no_vocals.wav(no_vocals 即纯伴奏混合轨,后续 mix_audio 用)"
            "\n聊天里会自动列出这些文件并附预览按钮。"
        ),
    },
    "mix_audio": {
        "summary": (
            "用 ffmpeg 把多路音频合并成一路。两种模式:"
            "\n  - mix(默认,amix 叠加):同时播放多路,长度取最长,可调权重。"
            "翻唱场景标准用法:把 RVC / SoVITS 处理后的新人声 与 demucs 产出的 no_vocals.wav 伴奏 amix 起来,得到成品。"
            "\n  - concat(顺序拼接):多路首尾相连,要求各路采样率 / 声道一致。适合拼合辑。"
            "\n输入路径必须都是已经存在的本地文件;调本工具前确保上游(demucs / RVC 等)已经跑完。"
        ),
        "params": {
            "inputs": {
                "type": "array",
                "required": True,
                "desc": "至少 1 路本地音频绝对路径数组。"
                        "翻唱场景两路即可:[<rvc/sovits 处理后的 vocals>, <demucs 的 no_vocals.wav>]。"
                        "只传 1 路时直接复制,不走 ffmpeg",
            },
            "mode": {
                "type": "string",
                "required": False,
                "default": "mix",
                "enum": ["mix", "concat"],
                "desc": "mix=同时播放叠加(默认);concat=顺序拼接",
            },
            "weights": {
                "type": "array",
                "required": False,
                "default": None,
                "desc": "各路权重(数字数组),长度与 inputs 对齐;省略时各路 1.0 等权。"
                        "翻唱常用 [1.0, 0.6~0.8](人声满,伴奏压低一点),听感更舒服。"
                        "仅 mode=mix 生效",
            },
            "format": {
                "type": "string",
                "required": False,
                "default": "wav",
                "enum": ["wav", "mp3", "flac"],
                "desc": "输出格式,默认 wav",
            },
        },
        "returns": "成功时返回合成音频的绝对路径,聊天里会自动出现可播放卡片",
    },
    "whisper_transcribe": {
        "summary": (
            "调用本地 Whisper 把音频(或视频音轨)转成文字 / 字幕。"
            "支持一次传一个文件,也支持把一个文件数组一次性批量转录。"
            "\n用户如果丢的是目录,先用 list_dir 列出里面的音频文件,"
            "再把命中的路径以数组形式传给 input,工具会顺序处理并落到同一个输出目录。"
            "\n常见耗时: small 模型 1 分钟音频在 GPU 上 5~15 秒;"
            "large 系列慢 5~10 倍,设备改 cpu 再慢 10 倍以上,告诉用户合理预期。"
        ),
        "params": {
            "input": {
                "type": "string | array",
                "required": True,
                "desc": "待转录音频的本地绝对路径;可传单字符串,也可传字符串数组批量处理。"
                        "支持 .mp3 / .wav / .flac / .m4a / .mp4 等 ffmpeg 能读的格式",
            },
            "model": {
                "type": "string",
                "required": False,
                "default": "small",
                "enum": ["tiny", "base", "small", "medium", "large", "large-v3"],
                "desc": "Whisper 模型规模。小模型快但质量差,large 系列质量最好但慢且占显存。"
                        "首次使用会自动下载模型到本地缓存目录,后续复用",
            },
            "device": {
                "type": "string",
                "required": False,
                "default": "cuda",
                "enum": ["cuda", "cpu"],
                "desc": "推理设备,默认 cuda;无显卡或显存不够填 cpu",
            },
            "language": {
                "type": "string",
                "required": False,
                "default": None,
                "enum": ["auto", "zh", "en", "ja", "ko", "fr", "de", "es", "ru",
                         "it", "pt", "ar", "hi"],
                "desc": "音频语种的 ISO 639-1 编码。不确定 / 用户没说就留空或填 auto,"
                        "Whisper 会自己检测;明确给出能稍微提速并避免误判",
            },
            "task": {
                "type": "string",
                "required": False,
                "default": "transcribe",
                "enum": ["transcribe", "translate"],
                "desc": "transcribe 保持原语言转写;translate 把任意语言翻译成英文文本输出",
            },
            "output_format": {
                "type": "string",
                "required": False,
                "default": "all",
                "enum": ["all", "txt", "srt", "vtt", "json"],
                "desc": "输出格式;默认 all 同时产出 txt/srt/vtt/json/tsv",
            },
            "word_timestamps": {
                "type": "boolean",
                "required": False,
                "default": False,
                "desc": "是否输出逐词时间戳(更慢,但字幕/对齐场景需要)",
            },
        },
        "returns": "成功时返回输出目录的绝对路径(其下含与输入同名的 .txt/.srt/.vtt/.json 等文件)",
    },
    "rvc_convert": {
        "summary": (
            "调用本地 RVC 做声色转换 —— 把输入音频里的人声换成目标说话人的音色。"
            "输入若是带伴奏的整段音乐,RVC 会把伴奏也一起被音色化,效果差;"
            "推荐先用 demucs_separate 抽出 vocals.wav 再喂给本工具,然后另行混回伴奏。"
            "\n\n需要一份 RVC 模型权重 .pth;同名同目录的 .index 检索文件如果存在,"
            "带上能显著降低发音失真,但找不到就省略,不要瞎指一个不匹配的 .index。"
            "\n用户经常丢一个 RVC 模型包目录(里面通常有同名的 .pth + .index 一对),"
            "请用 list_dir 探查找出这一对再调用;一次只能用一个角色的模型。"
            "支持单文件或文件数组,会顺序处理并落到同一输出目录。"
        ),
        "params": {
            "input": {
                "type": "string | array",
                "required": True,
                "desc": "待转换音频的本地绝对路径,可单字符串或字符串数组批量处理。"
                        "强烈建议先经 demucs_separate 拿到 vocals.wav 再喂",
            },
            "model_path": {
                "type": "string",
                "required": True,
                "desc": "RVC 模型权重 .pth 绝对路径;由用户提供,"
                        "或从 list_dir 的结果里挑一份 .pth",
            },
            "index_path": {
                "type": "string",
                "required": False,
                "default": None,
                "desc": ".index 检索文件绝对路径,通常与 .pth 同名同目录。"
                        "找不到就省略,worker 会自动跳过;不要瞎指不匹配的 .index",
            },
            "device": {
                "type": "string",
                "required": False,
                "default": "cuda:0",
                "enum": ["cuda:0", "cpu"],
                "desc": "推理设备;无 GPU 或显存不足填 cpu(慢得多)",
            },
            "f0_method": {
                "type": "string",
                "required": False,
                "default": "rmvpe",
                "enum": ["rmvpe", "crepe", "harvest", "pm"],
                "desc": "音高提取算法。rmvpe 综合最好,默认即可;"
                        "唱歌 / 高音密集场景可试 crepe;pm 最快但抖",
            },
            "transpose": {
                "type": "number",
                "required": False,
                "default": 0,
                "desc": "变调半音(整数)。男声 -> 女声约 +12,女声 -> 男声约 -12;"
                        "同性别一般保持 0",
            },
            "index_rate": {
                "type": "number",
                "required": False,
                "default": 0.75,
                "desc": "检索特征占比 0.0~1.0;越高越像参考音色,越低越保留输入的发音",
            },
            "format": {
                "type": "string",
                "required": False,
                "default": "wav",
                "enum": ["wav", "flac", "mp3"],
                "desc": "输出格式",
            },
        },
        "returns": "成功时返回输出目录的绝对路径(其下每个输入对应一份 <name>_rvc.<format>)",
    },
    "gptsovits_tts": {
        "summary": (
            "调用本地 GPT-SoVITS 把任意文本合成成参考说话人音色的 wav。"
            "需要 5 项必填:目标文本 / GPT 权重(.ckpt) / SoVITS 权重(.pth) / 参考音频(.wav) / 参考文本。"
            "本工具不会读其它页面的状态,所有路径必须由你从对话上下文里自己定位。"
            "\n\n用户经常直接丢一个数据集 / 素材目录(在消息里表现为 `--- 目录: ...` 附件)。"
            "遇到这种情况,不要立刻调本工具,先按下面步骤探查再合成:"
            "\n  1) 用 list_dir 列出目录,优先找 .list 文件 —— 这是 GPT-SoVITS 标准转写清单。"
            "找到 .list 就用 read_file 打开它,每行 4 列以 `|` 分隔:"
            "`音频路径|说话人|语言|文本`。任选其中一行,把音频路径当 ref_audio、文本当 ref_text;"
            "音频路径常是相对路径,需要与 .list 所在目录拼成绝对路径。语言列(ZH/EN/JA/KO/YUE)"
            "映射到 ref_language 的中文取值(中文/英文/日文/韩文/粤语)。"
            "\n  2) 没 .list 时再翻别的:.ckpt 是 GPT 权重(gpt_model),.pth 是 SoVITS 权重(sovits_model),"
            ".wav/.mp3/.flac 是参考音频候选,.txt 可能放对应转写。如果目录有 \"GPT_weights\" / \"SoVITS_weights\" "
            "之类子目录,用 list_dir 带 recursive=true 再翻一层。"
            "\n  3) 任何一项找不到 / 不能确定时,把候选列出来让用户挑或补,不要瞎选路径。"
        ),
        "params": {
            "target_text": {
                "type": "string",
                "required": True,
                "desc": "要合成的目标文本;支持多句,长文走 how_to_cut 切分",
            },
            "gpt_model": {
                "type": "string",
                "required": True,
                "desc": "GPT 权重 .ckpt 绝对路径;用户直接给 / 或从 list_dir 的结果里挑一份 .ckpt",
            },
            "sovits_model": {
                "type": "string",
                "required": True,
                "desc": "SoVITS 权重 .pth 绝对路径;用户直接给 / 或从 list_dir 的结果里挑一份 .pth",
            },
            "ref_audio": {
                "type": "string",
                "required": True,
                "desc": "主参考音频(3~10s)绝对路径;用户直接给 / 或从 .list 抽一行,"
                        "把其中的音频路径与 .list 所在目录拼成绝对路径",
            },
            "ref_text": {
                "type": "string",
                "required": True,
                "desc": "参考音频对应的转写文本;用户直接给 / 或从 .list 同一行的文本列取得",
            },
            "target_language": {
                "type": "string",
                "required": False,
                "default": "中文",
                "enum": _SOVITS_LANGS,
                "desc": "目标语种(必须用列出的中文字面值,inference_webui 内部以此查表)",
            },
            "ref_language": {
                "type": "string",
                "required": False,
                "default": "中文",
                "enum": _SOVITS_LANGS,
                "desc": "参考音频语种(同 target_language 的取值规则)",
            },
            "how_to_cut": {
                "type": "string",
                "required": False,
                "default": "凑四句一切",
                "enum": _SOVITS_CUTS,
                "desc": "长文本切分策略",
            },
            "speed": {
                "type": "number",
                "required": False,
                "default": 1.0,
                "desc": "语速 0.5~2.0",
            },
            "device": {
                "type": "string",
                "required": False,
                "default": "cuda:0",
                "enum": ["cuda:0", "cpu"],
                "desc": "推理设备;无 GPU 或显存不足时填 cpu",
            },
        },
        "returns": "成功时返回合成 wav 的绝对路径,聊天里会自动出现可播放的输出卡片",
    },
    "yolo_detect": {
        "summary": (
            "调用本地 YOLO(v8 / v11) 在图片上做目标检测。"
            "支持单张、整个目录、路径数组 —— 目录会展开成里面所有图片一次跑完。"
            "\n模型选型(按 enum 取):n 最快但精度低,适合预览;s/m 是常用平衡档;"
            "l/x 高精度但慢且占显存。v11 比 v8 同档稍精且略慢,首选 yolov8m / yolo11m。"
            "\n首次使用某个模型会自动从 ultralytics GitHub 下载权重到本地缓存,后续复用。"
            "\n返回:标注图(画了框)落到 outputs/yolo/llm_<时间戳>/,聊天里会列出文件;"
            "结构化的检测框 / 类别 / 置信度同时按 save_mode 落 TXT 或 JSON 旁路保存。"
        ),
        "params": {
            "input": {
                "type": "string | array",
                "required": True,
                "desc": "待检测的本地图像绝对路径(.png/.jpg/.jpeg/.webp/.bmp),"
                        "或一个含图像的目录,或字符串数组批量处理",
            },
            "model": {
                "type": "string",
                "required": False,
                "default": "yolov8m.pt",
                "enum": ["yolov8n.pt", "yolov8s.pt", "yolov8m.pt",
                         "yolov8l.pt", "yolov8x.pt",
                         "yolo11n.pt", "yolo11s.pt", "yolo11m.pt",
                         "yolo11l.pt", "yolo11x.pt"],
                "desc": "权重文件名(含 .pt 后缀)。从 enum 挑;别凭历史名字猜",
            },
            "conf": {
                "type": "number",
                "required": False,
                "default": 0.25,
                "desc": "置信度阈值 0~1,低于该分数的框丢弃。默认 0.25;"
                        "想多召回降到 0.15,想少误检升到 0.4",
            },
            "iou": {
                "type": "number",
                "required": False,
                "default": 0.45,
                "desc": "NMS 的 IoU 阈值 0~1,越低越激进合并重叠框",
            },
            "device": {
                "type": "string",
                "required": False,
                "default": "auto",
                "enum": ["auto", "cuda", "cpu"],
                "desc": "推理设备。auto=有显卡选 cuda,否则 cpu;显存爆了改 cpu",
            },
            "classes": {
                "type": "array",
                "required": False,
                "default": None,
                "desc": "只保留这些 COCO 类别 id(0-79)的检测结果;留空=全部类别。"
                        "常用:[0]=person,[2]=car,[16]=dog,[17]=cat",
            },
            "save_mode": {
                "type": "string",
                "required": False,
                "default": "图片+TXT(YOLO)",
                "enum": ["图片+TXT(YOLO)", "图片+JSON(COCO)", "仅图片", "不保存"],
                "desc": "落盘策略。TXT 是 YOLO 格式(每行 class cx cy w h),"
                        "JSON 是 COCO 格式带 bbox/score",
            },
        },
        "returns": (
            "成功时返回任务输出目录绝对路径(outputs/yolo/llm_<时间戳>/),"
            "其下含全部标注图与对应 TXT/JSON;聊天里会自动列出文件"
        ),
    },
    "musicgen_compose": {
        "summary": (
            "调用本地 MusicGen 用文字描述生成一段音乐(WAV/MP3)。"
            "支持单条 prompt,也支持字符串数组并行出多个版本。"
            "\n模型选型(按 enum):small 最快但糙(8s 片段 GPU 约 5~10s);"
            "medium 是常用平衡档;large 质量最好但慢且吃显存(>10GB);"
            "melody 是变体,可以传 melody 参数指定一段参考旋律做风格迁移。"
            "\n时长 1~30 秒,常用 8~15 秒。GPU 强烈推荐,CPU 上几乎跑不动。"
            "\n⚠ prompt 用英文效果远好于中文,例如 'cinematic orchestral, "
            "90s anime opening, energetic and bright'。"
            "\n首次使用某规模会自动下载到 data/models/audiocraft。"
        ),
        "params": {
            "prompts": {
                "type": "string | array",
                "required": True,
                "desc": "音乐风格 / 情绪描述,英文为主。可传单字符串,"
                        "也可传字符串数组并行出多个版本",
            },
            "model": {
                "type": "string",
                "required": False,
                "default": "small",
                "enum": ["small", "medium", "large", "melody"],
                "desc": "MusicGen 规模。small ~1GB / medium ~3GB / large ~6GB / "
                        "melody 接受 melody 参考音频;按用户对质量与速度的要求选",
            },
            "duration": {
                "type": "number",
                "required": False,
                "default": 10,
                "desc": "时长(秒),范围 1~30。默认 10s;时长越长越占显存,"
                        "large 在 30s 时容易爆显存",
            },
            "melody": {
                "type": "string",
                "required": False,
                "default": None,
                "desc": "可选,旋律参考音频本地绝对路径(.wav/.mp3);"
                        "只在 model=\"melody\" 时生效,其它模型会忽略",
            },
            "device": {
                "type": "string",
                "required": False,
                "default": "cuda",
                "enum": ["cuda", "cuda:0", "cuda:1", "cpu"],
                "desc": "推理设备。强烈推荐 cuda;cpu 上 small 也要分钟级",
            },
            "temperature": {
                "type": "number",
                "required": False,
                "default": 1.0,
                "desc": "采样温度。越高越发散,通常 0.8~1.2",
            },
            "top_k": {
                "type": "number",
                "required": False,
                "default": 250,
                "desc": "top-k 采样。一般保持默认 250",
            },
            "cfg_coef": {
                "type": "number",
                "required": False,
                "default": 3.0,
                "desc": "classifier-free guidance 系数。越高越贴 prompt 但音质可能下降",
            },
            "output_format": {
                "type": "string",
                "required": False,
                "default": "wav",
                "enum": ["wav", "mp3"],
                "desc": "落盘格式,wav 无损 / mp3 占空间小",
            },
        },
        "returns": (
            "成功时返回任务输出目录绝对路径(outputs/audiocraft/llm_<时间戳>/),"
            "其下含 musicgen_<时间戳>_NN.wav/.mp3;聊天里会自动列出可播放的音频卡片"
        ),
    },
    "audiogen_create": {
        "summary": (
            "调用本地 AudioGen 用文字描述生成环境声 / 音效片段(WAV/MP3),不是音乐。"
            "适合下雨声、键盘敲击、街道喧闹、玻璃破碎这类 sound design 场景。"
            "\n模型当前只发了 medium 一档(~1.5GB),GPU 必备。"
            "\n时长 1~30 秒,常用 5~10 秒;prompt 用英文效果好于中文。"
            "\n⚠ 想要音乐请用 musicgen_compose;AudioGen 不会生成有调旋律。"
        ),
        "params": {
            "prompts": {
                "type": "string | array",
                "required": True,
                "desc": "音效描述,英文为主。可传单字符串,"
                        "也可传字符串数组并行出多个版本。"
                        "例如 'heavy rain on roof, distant thunder'",
            },
            "model": {
                "type": "string",
                "required": False,
                "default": "medium",
                "enum": ["medium"],
                "desc": "AudioGen 规模。当前 facebook 只发了 medium",
            },
            "duration": {
                "type": "number",
                "required": False,
                "default": 8,
                "desc": "时长(秒),范围 1~30。默认 8s",
            },
            "device": {
                "type": "string",
                "required": False,
                "default": "cuda",
                "enum": ["cuda", "cuda:0", "cuda:1", "cpu"],
                "desc": "推理设备。强烈推荐 cuda",
            },
            "temperature": {
                "type": "number",
                "required": False,
                "default": 1.0,
                "desc": "采样温度,越高越发散",
            },
            "top_k": {
                "type": "number",
                "required": False,
                "default": 250,
                "desc": "top-k 采样,保持默认 250 即可",
            },
            "cfg_coef": {
                "type": "number",
                "required": False,
                "default": 3.0,
                "desc": "classifier-free guidance 系数",
            },
            "output_format": {
                "type": "string",
                "required": False,
                "default": "wav",
                "enum": ["wav", "mp3"],
                "desc": "落盘格式",
            },
        },
        "returns": (
            "成功时返回任务输出目录绝对路径(outputs/audiocraft/llm_<时间戳>/),"
            "其下含 audiogen_<时间戳>_NN.wav/.mp3;聊天里会自动列出可播放的音频卡片"
        ),
    },
    "realesrgan_upscale": {
        "summary": (
            "调用本地 Real-ESRGAN (ncnn-vulkan) 对图片做超分放大。"
            "支持三种输入形态:单张图、整个目录、路径数组 —— 目录会把里面所有图片一次跑完。"
            "\n选型(按 model enum 中实际命中):含 'plus' 不含 'anime' 是通用真实照片;"
            "含 'plus' 且 'anime' 是动漫/插画;含 'animevideo' 是动漫视频帧(唯一支持 2/3 倍,"
            "其它模型都只能 4 倍);含 'generalv3' 是通用 v3 增强。"
            "\n显存不够会爆 vk_create_image 失败,这种时候 tile 选 256/512 切片处理。"
            "\n⚠ model 参数必须从下面 enum 里挑;别凭历史名字猜(如 realesrgan-x4plus 已被改名)。"
        ),
        "params": {
            "input": {
                "type": "string | array",
                "required": True,
                "desc": "待放大的本地图像绝对路径(.png/.jpg/.webp),"
                        "或一个含图像的目录,或字符串数组批量处理。"
                        "目录里非图像文件会被 exe 自行跳过",
            },
            "model": {
                "type": "string",
                "required": False,
                "default": _REALESRGAN_DEFAULT_MODEL,
                "enum": _REALESRGAN_MODELS,
                "desc": "模型名 —— 必须从 enum 取值,这是当前安装实际存在的模型。"
                        "按命名特征选: 真实照片选含 'plus' 不含 'anime' 的; "
                        "动漫/插画选含 'plus' 和 'anime' 的; "
                        "动漫视频帧选含 'animevideo' 的(也只有它支持 2/3 倍)",
            },
            "scale": {
                "type": "number",
                "required": False,
                "default": 4,
                "enum": [2, 3, 4],
                "desc": "放大倍数。除 animevideo 系列外的模型都只支持 4;"
                        "选 2/3 时必须搭配含 'animevideo' 的模型",
            },
            "tile": {
                "type": "number",
                "required": False,
                "default": 0,
                "desc": "图块大小,0=自动整图处理。显存不足时设 256 或 512 切片,"
                        "越小越省显存但越慢",
            },
            "gpu_id": {
                "type": "string",
                "required": False,
                "default": "auto",
                "desc": "GPU 编号字符串。\"auto\"=第一块可用 GPU(默认),"
                        "\"0\"/\"1\"=指定显卡,\"-1\"=CPU(慢 10 倍以上)",
            },
            "fmt": {
                "type": "string",
                "required": False,
                "default": "png",
                "enum": ["png", "jpg", "webp"],
                "desc": "输出格式,默认 png 无损;批量怕占盘可改 jpg/webp",
            },
            "tta": {
                "type": "boolean",
                "required": False,
                "default": False,
                "desc": "TTA 测试时增强,质量略升但速度变 8 倍慢,默认关",
            },
        },
        "returns": (
            "成功时返回输出路径:单文件输入返回单张超分图绝对路径"
            "(如 outputs/realesrgan/<stem>_x4.png);目录或数组输入返回 outputs/realesrgan/ 目录,"
            "其下含全部输出图;聊天里会自动列出文件列表"
        ),
    },
    "search_song": {
        "summary": (
            "在网易云音乐或 B 站搜索歌曲 / 视频,返回候选列表(不下载)。"
            "用来给用户一份带歌名 / 歌手 / 时长的命中,再让用户决定下哪首,"
            "或者直接挑第一条调 download_song。"
            "用户已经明确点名某首歌(\"帮我下 xxx\")时优先用 fetch_song 一步到位,省一轮往返。"
        ),
        "params": {
            "keyword": {
                "type": "string",
                "required": True,
                "desc": "搜索关键词,例如 \"周杰伦 稻香\" / \"原神 见龙\"",
            },
            "source": {
                "type": "string",
                "required": False,
                "default": "netease",
                "enum": ["netease", "bilibili"],
                "desc": "数据源。netease=网易云音乐(纯音轨,质量最好);"
                        "bilibili=B 站视频(取音频流);默认 netease",
            },
            "limit": {
                "type": "number",
                "required": False,
                "default": 10,
                "desc": "返回条数,1~30,默认 10",
            },
            "drop_instrumental": {
                "type": "boolean",
                "required": False,
                "default": True,
                "desc": "是否过滤纯伴奏 / instrumental / 卡拉版(仅对 netease 生效)",
            },
        },
        "returns": (
            "JSON 字符串:{source, keyword, count, hits:[...]}。"
            "hits 内每条:netease 是 {id, name, artists, album, duration_ms, source},"
            "bilibili 是 {bvid, title, author, duration, pic, play, source}。"
            "下一步调 download_song 时,source 原样传,id 用 netease 的 `id` 或 bilibili 的 `bvid`。"
        ),
    },
    "download_song": {
        "summary": (
            "按 source + id 下载具体一首歌 / 一个 B 站视频的音频流。"
            "id 必须来自上一轮 search_song 的命中,不要自己编。"
            "下载完成后聊天里会出现可播放的音频卡片。"
            "下到本地的音频可以直接传给 demucs_separate / whisper_transcribe / rvc_convert 等下游工具继续加工。"
        ),
        "params": {
            "source": {
                "type": "string",
                "required": True,
                "enum": ["netease", "bilibili"],
                "desc": "数据源,与 search_song 的 source 一致",
            },
            "id": {
                "type": "string | number",
                "required": True,
                "desc": "歌曲 / 视频标识。"
                        "netease 传 song_id(整数或可转 int 的字符串);"
                        "bilibili 传 bvid 字符串(形如 \"BV1xxx\")。"
                        "都从 search_song 的 hits 里取",
            },
            "title": {
                "type": "string",
                "required": False,
                "default": None,
                "desc": "落盘文件名(不含扩展名),通常拼成 \"歌名 - 歌手\"。"
                        "省略时用 source 自带默认名,可能不可读",
            },
        },
        "returns": "成功时返回下载后的本地绝对路径(.mp3/.flac/.m4a 等)",
    },
    "fetch_song": {
        "summary": (
            "一键搜索并下载第一个非伴奏匹配项 —— 搜 + 取首条 + 下,合并 search_song + download_song 的常见用法。"
            "适合用户随口说 \"帮我下个 xxx 的歌\" 这种,不需要给候选让用户挑的场景。"
            "如果用户对结果挑剔,改用 search_song 显示候选,让用户点名再 download_song。"
        ),
        "params": {
            "keyword": {
                "type": "string",
                "required": True,
                "desc": "搜索关键词,通常 \"歌名 歌手\" 命中率最高",
            },
            "source": {
                "type": "string",
                "required": False,
                "default": "netease",
                "enum": ["netease", "bilibili"],
                "desc": "数据源,默认 netease",
            },
            "drop_instrumental": {
                "type": "boolean",
                "required": False,
                "default": True,
                "desc": "是否过滤伴奏(仅对 netease 生效)",
            },
        },
        "returns": "成功时返回下载后的本地绝对路径,聊天里会出现可播放的音频卡片",
    },
}


def _format_tool_spec(name: str, spec: dict) -> str:
    lines = [f"#### `{name}`", spec["summary"], "", "参数："]
    for pname, p in spec["params"].items():
        req = "必填" if p.get("required") else "可选"
        default = ""
        if not p.get("required"):
            dv = p.get("default")
            default = f"，默认 `{json.dumps(dv, ensure_ascii=False)}`"
        enum = ""
        if p.get("enum"):
            enum = "，取值 " + " / ".join(f"`{v}`" for v in p["enum"])
        lines.append(
            f"- `{pname}` ({p['type']}, {req}{default}{enum})：{p['desc']}")
    lines.append("")
    lines.append(f"返回：{spec['returns']}")
    return "\n".join(lines)


# 用 __TOOLS__ 占位避免与 JSON 的花括号冲突，不走 .format()
_TOOL_PROTOCOL_TEMPLATE = """

## 可用工具（MCP 风格）

涉及本地 AI 工具的实际操作（例如分离音频）通过工具调用真实执行，不要凭空给出"结果"。
工具会在用户机器上真跑，注意耗时（Demucs 单首歌通常 1-3 分钟）。

### 调用协议
在你的回复里嵌入下面的标签，标签内只能写合法 JSON：

<tool_call>
{"name": "工具名", "arguments": {"参数名": "参数值"}}
</tool_call>

- 一次回复可以含多个 `<tool_call>`，会按出现顺序串行执行。
- 标签外的普通文字会展示给用户，可以用来说明你要做什么。
- 工具跑完后系统会再调你一次，并把每个结果以
  `<tool_result name="..." status="ok|error">...</tool_result>` 注入上下文；
  收到结果后用普通文字给用户最终答复，不要再发 `<tool_call>` 除非确实需要新工具。
- 不要伪造 `<tool_result>`，那只能由系统注入。
- 参数缺失或不确定时，不要瞎填；先用普通文字向用户问清楚再调用。

### 附件路径
用户上传的附件会在消息里以下面三种形式之一出现：
- 文本附件： `--- 文件: name (... bytes) ---` 后跟 `路径: <绝对路径>` 与代码块内容
- 二进制 / 大文件附件： `--- 附件: name (...) [二进制或过大，未内联] ---` 后跟 `路径: <绝对路径>`
- 目录附件： `--- 目录: name ---` 后跟 `路径: <绝对路径>`
其中的 `路径:` 字段就是文件 / 目录在用户机器上的本地绝对路径，
可以直接作为工具调用的文件参数（例如 `demucs_separate` 的 `input`、`list_dir` 的 `path`），
不要再向用户索要一次路径。

收到目录附件时不要直接回答里面有什么 —— 先用 `list_dir` 探查内容，
对疑似转写清单 / 配置 / 描述这类文本文件再用 `read_file` 看具体内容
（例如 GPT-SoVITS 数据集里的 `.list`），然后才能据此调用下游工具。

### 工具列表

__TOOLS__

### 示例

用户：帮我提取 D:/music/song.mp3 里的人声
助手：
我用 Demucs 跑一下，只分两轨更快。
<tool_call>
{"name": "demucs_separate", "arguments": {"input": "D:/music/song.mp3", "two_stems": "vocals"}}
</tool_call>
"""


def _build_tool_prompt() -> str:
    blocks = "\n\n".join(
        _format_tool_spec(n, s) for n, s in TOOL_REGISTRY.items()
    )
    return _TOOL_PROTOCOL_TEMPLATE.replace("__TOOLS__", blocks)


SYSTEM_PROMPT = SYSTEM_PROMPT_BASE + _build_tool_prompt()


# `<tool_call> ... </tool_call>` 解析。标签内允许跨行；用 `.+?` 非贪婪匹配整段。
# 有些模型 (尤其 DeepSeek / Qwen 系,或被 max_tokens 截断时) 会漏 `</tool_call>`,
# 所以再准备一个只匹配起始标签的正则和「取回未闭合尾巴」的解析器,免得整轮
# tool call 被静默丢弃。
_TOOL_CALL_RE = re.compile(
    r"<tool_call>\s*(.+?)\s*</tool_call>", re.DOTALL | re.IGNORECASE)
_TOOL_CALL_OPEN_RE = re.compile(r"<tool_call>", re.IGNORECASE)
# 从 `<tool_call>` 起一直吃到消息结尾,给"漏闭合"分支用
_TOOL_CALL_TAIL_RE = re.compile(
    r"<tool_call>[\s\S]*$", re.IGNORECASE)


# ============================================================================
# 轻量 Markdown -> HTML —— 给助手气泡用
# 只覆盖 LLM 实际会输出的语法:段落 / 代码块 / 行内代码 / 无序与有序列表 /
# 标题 / 加粗。对流式过程中的未闭合代码块也保持稳健(整段当 <pre> 渲染)。
# ============================================================================
_INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_OL_RE = re.compile(r"^(\d+)\.\s+(.*)$")

_CODE_BLOCK_STYLE = (
    "background:rgba(255,255,255,0.06);"
    "border:1px solid rgba(255,255,255,0.08);"
    "border-radius:6px;padding:8px 10px;margin:6px 0;"
    "font-family:Consolas,'Microsoft YaHei',monospace;font-size:12px;"
    f"color:{INK_PRIMARY};white-space:pre-wrap;"
)
_INLINE_CODE_STYLE = (
    "background:rgba(255,255,255,0.10);"
    "border-radius:3px;padding:1px 5px;"
    "font-family:Consolas,'Microsoft YaHei',monospace;"
)


def _md_inline(s: str) -> str:
    s = _htmllib.escape(s)
    s = _BOLD_RE.sub(r"<b>\1</b>", s)
    s = _INLINE_CODE_RE.sub(
        rf'<code style="{_INLINE_CODE_STYLE}">\1</code>', s)
    return s


def _render_markdown(md: str) -> str:
    lines = md.split("\n")
    out: list[str] = []
    code_buf: list[str] = []
    in_code = False
    in_ul = False
    in_ol = False
    para: list[str] = []

    def flush_para():
        if para:
            out.append(
                '<p style="margin:4px 0;">'
                + "<br>".join(_md_inline(l) for l in para)
                + "</p>"
            )
            para.clear()

    def close_lists():
        nonlocal in_ul, in_ol
        if in_ul:
            out.append("</ul>")
            in_ul = False
        if in_ol:
            out.append("</ol>")
            in_ol = False

    def flush_code():
        nonlocal in_code
        txt = _htmllib.escape("\n".join(code_buf))
        out.append(f'<pre style="{_CODE_BLOCK_STYLE}">{txt}</pre>')
        code_buf.clear()
        in_code = False

    for line in lines:
        if line.lstrip().startswith("```"):
            if in_code:
                flush_code()
            else:
                flush_para()
                close_lists()
                in_code = True
            continue
        if in_code:
            code_buf.append(line)
            continue

        stripped = line.strip()
        if not stripped:
            flush_para()
            close_lists()
            continue

        m_h = _HEADING_RE.match(stripped)
        if m_h:
            flush_para()
            close_lists()
            lvl = len(m_h.group(1))
            size = {1: 18, 2: 16, 3: 14}.get(lvl, 13)
            out.append(
                f'<div style="font-weight:bold;font-size:{size}px;'
                f'margin:8px 0 2px 0;color:{INK_PRIMARY};">'
                f'{_md_inline(m_h.group(2))}</div>'
            )
            continue

        if stripped.startswith(("- ", "* ", "+ ")):
            flush_para()
            if in_ol:
                out.append("</ol>")
                in_ol = False
            if not in_ul:
                out.append('<ul style="margin:4px 0;padding-left:22px;">')
                in_ul = True
            out.append(f"<li>{_md_inline(stripped[2:])}</li>")
            continue

        m_ol = _OL_RE.match(stripped)
        if m_ol:
            flush_para()
            if in_ul:
                out.append("</ul>")
                in_ul = False
            if not in_ol:
                out.append('<ol style="margin:4px 0;padding-left:24px;">')
                in_ol = True
            out.append(f"<li>{_md_inline(m_ol.group(2))}</li>")
            continue

        close_lists()
        para.append(line)

    if in_code:
        flush_code()
    flush_para()
    close_lists()
    return "".join(out) or "&nbsp;"


PROVIDER_PRESETS = {
    "OpenAI":      ("https://api.openai.com/v1", "gpt-4o-mini"),
    "DeepSeek":    ("https://api.deepseek.com/v1", "deepseek-chat"),
    "Moonshot":    ("https://api.moonshot.cn/v1", "moonshot-v1-8k"),
    "智谱 GLM":    ("https://open.bigmodel.cn/api/paas/v4", "glm-4-flash"),
    "硅基流动":    ("https://api.siliconflow.cn/v1", "Qwen/Qwen2.5-7B-Instruct"),
    "Ollama 本地": ("http://127.0.0.1:11434/v1", "qwen2.5:7b"),
    "自定义":      ("", ""),
}

TEXT_EXTS = {
    ".txt", ".md", ".markdown", ".rst", ".log",
    ".py", ".js", ".ts", ".tsx", ".jsx", ".html", ".css", ".scss",
    ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg",
    ".c", ".cpp", ".h", ".hpp", ".java", ".go", ".rs", ".rb", ".php",
    ".sh", ".bat", ".ps1",
    ".srt", ".vtt", ".csv", ".tsv", ".sql", ".xml",
}
MAX_FILE_BYTES = 256 * 1024


# ============================================================================
# Worker —— 流式调用 OpenAI 兼容 /chat/completions
# ============================================================================
class LLMWorker(QThread):
    chunk = pyqtSignal(str)
    finished_msg = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self, api_key: str, base_url: str, model: str,
                 messages: list, temperature: float = 0.7, parent=None):
        super().__init__(parent)
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.messages = messages
        self.temperature = temperature
        self._is_cancelled = False

    def cancel(self):
        self._is_cancelled = True

    def run(self):
        try:
            url = f"{self.base_url}/chat/completions"
            payload = {
                "model": self.model,
                "messages": self.messages,
                "temperature": self.temperature,
                "stream": True,
            }
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers = {
                "Content-Type": "application/json",
                "Accept": "text/event-stream",
            }
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"

            req = urllib.request.Request(
                url, data=data, headers=headers, method="POST")

            full = []
            with urllib.request.urlopen(req, timeout=120) as resp:
                for raw in resp:
                    if self._is_cancelled:
                        break
                    line = raw.decode("utf-8", errors="replace").strip()
                    if not line:
                        continue
                    if line.startswith("data:"):
                        line = line[5:].strip()
                    if line == "[DONE]":
                        break
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    choices = obj.get("choices") or []
                    if not choices:
                        continue
                    delta = choices[0].get("delta") or {}
                    piece = delta.get("content") or ""
                    if piece:
                        full.append(piece)
                        self.chunk.emit(piece)

            self.finished_msg.emit("".join(full))

        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8", errors="replace")
            except Exception:
                pass
            self.error.emit(f"HTTP {e.code}: {body[:400]}")
        except urllib.error.URLError as e:
            self.error.emit(f"网络错误: {e.reason}")
        except Exception as e:
            self.error.emit(f"{type(e).__name__}: {e}")


# ============================================================================
# ModelListWorker —— GET {base}/models 拉模型列表
# 兼容 OpenAI 风格 {data:[{id}]} 和 Ollama 风格 {models:[{name}]}
# ============================================================================
class ModelListWorker(QThread):
    finished_list = pyqtSignal(list)  # 排序去重后的模型 ID 列表
    error = pyqtSignal(str)

    def __init__(self, api_key: str, base_url: str, parent=None):
        super().__init__(parent)
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")

    def run(self):
        try:
            url = f"{self.base_url}/models"
            req = urllib.request.Request(url, method="GET")
            req.add_header("Accept", "application/json")
            if self.api_key:
                req.add_header("Authorization", f"Bearer {self.api_key}")

            with urllib.request.urlopen(req, timeout=20) as resp:
                raw = resp.read().decode("utf-8", errors="replace")

            try:
                data = json.loads(raw)
            except json.JSONDecodeError as e:
                self.error.emit(f"返回不是 JSON：{e}（响应前 200 字符：{raw[:200]}）")
                return

            ids: list[str] = []
            if isinstance(data, dict):
                lst = data.get("data") or data.get("models") or []
                if isinstance(lst, list):
                    for item in lst:
                        if isinstance(item, dict):
                            mid = item.get("id") or item.get(
                                "name") or item.get("model")
                            if isinstance(mid, str) and mid.strip():
                                ids.append(mid.strip())
                        elif isinstance(item, str):
                            ids.append(item.strip())

            ids = sorted(set(ids), key=str.lower)
            if not ids:
                self.error.emit("响应里没找到模型 ID（不是标准 OpenAI / Ollama 结构）")
                return
            self.finished_list.emit(ids)

        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8", errors="replace")
            except Exception:
                pass
            self.error.emit(f"HTTP {e.code}: {body[:200]}")
        except urllib.error.URLError as e:
            self.error.emit(f"网络错误: {e.reason}")
        except Exception as e:
            self.error.emit(f"{type(e).__name__}: {e}")


# ============================================================================
# 状态 pill —— 顶部"就绪 / 未配置"指示器，点击展开/收起设置抽屉
# ============================================================================
class StatusPill(QFrame):
    clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(28)

        h = QHBoxLayout(self)
        h.setContentsMargins(10, 0, 10, 0)
        h.setSpacing(8)

        self._dot = QFrame(self)
        self._dot.setFixedSize(6, 6)
        h.addWidget(self._dot, 0, Qt.AlignmentFlag.AlignVCenter)

        self._label = CaptionLabel("未配置", self)
        h.addWidget(self._label, 0, Qt.AlignmentFlag.AlignVCenter)

        self._chev = IconWidget(ICON_CHEV_R, self)
        self._chev.setFixedSize(10, 10)
        h.addWidget(self._chev, 0, Qt.AlignmentFlag.AlignVCenter)

        self.set_state(False, "未配置")

    def set_state(self, ok: bool, text: str):
        bg = PILL_BG_OK if ok else PILL_BG_WARN
        fg = PILL_FG_OK if ok else PILL_FG_WARN
        self._label.setText(text)
        self.setStyleSheet(
            f"StatusPill {{ background-color: {bg}; border-radius: 14px; }}"
        )
        self._dot.setStyleSheet(
            f"QFrame {{ background-color: {fg}; border-radius: 3px; }}")
        self._label.setStyleSheet(f"color: {fg};")

    def set_expanded(self, expanded: bool):
        self._chev.setIcon(ICON_CHEV_D if expanded else ICON_CHEV_R)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(e)


# ============================================================================
# 输入区里的附件 chip
# ============================================================================
class AttachmentChip(QFrame):
    removed = pyqtSignal(object)

    def __init__(self, path: str, parent=None):
        super().__init__(parent)
        self.path = path
        self.is_dir = os.path.isdir(path)
        self.setObjectName("AttachmentChip")
        self.setStyleSheet(
            f"#AttachmentChip {{"
            f"  background-color: {CHIP_BG};"
            f"  border: 1px solid {HAIRLINE};"
            f"  border-radius: 13px;"
            f"}}"
            f"#AttachmentChip:hover {{"
            f"  background-color: rgba(255, 255, 255, 0.10);"
            f"}}"
        )
        self.setFixedHeight(26)

        h = QHBoxLayout(self)
        h.setContentsMargins(8, 0, 4, 0)
        h.setSpacing(6)

        ic = IconWidget(ICON_FOLDER if self.is_dir else ICON_DOC, self)
        ic.setFixedSize(12, 12)
        h.addWidget(ic, 0, Qt.AlignmentFlag.AlignVCenter)

        name = Path(path).name
        if len(name) > 28:
            name = name[:14] + "…" + name[-12:]
        if self.is_dir:
            name = name + "/"
        lbl = CaptionLabel(name, self)
        lbl.setToolTip(
            path + ("\n(目录,LLM 将用 list_dir 探查)" if self.is_dir else ""))
        lbl.setStyleSheet(f"color: {INK_PRIMARY};")
        h.addWidget(lbl, 0, Qt.AlignmentFlag.AlignVCenter)

        btn = TransparentToolButton(ICON_CLOSE, self)
        btn.setFixedSize(18, 18)
        btn.setIconSize(QSize(8, 8))
        btn.clicked.connect(lambda: self.removed.emit(self))
        h.addWidget(btn, 0, Qt.AlignmentFlag.AlignVCenter)


# ============================================================================
# 支持拖入文件的输入框
# TextEdit 默认会把拖进来的 file:// URL 当文本插进去；这里把 URL drop
# 转成 filesDropped 信号转交给 LLMChatPage,沿用 _on_attach 的入附件流程。
# 文本 / 内部 drop 仍走父类默认实现,不影响选区拖拽编辑。
# ============================================================================
class DropAwareTextEdit(TextEdit):
    filesDropped = pyqtSignal(list)  # list[str] -> 本地绝对路径

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)

    @staticmethod
    def _local_files(mime) -> list[str]:
        """文件和目录都接受。目录走 list_dir / read_file 的探查链路,
        交给 LLM 自己看里面有什么。"""
        if not mime.hasUrls():
            return []
        out = []
        for u in mime.urls():
            if u.isLocalFile():
                p = u.toLocalFile()
                if p and (os.path.isfile(p) or os.path.isdir(p)):
                    out.append(p)
        return out

    def dragEnterEvent(self, e):
        if not self.isEnabled():
            e.ignore()
            return
        if self._local_files(e.mimeData()):
            e.acceptProposedAction()
            return
        super().dragEnterEvent(e)

    def dragMoveEvent(self, e):
        if self._local_files(e.mimeData()):
            e.acceptProposedAction()
            return
        super().dragMoveEvent(e)

    def dropEvent(self, e):
        files = self._local_files(e.mimeData())
        if files:
            e.acceptProposedAction()
            self.filesDropped.emit(files)
            return
        super().dropEvent(e)


# ============================================================================
# 可点击的 QFrame —— 给"输出文件"折叠头用,QFrame 本身没有 clicked 信号
# ============================================================================
class _ClickableFrame(QFrame):
    clicked = pyqtSignal()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(e)


# ============================================================================
# 输出文件的单行 —— 默认只显示文件名 / 大小 / 操作按钮。
# 点击"播放"才真正实例化 AudioWaveformWidget / VideoWidget,再点一次销毁。
# 避免一次工具完成就把 4 个 stems 全部解码进内存。
# ============================================================================
class MediaListItem(QWidget):
    AUDIO_EXTS = {".wav", ".mp3", ".flac", ".ogg", ".m4a", ".aac"}
    VIDEO_EXTS = {".mp4", ".webm", ".mkv", ".mov", ".avi"}

    def __init__(self, path: str, parent=None):
        super().__init__(parent)
        self._path = path
        self._player = None
        ext = os.path.splitext(path)[1].lower()
        if ext in self.AUDIO_EXTS:
            self._kind = "audio"
        elif ext in self.VIDEO_EXTS:
            self._kind = "video"
        else:
            self._kind = "other"

        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(4)

        row_w = QFrame(self)
        row_w.setObjectName("MediaRow")
        row_w.setStyleSheet(
            f"#MediaRow {{"
            f"  background-color: {CHIP_BG};"
            f"  border: 1px solid {HAIRLINE};"
            f"  border-radius: 6px;"
            f"}}"
            f"#MediaRow:hover {{"
            f"  background-color: rgba(255,255,255,0.09);"
            f"}}"
        )
        row = QHBoxLayout(row_w)
        row.setContentsMargins(10, 6, 6, 6)
        row.setSpacing(8)

        ic = IconWidget(ICON_DOC, row_w)
        ic.setFixedSize(14, 14)
        row.addWidget(ic, 0, Qt.AlignmentFlag.AlignVCenter)

        name_lbl = CaptionLabel(os.path.basename(path), row_w)
        name_lbl.setStyleSheet(f"color: {INK_PRIMARY};")
        name_lbl.setToolTip(path)
        row.addWidget(name_lbl, 0, Qt.AlignmentFlag.AlignVCenter)

        try:
            sz = os.path.getsize(path)
            size_str = self._fmt_size(sz)
        except OSError:
            size_str = ""
        if size_str:
            size_lbl = CaptionLabel(size_str, row_w)
            size_lbl.setStyleSheet(f"color: {INK_TERTIARY};")
            row.addWidget(size_lbl, 0, Qt.AlignmentFlag.AlignVCenter)

        row.addStretch()

        folder_btn = TransparentToolButton(
            _fic("FOLDER", "FOLDER_ADD"), row_w)
        folder_btn.setFixedSize(22, 22)
        folder_btn.setIconSize(QSize(12, 12))
        folder_btn.setToolTip("在文件夹中显示")
        folder_btn.clicked.connect(self._on_open_folder)
        row.addWidget(folder_btn, 0, Qt.AlignmentFlag.AlignVCenter)

        # 仅对认识的媒体后缀挂播放按钮
        self._play_btn = None
        if self._kind != "other":
            self._play_btn = TransparentToolButton(
                _fic("PLAY", "PLAY_SOLID", "RIGHT_ARROW"), row_w)
            self._play_btn.setFixedSize(22, 22)
            self._play_btn.setIconSize(QSize(12, 12))
            self._play_btn.setToolTip("展开预览 / 收起")
            self._play_btn.clicked.connect(self._on_toggle_player)
            row.addWidget(self._play_btn, 0, Qt.AlignmentFlag.AlignVCenter)

        v.addWidget(row_w)

        # 懒加载的播放器槽位 —— 展开时往里塞,收起时清掉
        self._player_box = QVBoxLayout()
        self._player_box.setContentsMargins(20, 0, 0, 0)  # 左缩进表明属于上一行
        self._player_box.setSpacing(0)
        v.addLayout(self._player_box)

    @staticmethod
    def _fmt_size(n: float) -> str:
        for unit in ("B", "KB", "MB"):
            if n < 1024:
                return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
            n /= 1024
        return f"{n:.1f} GB"

    def _destroy_player(self):
        """主动释放当前播放器持有的资源,再删除 widget。

        音频组件: 调 cleanup() 拆 sounddevice 流的 callback bound-method
        循环引用,顺手清掉数十 MB 的解码缓冲;
        视频组件: 调 stop() 释放 QMediaPlayer 占用的底层句柄。
        最后才 setParent+deleteLater,这样 Qt 真正销毁前资源已经释放。
        """
        p = self._player
        if p is None:
            return
        cleanup = getattr(p, "cleanup", None)
        if callable(cleanup):
            try:
                cleanup()
            except Exception:
                pass
        else:
            stop = getattr(p, "stop", None)
            if callable(stop):
                try:
                    stop()
                except Exception:
                    pass
        try:
            p.setParent(None)
            p.deleteLater()
        except Exception:
            pass
        self._player = None

    def closeEvent(self, e):
        # 走到这里说明宿主主动 close 我们(目前路径很少);兜底清一遍。
        self._destroy_player()
        super().closeEvent(e)

    def deleteLater(self):
        # "清空对话"会直接 deleteLater 上层卡片,带着我们一起被销毁。
        # 在 Qt 真正回收前先把播放器拆掉,否则音频流回调仍持有 widget 强引用。
        self._destroy_player()
        super().deleteLater()

    def _on_open_folder(self):
        """跨平台地在系统文件管理器里高亮该文件。"""
        import subprocess
        import sys as _sys
        try:
            ap = os.path.abspath(self._path)
            if _sys.platform == "win32":
                # /select, 让 explorer 打开父目录并高亮文件
                subprocess.Popen(["explorer", f"/select,{ap}"])
            elif _sys.platform == "darwin":
                subprocess.Popen(["open", "-R", ap])
            else:
                subprocess.Popen(["xdg-open", os.path.dirname(ap) or ap])
        except Exception:
            pass

    def _on_toggle_player(self):
        # 已展开 -> 收起并销毁,释放音频缓冲 / 视频解码器
        if self._player is not None:
            self._destroy_player()
            if self._play_btn is not None:
                self._play_btn.setIcon(
                    _fic("PLAY", "PLAY_SOLID", "RIGHT_ARROW"))
                self._play_btn.setToolTip("展开预览 / 收起")
            return

        # 第一次展开 -> 实例化播放器
        if self._kind == "audio":
            try:
                from widgets.audio_waveform_widget import AudioWaveformWidget
            except Exception:
                return
            w = AudioWaveformWidget()
            w.set_embedded_mode(True)
            w.disable_mic_recording()
            w.setMinimumHeight(140)
            w.setMaximumHeight(200)
            self._player_box.addWidget(w)
            try:
                w.load_file(self._path)
            except Exception:
                pass
            self._player = w
        elif self._kind == "video":
            try:
                from qfluentwidgets.multimedia import VideoWidget
                from PyQt6.QtCore import QUrl
            except Exception:
                return
            vid = VideoWidget(self)
            vid.setMinimumHeight(260)
            try:
                vid.setVideo(QUrl.fromLocalFile(os.path.abspath(self._path)))
            except Exception:
                pass
            self._player_box.addWidget(vid)
            self._player = vid
        else:
            return

        if self._play_btn is not None:
            self._play_btn.setIcon(
                _fic("CLOSE", "CANCEL_MEDIUM", "REMOVE"))
            self._play_btn.setToolTip("收起预览")


# ============================================================================
# 工具调用卡片 —— 在对话流里显示某次工具调用的参数 / 实时进度 / 最终结果。
# 流式期间显示 "运行中 · NN%" 与最新进度行；finished 后切到结果文本。
# ============================================================================
class ToolCallCard(CardWidget):
    PILL_RUNNING = ("rgba(80, 140, 220, 0.18)", "#88B6F0")
    PILL_OK = (PILL_BG_OK, PILL_FG_OK)
    PILL_FAIL = ("rgba(220, 80, 80, 0.18)", "#FF8585")

    def __init__(self, tool_name: str, args: dict, parent=None):
        super().__init__(parent)
        self.setBorderRadius(10)
        self._tool_name = tool_name

        v = QVBoxLayout(self)
        v.setContentsMargins(14, 10, 14, 12)
        v.setSpacing(6)

        head = QHBoxLayout()
        head.setSpacing(8)
        ic = IconWidget(ICON_BOT, self)
        ic.setFixedSize(14, 14)
        head.addWidget(ic, 0, Qt.AlignmentFlag.AlignVCenter)
        title = StrongBodyLabel(f"工具调用 · {tool_name}", self)
        title.setStyleSheet(f"color: {INK_PRIMARY};")
        head.addWidget(title, 0, Qt.AlignmentFlag.AlignVCenter)
        head.addStretch()

        self._status_pill = QFrame(self)
        self._status_pill.setFixedHeight(20)
        sp_lay = QHBoxLayout(self._status_pill)
        sp_lay.setContentsMargins(8, 0, 8, 0)
        sp_lay.setSpacing(0)
        self._status_lbl = CaptionLabel("准备中…", self._status_pill)
        sp_lay.addWidget(self._status_lbl, 0, Qt.AlignmentFlag.AlignVCenter)
        head.addWidget(self._status_pill, 0, Qt.AlignmentFlag.AlignVCenter)
        v.addLayout(head)

        # 参数（折行 / 可选）
        try:
            arg_str = json.dumps(args, ensure_ascii=False, indent=2)
        except Exception:
            arg_str = repr(args)
        self._args_lbl = CaptionLabel(arg_str, self)
        self._args_lbl.setWordWrap(True)
        self._args_lbl.setStyleSheet(
            f"color: {INK_SECONDARY}; "
            f"font-family: Consolas, 'Microsoft YaHei', monospace; font-size: 11px;"
        )
        self._args_lbl.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        v.addWidget(self._args_lbl)

        # 实时进度行
        self._progress_lbl = CaptionLabel("", self)
        self._progress_lbl.setWordWrap(True)
        self._progress_lbl.setStyleSheet(f"color: {INK_TERTIARY};")
        v.addWidget(self._progress_lbl)

        # 结果 —— 短结果直接显示;长结果(>150 字符 或多行) 折叠成
        # "首行摘要 + ▾ 展开详细输出" 卡片,避免几十行 cli stack 撑爆消息列表。
        self._result_lbl = BodyLabel("", self)
        self._result_lbl.setWordWrap(True)
        self._result_lbl.setStyleSheet(f"color: {INK_PRIMARY};")
        self._result_lbl.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        self._result_lbl.setVisible(False)
        v.addWidget(self._result_lbl)

        # 详情折叠头(默认隐藏,长结果时才显示)
        self._detail_header = _ClickableFrame(self)
        self._detail_header.setObjectName("DetailHeader")
        self._detail_header.setCursor(Qt.CursorShape.PointingHandCursor)
        self._detail_header.setStyleSheet(
            "#DetailHeader { background: transparent; border: none; }"
            "#DetailHeader:hover { background: rgba(255,255,255,0.05); border-radius: 6px; }"
        )
        dh = QHBoxLayout(self._detail_header)
        dh.setContentsMargins(6, 4, 6, 4)
        dh.setSpacing(6)
        self._detail_chev = IconWidget(ICON_CHEV_R, self._detail_header)
        self._detail_chev.setFixedSize(10, 10)
        dh.addWidget(self._detail_chev, 0, Qt.AlignmentFlag.AlignVCenter)
        self._detail_title = CaptionLabel("详细输出", self._detail_header)
        self._detail_title.setStyleSheet(f"color: {INK_SECONDARY};")
        dh.addWidget(self._detail_title, 0, Qt.AlignmentFlag.AlignVCenter)
        dh.addStretch()
        self._detail_header.setVisible(False)
        self._detail_header.clicked.connect(self._toggle_detail)
        v.addWidget(self._detail_header)

        # 详情正文(monospace + 限高,默认折叠)
        self._detail_body = TextEdit(self)
        self._detail_body.setReadOnly(True)
        self._detail_body.setAcceptRichText(False)
        self._detail_body.setStyleSheet(
            "TextEdit { "
            "background-color: rgba(0,0,0,0.25); "
            "color: #d4d4d4; "
            "font-family: Consolas, 'Microsoft YaHei', monospace; "
            "font-size: 11px; "
            "border: 1px solid rgba(255,255,255,0.08); "
            "border-radius: 6px; "
            "}"
        )
        self._detail_body.setMaximumHeight(220)
        self._detail_body.setVisible(False)
        v.addWidget(self._detail_body)

        # 媒体结果区 —— set_media_results() 往里放 AudioWaveformWidget / VideoWidget
        self._media_box = QVBoxLayout()
        self._media_box.setSpacing(8)
        self._media_box.setContentsMargins(0, 0, 0, 0)
        v.addLayout(self._media_box)

        self._apply_pill(*self.PILL_RUNNING)

    def _toggle_detail(self):
        opened = not self._detail_body.isVisible()
        self._detail_body.setVisible(opened)
        self._detail_chev.setIcon(ICON_CHEV_D if opened else ICON_CHEV_R)

    def _apply_pill(self, bg: str, fg: str):
        self._status_pill.setStyleSheet(
            f"QFrame {{ background-color: {bg}; border-radius: 10px; }}")
        self._status_lbl.setStyleSheet(f"color: {fg};")

    def set_progress(self, percent: int, text: str):
        self._status_lbl.setText(f"运行中 · {percent}%")
        # 进度行只保留最新一条，避免文本爆炸
        line = text.strip()
        if len(line) > 200:
            line = line[:200] + "…"
        self._progress_lbl.setText(line)

    # 长结果阈值:超过这两个之一就走折叠 UI,只显示首行摘要
    _DETAIL_MAX_CHARS = 150
    _DETAIL_MAX_LINES = 2

    def set_done(self, ok: bool, result: str):
        if ok:
            self._apply_pill(*self.PILL_OK)
            self._status_lbl.setText("已完成")
        else:
            self._apply_pill(*self.PILL_FAIL)
            self._status_lbl.setText("失败")
        self._progress_lbl.setText("")

        body = result or "(无返回)"
        lines = body.splitlines()
        is_long = len(body) > self._DETAIL_MAX_CHARS or len(lines) > self._DETAIL_MAX_LINES

        if is_long:
            # 首行/首段当摘要,完整内容塞详情折叠区
            summary = lines[0].strip() if lines else body
            if len(summary) > self._DETAIL_MAX_CHARS:
                summary = summary[: self._DETAIL_MAX_CHARS] + "…"
            self._result_lbl.setText(("✓ " if ok else "✗ ") + summary)
            self._result_lbl.setVisible(True)

            self._detail_body.setPlainText(body)
            # 滚到顶,失败的 stack trace 第一行最重要
            self._detail_body.moveCursor(QTextCursor.MoveOperation.Start)
            self._detail_title.setText(f"详细输出 ({len(lines)} 行)")
            self._detail_header.setVisible(True)
            # 默认折叠;失败时给个轻提示(标题文案)但仍不展开,避免一次摊开太多
            self._detail_body.setVisible(False)
            self._detail_chev.setIcon(ICON_CHEV_R)
        else:
            self._result_lbl.setText(("✓ " if ok else "✗ ") + body)
            self._result_lbl.setVisible(True)
            self._detail_header.setVisible(False)
            self._detail_body.setVisible(False)

    # 媒体结果区扩展 -----------------------------------------------------------
    # 支持的可内嵌后缀。NOTE: AudioWaveformWidget 走 soundfile 解码,m4a/aac
    # 在某些环境下不被 libsndfile 支持,失败会通过 InfoBar 报错,不会崩。
    # IMAGE_EXTS 用于 Real-ESRGAN 这种产图工具:列出来让用户能点"打开文件夹",
    # MediaListItem 落到 "other" 分支,不挂播放按钮(无内嵌预览)。
    AUDIO_EXTS = {".wav", ".mp3", ".flac", ".ogg", ".m4a", ".aac"}
    VIDEO_EXTS = {".mp4", ".webm", ".mkv", ".mov", ".avi"}
    IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}

    def set_media_results(self, paths):
        """挂一个可折叠的输出文件列表。
        每个文件初始是一行(文件名 + 大小 + 打开目录 + 播放按钮);
        点击播放才懒加载对应的 AudioWaveformWidget / VideoWidget,
        再点一次会销毁释放内存。
        """
        if not paths:
            return

        # 头(可点击折叠) -------------------------------------------------------
        header = _ClickableFrame(self)
        header.setObjectName("MediaHeader")
        header.setCursor(Qt.CursorShape.PointingHandCursor)
        header.setStyleSheet(
            "#MediaHeader { background: transparent; border: none; }"
            "#MediaHeader:hover { background: rgba(255,255,255,0.05); border-radius: 6px; }"
        )
        h = QHBoxLayout(header)
        h.setContentsMargins(6, 4, 6, 4)
        h.setSpacing(6)
        chev = IconWidget(ICON_CHEV_D, header)
        chev.setFixedSize(10, 10)
        h.addWidget(chev, 0, Qt.AlignmentFlag.AlignVCenter)
        title = StrongBodyLabel(f"输出文件 ({len(paths)})", header)
        title.setStyleSheet(f"color: {INK_PRIMARY};")
        h.addWidget(title, 0, Qt.AlignmentFlag.AlignVCenter)
        hint = CaptionLabel("点击文件名行的 ▶ 进行预览", header)
        hint.setStyleSheet(f"color: {INK_TERTIARY};")
        h.addSpacing(8)
        h.addWidget(hint, 0, Qt.AlignmentFlag.AlignVCenter)
        h.addStretch()
        self._media_box.addWidget(header)

        # 列表容器(行) ---------------------------------------------------------
        list_w = QWidget(self)
        list_v = QVBoxLayout(list_w)
        list_v.setContentsMargins(0, 2, 0, 0)
        list_v.setSpacing(6)
        for p in paths:
            list_v.addWidget(MediaListItem(p, list_w))
        self._media_box.addWidget(list_w)

        def _toggle():
            now_open = not list_w.isVisible()
            list_w.setVisible(now_open)
            chev.setIcon(ICON_CHEV_D if now_open else ICON_CHEV_R)
        header.clicked.connect(_toggle)


# ============================================================================
# 消息气泡
# ============================================================================
class MessageBubble(QWidget):
    """单条消息。role: user / assistant
    - user: 右对齐，深表面 + brand 描边
    - assistant: 左对齐，无卡片，avatar + 全宽正文；流式时尾部带 ▍
    """
    CARET = "▍"

    copy_requested = pyqtSignal()  # 只在 assistant 上触发

    def __init__(self, role: str, model_name: str = "", parent=None):
        super().__init__(parent)
        self.role = role
        self.model_name = model_name
        self._text = ""
        self._streaming = False
        self._copy_btn: TransparentToolButton | None = None

        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        if role == "user":
            self._build_user(outer)
        else:
            self._build_assistant(outer)

    def _build_user(self, outer: QHBoxLayout):
        outer.addStretch(2)

        self.card = QWidget(self)
        self.card.setObjectName("UserBubble")
        self.card.setStyleSheet(
            f"#UserBubble {{"
            f"  background-color: {USER_SURFACE};"
            f"  border: 1px solid {USER_HAIRLINE};"
            f"  border-radius: 14px;"
            f"}}"
        )
        c = QVBoxLayout(self.card)
        c.setContentsMargins(14, 10, 14, 12)
        c.setSpacing(6)

        header = QHBoxLayout()
        header.setSpacing(8)
        name = StrongBodyLabel("你", self.card)
        name.setStyleSheet(f"color: {INK_PRIMARY};")
        ts = CaptionLabel(datetime.now().strftime("%H:%M:%S"), self.card)
        ts.setStyleSheet(f"color: {INK_SECONDARY};")
        header.addWidget(name)
        header.addStretch()
        header.addWidget(ts)
        c.addLayout(header)

        self.body = BodyLabel("", self.card)
        self.body.setWordWrap(True)
        self.body.setStyleSheet(f"color: {INK_PRIMARY};")
        self.body.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        self.body.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        c.addWidget(self.body)

        self.attach_layout = QVBoxLayout()
        self.attach_layout.setSpacing(4)
        c.addLayout(self.attach_layout)

        outer.addWidget(self.card, 8)

    def _build_assistant(self, outer: QHBoxLayout):
        row_wrap = QWidget(self)
        row_wrap.setStyleSheet("background: transparent;")
        self.card = row_wrap

        row = QHBoxLayout(row_wrap)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(12)

        # Avatar
        avatar = QFrame(row_wrap)
        avatar.setFixedSize(32, 32)
        avatar.setObjectName("Avatar")
        avatar.setStyleSheet(
            f"#Avatar {{"
            f"  background-color: {ASSISTANT_AVATAR};"
            f"  border: 1px solid {HAIRLINE};"
            f"  border-radius: 16px;"
            f"}}"
        )
        av = QHBoxLayout(avatar)
        av.setContentsMargins(0, 0, 0, 0)
        ic = IconWidget(ICON_BOT, avatar)
        ic.setFixedSize(16, 16)
        av.addWidget(ic, 0, Qt.AlignmentFlag.AlignCenter)
        row.addWidget(avatar, 0, Qt.AlignmentFlag.AlignTop)

        # 内容列
        col = QVBoxLayout()
        col.setSpacing(4)
        col.setContentsMargins(0, 4, 0, 0)

        header = QHBoxLayout()
        header.setSpacing(8)
        name = StrongBodyLabel("助手", row_wrap)
        name.setStyleSheet(f"color: {INK_PRIMARY};")
        header.addWidget(name)
        if self.model_name:
            tag = CaptionLabel(self.model_name, row_wrap)
            tag.setStyleSheet(f"color: {INK_SECONDARY};")
            header.addWidget(tag)
        header.addStretch()

        # 复制按钮（流式期间隐藏）
        self._copy_btn = TransparentToolButton(ICON_COPY, row_wrap)
        self._copy_btn.setFixedSize(22, 22)
        self._copy_btn.setIconSize(QSize(12, 12))
        self._copy_btn.setToolTip("复制回复")
        self._copy_btn.clicked.connect(self._on_copy)
        header.addWidget(self._copy_btn, 0, Qt.AlignmentFlag.AlignVCenter)

        ts = CaptionLabel(datetime.now().strftime("%H:%M:%S"), row_wrap)
        ts.setStyleSheet(f"color: {INK_SECONDARY};")
        header.addWidget(ts)
        col.addLayout(header)

        self.body = BodyLabel("", row_wrap)
        self.body.setWordWrap(True)
        self.body.setTextFormat(Qt.TextFormat.RichText)
        self.body.setStyleSheet(f"color: {INK_PRIMARY};")
        self.body.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        self.body.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        col.addWidget(self.body)

        self.attach_layout = QVBoxLayout()
        self.attach_layout.setSpacing(4)
        col.addLayout(self.attach_layout)

        row.addLayout(col, 1)

        outer.addWidget(row_wrap, 8)
        outer.addStretch(2)

    # ------------------------------------------------------------ public API
    def set_text(self, text: str):
        self._text = text
        self._render()

    def append_text(self, piece: str):
        self._text += piece
        self._render()

    def set_streaming(self, streaming: bool):
        self._streaming = streaming
        self._render()
        if self._copy_btn is not None:
            # 流式过程中先藏起复制按钮，等回复完整再露
            self._copy_btn.setVisible(not streaming and bool(self._text))

    def _render(self):
        if self.role == "assistant":
            text = self._text
            if self._streaming:
                text = (text + " " + self.CARET) if text else self.CARET
            self.body.setText(_render_markdown(text) if text else "")
        else:
            if self._streaming:
                self.body.setText(
                    (self._text + " " + self.CARET) if self._text else self.CARET)
            else:
                self.body.setText(self._text)

    def _on_copy(self):
        if self._text:
            QApplication.clipboard().setText(self._text)
            self.copy_requested.emit()

    def add_attachment(self, filename: str, size_bytes: int):
        chip = QFrame(self.card)
        chip.setObjectName("BubbleChip")
        bg = ATTACH_USER_BG if self.role == "user" else CHIP_BG
        chip.setStyleSheet(
            f"#BubbleChip {{"
            f"  background-color: {bg};"
            f"  border-radius: 6px;"
            f"}}"
        )
        chip.setFixedHeight(24)
        h = QHBoxLayout(chip)
        h.setContentsMargins(8, 0, 8, 0)
        h.setSpacing(6)
        ic = IconWidget(ICON_DOC, chip)
        ic.setFixedSize(12, 12)
        lbl = CaptionLabel(
            f"{filename}  ·  {self._fmt_size(size_bytes)}", chip)
        lbl.setStyleSheet(f"color: {INK_PRIMARY};")
        h.addWidget(ic)
        h.addWidget(lbl)
        h.addStretch()
        self.attach_layout.addWidget(chip)

    @staticmethod
    def _fmt_size(n: int) -> str:
        for unit in ("B", "KB", "MB"):
            if n < 1024:
                return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
            n /= 1024
        return f"{n:.1f} GB"


# ============================================================================
# 主页
# ============================================================================
class LLMChatPage(QWidget):
    """LaunchAI 内置的 LLM 对话页。挂在 Window 的导航里。"""

    CFG_NAMESPACE = "llm_chat"  # configs/config.json 里的命名空间

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("LLMChatPage")

        self._worker: LLMWorker | None = None
        self._models_worker: ModelListWorker | None = None
        self._pending_files: list[str] = []
        self._history: list[dict] = []
        self._streaming_bubble: MessageBubble | None = None

        # 工具调度状态。每轮 LLM 回复结束后,如果解析出 <tool_call>,
        # 进入串行执行模式;全部跑完再把 <tool_result> 拼到 history,
        # 触发下一轮 LLM 调用直到没有新的工具调用为止。
        self._tool_handlers = {
            "list_dir": self._tool_list_dir,
            "read_file": self._tool_read_file,
            "demucs_separate": self._tool_demucs_separate,
            "mix_audio": self._tool_mix_audio,
            "whisper_transcribe": self._tool_whisper_transcribe,
            "rvc_convert": self._tool_rvc_convert,
            "gptsovits_tts": self._tool_gptsovits_tts,
            "realesrgan_upscale": self._tool_realesrgan_upscale,
            "yolo_detect": self._tool_yolo_detect,
            "musicgen_compose": self._tool_musicgen_compose,
            "audiogen_create": self._tool_audiogen_create,
            "search_song": self._tool_search_song,
            "download_song": self._tool_download_song,
            "fetch_song": self._tool_fetch_song,
        }
        self._tool_worker = None              # 当前在跑的 QThread 工人
        self._pending_tool_calls: list[dict] = []
        self._tool_results: list[dict] = []   # [{name, ok, result}]
        self._current_tool_card: ToolCallCard | None = None

        # 持久化用的防抖 timer —— 任何配置字段变了就 .start() 一次，
        # 800 ms 内没新改动才真正落盘
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(800)
        self._save_timer.timeout.connect(self._flush_save)

        self._setup_ui()
        self._load_persisted_config()  # 在 connect 之前载入，避免触发回写
        self._connect_signals()
        self._refresh_status_pill()
        self._resize_input()

    # ------------------------------------------------------------------ UI
    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 22, 28, 22)
        root.setSpacing(14)

        root.addLayout(self._build_header())

        self.config_card = self._build_settings_drawer()
        self.config_card.setVisible(False)
        root.addWidget(self.config_card)

        # 消息区
        self.scroll = SmoothScrollArea(self)
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setStyleSheet(
            "SmoothScrollArea { background: transparent; border: none; }")
        self.scroll.viewport().setStyleSheet("background: transparent;")

        self.msg_container = QWidget()
        self.msg_container.setStyleSheet("background: transparent;")
        self.msg_layout = QVBoxLayout(self.msg_container)
        self.msg_layout.setContentsMargins(4, 4, 4, 4)
        self.msg_layout.setSpacing(14)

        self.empty_hint = CaptionLabel(
            "等待你的第一条消息。System prompt 已锁定，直接告诉它你想做什么。",
            self.msg_container)
        self.empty_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_hint.setStyleSheet(
            f"color: {INK_SECONDARY}; padding: 80px 24px;")
        self.msg_layout.addWidget(self.empty_hint)
        self.msg_layout.addStretch()

        self.scroll.setWidget(self.msg_container)
        root.addWidget(self.scroll, 1)

        root.addWidget(self._build_input_card())

    def _build_header(self) -> QHBoxLayout:
        header = QHBoxLayout()
        header.setSpacing(12)

        title_box = QVBoxLayout()
        title_box.setSpacing(2)

        title_row = QHBoxLayout()
        title_row.setSpacing(10)
        ic = IconWidget(ICON_HEADER, self)
        ic.setFixedSize(22, 22)
        title_row.addWidget(ic, 0, Qt.AlignmentFlag.AlignVCenter)
        title = TitleLabel("AI操作", self)
        title.setStyleSheet(f"color: {INK_PRIMARY};")
        title_row.addWidget(title, 0, Qt.AlignmentFlag.AlignVCenter)
        title_row.addStretch()
        title_box.addLayout(title_row)

        sub = CaptionLabel(
            "AI链式完成任务", self)
        sub.setStyleSheet(f"color: {INK_SECONDARY};")
        title_box.addWidget(sub)

        header.addLayout(title_box)
        header.addStretch()

        self._status_pill = StatusPill(self)
        header.addWidget(self._status_pill, 0, Qt.AlignmentFlag.AlignVCenter)

        self._clear_btn = TransparentToolButton(ICON_CLEAR, self)
        self._clear_btn.setToolTip("清空对话")
        self._clear_btn.setFixedSize(32, 32)
        header.addWidget(self._clear_btn, 0, Qt.AlignmentFlag.AlignVCenter)

        return header

    def _build_settings_drawer(self) -> CardWidget:
        card = CardWidget(self)
        card.setBorderRadius(14)
        cfg = QVBoxLayout(card)
        cfg.setContentsMargins(20, 16, 20, 16)
        cfg.setSpacing(12)

        title = StrongBodyLabel("API 设置", self)
        title.setStyleSheet(f"color: {INK_PRIMARY};")
        cfg.addWidget(title)

        # 第一行：服务商 + Base URL + 模型
        row1 = QHBoxLayout()
        row1.setSpacing(10)

        provider_box = self._labeled_field("服务商预设", width=160)
        self.provider_combo = ComboBox(self)
        self.provider_combo.addItems(list(PROVIDER_PRESETS.keys()))
        self.provider_combo.setFixedWidth(160)
        provider_box.layout().addWidget(self.provider_combo)
        row1.addWidget(provider_box)

        base_box = self._labeled_field("Base URL")
        self.base_edit = LineEdit(self)
        self.base_edit.setPlaceholderText("https://api.openai.com/v1")
        base_box.layout().addWidget(self.base_edit)
        row1.addWidget(base_box, 1)

        # 模型字段：可编辑 ComboBox + 刷新按钮（拉 /models 列表）
        model_box = self._labeled_field("模型", width=280)
        model_row = QHBoxLayout()
        model_row.setContentsMargins(0, 0, 0, 0)
        model_row.setSpacing(6)
        self.model_combo = EditableComboBox(self)
        self.model_combo.setPlaceholderText("gpt-4o-mini")
        self.model_combo.setFixedWidth(240)
        model_row.addWidget(self.model_combo)

        self._refresh_models_btn = TransparentToolButton(ICON_SYNC, self)
        self._refresh_models_btn.setFixedSize(32, 32)
        self._refresh_models_btn.setToolTip(
            "刷新模型列表\n从 {base_url}/models 拉取，本地服务（如 Ollama）也支持")
        model_row.addWidget(self._refresh_models_btn)
        model_box.layout().addLayout(model_row)
        row1.addWidget(model_box)

        cfg.addLayout(row1)

        # API Key
        key_box = self._labeled_field("API Key")
        self.key_edit = PasswordLineEdit(self)
        self.key_edit.setPlaceholderText("sk-...   Ollama 等本地服务可留空")
        key_box.layout().addWidget(self.key_edit)
        cfg.addWidget(key_box)

        # System Prompt 预览（写死，只读；含工具调用协议 / 工具表，可滚动查看）
        sp_box = self._labeled_field(
            "内置 System Prompt · 每次请求自动拼接（含工具调用协议与工具表）")
        self.sp_preview = TextEdit(self)
        self.sp_preview.setReadOnly(True)
        self.sp_preview.setPlainText(SYSTEM_PROMPT)
        self.sp_preview.setFixedHeight(220)
        self.sp_preview.setStyleSheet(
            f"TextEdit {{"
            f"  background: rgba(255, 255, 255, 0.04);"
            f"  border: 1px solid {HAIRLINE};"
            f"  border-radius: 8px;"
            f"  color: {INK_PRIMARY};"
            f"  font-family: Consolas, 'Microsoft YaHei', monospace;"
            f"  font-size: 12px;"
            f"  padding: 8px;"
            f"}}"
        )
        sp_box.layout().addWidget(self.sp_preview)
        cfg.addWidget(sp_box)

        # 持久化提示（暗示配置已被保存到本地）
        hint = CaptionLabel(
            "字段变化会自动写入 configs/config.json，下次启动自动加载。", self)
        hint.setStyleSheet(f"color: {INK_TERTIARY};")
        cfg.addWidget(hint)

        return card

    @staticmethod
    def _labeled_field(label: str, width: int | None = None) -> QWidget:
        wrap = QWidget()
        if width is not None:
            wrap.setFixedWidth(width)
        v = QVBoxLayout(wrap)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)
        lbl = CaptionLabel(label, wrap)
        lbl.setStyleSheet(f"color: {INK_SECONDARY};")
        v.addWidget(lbl)
        return wrap

    def _build_input_card(self) -> CardWidget:
        card = CardWidget(self)
        card.setBorderRadius(14)
        in_lay = QVBoxLayout(card)
        in_lay.setContentsMargins(14, 10, 14, 12)
        in_lay.setSpacing(8)

        # 附件 chip 行
        self.chip_row = QHBoxLayout()
        self.chip_row.setSpacing(6)
        self.chip_row.setContentsMargins(0, 0, 0, 0)
        self.chip_row.addStretch()
        chip_wrap = QWidget(card)
        chip_wrap.setLayout(self.chip_row)
        chip_wrap.setVisible(False)
        chip_wrap.setStyleSheet("background: transparent;")
        self.chip_wrap = chip_wrap
        in_lay.addWidget(chip_wrap)

        # 输入框（支持拖入文件 -> 进入附件区）
        self.input_edit = DropAwareTextEdit(card)
        self.input_edit.setPlaceholderText(
            "输入消息（Enter 发送，Shift+Enter 换行，可直接拖入文件作附件）…")
        self.input_edit.setFixedHeight(INPUT_MIN_H)
        self.input_edit.setStyleSheet(
            f"TextEdit {{"
            f"  background: transparent;"
            f"  border: none;"
            f"  color: {INK_PRIMARY};"
            f"  padding: 4px 0;"
            f"}}"
        )
        self.input_edit.installEventFilter(self)
        in_lay.addWidget(self.input_edit)

        # 工具行
        tool_row = QHBoxLayout()
        tool_row.setSpacing(8)

        self._attach_btn = TransparentToolButton(ICON_ATTACH, card)
        self._attach_btn.setToolTip(
            "添加文件附件\n"
            "支持纯文本 / 代码 / 字幕 / 日志；单文件 ≤ 256 KB\n"
            "二进制或更大的文件只发送元信息")
        self._attach_btn.setFixedSize(32, 32)
        tool_row.addWidget(self._attach_btn)

        tool_row.addStretch()

        self._cancel_btn = PushButton("中断", card)
        self._cancel_btn.setEnabled(False)
        self._cancel_btn.setFixedWidth(72)
        tool_row.addWidget(self._cancel_btn)

        self._send_btn = PrimaryPushButton(ICON_SEND, "发送", card)
        self._send_btn.setFixedWidth(108)
        f = self._send_btn.font()
        f.setBold(True)
        self._send_btn.setFont(f)
        tool_row.addWidget(self._send_btn)

        in_lay.addLayout(tool_row)

        return card

    # ------------------------------------------------------------------ events
    def eventFilter(self, obj, ev):
        if obj is self.input_edit and isinstance(ev, QKeyEvent) \
                and ev.type() == QKeyEvent.Type.KeyPress:
            if ev.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                if ev.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                    return False
                self._on_send()
                return True
        return super().eventFilter(obj, ev)

    def _connect_signals(self):
        self._send_btn.clicked.connect(self._on_send)
        self._cancel_btn.clicked.connect(self._on_cancel)
        self._attach_btn.clicked.connect(self._on_attach)
        self._clear_btn.clicked.connect(self._on_clear)
        self._status_pill.clicked.connect(self._on_toggle_config)
        self.provider_combo.currentTextChanged.connect(
            self._on_provider_changed)

        # 文本变化既触发状态 pill 刷新，也排队一次防抖保存
        # 注意：qfluentwidgets 的 EditableComboBox 是 LineEdit 的子类，
        # 用 textChanged 而不是 currentTextChanged。
        self.base_edit.textChanged.connect(self._on_config_field_changed)
        self.model_combo.textChanged.connect(self._on_config_field_changed)
        self.key_edit.textChanged.connect(self._on_config_field_changed)

        # 刷新模型列表
        self._refresh_models_btn.clicked.connect(self._refresh_models)

        self.input_edit.textChanged.connect(self._resize_input)
        self.input_edit.filesDropped.connect(self._on_files_dropped)

    # ------------------------------------------------------------------ config
    def _on_config_field_changed(self, *_):
        """任何配置字段变化时统一入口：刷新 pill + 排队防抖保存"""
        self._refresh_status_pill()
        self._save_timer.start()

    def _flush_save(self):
        """防抖 timer 到期：一次性把所有配置字段批量写盘"""
        get_config_manager().update_global_config({
            self.CFG_NAMESPACE: {
                "provider": self.provider_combo.currentText(),
                "base_url": self.base_edit.text().strip(),
                "model": self.model_combo.currentText().strip(),
                "api_key": self.key_edit.text().strip(),
            }
        })

    def _load_persisted_config(self):
        cfg_provider = get_field(f"{self.CFG_NAMESPACE}.provider", "OpenAI")
        cfg_base = get_field(f"{self.CFG_NAMESPACE}.base_url", "")
        cfg_model = get_field(f"{self.CFG_NAMESPACE}.model", "")
        cfg_key = get_field(f"{self.CFG_NAMESPACE}.api_key", "")
        cfg_models = get_field(f"{self.CFG_NAMESPACE}.models", []) or []

        # 先把 provider 选好（不触发 _apply_provider）
        if cfg_provider in PROVIDER_PRESETS:
            self.provider_combo.blockSignals(True)
            self.provider_combo.setCurrentText(cfg_provider)
            self.provider_combo.blockSignals(False)

        # 灌入上次拉到的模型列表（如果有），保证下次启动不用重新拉
        if cfg_models:
            self.model_combo.blockSignals(True)
            self.model_combo.addItems(
                [m for m in cfg_models if isinstance(m, str)])
            self.model_combo.blockSignals(False)

        # 决定 base/model/key
        # 注意：EditableComboBox 的可编辑文本走 setText，setCurrentText 在没有
        # 匹配 item 时是个 no-op（它内部是 LineEdit）。
        if cfg_base or cfg_model or cfg_key:
            # 用户曾经填过 —— 直接还原
            if cfg_base:
                self.base_edit.setText(cfg_base)
            if cfg_model:
                self.model_combo.setText(cfg_model)
            if cfg_key:
                self.key_edit.setText(cfg_key)
        else:
            # 首次使用 —— 用 provider 的默认值填上 base/model
            preset_base, preset_model = PROVIDER_PRESETS.get(
                cfg_provider, PROVIDER_PRESETS["OpenAI"])
            self.base_edit.setText(preset_base)
            self.model_combo.setText(preset_model)

    # ------------------------------------------------------------------ slots
    def _on_provider_changed(self, name: str):
        self._apply_provider(name)
        # provider 切换会触发 base/model 的 setText/setCurrentText，
        # 它们各自的信号会经过 _on_config_field_changed 排队保存 —— 这里不用再手动写
        self._save_timer.start()

    def _apply_provider(self, name: str):
        if name not in PROVIDER_PRESETS:
            return
        base, model = PROVIDER_PRESETS[name]
        preset_bases = [v[0] for v in PROVIDER_PRESETS.values()]
        preset_models = [v[1] for v in PROVIDER_PRESETS.values()]
        # 只在用户没改过时才覆盖（当前值要么空、要么命中其它预设）
        if not self.base_edit.text().strip() or self.base_edit.text() in preset_bases:
            self.base_edit.setText(base)
        cur_model = self.model_combo.text().strip()
        if not cur_model or cur_model in preset_models:
            self.model_combo.setText(model)
        self._refresh_status_pill()

    # ------------------------------------------------------------ models list
    def _refresh_models(self):
        if self._models_worker and self._models_worker.isRunning():
            return
        base = self.base_edit.text().strip()
        if not base:
            InfoBar.warning("缺少 Base URL", "请先填写 Base URL 再刷新",
                            parent=self, duration=1500,
                            position=InfoBarPosition.TOP_RIGHT)
            return

        InfoBar.info("正在刷新", f"GET {base}/models",
                     parent=self, duration=1500,
                     position=InfoBarPosition.TOP_RIGHT)
        self._refresh_models_btn.setEnabled(False)

        self._models_worker = ModelListWorker(
            api_key=self.key_edit.text().strip(),
            base_url=base,
            parent=self,
        )
        self._models_worker.finished_list.connect(self._on_models_loaded)
        self._models_worker.error.connect(self._on_models_error)
        self._models_worker.finished.connect(
            lambda: self._refresh_models_btn.setEnabled(True))
        self._models_worker.start()

    def _on_models_loaded(self, ids: list):
        # 保留用户当前输入；只换下拉列表
        cur = self.model_combo.text()
        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        self.model_combo.addItems(ids)
        # addItems 会把显示文字重置成第一个 item；setText 拿回用户原本的输入
        if cur:
            self.model_combo.setText(cur)
        self.model_combo.blockSignals(False)

        # 列表也存进 config，下次启动直接有下拉项可选
        get_config_manager().update_global_config({
            self.CFG_NAMESPACE: {"models": ids}
        })

        InfoBar.success("已刷新", f"拿到 {len(ids)} 个模型",
                        parent=self, duration=1500,
                        position=InfoBarPosition.TOP_RIGHT)

    def _on_models_error(self, msg: str):
        InfoBar.error("刷新失败", msg[:200], parent=self,
                      duration=4000, position=InfoBarPosition.TOP_RIGHT)

    def _on_toggle_config(self):
        new_visible = not self.config_card.isVisible()
        self.config_card.setVisible(new_visible)
        self._status_pill.set_expanded(new_visible)

    def _refresh_status_pill(self):
        base = self.base_edit.text().strip()
        model = self.model_combo.currentText().strip()
        key = self.key_edit.text().strip()

        provider = "自定义"
        for n, (b, _m) in PROVIDER_PRESETS.items():
            if b and base == b:
                provider = n
                break

        if not base or not model:
            self._status_pill.set_state(False, "未配置 · 缺 Base URL / 模型")
            return

        is_local = (base.startswith("http://127.0.0.1")
                    or base.startswith("http://localhost")
                    or "ollama" in base.lower())
        if not key and not is_local:
            self._status_pill.set_state(False, f"{provider} · 缺 API Key")
            return

        self._status_pill.set_state(True, f"就绪 · {provider} · {model}")

    def _on_attach(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "选择文件附件", "", "全部文件 (*)")
        self._add_attachments(paths)

    def _on_files_dropped(self, paths: list):
        n = self._add_attachments(paths)
        if n:
            InfoBar.success(
                "已添加附件", f"通过拖拽添加 {n} 个文件",
                parent=self, duration=1500,
                position=InfoBarPosition.TOP_RIGHT,
            )

    def _add_attachments(self, paths) -> int:
        """统一的附件入栈逻辑;返回本次新加的文件数(去重后)。"""
        added = 0
        for p in paths or []:
            if not p or p in self._pending_files:
                continue
            self._pending_files.append(p)
            chip = AttachmentChip(p, self)
            chip.removed.connect(self._remove_chip)
            self.chip_row.insertWidget(self.chip_row.count() - 1, chip)
            added += 1
        self.chip_wrap.setVisible(bool(self._pending_files))
        return added

    def _remove_chip(self, chip: AttachmentChip):
        if chip.path in self._pending_files:
            self._pending_files.remove(chip.path)
        chip.setParent(None)
        chip.deleteLater()
        self.chip_wrap.setVisible(bool(self._pending_files))

    def _on_clear(self):
        if self._worker and self._worker.isRunning():
            InfoBar.warning("无法清空", "请先中断当前请求", parent=self)
            return
        while self.msg_layout.count() > 0:
            item = self.msg_layout.takeAt(0)
            w = item.widget()
            if w is not None and w is not self.empty_hint:
                w.deleteLater()
        self.empty_hint.setVisible(True)
        self.msg_layout.addWidget(self.empty_hint)
        self.msg_layout.addStretch()
        self._history.clear()

    def _on_cancel(self):
        any_cancelled = False
        if self._worker and self._worker.isRunning():
            self._worker.cancel()
            self._worker.wait(1000)
            any_cancelled = True
        if self._tool_worker and self._tool_worker.isRunning():
            try:
                self._tool_worker.cancel()
            except Exception:
                pass
            self._tool_worker.wait(2000)
            any_cancelled = True
        # 清空工具调度状态,避免下一次 send 时残留
        self._pending_tool_calls = []
        if self._current_tool_card is not None:
            self._current_tool_card.set_done(False, "已中断")
            self._current_tool_card = None
        if self._streaming_bubble:
            self._streaming_bubble.set_streaming(False)
            self._streaming_bubble.append_text("\n\n⚠ 已中断")
            self._streaming_bubble = None
        self._set_busy(False)
        if any_cancelled:
            InfoBar.warning("已中断", "请求已取消", parent=self, duration=1500)

    def _on_send(self):
        if self._worker and self._worker.isRunning():
            return
        text = self.input_edit.toPlainText().strip()
        if not text and not self._pending_files:
            InfoBar.warning("空消息", "请输入内容或添加附件",
                            parent=self, duration=1500)
            return

        base = self.base_edit.text().strip()
        model = self.model_combo.currentText().strip()
        if not base or not model:
            InfoBar.warning("缺少配置", "请先填写 Base URL 与 模型",
                            parent=self)
            if not self.config_card.isVisible():
                self._on_toggle_config()
            return

        self.empty_hint.setVisible(False)

        user_bubble = self._add_bubble("user")
        user_bubble.set_text(text if text else "(仅附件)")
        for p in self._pending_files:
            try:
                sz = os.path.getsize(p)
            except OSError:
                sz = 0
            user_bubble.add_attachment(Path(p).name, sz)

        full_user_msg = self._build_user_content(text, self._pending_files)
        self._history.append({"role": "user", "content": full_user_msg})

        bot_bubble = self._add_bubble("assistant")
        bot_bubble.set_streaming(True)
        self._streaming_bubble = bot_bubble

        self.input_edit.clear()
        self._clear_pending_files()

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT}] + self._history
        self._set_busy(True)
        self._worker = LLMWorker(
            api_key=self.key_edit.text().strip(),
            base_url=base,
            model=model,
            messages=messages,
        )
        self._worker.chunk.connect(self._on_chunk)
        self._worker.finished_msg.connect(self._on_finished)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    # ------------------------------------------------------------------ helpers
    def _resize_input(self):
        doc_h = self.input_edit.document().size().height()
        target = int(doc_h) + 16
        target = max(INPUT_MIN_H, min(INPUT_MAX_H, target))
        if self.input_edit.height() != target:
            self.input_edit.setFixedHeight(target)

    def _clear_pending_files(self):
        self._pending_files.clear()
        while self.chip_row.count() > 1:
            item = self.chip_row.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self.chip_wrap.setVisible(False)

    def _build_user_content(self, text: str, files: list[str]) -> str:
        if not files:
            return text
        parts = [text] if text else []
        for p in files:
            abspath = os.path.abspath(p)
            name = Path(p).name
            # 目录附件:不内联内容,只交一个路径 + 提示,让 LLM 用 list_dir 探查
            if os.path.isdir(p):
                parts.append(
                    f"\n\n--- 目录: {name} ---\n"
                    f"路径: {abspath}\n"
                    f"(目录附件,请用 list_dir 探查内容;"
                    f"看到 .list 这类文本清单优先用 read_file 打开。)"
                )
                continue
            ext = Path(p).suffix.lower()
            try:
                size = os.path.getsize(p)
            except OSError:
                size = 0
            if ext in TEXT_EXTS and size <= MAX_FILE_BYTES:
                try:
                    with open(p, "r", encoding="utf-8", errors="replace") as f:
                        content = f.read(MAX_FILE_BYTES + 1)
                    truncated = len(content) > MAX_FILE_BYTES
                    if truncated:
                        content = content[:MAX_FILE_BYTES] + "\n…（已截断）"
                    parts.append(
                        f"\n\n--- 文件: {name} ({size} bytes)"
                        f"{' [TRUNCATED]' if truncated else ''} ---\n"
                        f"路径: {abspath}\n"
                        f"```{ext.lstrip('.')}\n{content}\n```"
                    )
                except Exception as e:
                    parts.append(
                        f"\n\n--- 文件: {name} 读取失败: {e} ---\n"
                        f"路径: {abspath}"
                    )
            else:
                # 二进制 / 超大文件不内联,只把元信息和绝对路径告诉 LLM。
                # 路径是 LLM 调本地工具(demucs_separate 等)时 `input` 参数的来源。
                mime = mimetypes.guess_type(p)[0] or "unknown"
                parts.append(
                    f"\n\n--- 附件: {name} ({size} bytes, {mime}) "
                    f"[二进制或过大，未内联] ---\n"
                    f"路径: {abspath}"
                )
        return "\n".join(parts)

    def _add_bubble(self, role: str) -> MessageBubble:
        model = self.model_combo.currentText().strip() if role == "assistant" else ""
        bubble = MessageBubble(role, model_name=model, parent=self)
        if role == "assistant":
            bubble.copy_requested.connect(self._on_copy_assistant)
        self.msg_layout.insertWidget(self.msg_layout.count() - 1, bubble)
        # 用户刚发消息时强制滚到底(显然要看到自己的输入);
        # 助手新建空气泡时遵循"用户是否在底部"的判断 —— 用户正在翻历史
        # 就不要把他拉走。
        force = (role == "user")
        QTimer.singleShot(30, lambda: self._scroll_to_bottom(force=force))
        return bubble

    def _on_copy_assistant(self):
        InfoBar.success("已复制", "助手回复已写入剪贴板",
                        parent=self, duration=1200,
                        position=InfoBarPosition.TOP_RIGHT)

    # 用户向上滚开多少像素后就停止"跟随到底"。约等于一行半,
    # 既能容忍最后一行刚好被新内容顶超出的瞬时差,又能让用户主动
    # 往上滚一点就脱离自动滚动。
    _AUTO_SCROLL_STICKY_PX = 64

    def _scroll_to_bottom(self, force: bool = False):
        bar = self.scroll.verticalScrollBar()
        if not force:
            # 距离底部超过阈值 = 用户已经主动滚开,这种情况下绝不强制拉回底部
            if (bar.maximum() - bar.value()) > self._AUTO_SCROLL_STICKY_PX:
                return
        bar.setValue(bar.maximum())

    def _set_busy(self, busy: bool):
        self._send_btn.setEnabled(not busy)
        self._cancel_btn.setEnabled(busy)
        self.input_edit.setEnabled(not busy)
        self._attach_btn.setEnabled(not busy)
        self.provider_combo.setEnabled(not busy)
        self.base_edit.setEnabled(not busy)
        self.model_combo.setEnabled(not busy)
        self.key_edit.setEnabled(not busy)
        self._refresh_models_btn.setEnabled(not busy)

    # ------------------------------------------------------------------ worker callbacks
    def _on_chunk(self, piece: str):
        if not self._streaming_bubble:
            return
        self._streaming_bubble.append_text(piece)
        self._scroll_to_bottom()

    def _on_finished(self, full: str):
        # 先解析 <tool_call>,这样后续渲染可以决定:
        #  - 有工具调用 -> 隐藏标签原文,只显示标签外的普通文字(更干净);
        #  - 无工具调用 -> 走原来的展示路径。
        calls = self._parse_tool_calls(full or "")
        # 先剪闭合对,再吃掉最后一段没关的 <tool_call> 尾巴 (parser 已经消化过它了)
        display_text = _TOOL_CALL_RE.sub("", full or "")
        display_text = _TOOL_CALL_TAIL_RE.sub("", display_text).strip()

        if self._streaming_bubble:
            self._streaming_bubble.set_streaming(False)
            if display_text:
                self._streaming_bubble.set_text(display_text)
            elif calls:
                self._streaming_bubble.set_text("(正在调用工具…)")
            elif not self._streaming_bubble._text:
                self._streaming_bubble.set_text("(无返回内容)")
        # history 里保留 LLM 的原始回复(含 <tool_call>),
        # 这样下一轮请求 LLM 能看到自己刚发起过哪些调用。
        if full:
            self._history.append({"role": "assistant", "content": full})
        self._streaming_bubble = None
        self._scroll_to_bottom()

        if calls:
            # 进入工具执行流程,保持 busy 状态,直到全部跑完并拿到下一轮 LLM 回复。
            self._execute_tool_calls(calls)
            return
        self._set_busy(False)

    # ------------------------------------------------------------------ tool calling
    def _parse_tool_calls(self, text: str) -> list[dict]:
        """从 LLM 输出里抽 <tool_call>{json}</tool_call> 块。
        允许 LLM 在标签内套 ```json ... ``` 代码栅栏,会自动剥掉。
        如果最后一个 <tool_call> 没有 </tool_call> 尾标签 (被截断或模型漏写),
        再退一步用平衡花括号扫描找到 JSON,不要因为一个尾标签把整轮 tool call 丢掉。
        """
        out: list[dict] = []
        matched_spans: list[tuple[int, int]] = []
        for m in _TOOL_CALL_RE.finditer(text):
            matched_spans.append(m.span())
            obj = self._parse_tool_call_body(m.group(1))
            if obj is not None:
                out.append(obj)

        # 有开标签但没被上面正则匹上的 —— 意味着漏了 </tool_call>。
        # 只处理"排在所有闭合命中之后"的那些开标签,免得干扰前面正常闭合的对。
        last_matched_end = matched_spans[-1][1] if matched_spans else 0
        for om in _TOOL_CALL_OPEN_RE.finditer(text, last_matched_end):
            body = self._extract_json_after(text, om.end())
            if body is None:
                continue
            obj = self._parse_tool_call_body(body)
            if obj is not None:
                out.append(obj)
        return out

    def _parse_tool_call_body(self, raw: str) -> dict | None:
        """把 <tool_call> 里的一段文本解析成 {name, arguments} dict, 失败返回 None."""
        raw = raw.strip()
        if raw.startswith("```"):
            lines = raw.splitlines()
            if len(lines) >= 2 and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip().startswith("```"):
                lines = lines[:-1]
            raw = "\n".join(lines).strip()
        # 若结尾带遗留 `</tool_call>` (万一) 或前后杂空白,再收一次
        raw = raw.rstrip("`").strip()
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            return None
        if not (isinstance(obj, dict) and isinstance(obj.get("name"), str)):
            return None
        if not isinstance(obj.get("arguments"), dict):
            obj["arguments"] = {}
        return obj

    @staticmethod
    def _extract_json_after(text: str, start: int) -> str | None:
        """从 `text[start:]` 里定位第一个 `{`,按平衡花括号扫描把整个 JSON 对象切出来。
        支持字符串内的 `{}` 转义与 `\\"` 转义。返回不带前后杂物的 JSON 文本。
        找不到平衡尾就返回 None(说明确实被截断得连 JSON 都没写完,交给上层放弃)。
        """
        n = len(text)
        i = start
        while i < n and text[i] != "{":
            i += 1
        if i >= n:
            return None
        depth = 0
        in_str = False
        esc = False
        j = i
        while j < n:
            ch = text[j]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
            else:
                if ch == '"':
                    in_str = True
                elif ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        return text[i:j + 1]
            j += 1
        return None

    def _execute_tool_calls(self, calls: list[dict]):
        self._pending_tool_calls = list(calls)
        self._tool_results = []
        self._run_next_tool()

    def _run_next_tool(self):
        if not self._pending_tool_calls:
            self._continue_dialog_with_tool_results()
            return
        call = self._pending_tool_calls.pop(0)
        name = call.get("name", "")
        args = call.get("arguments") or {}
        handler = self._tool_handlers.get(name)
        if handler is None:
            # 未知工具:直接记一个 error 结果,不阻塞后续工具
            self._add_tool_card(name, args)
            if self._current_tool_card:
                self._current_tool_card.set_done(False, f"未知工具: {name}")
                self._current_tool_card = None
            self._tool_results.append(
                {"name": name, "ok": False, "result": f"未知工具: {name}"})
            self._run_next_tool()
            return
        # 工具底层包就绪检查:没装就让卡片直接报"请去 XX 页装",
        # 不让 worker 的 subprocess ModuleNotFoundError 整页 stack 污染对话
        from utils.tool_ready import check as _check_tool_ready
        not_ready = _check_tool_ready(name)
        if not_ready:
            self._add_tool_card(name, args)
            if self._current_tool_card:
                self._current_tool_card.set_done(False, not_ready)
                self._current_tool_card = None
            self._tool_results.append(
                {"name": name, "ok": False, "result": not_ready})
            self._run_next_tool()
            return
        try:
            self._add_tool_card(name, args)
            handler(args)  # 应当启动 worker,异步触发 _on_tool_done
        except Exception as e:
            if self._current_tool_card:
                self._current_tool_card.set_done(False, f"启动失败: {e}")
                self._current_tool_card = None
            self._tool_results.append(
                {"name": name, "ok": False, "result": f"启动失败: {e}"})
            self._run_next_tool()

    def _add_tool_card(self, name: str, args: dict) -> ToolCallCard:
        card = ToolCallCard(name, args, parent=self.msg_container)
        self.msg_layout.insertWidget(self.msg_layout.count() - 1, card)
        self._current_tool_card = card
        QTimer.singleShot(30, self._scroll_to_bottom)
        return card

    def _on_tool_progress(self, percent: int, status: str):
        if self._current_tool_card:
            self._current_tool_card.set_progress(percent, status)

    def _on_tool_done(self, name: str, ok: bool, result: str):
        if self._current_tool_card:
            self._current_tool_card.set_done(ok, result)
            if ok and result:
                media = self._collect_media(result)
                if media:
                    self._current_tool_card.set_media_results(media)
            self._current_tool_card = None
        # 清理 worker 引用,允许 GC
        self._tool_worker = None
        self._tool_results.append(
            {"name": name, "ok": bool(ok), "result": str(result)})
        self._scroll_to_bottom()
        self._run_next_tool()

    def _collect_media(self, path: str) -> list:
        """从工具返回路径里捞出可播放的音 / 视频文件。
        - 返回值是文件 -> 单元素列表(命中白名单)
        - 返回值是目录 -> 列出直接子文件里命中白名单的
        - 其它 -> 空列表

        兼容 result 含附加信息的多行格式(yolo_detect 用):首行非空当作路径。
        """
        if not path:
            return []
        if "\n" in path:
            first = path.splitlines()[0].strip()
            if first:
                path = first
        media_exts = (ToolCallCard.AUDIO_EXTS
                      | ToolCallCard.VIDEO_EXTS
                      | ToolCallCard.IMAGE_EXTS)
        if os.path.isfile(path):
            return [path] if os.path.splitext(path)[1].lower() in media_exts else []
        if os.path.isdir(path):
            out = []
            try:
                entries = sorted(os.listdir(path))
            except OSError:
                return []
            for entry in entries:
                full = os.path.join(path, entry)
                if (os.path.isfile(full)
                        and os.path.splitext(full)[1].lower() in media_exts):
                    out.append(full)
            return out
        return []

    # tool_result 喂回 LLM 时单条最长允许的字符数 —— 失败 worker 的 stack trace
        # 经常上千字符,全塞回去会迅速把模型上下文撑满且让模型注意力被噪音带跑。
        # 长结果只保留首部 + 末部(末部往往包含真正的错误信息),中间用一行省略。
    _LLM_RESULT_MAX = 600
    _LLM_RESULT_HEAD = 240
    _LLM_RESULT_TAIL = 320

    def _truncate_for_llm(self, text: str) -> str:
        if not text or len(text) <= self._LLM_RESULT_MAX:
            return text
        head = text[: self._LLM_RESULT_HEAD]
        tail = text[-self._LLM_RESULT_TAIL :]
        omitted = len(text) - self._LLM_RESULT_HEAD - self._LLM_RESULT_TAIL
        return f"{head}\n…(省略 {omitted} 字符 中间内容,用户可在工具卡片展开详情查看)…\n{tail}"

    def _continue_dialog_with_tool_results(self):
        """所有工具跑完:把结果以 user 消息送回 LLM,启动下一轮回复。"""
        parts = []
        for r in self._tool_results:
            status = "ok" if r["ok"] else "error"
            # 转义最小: <tool_result> 内容里如果含 `</tool_result>` 会破坏解析,
            # 但模型不会把它写回提示,且用户上下文里不太会自然出现这个字串,先不做转义。
            result_text = self._truncate_for_llm(str(r["result"]))
            parts.append(
                f'<tool_result name="{r["name"]}" status="{status}">'
                f'{result_text}'
                f'</tool_result>'
            )
        tool_msg = "\n".join(
            parts) if parts else "<tool_result status=\"error\">no result</tool_result>"
        self._history.append({"role": "user", "content": tool_msg})
        self._tool_results = []

        # 启动后续 LLM 调用,沿用当前 base/model/key
        base = self.base_edit.text().strip()
        model = self.model_combo.currentText().strip()
        if not base or not model:
            # 配置在工具执行期间被改坏了,保底退出
            self._set_busy(False)
            InfoBar.error("配置丢失", "无法继续对话:Base URL / 模型 已被清空",
                          parent=self, duration=3000,
                          position=InfoBarPosition.TOP)
            return

        bot_bubble = self._add_bubble("assistant")
        bot_bubble.set_streaming(True)
        self._streaming_bubble = bot_bubble

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT}] + self._history
        self._worker = LLMWorker(
            api_key=self.key_edit.text().strip(),
            base_url=base,
            model=model,
            messages=messages,
        )
        self._worker.chunk.connect(self._on_chunk)
        self._worker.finished_msg.connect(self._on_finished)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    # 工具实现 ----------------------------------------------------------------
    def _tool_list_dir(self, args: dict):
        """同步列目录。通过 QTimer 把回调推回事件循环,
        避免 handler -> _on_tool_done -> _run_next_tool -> handler ... 同步递归。
        """
        def reply(ok: bool, result: str):
            QTimer.singleShot(
                0, lambda: self._on_tool_done("list_dir", ok, result))

        path = args.get("path")
        if not path or not isinstance(path, str):
            reply(False, "缺少 path 参数(目录绝对路径)")
            return
        if not os.path.isdir(path):
            reply(False, f"目录不存在或不是目录: {path}")
            return

        recursive = bool(args.get("recursive"))
        MAX_ENTRIES = 200
        MAX_DEPTH = 2 if recursive else 1

        entries: list[dict] = []
        truncated = [False]

        def walk(d: str, depth: int):
            if truncated[0] or depth > MAX_DEPTH:
                return
            try:
                names = sorted(os.listdir(d))
            except OSError:
                return
            for n in names:
                if len(entries) >= MAX_ENTRIES:
                    truncated[0] = True
                    return
                full = os.path.join(d, n)
                try:
                    if os.path.isdir(full):
                        entries.append({
                            "name": n, "type": "dir",
                            "size": None, "path": full,
                        })
                        if recursive and depth < MAX_DEPTH:
                            walk(full, depth + 1)
                    elif os.path.isfile(full):
                        try:
                            sz = os.path.getsize(full)
                        except OSError:
                            sz = None
                        entries.append({
                            "name": n, "type": "file",
                            "size": sz, "path": full,
                        })
                except OSError:
                    continue

        walk(path, 1)

        payload = {
            "path": os.path.abspath(path),
            "count": len(entries),
            "truncated": truncated[0],
            "entries": entries,
        }
        reply(True, json.dumps(payload, ensure_ascii=False))

    def _tool_read_file(self, args: dict):
        """同步读文件;只用来看文本,二进制 / 大权重不要走这里。"""
        def reply(ok: bool, result: str):
            QTimer.singleShot(
                0, lambda: self._on_tool_done("read_file", ok, result))

        path = args.get("path")
        if not path or not isinstance(path, str):
            reply(False, "缺少 path 参数(文件绝对路径)")
            return
        if not os.path.isfile(path):
            reply(False, f"文件不存在或不是文件: {path}")
            return

        raw_mb = args.get("max_bytes")
        try:
            max_bytes = int(raw_mb) if raw_mb is not None else 65536
        except (TypeError, ValueError):
            max_bytes = 65536
        max_bytes = max(1, min(max_bytes, 1024 * 1024))

        try:
            size = os.path.getsize(path)
            with open(path, "rb") as f:
                buf = f.read(max_bytes + 1)
        except OSError as e:
            reply(False, f"读取失败: {e}")
            return

        truncated = len(buf) > max_bytes
        if truncated:
            buf = buf[:max_bytes]
        text = buf.decode("utf-8", errors="replace")
        header = (
            f"[path: {os.path.abspath(path)}, size: {size}, "
            f"returned_bytes: {len(buf)}, truncated: {truncated}]\n"
        )
        reply(True, header + text)

    def _tool_demucs_separate(self, args: dict):
        """启动 DemucsWorker。完成 / 失败时回调 _on_tool_done。"""
        # 延迟 import 避免冷启动加载 torch 相关依赖。
        from workers.demucs_worker import DemucsWorker
        from utils import paths as _paths

        input_path = args.get("input")
        if not input_path or not isinstance(input_path, str):
            self._on_tool_done("demucs_separate", False,
                               "缺少 input 参数(本地音频绝对路径)")
            return
        if not os.path.exists(input_path):
            self._on_tool_done("demucs_separate", False,
                               f"输入文件不存在: {input_path}")
            return

        model = args.get("model") or "htdemucs"
        device = args.get("device") or "cuda"
        two_stems = args.get("two_stems") or None
        fmt = args.get("format") or "wav"

        params = {
            "input": input_path,
            "output": _paths.output_dir("demucs"),
            "model": model,
            "device": device,
            # 4 轨全开;LLM 想精简就靠 two_stems
            "tracks": {"vocals": True, "drums": True, "bass": True, "other": True},
            "shifts": 1,
            # segment 上限取决于模型: htdemucs(Transformer) 训练最大 7.8,超了会 FATAL;
            # 与 GUI 页面 Slider 默认值一致选 7,各模型都安全。
            "segment": 7,
            "overlap": 0.25,
            "format": fmt,
            "two_stems": two_stems,
        }

        worker = DemucsWorker(params)
        self._tool_worker = worker
        worker.progress.connect(self._on_tool_progress)
        worker.finished.connect(
            lambda out_dir: self._on_tool_done("demucs_separate", True, out_dir))
        worker.error.connect(
            lambda msg: self._on_tool_done("demucs_separate", False, msg))
        worker.start()

    def _tool_mix_audio(self, args: dict):
        """启动 AudioMixWorker 用 ffmpeg amix / concat 合并多路音频。"""
        from workers.audio_mix_worker import AudioMixWorker
        from utils import paths as _paths

        raw = args.get("inputs")
        if not isinstance(raw, list) or not raw:
            self._on_tool_done(
                "mix_audio", False,
                "缺少 inputs(音频文件路径数组,至少 1 路;翻唱常见两路:rvc/sovits 处理后的人声 + demucs 的 no_vocals.wav)")
            return
        inputs: list[str] = []
        for p in raw:
            if isinstance(p, str) and p.strip():
                inputs.append(p.strip())
        if not inputs:
            self._on_tool_done("mix_audio", False, "inputs 全是空字符串")
            return
        for p in inputs:
            if not os.path.isfile(p):
                self._on_tool_done(
                    "mix_audio", False, f"输入不存在: {p}")
                return

        mode = (args.get("mode") or "mix").lower()
        if mode not in ("mix", "concat"):
            self._on_tool_done(
                "mix_audio", False,
                f"未知 mode: {mode!r},仅支持 mix / concat")
            return

        weights = args.get("weights")
        if weights is not None:
            if not isinstance(weights, list):
                self._on_tool_done(
                    "mix_audio", False, "weights 必须是数字数组")
                return
            try:
                weights = [float(w) for w in weights]
            except (TypeError, ValueError):
                self._on_tool_done(
                    "mix_audio", False, f"weights 含非数字: {weights!r}")
                return

        fmt = (args.get("format") or "wav").lower()
        if fmt not in ("wav", "mp3", "flac"):
            fmt = "wav"

        out_dir = _paths.output_dir("node", "audio_merge")
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        output = os.path.join(out_dir, f"mix_{mode}_{ts}.{fmt}")

        worker = AudioMixWorker({
            "inputs":  inputs,
            "mode":    mode,
            "weights": weights,
            "output":  output,
        })
        self._tool_worker = worker
        worker.progress.connect(self._on_tool_progress)
        worker.finished.connect(
            lambda out: self._on_tool_done("mix_audio", True, out))
        worker.error.connect(
            lambda msg: self._on_tool_done("mix_audio", False, msg))
        worker.start()

    def _tool_whisper_transcribe(self, args: dict):
        """启动 WhisperWorker 做语音转录。完成 / 失败时回调 _on_tool_done。

        input 支持单字符串或字符串数组,worker._get_file_list 两种形态都吃。
        其它高级参数(beam_size / best_of / temperature / condition_on_previous_text)
        不暴露给 LLM,走 worker 默认值。
        """
        from workers.whisper_worker import WhisperWorker
        from utils import paths as _paths

        raw = args.get("input")
        if isinstance(raw, str):
            files = [raw.strip()] if raw.strip() else []
        elif isinstance(raw, list):
            files = [p.strip() for p in raw
                     if isinstance(p, str) and p.strip()]
        else:
            files = []

        if not files:
            self._on_tool_done(
                "whisper_transcribe", False,
                "缺少 input 参数(本地音频绝对路径,可单字符串或字符串数组)")
            return
        for f in files:
            if not os.path.isfile(f):
                self._on_tool_done(
                    "whisper_transcribe", False, f"输入文件不存在: {f}")
                return

        def pick(name, default):
            v = args.get(name)
            if v is None or (isinstance(v, str) and not v.strip()):
                return default
            return v.strip() if isinstance(v, str) else v

        # language: None / "" / "auto" 都视为自动检测
        lang = pick("language", None)
        if isinstance(lang, str) and lang.lower() == "auto":
            lang = None

        params = {
            "input": files if len(files) > 1 else files[0],
            "output": _paths.output_dir("whisper"),
            "model": pick("model", "small"),
            "device": pick("device", "cuda"),
            "language": lang,
            "task": pick("task", "transcribe"),
            "output_format": pick("output_format", "all"),
            "word_timestamps": bool(args.get("word_timestamps", False)),
        }

        worker = WhisperWorker(params)
        self._tool_worker = worker
        worker.progress.connect(self._on_tool_progress)
        worker.finished.connect(
            lambda out_dir: self._on_tool_done(
                "whisper_transcribe", True, out_dir))
        worker.error.connect(
            lambda msg: self._on_tool_done(
                "whisper_transcribe", False, msg))
        worker.start()

    def _tool_rvc_convert(self, args: dict):
        """启动 RVCInferWorker 做批量声色转换。

        高级参数(filter_radius / resample_sr / rms_mix_rate / protect /
        split_infer)不暴露给 LLM,走 worker 默认值;调音留给 GUI 页面。
        """
        from workers.rvc_worker import RVCInferWorker
        from utils import paths as _paths

        raw = args.get("input")
        if isinstance(raw, str):
            files = [raw.strip()] if raw.strip() else []
        elif isinstance(raw, list):
            files = [p.strip() for p in raw
                     if isinstance(p, str) and p.strip()]
        else:
            files = []

        if not files:
            self._on_tool_done(
                "rvc_convert", False,
                "缺少 input 参数(本地音频绝对路径,可单字符串或字符串数组)")
            return
        for f in files:
            if not os.path.isfile(f):
                self._on_tool_done(
                    "rvc_convert", False, f"输入文件不存在: {f}")
                return

        def s(name):
            v = args.get(name)
            return v.strip() if isinstance(v, str) else ""

        model_path = s("model_path")
        if not model_path:
            self._on_tool_done(
                "rvc_convert", False,
                "缺少 model_path(RVC .pth 权重绝对路径);"
                "如果用户给的是模型包目录,先用 list_dir 找出 .pth 后再调用")
            return
        if not os.path.isfile(model_path):
            self._on_tool_done(
                "rvc_convert", False, f"模型文件不存在: {model_path}")
            return

        index_path = s("index_path")
        if index_path and not os.path.isfile(index_path):
            # worker 自身就有警告 + 忽略的逻辑,这里直接清空让它兜底
            index_path = ""

        def pick(name, default):
            v = args.get(name)
            if v is None or (isinstance(v, str) and not v.strip()):
                return default
            return v.strip() if isinstance(v, str) else v

        # 数值类强制转,防 LLM 发字符串 "0" / "0.75"
        try:
            transpose = int(pick("transpose", 0))
        except (TypeError, ValueError):
            transpose = 0
        try:
            index_rate = float(pick("index_rate", 0.75))
        except (TypeError, ValueError):
            index_rate = 0.75
        index_rate = max(0.0, min(1.0, index_rate))

        params = {
            "input":      files if len(files) > 1 else files[0],
            "output":     _paths.output_dir("rvc"),
            "model_path": model_path,
            "index_path": index_path,
            "device":     pick("device", "cuda:0"),
            "f0_method":  pick("f0_method", "rmvpe"),
            "transpose":  transpose,
            "index_rate": index_rate,
            "format":     pick("format", "wav"),
        }

        worker = RVCInferWorker(params)
        self._tool_worker = worker
        worker.progress.connect(self._on_tool_progress)
        worker.finished.connect(
            lambda out_dir: self._on_tool_done(
                "rvc_convert", True, out_dir))
        worker.error.connect(
            lambda msg: self._on_tool_done(
                "rvc_convert", False, msg))
        worker.start()

    def _tool_gptsovits_tts(self, args: dict):
        """启动 GPTSoVITSInferWorker 做一次 TTS 合成。

        本工具完全自闭环,只读 LLM 这次调用的 args,不读其它页面的状态。
        模型 / 参考音频路径必须由用户在对话里提供;缺哪条就回一条人类可读
        的错误,让 LLM 转头去问用户。output 路径强制由本工具生成,避免 LLM
        指到奇怪位置。
        """
        from workers.gptsovits_worker import GPTSoVITSInferWorker
        from utils import paths as _paths

        def pick(name, default):
            v = args.get(name)
            if v is None or (isinstance(v, str) and not v.strip()):
                return default
            return v.strip() if isinstance(v, str) else v

        def s(name):
            v = args.get(name)
            return v.strip() if isinstance(v, str) else ""

        target_text = s("target_text")
        gpt_model = s("gpt_model")
        sovits_model = s("sovits_model")
        ref_audio = s("ref_audio")
        ref_text = s("ref_text")

        missing = []
        if not target_text:
            missing.append("target_text(要合成的文本)")
        if not gpt_model:
            missing.append("gpt_model(GPT 权重 .ckpt 绝对路径)")
        if not sovits_model:
            missing.append("sovits_model(SoVITS 权重 .pth 绝对路径)")
        if not ref_audio:
            missing.append("ref_audio(3~10s 参考 wav 绝对路径)")
        if not ref_text:
            missing.append("ref_text(参考音频对应的转写文本)")
        if missing:
            self._on_tool_done(
                "gptsovits_tts", False,
                "缺少必填参数,请向用户索取后再调用:"
                + "; ".join(missing),
            )
            return

        for label, p in (("GPT 模型", gpt_model),
                         ("SoVITS 模型", sovits_model),
                         ("参考音频", ref_audio)):
            if not os.path.isfile(p):
                self._on_tool_done(
                    "gptsovits_tts", False, f"{label}不存在: {p}")
                return

        out_dir = _paths.output_dir("gptsovits")
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = os.path.join(out_dir, f"gptsovits_{ts}.wav")

        params = {
            "gpt_model":       gpt_model,
            "sovits_model":    sovits_model,
            "ref_audio":       ref_audio,
            "aux_ref_audios":  [],
            "ref_text":        ref_text,
            "ref_language":    pick("ref_language", "中文"),
            "target_text":     target_text,
            "target_language": pick("target_language", "中文"),
            "output":          output_path,
            "how_to_cut":      pick("how_to_cut", "凑四句一切"),
            "top_k":           pick("top_k", 15),
            "top_p":           pick("top_p", 1.0),
            "temperature":     pick("temperature", 1.0),
            "speed":           pick("speed", 1.0),
            "device":          pick("device", "cuda:0"),
        }

        worker = GPTSoVITSInferWorker(params)
        self._tool_worker = worker
        worker.progress.connect(self._on_tool_progress)
        worker.finished.connect(
            lambda out: self._on_tool_done("gptsovits_tts", True, out))
        worker.error.connect(
            lambda msg: self._on_tool_done("gptsovits_tts", False, msg))
        worker.start()

    def _tool_realesrgan_upscale(self, args: dict):
        """启动 RealESRGANWorker / BatchRealESRGANWorker 做图像超分。

        - input 是单字符串(文件或目录) -> RealESRGANWorker 直接吃,worker 内部
          自己处理目录批量;
        - input 是数组 -> BatchRealESRGANWorker 逐文件起子进程跑(ncnn-vulkan
          单次只处理一个,这与现有 InferencePage 的批量路径一致)。
        其它参数(model/scale/tile/gpu_id/fmt/tta)均直接透传到 worker 默认实现。
        """
        from workers.realesrgan_worker import (
            RealESRGANWorker, BatchRealESRGANWorker, DEFAULT_EXE,
        )
        from utils import paths as _paths

        raw = args.get("input")
        if isinstance(raw, str):
            files = [raw.strip()] if raw.strip() else []
        elif isinstance(raw, list):
            files = [p.strip() for p in raw
                     if isinstance(p, str) and p.strip()]
        else:
            files = []

        if not files:
            self._on_tool_done(
                "realesrgan_upscale", False,
                "缺少 input 参数(本地图像/目录绝对路径,可单字符串或字符串数组)")
            return
        for p in files:
            if not os.path.exists(p):
                self._on_tool_done(
                    "realesrgan_upscale", False, f"输入不存在: {p}")
                return

        if not os.path.isfile(DEFAULT_EXE):
            self._on_tool_done(
                "realesrgan_upscale", False,
                f"未找到 realesrgan-ncnn-vulkan.exe:\n{DEFAULT_EXE}\n"
                "请确认 resource/realesrgan-ncnn-vulkan/ 目录完整")
            return

        def pick(name, default):
            v = args.get(name)
            if v is None or (isinstance(v, str) and not v.strip()):
                return default
            return v.strip() if isinstance(v, str) else v

        # 数值类强制转,防 LLM 发字符串 "4" / "512"
        try:
            scale = int(pick("scale", 4))
        except (TypeError, ValueError):
            scale = 4
        if scale not in (2, 3, 4):
            scale = 4
        try:
            tile = int(pick("tile", 0))
        except (TypeError, ValueError):
            tile = 0
        tile = max(0, tile)

        fmt = (pick("fmt", "png") or "png").lower()
        if fmt not in ("png", "jpg", "webp"):
            fmt = "png"

        output_dir = _paths.output_dir("realesrgan")
        base_params = {
            "exe_path":   DEFAULT_EXE,
            "output_dir": output_dir,
            # 默认走扫盘出来的可用模型;LLM 显式给的也会被原样透传,
            # 找不到对应 .param 时 exe 会自己抱怨,被 _capture_log 抓走
            "model":      pick("model", _REALESRGAN_DEFAULT_MODEL),
            "scale":      scale,
            "tile":       tile,
            "gpu_id":     str(pick("gpu_id", "auto")),
            "fmt":        fmt,
            "tta":        bool(args.get("tta", False)),
        }

        # ── 日志捕获 ──────────────────────────────────────────────────────
        # ncnn-vulkan 的 stderr 全部走 worker.output(HTML 串)。原来没接,
        # 失败时只剩 "推理失败,返回码 N" 这种没用的消息。这里:
        #   1) 实时把每行剥成纯文本打到 stderr,方便用户从终端看;
        #   2) 留一份 tail 缓冲,error 触发时把最后 ~30 行追加到错误消息里,
        #      用户在工具卡片上就能看到具体崩在哪。
        log_tail: list[str] = []
        _HTML_TAG_RE = re.compile(r"<[^>]+>")

        def _capture_log(html_line: str):
            plain = _HTML_TAG_RE.sub("", html_line or "").strip()
            if not plain:
                return
            log_tail.append(plain)
            if len(log_tail) > 120:
                del log_tail[: len(log_tail) - 120]
            try:
                sys.stderr.write(f"[realesrgan] {plain}\n")
                sys.stderr.flush()
            except Exception:
                pass

        def _wrap_error(msg: str):
            if log_tail:
                tail = "\n".join(log_tail[-30:])
                full = (f"{msg}\n\n"
                        f"--- worker 最后 {min(30, len(log_tail))} 行输出 ---\n"
                        f"{tail}")
            else:
                full = msg
            self._on_tool_done("realesrgan_upscale", False, full)

        if len(files) == 1:
            worker = RealESRGANWorker({**base_params, "input": files[0]})
            self._tool_worker = worker
            worker.progress.connect(self._on_tool_progress)
            worker.output.connect(_capture_log)
            # RealESRGANWorker.finished 是 pyqtSignal(str, float) ——
            # 第一个参数就是产物路径(文件输入->单张图;目录输入->输出目录)
            worker.finished.connect(
                lambda out_path, _elapsed: self._on_tool_done(
                    "realesrgan_upscale", True, out_path))
            worker.error.connect(_wrap_error)
            worker.start()
        else:
            worker = BatchRealESRGANWorker(files, base_params)
            self._tool_worker = worker
            worker.progress.connect(self._on_tool_progress)
            # BatchRealESRGANWorker 用 log_line 而不是 output;名字不同信号一样
            worker.log_line.connect(_capture_log)
            # BatchRealESRGANWorker.finished 是 pyqtSignal(int, float)
            # (成功数, 耗时),拿不到路径 —— 用我们传给 worker 的 output_dir
            worker.finished.connect(
                lambda _count, _elapsed: self._on_tool_done(
                    "realesrgan_upscale", True, output_dir))
            worker.error.connect(_wrap_error)
            worker.start()

    def _tool_yolo_detect(self, args: dict):
        """启动 YoloWorker 做图像目标检测。

        YoloWorker.finished 发的是 (success_count, elapsed) —— 拿不到产物路径,
        所以这里把 out_dir 提前算好作为 result 喂给 _on_tool_done,_collect_media
        扫这个目录捞标注图。每次任务用 outputs/yolo/llm_<时间戳>/ 子目录,
        避免和 UI 页面 / 历史结果搅在一起。
        """
        import time as _time
        import glob as _glob
        from workers.yolo_worker import YoloWorker, DEFAULT_WEIGHTS_DIR
        from utils import paths as _paths

        # ── 输入展开:支持单字符串 / 数组 / 目录 ─────────────────────────────
        _IMG_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff")
        raw = args.get("input")
        if isinstance(raw, str):
            raw = [raw.strip()] if raw.strip() else []
        elif isinstance(raw, list):
            raw = [p.strip() for p in raw if isinstance(p, str) and p.strip()]
        else:
            raw = []

        if not raw:
            self._on_tool_done(
                "yolo_detect", False,
                "缺少 input 参数(本地图像/目录绝对路径,可单字符串或字符串数组)")
            return

        files: list[str] = []
        for p in raw:
            if not os.path.exists(p):
                self._on_tool_done(
                    "yolo_detect", False, f"输入不存在: {p}")
                return
            if os.path.isdir(p):
                for ext in _IMG_EXTS:
                    files.extend(sorted(_glob.glob(os.path.join(p, f"*{ext}"))))
                    files.extend(sorted(_glob.glob(os.path.join(p, f"*{ext.upper()}"))))
            else:
                files.append(p)
        # 去重保序
        seen = set()
        files = [f for f in files if not (f in seen or seen.add(f))]
        if not files:
            self._on_tool_done(
                "yolo_detect", False,
                "未在 input 里找到任何图像文件(支持 png/jpg/jpeg/webp/bmp/tif)")
            return

        def pick(name, default):
            v = args.get(name)
            if v is None or (isinstance(v, str) and not v.strip()):
                return default
            return v.strip() if isinstance(v, str) else v

        # 数值字段强制转,防 LLM 发字符串
        try:
            conf = float(pick("conf", 0.25))
        except (TypeError, ValueError):
            conf = 0.25
        try:
            iou = float(pick("iou", 0.45))
        except (TypeError, ValueError):
            iou = 0.45
        conf = min(1.0, max(0.0, conf))
        iou = min(1.0, max(0.0, iou))

        model_name = pick("model", "yolov8m.pt")
        if not model_name.endswith(".pt"):
            model_name += ".pt"
        # 权重绝对路径:首次跑 ultralytics 会自己下载到 weights_dir
        weights = os.path.join(DEFAULT_WEIGHTS_DIR, model_name)

        # 输出子目录:llm_<时间戳>,避免和 GUI 页面共用 outputs/yolo/ 顶层混杂
        out_dir = _paths.output_dir("yolo", f"llm_{_time.strftime('%Y%m%d_%H%M%S')}")

        classes = args.get("classes")
        if isinstance(classes, list):
            classes = [int(c) for c in classes if isinstance(c, (int, float))]
            if not classes:
                classes = None
        else:
            classes = None

        save_mode = pick("save_mode", "图片+TXT(YOLO)")
        if save_mode not in ("图片+TXT(YOLO)", "图片+JSON(COCO)", "仅图片", "不保存"):
            save_mode = "图片+TXT(YOLO)"

        params = {
            "weights":     weights,
            "weights_dir": DEFAULT_WEIGHTS_DIR,
            "imgsz":       640,
            "conf":        conf,
            "iou":         iou,
            "max_det":     300,
            "device":      pick("device", "auto"),
            "fp16":        True,
            "tta":         False,
            "agnostic":    False,
            "classes":     classes,
            "out_dir":     out_dir,
            "save_mode":   save_mode,
            "draw_boxes":  True,
            "draw_label":  True,
            "line_w":      2,
        }

        worker = YoloWorker(files, params)
        self._tool_worker = worker
        worker.progress.connect(self._on_tool_progress)
        # yolo 的 finished 签名是 (int success, float elapsed) —— 拿不到路径,
        # 用提前算好的 out_dir,_collect_media 扫目录捞标注图
        worker.finished.connect(
            lambda count, elapsed: self._on_tool_done(
                "yolo_detect", True,
                f"{out_dir}\n({count}/{len(files)} 张完成,耗时 {elapsed:.1f}s)"))
        worker.error.connect(
            lambda msg: self._on_tool_done("yolo_detect", False, msg))
        worker.start()

    def _tool_musicgen_compose(self, args: dict):
        self._start_audiocraft(args, "musicgen", "musicgen_compose")

    def _tool_audiogen_create(self, args: dict):
        self._start_audiocraft(args, "audiogen", "audiogen_create")

    def _start_audiocraft(self, args: dict, task: str, tool_name: str):
        """MusicGen / AudioGen 共用入口。

        AudiocraftWorker.finished 发的是输出目录绝对路径,_collect_media 直接扫
        目录就能拿到 wav/mp3。每次任务用 outputs/audiocraft/llm_<时间戳>/ 子目录,
        避免和 GUI 历史结果搅在一起。
        """
        import time as _time
        from workers.audiocraft_worker import AudiocraftWorker
        from utils import paths as _paths

        # ── prompts:单字符串 / 数组,兼容 LLM 误传 prompt 单数键 ──────────
        raw = args.get("prompts")
        if raw is None:
            raw = args.get("prompt")
        if isinstance(raw, str):
            prompts = [raw.strip()] if raw.strip() else []
        elif isinstance(raw, list):
            prompts = [str(p).strip() for p in raw if str(p).strip()]
        else:
            prompts = []

        if not prompts:
            self._on_tool_done(
                tool_name, False,
                "缺少 prompts(音乐/音效描述,英文为主,单字符串或字符串数组都行)")
            return

        def pick(name, default):
            v = args.get(name)
            if v is None or (isinstance(v, str) and not v.strip()):
                return default
            return v.strip() if isinstance(v, str) else v

        # ── 数值字段强转,防 LLM 传字符串 ─────────────────────────────────
        try:
            duration = float(pick("duration", 10.0 if task == "musicgen" else 8.0))
        except (TypeError, ValueError):
            duration = 10.0 if task == "musicgen" else 8.0
        duration = max(1.0, min(30.0, duration))

        try:
            temperature = float(pick("temperature", 1.0))
        except (TypeError, ValueError):
            temperature = 1.0
        try:
            top_k = int(pick("top_k", 250))
        except (TypeError, ValueError):
            top_k = 250
        try:
            top_p = float(pick("top_p", 0.0))
        except (TypeError, ValueError):
            top_p = 0.0
        try:
            cfg_coef = float(pick("cfg_coef", 3.0))
        except (TypeError, ValueError):
            cfg_coef = 3.0

        default_model = "small" if task == "musicgen" else "medium"
        model = pick("model", default_model)
        device = pick("device", "cuda")
        output_format = (pick("output_format", "wav") or "wav").lower()
        if output_format not in ("wav", "mp3"):
            output_format = "wav"

        melody = pick("melody", "") or ""
        if melody:
            if task != "musicgen":
                melody = ""  # audiogen 不支持 melody,静默忽略
            elif not os.path.isfile(melody):
                self._on_tool_done(
                    tool_name, False, f"melody 文件不存在: {melody}")
                return

        out_dir = _paths.output_dir(
            "audiocraft", f"llm_{_time.strftime('%Y%m%d_%H%M%S')}")

        params = {
            "task":          task,
            "model":         model,
            "device":        device,
            "prompts":       prompts,
            "melody":        melody,
            "output":        out_dir,
            "output_format": output_format,
            "duration":      duration,
            "top_k":         top_k,
            "top_p":         top_p,
            "temperature":   temperature,
            "cfg_coef":      cfg_coef,
        }

        worker = AudiocraftWorker(params)
        self._tool_worker = worker
        worker.progress.connect(self._on_tool_progress)
        # finished 发输出目录绝对路径
        worker.finished.connect(
            lambda result_dir: self._on_tool_done(tool_name, True, result_dir))
        worker.error.connect(
            lambda msg: self._on_tool_done(tool_name, False, msg))
        worker.start()

    # 素材抓取:三个工具共享一个 MaterialFetchWorker,只是 op 不同 -----------------
    def _materials_out_dir(self):
        """与 subpage_materials 一致,使用 outputs/node/materials/ 子目录,
        避免在 utils/paths.py 的 _OUTPUT_TOOL_DIRS 里新增 key。"""
        from utils import paths as _paths
        return _paths.output_dir("node", "materials")

    def _start_material_worker(self, tool_name: str, op: str, payload: dict):
        from workers.material_fetch_worker import MaterialFetchWorker
        worker = MaterialFetchWorker(op, payload)
        self._tool_worker = worker
        worker.progress.connect(self._on_tool_progress)
        worker.finished.connect(
            lambda result: self._on_tool_done(tool_name, True, result))
        worker.error.connect(
            lambda msg: self._on_tool_done(tool_name, False, msg))
        worker.start()

    def _tool_search_song(self, args: dict):
        kw = args.get("keyword")
        if not isinstance(kw, str) or not kw.strip():
            self._on_tool_done(
                "search_song", False, "缺少 keyword(搜索关键词)")
            return
        source = args.get("source") or "netease"
        if source not in ("netease", "bilibili"):
            self._on_tool_done(
                "search_song", False,
                f"未知 source: {source!r},仅支持 netease / bilibili")
            return
        try:
            limit = int(args.get("limit") or 10)
        except (TypeError, ValueError):
            limit = 10
        payload = {
            "keyword": kw.strip(),
            "source": source,
            "limit": limit,
            "drop_instrumental": bool(args.get("drop_instrumental", True)),
        }
        self._start_material_worker("search_song", "search", payload)

    def _tool_download_song(self, args: dict):
        source = args.get("source")
        rid = args.get("id")
        title = args.get("title")
        if source not in ("netease", "bilibili"):
            self._on_tool_done(
                "download_song", False,
                f"未知 source: {source!r},仅支持 netease / bilibili")
            return
        if rid is None or (isinstance(rid, str) and not rid.strip()):
            self._on_tool_done(
                "download_song", False,
                "缺少 id(netease 用 song_id,bilibili 用 bvid;"
                "都从 search_song 的结果里取)")
            return
        if title is not None and not isinstance(title, str):
            title = str(title)
        payload = {
            "source": source,
            "id": rid,
            "title": (title or "").strip() or None,
            "out_dir": self._materials_out_dir(),
        }
        self._start_material_worker("download_song", "download", payload)

    def _tool_fetch_song(self, args: dict):
        kw = args.get("keyword")
        if not isinstance(kw, str) or not kw.strip():
            self._on_tool_done(
                "fetch_song", False, "缺少 keyword(搜索关键词)")
            return
        source = args.get("source") or "netease"
        if source not in ("netease", "bilibili"):
            self._on_tool_done(
                "fetch_song", False,
                f"未知 source: {source!r},仅支持 netease / bilibili")
            return
        payload = {
            "keyword": kw.strip(),
            "source": source,
            "drop_instrumental": bool(args.get("drop_instrumental", True)),
            "out_dir": self._materials_out_dir(),
        }
        self._start_material_worker("fetch_song", "fetch_first", payload)

    def _on_error(self, msg: str):
        if self._streaming_bubble:
            self._streaming_bubble.set_streaming(False)
            self._streaming_bubble.set_text(f"调用失败：{msg}")
            self._streaming_bubble = None
        self._set_busy(False)
        InfoBar.error("请求失败", msg[:200], parent=self,
                      position=InfoBarPosition.TOP, duration=4000)
