
# tests/demo_demucs_ui.py
"""
Demucs 音频分离 — 紧凑美观版 UI 演示

优化重点:
  1. 去除丑陋的分割线，改用间距和标题层级区分模块
  2. 压缩整体布局间距，让内容更紧凑、一屏内容更丰富
  3. 优化控件对齐方式，视觉更整洁
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices, QDragEnterEvent, QDropEvent
from PyQt6.QtWidgets import (
    QApplication, QFileDialog, QFrame, QGridLayout,
    QHBoxLayout, QSizePolicy, QVBoxLayout, QWidget,
)
from qfluentwidgets import (
    BodyLabel, CaptionLabel, CardWidget, CheckBox, ComboBox,
    IconWidget, InfoBar, PrimaryPushButton, ProgressBar, PushButton,
    Slider, SpinBox, StrongBodyLabel, TitleLabel, ToolButton,
    FluentIcon as FIF, setTheme, setThemeColor, Theme,
)

ACCENT = "#0078D4"
AUDIO_EXTS = {".mp3", ".wav", ".flac", ".m4a", ".ogg"}
MODEL_OPTIONS = ["htdemucs (推荐)", "htdemucs_ft", "mdx_extra", "mdx"]
FORMAT_OPTIONS = ["wav (无损)", "flac (无损压缩)", "mp3 (320k)"]


# ---------------------------------------------------------------------------
# 拖放输入区
# ---------------------------------------------------------------------------
class DropZone(CardWidget):
    """紧凑的拖放区域"""
    fileSelected = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setBorderRadius(8)
        self.setAcceptDrops(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(180)  # 稍微压缩高度
        self._file_path = ""

        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 20, 20, 20)
        lay.setSpacing(8)
        lay.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._icon = IconWidget(FIF.MUSIC, self)
        self._icon.setFixedSize(40, 40)
        
        self._title = StrongBodyLabel("拖入音频文件 · 或点击选择", self)
        self._title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        self._hint = CaptionLabel("支持 mp3, wav, flac, m4a, ogg", self)
        self._hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._hint.setStyleSheet("color: gray;")

        lay.addWidget(self._icon, alignment=Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self._title)
        lay.addWidget(self._hint)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self.isEnabled():
            path, _ = QFileDialog.getOpenFileName(
                self, "选择音频文件", "",
                "音频文件 (*.mp3 *.wav *.flac *.m4a *.ogg)"
            )
            if path:
                self.set_file(path)
        super().mousePressEvent(e)

    def dragEnterEvent(self, e: QDragEnterEvent):
        if not self.isEnabled() or not e.mimeData().hasUrls():
            e.ignore()
            return
        for url in e.mimeData().urls():
            if Path(url.toLocalFile()).suffix.lower() in AUDIO_EXTS:
                e.acceptProposedAction()
                return
        e.ignore()

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
        self._title.setText(name if len(name) <= 40 else name[:37] + "…")
        self._title.setToolTip(path)
        self._hint.setText(f"路径: {os.path.dirname(path)}")

    def file_path(self) -> str:
        return self._file_path


# ---------------------------------------------------------------------------
# 主 UI
# ---------------------------------------------------------------------------
class DemucsUIDemo(QWidget):
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

    # ---------- 布局 ----------
    def _setup_ui(self):
        root = QVBoxLayout(self)
        # 减少整体边距
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(12)

        root.addLayout(self._build_header())

        main_row = QHBoxLayout()
        main_row.setSpacing(12)
        main_row.addLayout(self._build_left_column(), 5)
        main_row.addLayout(self._build_right_column(), 7) # 右侧参数区给更多比重
        root.addLayout(main_row, 1)

        root.addWidget(self._build_action_bar())
        root.addWidget(self._build_history_row())

    def _build_header(self) -> QHBoxLayout:
        header = QHBoxLayout()
        header.setSpacing(10)
        
        ic = IconWidget(FIF.MUSIC_FOLDER, self)
        ic.setFixedSize(24, 24)
        
        title = TitleLabel("音频分离", self)
        subtitle = CaptionLabel("Demucs 模型 · 人声/鼓/贝斯/其他分离", self)
        subtitle.setStyleSheet("color: gray;")

        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(0)
        text_col.addWidget(title)
        text_col.addWidget(subtitle)

        header.addWidget(ic, alignment=Qt.AlignmentFlag.AlignVCenter)
        header.addLayout(text_col)
        header.addStretch()
        return header

    def _build_left_column(self) -> QVBoxLayout:
        col = QVBoxLayout()
        col.setSpacing(10)

        self._drop = DropZone(self)
        col.addWidget(self._drop, 1)

        # 输出目录行
        out_card = CardWidget(self)
        out_card.setBorderRadius(8)
        out_card.setFixedHeight(44) # 更紧凑的高度
        out = QHBoxLayout(out_card)
        out.setContentsMargins(12, 8, 8, 8)
        out.setSpacing(8)
        
        out_ic = IconWidget(FIF.FOLDER, self)
        out_ic.setFixedSize(14, 14)
        out.addWidget(out_ic)
        out.addWidget(StrongBodyLabel("输出目录", self))
        
        self._dir_label = BodyLabel(self._output_dir, self)
        self._dir_label.setStyleSheet("color: gray;")
        self._dir_label.setToolTip(self._output_dir)
        out.addWidget(self._dir_label, 1)
        
        self._dir_btn = PushButton("浏览", self)
        self._dir_btn.setFixedWidth(60)
        out.addWidget(self._dir_btn)
        col.addWidget(out_card)

        return col

    def _build_right_column(self) -> QVBoxLayout:
        col = QVBoxLayout()
        col.setSpacing(0) # 卡片内部自己管理间距

        params_card = CardWidget(self)
        params_card.setBorderRadius(8)
        
        # 使用 QGridLayout 紧凑排列
        g = QGridLayout(params_card)
        g.setContentsMargins(16, 12, 16, 12)
        g.setHorizontalSpacing(12)
        g.setVerticalSpacing(8) # 减小垂直间距

        # 让控件列拉伸
        g.setColumnStretch(1, 1)
        g.setColumnStretch(3, 1)

        r = 0
        # -- 第一组: 模型 --
        g.addWidget(self._create_group_header("模型设置", FIF.SETTING), r, 0, 1, 4)
        r += 1
        
        # 第1行: 模型 + 设备
        g.addWidget(CaptionLabel("分离模型", self), r, 0, alignment=Qt.AlignmentFlag.AlignVCenter)
        self._model_cb = ComboBox()
        self._model_cb.addItems(MODEL_OPTIONS)
        self._model_cb.setCurrentText(MODEL_OPTIONS[0])
        g.addWidget(self._model_cb, r, 1)

        g.addWidget(CaptionLabel("计算设备", self), r, 2, alignment=Qt.AlignmentFlag.AlignVCenter)
        self._device_cb = ComboBox()
        for name, index in self._device_options.items():
            self._device_cb.addItem(f"{name} ({index})")
        g.addWidget(self._device_cb, r, 3)
        r += 1

        # -- 第二组: 输出 --
        # 增加组间距
        g.addItem(QSpacerItem(0, 12), r, 0, 1, 4)
        r += 1
        
        g.addWidget(self._create_group_header("输出音轨", FIF.ALBUM), r, 0, 1, 4)
        r += 1

        # 第2行: 复选框
        tracks = QWidget(self)
        tracks_lay = QHBoxLayout(tracks)
        tracks_lay.setContentsMargins(0, 0, 0, 0)
        tracks_lay.setSpacing(16) # 加宽复选框间距
        self._vocals_cb = CheckBox("人声", self)
        self._drums_cb = CheckBox("鼓", self)
        self._bass_cb = CheckBox("贝斯", self)
        self._other_cb = CheckBox("其他", self)
        for cb in (self._vocals_cb, self._drums_cb, self._bass_cb, self._other_cb):
            cb.setChecked(True)
            tracks_lay.addWidget(cb)
        tracks_lay.addStretch()
        g.addWidget(tracks, r, 0, 1, 4)
        r += 1

        # -- 第三组: 高级 --
        g.addItem(QSpacerItem(0, 12), r, 0, 1, 4)
        r += 1
        
        g.addWidget(self._create_group_header("高级参数", FIF.SETTING), r, 0, 1, 4)
        r += 1

        # 第3行: Shifts + Segment
        g.addWidget(CaptionLabel("移位量", self), r, 0, alignment=Qt.AlignmentFlag.AlignVCenter)
        self._shifts_spin = SpinBox(self)
        self._shifts_spin.setRange(1, 8)
        self._shifts_spin.setValue(1)
        g.addWidget(self._shifts_spin, r, 1)

        g.addWidget(CaptionLabel("分段长度", self), r, 2, alignment=Qt.AlignmentFlag.AlignVCenter)
        self._seg_spin = SpinBox(self)
        self._seg_spin.setRange(1, 7)
        self._seg_spin.setValue(7)
        g.addWidget(self._seg_spin, r, 3)
        r += 1

        # 第4行: Overlap + Format
        g.addWidget(CaptionLabel("重叠率", self), r, 0, alignment=Qt.AlignmentFlag.AlignVCenter)
        ov_wrap = QWidget(self)
        ov = QHBoxLayout(ov_wrap)
        ov.setContentsMargins(0, 0, 0, 0)
        ov.setSpacing(8)
        self._overlap_sl = Slider(Qt.Orientation.Horizontal, self)
        self._overlap_sl.setRange(0, 50)
        self._overlap_sl.setValue(25)
        self._overlap_val = BodyLabel("0.25", self)
        self._overlap_val.setFixedWidth(32)
        ov.addWidget(self._overlap_sl, 1)
        ov.addWidget(self._overlap_val)
        g.addWidget(ov_wrap, r, 1)

        g.addWidget(CaptionLabel("输出格式", self), r, 2, alignment=Qt.AlignmentFlag.AlignVCenter)
        self._fmt_cb = ComboBox(self)
        self._fmt_cb.addItems(FORMAT_OPTIONS)
        g.addWidget(self._fmt_cb, r, 3)
        r += 1

        col.addWidget(params_card, 1)
        return col

    def _create_group_header(self, text: str, icon=None) -> QWidget:
        """创建简洁的分组标题"""
        row = QWidget(self)
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, 0, 0, 4) # 底部留一点间距
        lay.setSpacing(6)
        
        # 可以加个小的装饰线或者仅靠文字区分
        if icon:
            ic = IconWidget(icon, row)
            ic.setFixedSize(14, 14)
            lay.addWidget(ic)
        
        lbl = StrongBodyLabel(text, row)
        lbl.setStyleSheet("color: #0091E8;") # 使用主题色高亮标题
        lay.addWidget(lbl)
        lay.addStretch()
        return row

    def _build_action_bar(self) -> CardWidget:
        bar = CardWidget(self)
        bar.setBorderRadius(8)
        bar.setFixedHeight(70) # 稍微压低
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(16, 12, 12, 12)
        lay.setSpacing(16)

        prog_col = QVBoxLayout()
        prog_col.setContentsMargins(0, 0, 0, 0)
        prog_col.setSpacing(2)
        self._progress = ProgressBar(self)
        self._progress.setValue(0)
        self._progress_label = CaptionLabel("等待开始", self)
        self._progress_label.setStyleSheet("color: gray;")
        prog_col.addWidget(self._progress)
        prog_col.addWidget(self._progress_label)
        lay.addLayout(prog_col, 1)

        self._start_btn = PrimaryPushButton("开始分离", self)
        self._start_btn.setFixedSize(160, 44)
        lay.addWidget(self._start_btn)
        return bar

    def _build_history_row(self) -> QWidget:
        wrap = QWidget(self)
        wrap.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        h = QHBoxLayout(wrap)
        h.setContentsMargins(0, 0, 0, 0) # 去掉边距
        h.setSpacing(8)
        h.addWidget(CaptionLabel("最近任务:", wrap))
        self._history_row = QHBoxLayout()
        self._history_row.setSpacing(8)
        h.addLayout(self._history_row)
        h.addStretch()
        wrap.setVisible(False)
        self._history_wrap = wrap
        return wrap

    # ---------- 信号 ----------
    def _connect_signals(self):
        self._dir_btn.clicked.connect(self._pick_output)
        self._overlap_sl.valueChanged.connect(
            lambda v: self._overlap_val.setText(f"{v / 100:.2f}")
        )
        self._start_btn.clicked.connect(self._on_start)

    def _pick_output(self):
        path = QFileDialog.getExistingDirectory(self, "选择输出目录", self._output_dir)
        if not path:
            return
        self._output_dir = path
        display = path if len(path) <= 50 else path[:47] + "…"
        self._dir_label.setText(display)
        self._dir_label.setToolTip(path)

    def _on_start(self):
        if not self._drop.file_path():
            InfoBar.warning("提示", "请先拖入或选择音频文件", parent=self)
            return
        self.separationRequested.emit(self._collect_params())
        InfoBar.info("演示模式", "正在模拟处理...", parent=self, duration=1500)
        self._start_running()

    def _collect_params(self) -> dict:
        model_map = {
            "htdemucs (推荐)": "htdemucs",
            "htdemucs_ft": "htdemucs_ft",
            "mdx_extra": "mdx_extra",
            "mdx": "mdx",
        }
        device_text = self._device_cb.currentText()
        # 兼容 "cpu (cpu)" 格式
        device = device_text.split(" (")[1].rstrip(")") if " (" in device_text else device_text
        fmt = self._fmt_cb.currentText().split()[0]
        return {
            "input": self._drop.file_path(),
            "output": self._output_dir,
            "model": model_map[self._model_cb.currentText()],
            "device": device,
            "tracks": {
                "vocals": self._vocals_cb.isChecked(),
                "drums": self._drums_cb.isChecked(),
                "bass": self._bass_cb.isChecked(),
                "other": self._other_cb.isChecked(),
            },
            "shifts": self._shifts_spin.value(),
            "segment": self._seg_spin.value(),
            "overlap": self._overlap_sl.value() / 100,
            "format": fmt,
        }

    # ---------- 伪 worker ----------
    def _start_running(self):
        self._set_locked(True)
        self._progress_val = 0
        self._progress.setValue(0)
        self._progress_label.setText("初始化...")
        self._progress_timer.start(60)

    def _tick_progress(self):
        self._progress_val += 2
        if self._progress_val >= 100:
            self._progress_val = 100
            self._progress.setValue(100)
            self._progress_label.setText("处理完成")
            self._progress_timer.stop()
            QTimer.singleShot(600, self._finish_running)
            return
        self._progress.setValue(self._progress_val)
        if self._progress_val < 15:
            self._progress_label.setText("加载模型...")
        elif self._progress_val < 90:
            self._progress_label.setText(f"正在分离: {self._progress_val}%")
        else:
            self._progress_label.setText("保存文件...")

    def _finish_running(self):
        self._set_locked(False)
        self._progress.setValue(0)
        self._progress_label.setText("等待开始")
        self._add_history(self._drop.file_path(), self._output_dir)

    def _set_locked(self, running: bool):
        enabled = not running
        for w in (
            self._start_btn, self._dir_btn, self._model_cb, self._device_cb,
            self._vocals_cb, self._drums_cb, self._bass_cb, self._other_cb,
            self._shifts_spin, self._seg_spin, self._overlap_sl, self._fmt_cb,
            self._drop,
        ):
            w.setEnabled(enabled)

    # ---------- 历史 ----------
    def _add_history(self, input_path: str, output_dir: str):
        if not input_path:
            return
        chip = CardWidget(self)
        chip.setBorderRadius(6)
        chip.setFixedHeight(28)
        cl = QHBoxLayout(chip)
        cl.setContentsMargins(8, 2, 4, 2)
        cl.setSpacing(6)
        name = Path(input_path).name
        cl.addWidget(CaptionLabel(name if len(name) <= 18 else name[:15] + "…", chip))
        open_btn = ToolButton(FIF.FOLDER, chip)
        open_btn.setFixedSize(20, 20)
        open_btn.clicked.connect(lambda _=False, d=output_dir: self._open_folder(d))
        cl.addWidget(open_btn)

        self._history_row.insertWidget(0, chip)
        while self._history_row.count() > 5: # 限制历史数量
            last = self._history_row.itemAt(self._history_row.count() - 1).widget()
            self._history_row.removeWidget(last)
            last.deleteLater()
        self._history_wrap.setVisible(True)

    def _open_folder(self, output_dir: str):
        if output_dir and os.path.exists(output_dir):
            QDesktopServices.openUrl(QUrl.fromLocalFile(output_dir))
        else:
            InfoBar.warning("错误", "目录不存在", parent=self)


# ---------------------------------------------------------------------------
# 补充缺失的 Import (QSpacerItem)
# ---------------------------------------------------------------------------
from PyQt6.QtWidgets import QSpacerItem

# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------
def main():
    setTheme(Theme.DARK)
    setThemeColor(ACCENT)
    app = QApplication(sys.argv)
    devices = {"cpu": "cpu", "cuda:0": "cuda:0"}
    w = DemucsUIDemo(device_options=devices)
    w.resize(1080, 720) # 稍微调小默认尺寸，符合紧凑感
    w.setWindowTitle("Demucs 音频分离")
    w.show()

    def _dump(params: dict):
        print("[separationRequested]", params)

    w.separationRequested.connect(_dump)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
