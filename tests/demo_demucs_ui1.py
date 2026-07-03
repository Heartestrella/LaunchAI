# tests/demo_demucs_ui1.py
"""
Demucs 音频分离 · UI Demo · 风格 B (stems ledger)

重构后的设计要点:
  - 视觉中心是"stems ledger": 4 条水平音轨条 (V / D / B / O),
    同时承担 include 复选框、结果预览、运行时进度三重角色。
  - 深蓝黑底 (#0B0E13) + 琥珀 accent (#F0B429), 避开 fluent 默认蓝。
  - 参数区用等宽字体, 排成两行密集网格, 看起来像 nvidia-smi 而非
    admin 表单。参数名保留 demucs 原生英文小写。
  - 主按钮不撑满宽度; 右侧同高显示实时命令行摘要, 按下前就能看到
    等价参数。
  - 无 CardWidget, 全部 QSS 手绘; 一色描边 + 大间距节奏。

无真 worker: QTimer 伪造进度以演示 idle / running / done 三态。
可用 LAUNCHAI_REDUCED_MOTION=1 环境变量关闭波形闲时呼吸动画。
"""

from __future__ import annotations

import math
import os
import random
import sys
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import (
    QColor, QDragEnterEvent, QDropEvent,
    QFont, QFontDatabase, QPainter, QPen,
)
from PyQt6.QtWidgets import (
    QApplication, QFileDialog, QGridLayout, QHBoxLayout,
    QSizePolicy, QVBoxLayout, QWidget,
)
from qfluentwidgets import (
    BodyLabel, CaptionLabel, CheckBox, ComboBox,
    InfoBar, PrimaryPushButton, ProgressBar, Slider, SpinBox,
    Theme, TitleLabel, TransparentPushButton,
    FluentIcon as FIF, setTheme, setThemeColor,
)

# ---------------------------------------------------------------------------
# 设计 token
# ---------------------------------------------------------------------------
COLORS = {
    "bg":       "#0B0E13",
    "panel":    "#12161F",
    "hairline": "rgba(232,230,222,26)",
    "text":     "#E8E6DE",   # ≥ 13:1 on bg
    "muted":    "#8891A0",   # ≥ 5.8:1 on bg (AA)
    "accent":   "#F0B429",   # 琥珀 · Solo/Rec 家族
    "ok":       "#6BCB77",
    "warn":     "#F0B429",
    "run":      "#F0B429",
}
STEM_COLORS = {
    "vocals": "#F0B429",   # 人声 — 主角, 复用 accent
    "drums":  "#FF6B6B",
    "bass":   "#7C6BFF",
    "other":  "#6BCB77",
}
STEMS: list[tuple[str, str, str]] = [
    # (id, chinese-name, letter)
    ("vocals", "人声", "V"),
    ("drums",  "鼓",   "D"),
    ("bass",   "贝斯", "B"),
    ("other",  "其他", "O"),
]
MODEL_OPTIONS = ["htdemucs", "htdemucs_ft", "mdx_extra", "mdx"]
FORMAT_OPTIONS = ["wav", "flac", "mp3"]
AUDIO_EXTS = {".mp3", ".wav", ".flac", ".m4a", ".ogg"}

REDUCED_MOTION = os.environ.get("LAUNCHAI_REDUCED_MOTION") == "1"


def _mono_family() -> str:
    fams = set(QFontDatabase.families())
    for f in ("JetBrains Mono", "IBM Plex Mono", "Cascadia Mono",
              "Cascadia Code", "Consolas", "Menlo", "DejaVu Sans Mono"):
        if f in fams:
            return f
    return "monospace"


def _mono(pt: int = 11, bold: bool = False) -> QFont:
    f = QFont(_mono_family(), pt)
    f.setStyleHint(QFont.StyleHint.Monospace)
    f.setBold(bold)
    return f


# ---------------------------------------------------------------------------
# DropStrip — 扁的拖放条, 而不是大方框
# ---------------------------------------------------------------------------
class DropStrip(QWidget):
    fileSelected = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setAcceptDrops(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(64)
        self._file_path = ""

        lay = QHBoxLayout(self)
        lay.setContentsMargins(18, 0, 18, 0)
        lay.setSpacing(14)

        self._arrow = BodyLabel("↧", self)
        self._arrow.setStyleSheet(
            f"color: {COLORS['accent']}; font-size: 20px; font-weight: 600;")
        lay.addWidget(self._arrow)

        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(0)
        self._title = BodyLabel("拖入音频文件 · 或点击选择", self)
        self._title.setStyleSheet(f"color: {COLORS['text']};")
        self._hint = CaptionLabel("mp3 · wav · flac · m4a · ogg", self)
        self._hint.setStyleSheet(f"color: {COLORS['muted']};")
        text_col.addWidget(self._title)
        text_col.addWidget(self._hint)
        lay.addLayout(text_col, 1)

        self._size_lbl = BodyLabel("", self)
        self._size_lbl.setFont(_mono(10))
        self._size_lbl.setStyleSheet(f"color: {COLORS['muted']};")
        lay.addWidget(self._size_lbl, alignment=Qt.AlignmentFlag.AlignRight)

        self._refresh_style(hover=False)

    def _refresh_style(self, hover: bool):
        if self._file_path:
            border = f"1px solid {COLORS['accent']}"
            bg = "rgba(240,180,41,10)"
        elif hover:
            border = f"1px dashed {COLORS['accent']}"
            bg = "rgba(240,180,41,14)"
        else:
            border = "1px dashed rgba(232,230,222,46)"
            bg = "transparent"
        self.setStyleSheet(
            "DropStrip {"
            f"  border: {border};"
            f"  border-radius: 4px;"
            f"  background: {bg};"
            "}"
        )

    def enterEvent(self, e):
        if not self._file_path:
            self._refresh_style(hover=True)
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._refresh_style(hover=False)
        super().leaveEvent(e)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self.isEnabled():
            path, _ = QFileDialog.getOpenFileName(
                self, "选择音频文件", "",
                "音频文件 (*.mp3 *.wav *.flac *.m4a *.ogg)",
            )
            if path:
                self.set_file(path)
        super().mousePressEvent(e)

    def dragEnterEvent(self, e: QDragEnterEvent):
        if not self.isEnabled() or not e.mimeData().hasUrls():
            e.ignore(); return
        for url in e.mimeData().urls():
            if Path(url.toLocalFile()).suffix.lower() in AUDIO_EXTS:
                e.acceptProposedAction()
                self._refresh_style(hover=True)
                return
        e.ignore()

    def dragLeaveEvent(self, e):
        self._refresh_style(hover=False)
        super().dragLeaveEvent(e)

    def dropEvent(self, e: QDropEvent):
        for url in e.mimeData().urls():
            path = url.toLocalFile()
            if Path(path).suffix.lower() in AUDIO_EXTS:
                self.set_file(path)
                e.acceptProposedAction()
                return

    def set_file(self, path: str):
        self._file_path = path
        name = Path(path).name
        self._arrow.setText("●")
        self._arrow.setStyleSheet(
            f"color: {COLORS['accent']}; font-size: 14px; font-weight: 700;")
        self._title.setText(name if len(name) <= 60 else name[:57] + "…")
        self._title.setToolTip(path)
        self._hint.setText(os.path.dirname(path))
        try:
            kb = os.path.getsize(path) / 1024
            if kb < 1024:
                self._size_lbl.setText(f"{kb:6.1f} KB")
            else:
                self._size_lbl.setText(f"{kb/1024:6.1f} MB")
        except OSError:
            self._size_lbl.setText("")
        self._refresh_style(hover=False)
        self.fileSelected.emit(path)

    def file_path(self) -> str:
        return self._file_path


# ---------------------------------------------------------------------------
# WaveStrip — 微型 sparkline; 静态波形数据 + 可选进度覆盖
# ---------------------------------------------------------------------------
class WaveStrip(QWidget):
    """一条极窄的波形. 有 progress ∈ [0,1] 时左侧填充轨色."""

    def __init__(self, color_hex: str, seed: int, parent=None):
        super().__init__(parent)
        self._color = QColor(color_hex)
        self._muted = QColor(COLORS["muted"])
        self._progress = 0.0
        self._breath = 0.0
        self._enabled_track = True
        rng = random.Random(seed)
        n = 64
        # 生成一条与 stem 类型 vaguely 相关的波形
        base = [
            0.35 + 0.25 * math.sin(i * 0.9 + seed) + 0.4 * rng.random()
            for i in range(n)
        ]
        self._samples = [max(0.06, min(1.0, v)) for v in base]
        self.setMinimumHeight(28)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        if not REDUCED_MOTION:
            self._breath_timer = QTimer(self)
            self._breath_timer.timeout.connect(self._tick_breath)
            self._breath_timer.start(120)
        else:
            self._breath_timer = None

    def _tick_breath(self):
        # idle 时非常轻微的呼吸, 只在 progress==0 时生效
        if self._progress > 0 or not self.isVisible():
            return
        self._breath = (self._breath + 0.05) % (2 * math.pi)
        self.update()

    def set_progress(self, p: float):
        self._progress = max(0.0, min(1.0, p))
        self.update()

    def set_enabled_track(self, on: bool):
        self._enabled_track = on
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        w = self.width()
        h = self.height()
        n = len(self._samples)
        if n == 0 or w <= 0:
            return
        step = w / n
        mid = h / 2
        max_amp = h * 0.42

        pen_muted = QPen(QColor(136, 145, 160, 90 if self._enabled_track else 40))
        pen_muted.setWidthF(1.2)
        pen_hot = QPen(self._color)
        pen_hot.setWidthF(1.6)
        cutoff = self._progress * w
        breath_scale = 1.0
        if self._progress == 0 and self._enabled_track and not REDUCED_MOTION:
            breath_scale = 0.94 + 0.06 * math.sin(self._breath)

        for i, s in enumerate(self._samples):
            x = i * step + step / 2
            amp = s * max_amp * breath_scale
            if x < cutoff:
                p.setPen(pen_hot)
            else:
                p.setPen(pen_muted)
            p.drawLine(int(x), int(mid - amp), int(x), int(mid + amp))

        # 播放头
        if 0 < self._progress < 1.0:
            head = QPen(QColor(self._color))
            head.setWidthF(1.2)
            p.setPen(head)
            p.drawLine(int(cutoff), 2, int(cutoff), h - 2)


# ---------------------------------------------------------------------------
# StemRow — 单条音轨: [章] [名称] [波形] [复选框]
# ---------------------------------------------------------------------------
class StemRow(QWidget):
    toggled = pyqtSignal(str, bool)

    def __init__(self, stem_id: str, name: str, letter: str,
                 color_hex: str, parent=None):
        super().__init__(parent)
        self.stem_id = stem_id
        self._color = color_hex
        self.setFixedHeight(44)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(14)

        # 字母章
        self._chip = BodyLabel(letter, self)
        self._chip.setFixedSize(28, 28)
        self._chip.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._chip.setFont(_mono(11, bold=True))
        self._chip.setStyleSheet(
            f"color: {color_hex};"
            f" background: rgba(255,255,255,8);"
            f" border: 1px solid {color_hex};"
            f" border-radius: 4px;"
        )
        lay.addWidget(self._chip)

        # 名称 + 状态
        name_col = QVBoxLayout()
        name_col.setContentsMargins(0, 0, 0, 0)
        name_col.setSpacing(0)
        self._name = BodyLabel(name, self)
        self._name.setStyleSheet(f"color: {COLORS['text']};")
        self._state = CaptionLabel("待分离", self)
        self._state.setStyleSheet(f"color: {COLORS['muted']};")
        self._state.setFont(_mono(9))
        name_col.addWidget(self._name)
        name_col.addWidget(self._state)
        w_name = QWidget(self)
        w_name.setLayout(name_col)
        w_name.setFixedWidth(64)
        lay.addWidget(w_name)

        # 波形
        self._wave = WaveStrip(color_hex, seed=hash(stem_id) & 0xFFFF, parent=self)
        lay.addWidget(self._wave, 1)

        # include 复选框 + 右锚百分比
        self._pct = BodyLabel("", self)
        self._pct.setFont(_mono(10))
        self._pct.setStyleSheet(f"color: {color_hex};")
        self._pct.setFixedWidth(40)
        self._pct.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        lay.addWidget(self._pct)

        self._cb = CheckBox(self)
        self._cb.setChecked(True)
        self._cb.setToolTip(f"是否输出 {name} 音轨")
        self._cb.stateChanged.connect(self._on_toggle)
        lay.addWidget(self._cb)

    def _on_toggle(self, _state):
        included = self._cb.isChecked()
        self._wave.set_enabled_track(included)
        self._name.setStyleSheet(
            f"color: {COLORS['text' if included else 'muted']};")
        if not included:
            self._chip.setStyleSheet(
                f"color: {COLORS['muted']};"
                f" background: transparent;"
                f" border: 1px solid rgba(136,145,160,80);"
                f" border-radius: 4px;"
            )
        else:
            self._chip.setStyleSheet(
                f"color: {self._color};"
                f" background: rgba(255,255,255,8);"
                f" border: 1px solid {self._color};"
                f" border-radius: 4px;"
            )
        self.toggled.emit(self.stem_id, included)

    def set_progress(self, p: float):
        if not self._cb.isChecked():
            return
        self._wave.set_progress(p)
        if p <= 0:
            self._pct.setText("")
            self._state.setText("待分离")
        elif p < 1.0:
            self._pct.setText(f"{int(p*100):>2}%")
            self._state.setText("分离中")
        else:
            self._pct.setText("✓")
            self._state.setText("完成")

    def reset(self):
        self._wave.set_progress(0)
        self._pct.setText("")
        self._state.setText("待分离")

    def is_included(self) -> bool:
        return self._cb.isChecked()


# ---------------------------------------------------------------------------
# 主 UI
# ---------------------------------------------------------------------------
class DemucsUIDemoB(QWidget):
    separationRequested = pyqtSignal(dict)

    def __init__(self, device_options: dict | None = None, parent=None):
        super().__init__(parent)
        self._device_options = device_options or {"cpu": "cpu", "cuda:0": "cuda:0"}
        self._output_dir = str(Path.home() / "demucs_out")

        self._progress_timer = QTimer(self)
        self._progress_timer.timeout.connect(self._tick_progress)
        self._progress_val = 0

        self._setup_ui()
        self._connect_signals()
        self._sync_command_preview()

    # ---------- 布局 ----------
    def _setup_ui(self):
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(self._page_qss())

        root = QVBoxLayout(self)
        root.setContentsMargins(32, 22, 32, 22)
        root.setSpacing(0)

        # ---- 头 ----
        head = QHBoxLayout()
        head.setSpacing(12)
        title_col = QVBoxLayout()
        title_col.setContentsMargins(0, 0, 0, 0)
        title_col.setSpacing(2)
        title = TitleLabel("音频分离", self)
        title.setStyleSheet(f"color: {COLORS['text']};")
        subtitle = CaptionLabel(
            "Demucs · Hybrid Transformer 源分离 · 拆解人声 / 鼓 / 贝斯 / 其他", self)
        subtitle.setStyleSheet(f"color: {COLORS['muted']};")
        title_col.addWidget(title)
        title_col.addWidget(subtitle)
        head.addLayout(title_col)
        head.addStretch()

        self._status = BodyLabel("idle", self)
        self._status.setFont(_mono(10, bold=True))
        self._status.setStyleSheet(f"color: {COLORS['muted']}; letter-spacing: 1px;")
        head.addWidget(self._status, alignment=Qt.AlignmentFlag.AlignVCenter)
        root.addLayout(head)
        root.addSpacing(18)

        # ---- Drop strip ----
        self._drop = DropStrip(self)
        root.addWidget(self._drop)
        root.addSpacing(8)

        # ---- 输出行 ----
        out_row = QHBoxLayout()
        out_row.setContentsMargins(2, 0, 2, 0)
        out_row.setSpacing(10)
        out_lbl = CaptionLabel("output", self)
        out_lbl.setFont(_mono(10))
        out_lbl.setStyleSheet(f"color: {COLORS['muted']};")
        out_lbl.setFixedWidth(60)
        out_row.addWidget(out_lbl)
        self._dir_label = BodyLabel(self._output_dir, self)
        self._dir_label.setFont(_mono(10))
        self._dir_label.setStyleSheet(f"color: {COLORS['text']};")
        self._dir_label.setToolTip(self._output_dir)
        out_row.addWidget(self._dir_label, 1)
        self._dir_btn = TransparentPushButton(FIF.FOLDER, "浏览", self)
        out_row.addWidget(self._dir_btn)
        root.addLayout(out_row)
        root.addSpacing(24)

        # ---- Section: stems ----
        root.addLayout(self._section_header("stems", "输出音轨 · 4 层"))
        root.addSpacing(6)

        self._stem_rows: dict[str, StemRow] = {}
        stems_wrap = QWidget(self)
        stems_wrap.setObjectName("stemsBox")
        stems_lay = QVBoxLayout(stems_wrap)
        stems_lay.setContentsMargins(14, 6, 14, 6)
        stems_lay.setSpacing(0)
        for i, (sid, name, letter) in enumerate(STEMS):
            row = StemRow(sid, name, letter, STEM_COLORS[sid], parent=stems_wrap)
            row.toggled.connect(self._on_stem_toggled)
            self._stem_rows[sid] = row
            stems_lay.addWidget(row)
            if i < len(STEMS) - 1:
                sep = QWidget(stems_wrap)
                sep.setFixedHeight(1)
                sep.setStyleSheet(f"background: {COLORS['hairline']};")
                stems_lay.addWidget(sep)
        root.addWidget(stems_wrap)
        root.addSpacing(24)

        # ---- Section: params ----
        root.addLayout(self._section_header("params", "模型 / 设备 / 高级"))
        root.addSpacing(6)

        params = QGridLayout()
        params.setHorizontalSpacing(20)
        params.setVerticalSpacing(12)
        # 4 列: [k][v][k][v]
        params.setColumnStretch(0, 0)
        params.setColumnStretch(1, 1)
        params.setColumnStretch(2, 0)
        params.setColumnStretch(3, 1)

        self._model_cb = ComboBox()
        self._model_cb.addItems(MODEL_OPTIONS)
        self._model_cb.setCurrentText(MODEL_OPTIONS[0])
        self._device_cb = ComboBox()
        for name, index in self._device_options.items():
            self._device_cb.addItem(f"{name} · {index}")
        self._shifts_spin = SpinBox()
        self._shifts_spin.setRange(1, 8)
        self._shifts_spin.setValue(1)
        self._shifts_spin.setToolTip("大于 1 会显著变慢")
        self._seg_spin = SpinBox()
        self._seg_spin.setRange(1, 7)
        self._seg_spin.setValue(7)
        self._seg_spin.setToolTip("越大越吃显存")
        self._fmt_cb = ComboBox()
        self._fmt_cb.addItems(FORMAT_OPTIONS)

        ov_wrap = QWidget()
        ov = QHBoxLayout(ov_wrap)
        ov.setContentsMargins(0, 0, 0, 0)
        ov.setSpacing(8)
        self._overlap_sl = Slider(Qt.Orientation.Horizontal)
        self._overlap_sl.setRange(0, 50)
        self._overlap_sl.setValue(25)
        self._overlap_val = BodyLabel("0.25")
        self._overlap_val.setFont(_mono(10))
        self._overlap_val.setFixedWidth(36)
        self._overlap_val.setStyleSheet(f"color: {COLORS['accent']};")
        self._overlap_val.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        ov.addWidget(self._overlap_sl, 1)
        ov.addWidget(self._overlap_val)

        rows: list[tuple[str, QWidget]] = [
            ("model",   self._model_cb),
            ("device",  self._device_cb),
            ("shifts",  self._shifts_spin),
            ("segment", self._seg_spin),
            ("overlap", ov_wrap),
            ("format",  self._fmt_cb),
        ]
        for i, (k, w) in enumerate(rows):
            col = (i % 2) * 2
            r = i // 2
            k_lbl = BodyLabel(k, self)
            k_lbl.setFont(_mono(10))
            k_lbl.setStyleSheet(f"color: {COLORS['muted']};")
            k_lbl.setFixedWidth(72)
            k_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            params.addWidget(k_lbl, r, col)
            params.addWidget(w, r, col + 1)
        root.addLayout(params)

        root.addStretch(1)

        # ---- 底部: 进度 + 命令预览 + 按钮 ----
        rule = QWidget(self)
        rule.setFixedHeight(1)
        rule.setStyleSheet(f"background: {COLORS['hairline']};")
        root.addWidget(rule)
        root.addSpacing(14)

        self._progress = ProgressBar(self)
        self._progress.setValue(0)
        self._progress.setFixedHeight(3)
        root.addWidget(self._progress)
        root.addSpacing(6)

        self._progress_label = CaptionLabel("等待开始", self)
        self._progress_label.setFont(_mono(10))
        self._progress_label.setStyleSheet(f"color: {COLORS['muted']};")
        root.addWidget(self._progress_label)
        root.addSpacing(12)

        action_row = QHBoxLayout()
        action_row.setSpacing(16)

        self._start_btn = PrimaryPushButton("分离", self)
        self._start_btn.setFixedSize(160, 46)
        f = self._start_btn.font()
        f.setBold(True)
        f.setPointSize(11)
        self._start_btn.setFont(f)
        action_row.addWidget(self._start_btn)

        cmd_col = QVBoxLayout()
        cmd_col.setContentsMargins(0, 0, 0, 0)
        cmd_col.setSpacing(0)
        cmd_head = CaptionLabel("equivalent", self)
        cmd_head.setFont(_mono(9))
        cmd_head.setStyleSheet(f"color: {COLORS['muted']}; letter-spacing: 1px;")
        self._cmd_preview = BodyLabel("", self)
        self._cmd_preview.setFont(_mono(11))
        self._cmd_preview.setStyleSheet(f"color: {COLORS['text']};")
        self._cmd_preview.setWordWrap(True)
        cmd_col.addWidget(cmd_head)
        cmd_col.addWidget(self._cmd_preview)
        action_row.addLayout(cmd_col, 1)
        root.addLayout(action_row)

    def _section_header(self, key: str, note: str) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(10)
        k = BodyLabel(f"─ {key}", self)
        k.setFont(_mono(11, bold=True))
        k.setStyleSheet(f"color: {COLORS['accent']}; letter-spacing: 1px;")
        row.addWidget(k)
        n = CaptionLabel(note, self)
        n.setStyleSheet(f"color: {COLORS['muted']};")
        row.addWidget(n)
        row.addStretch()
        return row

    def _page_qss(self) -> str:
        return f"""
        DemucsUIDemoB {{
            background: {COLORS['bg']};
        }}
        QWidget#stemsBox {{
            background: {COLORS['panel']};
            border: 1px solid {COLORS['hairline']};
            border-radius: 6px;
        }}
        ComboBox, SpinBox {{
            background: {COLORS['panel']};
        }}
        PrimaryPushButton {{
            background: {COLORS['accent']};
            color: #1a1300;
            border-radius: 4px;
            font-weight: 700;
            letter-spacing: 4px;
        }}
        PrimaryPushButton:hover {{
            background: #F7C13E;
        }}
        PrimaryPushButton:pressed {{
            background: #D69B0F;
        }}
        PrimaryPushButton:disabled {{
            background: rgba(240,180,41,64);
            color: rgba(255,255,255,120);
        }}
        """

    # ---------- 信号 ----------
    def _connect_signals(self):
        self._dir_btn.clicked.connect(self._pick_output)
        self._overlap_sl.valueChanged.connect(self._on_overlap_changed)
        self._model_cb.currentTextChanged.connect(self._sync_command_preview)
        self._device_cb.currentTextChanged.connect(self._sync_command_preview)
        self._shifts_spin.valueChanged.connect(self._sync_command_preview)
        self._seg_spin.valueChanged.connect(self._sync_command_preview)
        self._fmt_cb.currentTextChanged.connect(self._sync_command_preview)
        self._start_btn.clicked.connect(self._on_start)

    def _on_overlap_changed(self, v):
        self._overlap_val.setText(f"{v / 100:.2f}")
        self._sync_command_preview()

    def _on_stem_toggled(self, *_):
        self._sync_command_preview()

    def _sync_command_preview(self):
        p = self._collect_params()
        stems_kept = [k for k, v in p["tracks"].items() if v]
        if len(stems_kept) == 4:
            stems_repr = "all"
        elif not stems_kept:
            stems_repr = "∅"
        else:
            stems_repr = "+".join(stems_kept)
        parts = [
            p["model"],
            p["device"],
            f"shifts={p['shifts']}",
            f"segment={p['segment']}",
            f"overlap={p['overlap']:.2f}",
            f"out={p['format']}",
            f"stems={stems_repr}",
        ]
        self._cmd_preview.setText(" · ".join(parts))

    def _pick_output(self):
        path = QFileDialog.getExistingDirectory(
            self, "选择输出目录", self._output_dir)
        if not path:
            return
        self._output_dir = path
        display = path if len(path) <= 70 else path[:67] + "…"
        self._dir_label.setText(display)
        self._dir_label.setToolTip(path)

    def _on_start(self):
        if not self._drop.file_path():
            InfoBar.warning("缺少输入", "请先选择音频文件", parent=self)
            return
        if not self._output_dir:
            InfoBar.warning("缺少输出目录", "请选择输出目录", parent=self)
            return
        if not any(row.is_included() for row in self._stem_rows.values()):
            InfoBar.warning("没有 stem", "至少要输出一条音轨", parent=self)
            return
        self.separationRequested.emit(self._collect_params())
        InfoBar.info("演示模式", "使用 QTimer 伪造进度", parent=self, duration=1500)
        self._start_running()

    def _collect_params(self) -> dict:
        device_text = self._device_cb.currentText()
        device = device_text.split(" · ", 1)[1] if " · " in device_text else device_text
        return {
            "input": self._drop.file_path(),
            "output": self._output_dir,
            "model": self._model_cb.currentText(),
            "device": device,
            "tracks": {sid: row.is_included() for sid, row in self._stem_rows.items()},
            "shifts": self._shifts_spin.value(),
            "segment": self._seg_spin.value(),
            "overlap": self._overlap_sl.value() / 100,
            "format": self._fmt_cb.currentText(),
        }

    # ---------- 伪 worker ----------
    def _start_running(self):
        self._set_locked(True)
        self._status.setText("running")
        self._status.setStyleSheet(
            f"color: {COLORS['accent']}; letter-spacing: 1px;")
        self._progress_val = 0
        self._progress.setValue(0)
        self._progress_label.setText("loading model …")
        for row in self._stem_rows.values():
            row.reset()
        self._progress_timer.start(80)

    def _tick_progress(self):
        self._progress_val += 2
        pct = self._progress_val
        if pct >= 100:
            pct = 100
            self._progress.setValue(100)
            self._progress_label.setText("done · flushing stems")
            self._status.setText("done")
            self._status.setStyleSheet(
                f"color: {COLORS['ok']}; letter-spacing: 1px;")
            for row in self._stem_rows.values():
                row.set_progress(1.0)
            self._progress_timer.stop()
            QTimer.singleShot(900, self._finish_running)
            return
        self._progress.setValue(pct)
        if pct < 12:
            self._progress_label.setText("loading model …")
        elif pct < 92:
            self._progress_label.setText(f"separating · {pct}%")
        else:
            self._progress_label.setText("writing stems …")

        # 让各 stem 稍微错开进度
        offsets = {"vocals": 0.0, "drums": -0.06, "bass": -0.10, "other": -0.14}
        for sid, row in self._stem_rows.items():
            local = max(0.0, min(1.0, pct / 100 + offsets[sid]))
            row.set_progress(local)

    def _finish_running(self):
        self._set_locked(False)
        self._status.setText("idle")
        self._status.setStyleSheet(
            f"color: {COLORS['muted']}; letter-spacing: 1px;")
        self._progress.setValue(0)
        self._progress_label.setText("等待开始")
        for row in self._stem_rows.values():
            row.reset()

    def _set_locked(self, running: bool):
        enabled = not running
        widgets = [
            self._start_btn, self._dir_btn, self._model_cb, self._device_cb,
            self._shifts_spin, self._seg_spin, self._overlap_sl, self._fmt_cb,
            self._drop,
        ]
        for w in widgets:
            w.setEnabled(enabled)
        for row in self._stem_rows.values():
            row._cb.setEnabled(enabled)

# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------
def main():
    setTheme(Theme.DARK)
    setThemeColor(COLORS["accent"])
    app = QApplication(sys.argv)
    devices = {"cpu": "cpu", "cuda:0": "cuda:0", "cuda:1": "cuda:1"}
    w = DemucsUIDemoB(device_options=devices)
    w.resize(1100, 780)
    w.setWindowTitle("Demucs · UI Demo · stems ledger")
    w.show()

    def _dump(params: dict):
        print("[separationRequested]", params)

    w.separationRequested.connect(_dump)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
