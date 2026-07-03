"""
subpage_demucs — 音频分离页
 
变更:
  1. 移除底部无意义的空白, 改为显示当前模型的详细说明
  2. 模型说明文本根据下拉框选择自动切换
  3. 保持原有的紧凑布局风格
"""
 
from __future__ import annotations
 
import os
from pathlib import Path
 
from PyQt6.QtCore import Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices, QDragEnterEvent, QDropEvent
from PyQt6.QtWidgets import (
    QFileDialog, QGridLayout, QHBoxLayout, QSizePolicy, QVBoxLayout, QWidget,
)
from qfluentwidgets import (
    BodyLabel, CaptionLabel, CheckBox, ComboBox, IconWidget,
    InfoBar, PrimaryPushButton, ProgressBar, Slider, SpinBox,
    StrongBodyLabel, TitleLabel, ToolButton, TransparentPushButton,
    FluentIcon as FIF,
)
 
from utils import paths as _paths
from widgets.audio_waveform_widget import AudioWaveformWidget
 
AUDIO_EXTS = {".mp3", ".wav", ".flac", ".m4a", ".ogg"}
MODEL_OPTIONS = ["htdemucs (推荐)", "htdemucs_ft", "mdx_extra", "mdx"]
FORMAT_OPTIONS = ["wav (无损)", "flac (无损压缩)", "mp3 (320k)"]
 
# 模型介绍文案
MODEL_DESCRIPTIONS = {
    "htdemucs (推荐)": "最新混合 Transformer 架构对大多数现代音乐效果最佳，人声与鼓点分离精准，延迟适中",
    "htdemucs_ft": "基于 htdemucs 的微调版专门针对人声分离优化，适合人声提取不干净的场景，处理速度稍慢",
    "mdx_extra": "Demucs v3 架构对年代较久或非典型风格的音乐有更好表现，稳健但资源占用略高",
    "mdx": "经典 MDX 架构速度较快，适合快速预览或对分离精度要求不高的场景",
}
 
_STATUS_STYLES = {
    "idle":    ("● 就绪",   "#4CAF50", "rgba(76,175,80,32)"),
    "running": ("● 运行中", "#FFA726", "rgba(255,167,38,36)"),
    "done":    ("● 完成",   "#42A5F5", "rgba(66,165,245,36)"),
}
 
 
# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _param_row(label: str, control: QWidget, hint: str = "") -> QWidget:
    row = QWidget()
    lay = QHBoxLayout(row)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(10)
    lbl = BodyLabel(label)
    lbl.setFixedWidth(100)
    lay.addWidget(lbl)
    lay.addWidget(control, 1)
    if hint:
        h = CaptionLabel(hint)
        h.setFixedWidth(160)
        h.setStyleSheet("color: #7a7a7a;")
        h.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        lay.addWidget(h)
    return row
 
 
# ---------------------------------------------------------------------------
# DashedDropZone
# ---------------------------------------------------------------------------
class DashedDropZone(QWidget):
    fileSelected = pyqtSignal(str)
 
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setAcceptDrops(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(130)
        self.setMaximumHeight(160)
        self._file_path = ""
        self._refresh_style(hover=False, active=False)
 
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 12, 18, 12)
        lay.setSpacing(2)
        lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
 
        self._icon = IconWidget(FIF.MUSIC, self)
        self._icon.setFixedSize(30, 30)
        self._title = StrongBodyLabel("拖入音频文件 · 或点击选择", self)
        self._title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._hint = CaptionLabel("mp3 · wav · flac · m4a · ogg", self)
        self._hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._hint.setStyleSheet("color: #8a8a8a;")
 
        lay.addWidget(self._icon, alignment=Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self._title)
        lay.addWidget(self._hint)
 
    def _refresh_style(self, hover: bool, active: bool):
        if active:
            border = "1.2px solid rgba(76,175,80,150)"
            bg = "rgba(76,175,80,18)"
        elif hover:
            border = "1.5px dashed rgba(255,255,255,140)"
            bg = "rgba(255,255,255,16)"
        else:
            border = "1.5px dashed rgba(255,255,255,55)"
            bg = "rgba(255,255,255,6)"
        self.setStyleSheet(
            f"DashedDropZone {{"
            f"  border: {border};"
            f"  border-radius: 12px;"
            f"  background: {bg};"
            f"}}"
        )
 
    def enterEvent(self, e):
        if not self._file_path:
            self._refresh_style(hover=True, active=False)
        super().enterEvent(e)
 
    def leaveEvent(self, e):
        self._refresh_style(hover=False, active=bool(self._file_path))
        super().leaveEvent(e)
 
    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self.isEnabled():
            path, _ = QFileDialog.getOpenFileName(
                self, "选择音频文件", os.path.join(os.getcwd(), "data", "outputs", "node", "materials"),
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
                self._refresh_style(hover=True, active=False)
                return
        e.ignore()
 
    def dragLeaveEvent(self, e):
        self._refresh_style(hover=False, active=bool(self._file_path))
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
        self._icon.setIcon(FIF.ACCEPT)
        self._title.setText(name if len(name) <= 48 else name[:45] + "…")
        self._title.setToolTip(path)
        self._hint.setText(f"来自 {os.path.dirname(path)}")
        self._refresh_style(hover=False, active=True)
        self.fileSelected.emit(path)
 
    def file_path(self) -> str:
        return self._file_path
 
 
# ---------------------------------------------------------------------------
# StatusPill
# ---------------------------------------------------------------------------
class StatusPill(CaptionLabel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.set_state("idle")
 
    def set_state(self, state: str):
        text, fg, bg = _STATUS_STYLES[state]
        self.setText(text)
        self.setStyleSheet(
            f"color: {fg};"
            f" padding: 4px 10px;"
            f" background: {bg};"
            f" border-radius: 10px;"
        )
 
 
# ---------------------------------------------------------------------------
# 主页面
# ---------------------------------------------------------------------------
class AudioSeparationWidget(QWidget):
    separationRequested = pyqtSignal(dict)
 
    def __init__(self, parent=None, device_options: dict = {}):
        super().__init__(parent)
        self.device_options = device_options
        self._input_path = ""
        self._output_dir = _paths.output_dir("demucs")
        self._waveform_previews: list[AudioWaveformWidget] = []
 
        self._setup_ui()
        self._connect_signals()
 
    # ---------- 布局 ----------
    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(32, 16, 32, 16)
        root.setSpacing(6)
 
        # ---- 头部 ----
        head = QHBoxLayout()
        head.setSpacing(10)
        title = TitleLabel("音频分离", self)
        subtitle = CaptionLabel("Demucs · 拆解人声 / 鼓 / 贝斯 / 其他", self)
        subtitle.setStyleSheet("color: #8a8a8a;")
        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(0)
        text_col.addWidget(title)
        text_col.addWidget(subtitle)
        head.addLayout(text_col)
        head.addStretch()
        self._status_pill = StatusPill(self)
        head.addWidget(self._status_pill, alignment=Qt.AlignmentFlag.AlignVCenter)
        root.addLayout(head)
 
        root.addSpacing(6)
 
        # ---- 拖放输入区 ----
        self._drop = DashedDropZone(self)
        root.addWidget(self._drop)
 
        # ---- 输出目录行 ----
        out_wrap = QWidget(self)
        out = QHBoxLayout(out_wrap)
        out.setContentsMargins(0, 0, 0, 0)
        out.setSpacing(10)
        out_lbl = BodyLabel("输出目录")
        out_lbl.setFixedWidth(100)
        out.addWidget(out_lbl)
        self._dir_label = BodyLabel(self._display_path(self._output_dir), self)
        self._dir_label.setStyleSheet("color: #a0a0a0;")
        self._dir_label.setToolTip(self._output_dir)
        out.addWidget(self._dir_label, 1)
        self._dir_btn = TransparentPushButton(FIF.FOLDER, "浏览", self)
        out.addWidget(self._dir_btn)
        root.addWidget(out_wrap)
 
        root.addSpacing(4)
 
        # ---- 主参数 ----
        self._model_combo = ComboBox()
        self._model_combo.addItems(MODEL_OPTIONS)
        self._model_combo.setCurrentText(MODEL_OPTIONS[0])
        root.addWidget(_param_row(
            "分离模型", self._model_combo,
            "htdemucs 平衡, ft 更精细"
        ))
 
        self._device_combo = ComboBox()
        for name, index in self.device_options.items():
            self._device_combo.addItem(f"{name} · {index}")
        root.addWidget(_param_row(
            "计算设备", self._device_combo,
            "首选 GPU 才实用"
        ))
 
        tracks_wrap = QWidget()
        tl = QHBoxLayout(tracks_wrap)
        tl.setContentsMargins(0, 0, 0, 0)
        tl.setSpacing(18)
        self._vocals_cb = CheckBox("人声")
        self._drums_cb = CheckBox("鼓")
        self._bass_cb = CheckBox("贝斯")
        self._other_cb = CheckBox("其他")
        for cb in (self._vocals_cb, self._drums_cb, self._bass_cb, self._other_cb):
            cb.setChecked(True)
            tl.addWidget(cb)
        tl.addStretch()
        root.addWidget(_param_row("输出音轨", tracks_wrap, "只导出勾选的音轨"))
 
        root.addSpacing(4)
 
        # ---- 高级参数 (单行横排 4 列) ----
        adv_wrap = QWidget(self)
        adv = QGridLayout(adv_wrap)
        adv.setContentsMargins(0, 0, 0, 0)
        adv.setHorizontalSpacing(16)
        adv.setVerticalSpacing(2)
 
        adv.addWidget(CaptionLabel("移位量"), 0, 0)
        self._shifts_spin = SpinBox()
        self._shifts_spin.setRange(1, 8)
        self._shifts_spin.setValue(1)
        adv.addWidget(self._shifts_spin, 1, 0)
 
        adv.addWidget(CaptionLabel("分段长度"), 0, 1)
        self._seg_spin = SpinBox()
        self._seg_spin.setRange(1, 7)
        self._seg_spin.setValue(7)
        adv.addWidget(self._seg_spin, 1, 1)
 
        adv.addWidget(CaptionLabel("重叠率"), 0, 2)
        ov_wrap = QWidget()
        ov = QHBoxLayout(ov_wrap)
        ov.setContentsMargins(0, 0, 0, 0)
        ov.setSpacing(8)
        self._overlap_sl = Slider(Qt.Orientation.Horizontal)
        self._overlap_sl.setRange(0, 50)
        self._overlap_sl.setValue(25)
        self._overlap_val = BodyLabel("0.25")
        self._overlap_val.setFixedWidth(36)
        self._overlap_val.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        ov.addWidget(self._overlap_sl, 1)
        ov.addWidget(self._overlap_val)
        adv.addWidget(ov_wrap, 1, 2)
 
        adv.addWidget(CaptionLabel("输出格式"), 0, 3)
        self._fmt_combo = ComboBox()
        self._fmt_combo.addItems(FORMAT_OPTIONS)
        adv.addWidget(self._fmt_combo, 1, 3)
 
        for c in range(4):
            adv.setColumnStretch(c, 1)
        root.addWidget(_param_row("高级参数", adv_wrap))
 
        # ---- 底部模型说明区 (替代原弹性空白) ----
        root.addSpacing(12)
        self._model_info_label = BodyLabel(self)
        self._model_info_label.setWordWrap(True)
        self._model_info_label.setObjectName("modelInfoLabel")
        # 稍微调小字体颜色，作为辅助信息
        self._model_info_label.setStyleSheet("#modelInfoLabel { color: #888888; }") 
        self._model_info_label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        # 设置固定高度或最小高度防止文本变化时布局跳动过大，或者直接给个 stretch
        self._model_info_label.setMinimumHeight(40) 
        root.addWidget(self._model_info_label, 1) # 拉伸因子为1，填充剩余空间
 
        # ---- 进度 + 主按钮 ----
        self._progress = ProgressBar(self)
        self._progress.setValue(0)
        self._progress_label = CaptionLabel("等待开始", self)
        self._progress_label.setStyleSheet("color: #8a8a8a;")
        prog_wrap = QVBoxLayout()
        prog_wrap.setContentsMargins(0, 0, 0, 0)
        prog_wrap.setSpacing(2)
        prog_wrap.addWidget(self._progress)
        prog_wrap.addWidget(self._progress_label)
        root.addLayout(prog_wrap)
 
        root.addSpacing(2)
 
        self._start_btn = PrimaryPushButton("开 始 分 离", self)
        self._start_btn.setFixedHeight(52)
        self._start_btn.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        f = self._start_btn.font()
        f.setBold(True)
        f.setPointSize(13)
        self._start_btn.setFont(f)
        root.addWidget(self._start_btn)
 
        # ---- 历史 ----
        self._history_wrap = QWidget(self)
        hw = QHBoxLayout(self._history_wrap)
        hw.setContentsMargins(0, 2, 0, 0)
        hw.setSpacing(10)
        self._history_lbl = CaptionLabel("最近", self._history_wrap)
        self._history_lbl.setStyleSheet("color: #8a8a8a;")
        hw.addWidget(self._history_lbl)
        self._history_row = QHBoxLayout()
        self._history_row.setSpacing(8)
        hw.addLayout(self._history_row)
        hw.addStretch()
        self._history_wrap.setVisible(False)
        root.addWidget(self._history_wrap)
 
        # 初始化说明文字
        self._update_model_info(self._model_combo.currentText())
 
    # ---------- 信号 ----------
    def _connect_signals(self):
        self._dir_btn.clicked.connect(self._pick_output)
        self._overlap_sl.valueChanged.connect(
            lambda v: self._overlap_val.setText(f"{v / 100:.2f}")
        )
        self._start_btn.clicked.connect(self._on_start)
        # 关联模型切换信号
        self._model_combo.currentTextChanged.connect(self._update_model_info)
 
    def _update_model_info(self, model_name: str):
        """更新底部模型说明文本"""
        desc = MODEL_DESCRIPTIONS.get(model_name, "")
        self._model_info_label.setText(f"💡 当前模型: {desc}")
 
    # ---------- 输入输出 ----------
    def _display_path(self, path: str) -> str:
        return path if len(path) <= 60 else path[:57] + "…"
 
    def _pick_output(self):
        path = QFileDialog.getExistingDirectory(
            self, "选择输出目录", self._output_dir)
        if not path:
            return
        self._output_dir = path
        self._dir_label.setText(self._display_path(path))
        self._dir_label.setToolTip(path)
 
    def _on_start(self):
        if not self._drop.file_path():
            InfoBar.warning("缺少输入文件", "请先选择音频文件", parent=self)
            return
        if not self._output_dir:
            InfoBar.warning("缺少输出目录", "请选择输出目录", parent=self)
            return
        self._input_path = self._drop.file_path()
        self.separationRequested.emit(self.get_params())
 
    # ---------- 参数字典 ----------
    def get_params(self) -> dict:
        model_map = {
            "htdemucs (推荐)": "htdemucs",
            "htdemucs_ft": "htdemucs_ft",
            "mdx_extra": "mdx_extra",
            "mdx": "mdx",
        }
        device_text = self._device_combo.currentText()
        device = device_text.split(" · ", 1)[1] if " · " in device_text else device_text
        fmt = self._fmt_combo.currentText().split()[0]
        return {
            "input": self._drop.file_path(),
            "output": self._output_dir,
            "model": model_map[self._model_combo.currentText()],
            "device": device,
            "tracks": {
                "vocals": self._vocals_cb.isChecked(),
                "drums": self._drums_cb.isChecked(),
                "bass": self._bass_cb.isChecked(),
                "other": self._other_cb.isChecked(),
            },
            "shifts": self._shifts_spin.value(),
            "segment": int(self._seg_spin.value()),
            "overlap": self._overlap_sl.value() / 100,
            "format": fmt,
        }
 
    # ---------- 对外接口 ----------
    def set_progress(self, value: int, label: str = ""):
        self._progress.setValue(int(value))
        if label:
            self._progress_label.setText(label)
        self._status_pill.set_state("running")
 
    def reset_progress(self):
        self._progress.setValue(0)
        self._progress_label.setText("等待开始")
        self._status_pill.set_state("idle")
 
    def set_running(self, running: bool):
        enabled = not running
        for w in (
            self._start_btn, self._dir_btn, self._model_combo, self._device_combo,
            self._vocals_cb, self._drums_cb, self._bass_cb, self._other_cb,
            self._shifts_spin, self._seg_spin, self._overlap_sl, self._fmt_combo,
            self._drop,
        ):
            w.setEnabled(enabled)
        if running:
            self._status_pill.set_state("running")
 
    # ---------- 历史 ----------
    def add_history_task(self, input_path: str, output_dir: str):
        if not input_path:
            return
        from datetime import datetime
 
        chip = QWidget(self)
        chip.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        chip.setStyleSheet(
            "QWidget {"
            "  background: rgba(255,255,255,10);"
            "  border-radius: 6px;"
            "}"
        )
        cl = QHBoxLayout(chip)
        cl.setContentsMargins(10, 2, 4, 2)
        cl.setSpacing(6)
 
        name = Path(input_path).name
        stamp = datetime.now().strftime("%H:%M:%S")
        title = CaptionLabel(name if len(name) <= 22 else name[:19] + "…", chip)
        title.setToolTip(f"{name}\n完成于 {stamp}\n输出: {output_dir}")
        cl.addWidget(title)
 
        play_btn = ToolButton(FIF.PLAY, chip)
        play_btn.setFixedSize(22, 22)
        play_btn.setToolTip("波形预览")
        play_btn.clicked.connect(
            lambda _=False, d=output_dir: self._open_waveform_preview(d))
        cl.addWidget(play_btn)
 
        folder_btn = ToolButton(FIF.FOLDER, chip)
        folder_btn.setFixedSize(22, 22)
        folder_btn.setToolTip(f"打开 {output_dir}")
        folder_btn.clicked.connect(
            lambda _=False, d=output_dir: self._open_output_folder(d))
        cl.addWidget(folder_btn)
 
        self._history_row.insertWidget(0, chip)
        while self._history_row.count() > 6:
            last = self._history_row.itemAt(self._history_row.count() - 1).widget()
            self._history_row.removeWidget(last)
            last.deleteLater()
        self._history_wrap.setVisible(True)
 
    def _open_output_folder(self, output_dir: str):
        if output_dir and os.path.exists(output_dir):
            QDesktopServices.openUrl(QUrl.fromLocalFile(output_dir))
        else:
            InfoBar.warning("目录不存在", "输出目录已被移动或删除", parent=self)
 
    def _open_waveform_preview(self, output_dir: str):
        if not output_dir or not os.path.isdir(output_dir):
            InfoBar.warning("路径无效", "输出目录不存在或不是文件夹", parent=self)
            return
        preview = AudioWaveformWidget(os.path.join(output_dir, "vocals.wav"))
        preview.set_file_root_dir(output_dir)
        preview.disable_mic_recording()
        preview.show()
        self._waveform_previews.append(preview)
        preview.destroyed.connect(
            lambda _, p=preview: self._waveform_previews.remove(p)
            if p in self._waveform_previews else None
        )