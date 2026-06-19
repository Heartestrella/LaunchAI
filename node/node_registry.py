"""
node_registry.py
~~~~~~~~~~~~~~~~
节点注册表 —— 标准化接口，后续添加新节点只需在此注册。

每个节点定义包含：
  - category   : 大类（"基础节点" / "音频" / "图像/视频" / ...）
  - title      : 显示名
  - color      : 节点标题栏颜色（hex）
  - inputs     : list[PortDef]  输入端口
  - outputs    : list[PortDef]  输出端口

PortDef = {
    "name"   : str,         端口标识
    "label"  : str,         UI显示名
    "type"   : str,         数据类型 ("audio"|"image"|"video"|"text"|"file"|"any")
    "multi"  : bool,        是否允许多连接（默认 False）
}
"""

from dataclasses import dataclass, field
from typing import Any
import os


def _scan_realesrgan_models() -> list[str]:
    """扫描 resource/realesrgan-ncnn-vulkan/models/*.param,返回可用模型名(stem)。

    不同 ncnn-vulkan 发行版的模型命名差别很大（旧版 ``realesrgan-x4plus``、
    新版 ``realesrgan-plus-x4`` 等），写死的列表很容易跟用户实际安装对不上。
    所以这里在导入时扫一次,过滤掉 ``-wdn-`` 含 Clip 层的版本(ncnn-vulkan 不兼容)。
    扫不到 → 返回 [] 让上层退回硬编码。
    """
    candidates = [
        os.path.join(os.getcwd(), "resource",
                     "realesrgan-ncnn-vulkan", "models"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     "..", "resource", "realesrgan-ncnn-vulkan", "models"),
    ]
    for d in candidates:
        if not os.path.isdir(d):
            continue
        try:
            stems = []
            for fn in sorted(os.listdir(d)):
                if not fn.lower().endswith(".param"):
                    continue
                stem = os.path.splitext(fn)[0]
                if "wdn" in stem.lower():
                    continue
                stems.append(stem)
            if stems:
                return stems
        except OSError:
            continue
    return []


# ── 端口类型颜色映射 ─────────────────────────────────────────────────
PORT_COLORS = {
    "audio":   "#60CDFF",
    "image":   "#0DB37E",
    "video":   "#9B59B6",
    "text":    "#F7B731",
    "file":    "#888888",
    "any":     "#AAAAAA",
    "number":  "#FC5C65",
    "bool":    "#0078D4",
}

# ── 节点大类颜色 ──────────────────────────────────────────────────────
CATEGORY_COLORS = {
    "基础节点":   "#555555",
    "音频":      "#0078D4",
    "图像/视频": "#0DB37E",
}


@dataclass
class PortDef:
    name:  str
    label: str
    type:  str = "any"
    multi: bool = False


@dataclass
class NodeDef:
    id:       str              # 唯一标识，用于实例化
    title:    str
    category: str
    inputs:   list[PortDef] = field(default_factory=list)
    outputs:  list[PortDef] = field(default_factory=list)
    # 可选：节点特有的参数字段（供 PropertyPanel 渲染）
    params:   dict[str, Any] = field(default_factory=dict)
    # 可选：参数枚举选项 {param_key: [可选值, ...]}
    # PropertyPanel 见到则渲染为下拉框；首项当作默认。
    param_choices: dict[str, list] = field(default_factory=dict)

    @property
    def color(self) -> str:
        return CATEGORY_COLORS.get(self.category, "#555555")


# ══════════════════════════════════════════════════════════════════════
#  注册表（全局单例）
# ══════════════════════════════════════════════════════════════════════

class NodeRegistry:
    def __init__(self):
        self._defs: dict[str, NodeDef] = {}

    def register(self, node_def: NodeDef):
        self._defs[node_def.id] = node_def

    def get(self, node_id: str) -> NodeDef | None:
        return self._defs.get(node_id)

    def all(self) -> list[NodeDef]:
        return list(self._defs.values())

    def by_category(self) -> dict[str, list[NodeDef]]:
        result: dict[str, list[NodeDef]] = {}
        for d in self._defs.values():
            result.setdefault(d.category, []).append(d)
        return result


REGISTRY = NodeRegistry()


# ══════════════════════════════════════════════════════════════════════
#  内置节点注册
# ══════════════════════════════════════════════════════════════════════

def _reg(node_def: NodeDef):
    REGISTRY.register(node_def)


# ── 基础节点 ──────────────────────────────────────────────────────────

_reg(NodeDef(
    id="file_input",
    title="文件输入",
    category="基础节点",
    outputs=[
        PortDef("file_out", "文件", "file"),
    ],
    params={"path": ""},
))

_reg(NodeDef(
    id="text_input",
    title="文本输入",
    category="基础节点",
    outputs=[
        PortDef("text_out", "文本", "text"),
    ],
    # text 键名在 PropertyPanel._MULTILINE_TEXT_PARAM_KEYS 里
    # 会自动渲染为多行 PlainTextEdit
    params={"text": ""},
))

_reg(NodeDef(
    id="file_output",
    title="文件输出",
    category="基础节点",
    inputs=[
        PortDef("file_in", "文件", "file"),
    ],
    params={"directory": "./output", "filename": ""},
))

_reg(NodeDef(
    id="format_convert",
    title="格式转换",
    category="基础节点",
    inputs=[
        PortDef("file_in", "输入", "file"),
    ],
    outputs=[
        PortDef("file_out", "输出", "file"),
    ],
    params={"target_format": "wav"},
    param_choices={"target_format": ["wav", "mp3", "flac",
                                     "png", "jpg", "mp4"]},
))

_reg(NodeDef(
    id="batch_input",
    title="批量输入",
    category="基础节点",
    outputs=[
        PortDef("files_out", "文件列表", "file", multi=True),
    ],
    params={"directory": "", "glob": "*.*"},
))

_reg(NodeDef(
    id="preview",
    title="预览",
    category="基础节点",
    inputs=[
        PortDef("input", "任意输入", "any"),
    ],
    params={"path": ""},
))

_reg(NodeDef(
    id="text_note",
    title="文本注释",
    category="基础节点",
    params={"text": "注释内容…"},
))

# ── 音频节点 ──────────────────────────────────────────────────────────

_reg(NodeDef(
    id="demucs",
    title="Demucs 音频分离",
    category="音频",
    inputs=[
        PortDef("audio_in", "音频输入", "audio"),
    ],
    outputs=[
        PortDef("vocals",  "人声",   "audio"),
        PortDef("drums",   "鼓",     "audio"),
        PortDef("bass",    "贝斯",   "audio"),
        PortDef("other",   "其他",   "audio"),
        PortDef("mix",     "混音",   "audio"),
    ],
    params={
        "model":   "htdemucs",
        "device":  "cpu",          # 默认安全值；GPU 由属性面板下拉显式选择
        "shifts":  1,
        "overlap": 0.25,
        "format":  "wav",
    },
    param_choices={
        "model":  ["htdemucs", "htdemucs_ft", "mdx",
                   "mdx_extra", "mdx_q", "mdx_extra_q"],
        "format": ["wav", "mp3", "flac"],
    },
))

_reg(NodeDef(
    id="whisper",
    title="Whisper 语音识别",
    category="音频",
    inputs=[
        PortDef("audio_in", "音频输入", "audio"),
    ],
    outputs=[
        PortDef("transcript", "转录文本", "text"),
        PortDef("srt",        "字幕文件", "file"),
        PortDef("json",       "时间戳JSON", "text"),
    ],
    params={
        "model":      "large-v3",
        "language":   "auto",
        "device":     "cpu",
        "task":       "transcribe",
    },
    param_choices={
        "model": ["tiny", "base", "small", "medium",
                  "large", "large-v2", "large-v3"],
        "task":  ["transcribe", "translate"],
    },
))

_reg(NodeDef(
    id="rvc",
    title="RVC 变声/翻唱",
    category="音频",
    inputs=[
        PortDef("audio_in", "音频输入", "audio"),
    ],
    outputs=[
        PortDef("audio_out", "变声输出", "audio"),
    ],
    params={
        "model_path":    "",
        "index_path":    "",
        "device":        "cpu",
        "f0_method":     "rmvpe+",
        "transpose":     0,
        "index_rate":    0.75,
        "filter_radius": 3,
        "resample_sr":   0,
        "rms_mix_rate":  0.25,
        "protect":       0.33,
        "split_infer":   False,
        "format":        "wav",
    },
    param_choices={
        "f0_method": ["rmvpe", "rmvpe+", "crepe", "pm", "harvest"],
        "format":    ["wav", "mp3", "flac"],
    },
))

_reg(NodeDef(
    id="sovits_list_input",
    title="GPT-SoVITS .list 数据源",
    category="音频",
    # 解析 GPT-SoVITS .list 数据集 取出指定行的 (音频, 文本)
    # 用于一次性给 gptsovits 节点喂参考音频 + 参考文本
    # list_path 走 _FILE_PARAM_KEYS 自动获得文件浏览按钮
    # audio_dir 走 _DIR_PARAM_KEYS 自动获得目录浏览按钮
    # entry_index = -1 时自动取首个可用条目 与 subpage 的"全选可用"语义对齐
    outputs=[
        PortDef("audio_out", "参考音频", "audio"),
        PortDef("text_out",  "参考文本", "text"),
    ],
    params={
        "list_path":   "",   # .list 文件路径 浏览按钮自动出现
        "audio_dir":   "",   # 音频根目录 跨机器场景按 basename 重映射 留空则按 .list 原路径
        "entry_index": -1,   # -1 自动选首个可用 >=0 取指定条 SpinBox 渲染
    },
))

_reg(NodeDef(
    id="gptsovits",
    title="GPT-SoVITS 语音合成",
    category="音频",
    # 所有可被上游驱动的输入都做成端口 同名 params 作为没接线时的兜底
    # 上游通常用 file_input (file → file/audio bridge) 和 text_input (text)
    inputs=[
        PortDef("gpt_model",    "GPT 模型 (.ckpt)",        "file"),
        PortDef("sovits_model", "SoVITS 模型 (.pth)",       "file"),
        PortDef("ref_audio",    "参考音频 (3~10s)",         "audio"),
        PortDef("ref_text",     "参考文本（与参考音频一致）", "text"),
        PortDef("target_text",  "目标文本",                "text"),
    ],
    outputs=[
        PortDef("audio_out", "合成音频", "audio"),
    ],
    params={
        # 兜底：端口未接时从这里取
        "gpt_model":       "",
        "sovits_model":    "",
        "ref_text":        "",
        "target_text":     "",
        # 真正只能从这里设的：语种 / 切分 / 采样 / 设备 / 格式
        "ref_language":    "中文",
        "target_language": "中文",
        "how_to_cut":      "不切",
        "top_k":           15,
        "top_p":           1.0,
        "temperature":     1.0,
        "speed":           1.0,
        "device":          "cpu",   # 默认安全值 GPU 由属性面板下拉显式选择
        "format":          "wav",
    },
    param_choices={
        # 字面值必须与 GPT-SoVITS inference_webui.dict_language_v2 一致
        # 否则推理时 dict_language[text_language] 会 KeyError
        "ref_language":    ["中文", "英文", "日文", "粤语", "韩文",
                            "中英混合", "日英混合", "粤英混合", "韩英混合",
                            "多语种混合", "多语种混合(粤语)"],
        "target_language": ["中文", "英文", "日文", "粤语", "韩文",
                            "中英混合", "日英混合", "粤英混合", "韩英混合",
                            "多语种混合", "多语种混合(粤语)"],
        # 与 inference_webui.how_to_cut 字面值一致 否则会静默不切
        "how_to_cut":      ["不切", "凑四句一切", "凑50字一切",
                            "按中文句号。切", "按英文句号.切",
                            "按标点符号切"],
        "format":          ["wav", "flac"],
    },
))

_reg(NodeDef(
    id="audio_merge",
    title="音频合并",
    category="音频",
    inputs=[
        PortDef("audio_a", "音频 A", "audio"),
        PortDef("audio_b", "音频 B", "audio"),
    ],
    outputs=[
        PortDef("merged", "合并输出", "audio"),
    ],
    params={"mode": "mix", "volume_a": 1.0, "volume_b": 1.0},
))

# _reg(NodeDef(
#     id="audio_trim",
#     title="音频裁剪",
#     category="音频",
#     inputs=[
#         PortDef("audio_in", "音频输入", "audio"),
#     ],
#     outputs=[
#         PortDef("audio_out", "音频输出", "audio"),
#     ],
#     params={"start_sec": 0.0, "end_sec": -1.0},
# ))

# ── 图像/视频节点 ─────────────────────────────────────────────────────

# models 列表与默认模型 —— 优先用扫描到的真实文件,扫不到才退回历史命名
_REALESRGAN_MODELS = _scan_realesrgan_models() or [
    "realesrgan-x4plus", "realesrgan-x4plus-anime",
    "realesr-animevideov3",
]
# 默认模型：优先选包含 "plus" 且不含 "anime" 的通用 4x 模型(同时兼容
# 新命名 realesrgan-plus-x4 与旧命名 realesrgan-x4plus),否则取首个
_REALESRGAN_DEFAULT_MODEL = next(
    (m for m in _REALESRGAN_MODELS
     if "plus" in m.lower() and "anime" not in m.lower()),
    _REALESRGAN_MODELS[0],
)

_reg(NodeDef(
    id="realesrgan",
    title="Real-ESRGAN 超分",
    category="图像/视频",
    inputs=[
        PortDef("image_in", "图像输入", "image"),
    ],
    outputs=[
        PortDef("image_out", "超分图像", "image"),
    ],
    params={
        "model":   _REALESRGAN_DEFAULT_MODEL,
        "scale":   4,
        "tile":    512,
        "gpu_id":  "auto",
        "fmt":     "png",
    },
    param_choices={
        "model": _REALESRGAN_MODELS,
        "scale": [2, 3, 4],
        "fmt":   ["png", "jpg", "webp"],
    },
))

_reg(NodeDef(
    id="image_resize",
    title="图像缩放",
    category="图像/视频",
    inputs=[
        PortDef("image_in", "图像输入", "image"),
    ],
    outputs=[
        PortDef("image_out", "输出图像", "image"),
    ],
    params={"width": 1920, "height": 1080, "keep_ratio": True},
))

# _reg(NodeDef(
#     id="video_extract_frames",
#     title="视频提帧",
#     category="图像/视频",
#     inputs=[
#         PortDef("video_in", "视频输入", "video"),
#     ],
#     outputs=[
#         PortDef("frames", "帧序列", "image", multi=True),
#     ],
#     params={"fps": 1, "format": "png"},
# ))

# _reg(NodeDef(
#     id="frames_to_video",
#     title="帧合成视频",
#     category="图像/视频",
#     inputs=[
#         PortDef("frames",   "帧序列", "image", multi=True),
#         PortDef("audio_in", "音轨（可选）", "audio"),
#     ],
#     outputs=[
#         PortDef("video_out", "视频输出", "video"),
#     ],
#     params={"fps": 24, "codec": "h264", "crf": 18},
# ))
