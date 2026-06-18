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
        "model":   "realesrgan-x4plus",
        "scale":   4,
        "tile":    512,
        "gpu_id":  "auto",
        "fmt":     "png",
    },
    param_choices={
        "model": ["realesrgan-x4plus", "realesrgan-x4plus-anime",
                  "realesr-animevideov3", "RealESRGAN_x4plus",
                  "RealESRGAN_x4plus_anime_6B", "RealESRNet_x4plus"],
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
