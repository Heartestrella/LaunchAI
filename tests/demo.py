# whisper_ui_demo_standalone.py
# Whisper 语音转文字 - UI 演示版（解码参数独立成组）

import sys
import os
from pathlib import Path
from PyQt6.QtCore import Qt, QUrl, QSize
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QFileDialog, QFrame, QSizePolicy, QApplication

from qfluentwidgets import (
    setTheme, Theme, setThemeColor,
    TitleLabel, BodyLabel, CaptionLabel, StrongBodyLabel,
    PrimaryPushButton, PushButton, ToolButton,
    ComboBox, Slider, CheckBox, SpinBox,
    ProgressBar, SmoothScrollArea, CardWidget, ExpandGroupSettingCard,
    IconWidget, InfoBar, InfoBarPosition, FluentIcon as FIF,
)

ACCENT = "#0078D4"
MODEL_OPTIONS = ["tiny", "base", "small", "medium", "large", "large-v3"]
LANGUAGE_OPTIONS = ["自动检测", "中文", "英文", "日文", "韩文", "法文", "德文", "西班牙文", "俄文"]
TASK_OPTIONS = ["转录 (原始语言)", "翻译成英文"]
OUTPUT_FORMATS = ["txt (纯文本)", "srt (字幕)", "vtt (网页字幕)", "所有格式"]


def _separator():
    sep = QFrame()
    sep.setFrameShape(QFrame.Shape.HLine)
    sep.setStyleSheet("background: rgba(128,128,128,40); max-height:1px;")
    return sep


def _section_title(text: str, icon=None, parent=None):
    row = QWidget(parent)
    lay = QHBoxLayout(row)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(8)
    if icon:
        ico = IconWidget(icon, row)
        ico.setFixedSize(16, 16)
        lay.addWidget(ico)
    lbl = StrongBodyLabel(text, row)
    lay.addWidget(lbl)
    lay.addStretch()
    return row


class WhisperUIDemo(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._input_paths = []
        self._output_dir = ""
        self._setup_ui()
        self._connect_signals()

    def _setup_ui(self):
        scroll = SmoothScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet(
            "SmoothScrollArea { background: transparent; border: none; }")

        container = QWidget()
        scroll.setWidget(container)
        root = QVBoxLayout(container)
        root.setContentsMargins(32, 28, 32, 32)
        root.setSpacing(24)

        # 头部
        header = QHBoxLayout()
        icon_widget = IconWidget(FIF.MICROPHONE, self)
        icon_widget.setFixedSize(32, 32)
        title_label = TitleLabel("语音转文字工作站", self)
        header.addWidget(icon_widget)
        header.addWidget(title_label)
        header.addStretch()
        root.addLayout(header)

        desc = BodyLabel("使用 OpenAI Whisper 将音频/视频文件转录为文本，支持多语言、字幕生成。"
                         "注意：「翻译成英文」任务会将其他语言转录并翻译为英文（Whisper 限制）。", self)
        desc.setWordWrap(True)
        root.addWidget(desc)

        # 输入输出卡片
        io_layout = QHBoxLayout()
        io_layout.setSpacing(20)
        self.input_card = CardWidget(self)
        self.input_card.setBorderRadius(12)
        self._setup_input_card()
        io_layout.addWidget(self.input_card, 1)
        self.output_card = CardWidget(self)
        self.output_card.setBorderRadius(12)
        self._setup_output_card()
        io_layout.addWidget(self.output_card, 1)
        root.addLayout(io_layout)

        # 模型与设备卡片
        config_card = CardWidget(self)
        config_card.setBorderRadius(12)
        config_layout = QHBoxLayout(config_card)
        config_layout.setContentsMargins(20, 12, 20, 12)
        model_box = QVBoxLayout()
        model_box.addWidget(CaptionLabel("Whisper 模型"))
        self.model_combo = ComboBox()
        self.model_combo.addItems(MODEL_OPTIONS)
        self.model_combo.setCurrentText("small")
        self.model_combo.setFixedWidth(150)
        model_box.addWidget(self.model_combo)
        config_layout.addLayout(model_box)
        config_layout.addSpacing(30)
        device_box = QVBoxLayout()
        device_box.addWidget(CaptionLabel("计算设备"))
        self.device_combo = ComboBox()
        self.device_combo.addItems(["CPU", "GPU (CUDA)"])
        self.device_combo.setFixedWidth(150)
        device_box.addWidget(self.device_combo)
        config_layout.addLayout(device_box)
        config_layout.addStretch()
        root.addWidget(config_card)

        # 基础参数卡片
        basic_card = CardWidget(self)
        basic_card.setBorderRadius(12)
        basic_layout = QVBoxLayout(basic_card)
        basic_layout.setContentsMargins(20, 16, 20, 20)
        basic_layout.setSpacing(12)
        basic_layout.addWidget(_section_title("转录参数", FIF.SETTING, self))
        lang_task_row = QHBoxLayout()
        lang_task_row.setSpacing(20)
        lang_widget = QWidget()
        lang_layout = QVBoxLayout(lang_widget)
        lang_layout.setContentsMargins(0, 0, 0, 0)
        lang_layout.addWidget(CaptionLabel("源语言"))
        self.lang_combo = ComboBox()
        self.lang_combo.addItems(LANGUAGE_OPTIONS)
        self.lang_combo.setFixedWidth(140)
        lang_layout.addWidget(self.lang_combo)
        lang_task_row.addWidget(lang_widget)
        task_widget = QWidget()
        task_layout = QVBoxLayout(task_widget)
        task_layout.setContentsMargins(0, 0, 0, 0)
        task_layout.addWidget(CaptionLabel("任务类型"))
        self.task_combo = ComboBox()
        self.task_combo.addItems(TASK_OPTIONS)
        self.task_combo.setFixedWidth(150)
        task_layout.addWidget(self.task_combo)
        lang_task_row.addWidget(task_widget)
        lang_task_row.addStretch()
        basic_layout.addLayout(lang_task_row)
        fmt_widget = QWidget()
        fmt_layout = QHBoxLayout(fmt_widget)
        fmt_layout.setContentsMargins(0, 0, 0, 0)
        fmt_layout.addWidget(CaptionLabel("输出格式"))
        fmt_layout.addSpacing(10)
        self.fmt_combo = ComboBox()
        self.fmt_combo.addItems(OUTPUT_FORMATS)
        self.fmt_combo.setFixedWidth(150)
        fmt_layout.addWidget(self.fmt_combo)
        fmt_layout.addStretch()
        basic_layout.addWidget(fmt_widget)
        root.addWidget(basic_card)

        # ========== 高级参数：三个参数独立成组 ==========
        self.adv_card = ExpandGroupSettingCard(
            FIF.DEVELOPER_TOOLS,
            "高级参数",
            "调整转录的详细算法参数",
            parent=self
        )

        # 1. Beam size 独立组
        beam_widget = QWidget()
        beam_layout = QHBoxLayout(beam_widget)
        beam_layout.setContentsMargins(0, 0, 0, 0)
        beam_label = BodyLabel("Beam size")
        beam_label.setFixedWidth(100)
        self.beam_spin = SpinBox()
        self.beam_spin.setRange(1, 20)
        self.beam_spin.setValue(5)
        self.beam_spin.setFixedWidth(120)
        beam_layout.addWidget(beam_label)
        beam_layout.addWidget(self.beam_spin)
        beam_layout.addStretch()
        self.adv_card.addGroup(
            FIF.SEARCH,
            "集束搜索宽度 (Beam size)",
            "集束搜索时保留的候选路径数量，越大质量越高但速度越慢",
            beam_widget
        )

        # 2. Best of 独立组
        best_widget = QWidget()
        best_layout = QHBoxLayout(best_widget)
        best_layout.setContentsMargins(0, 0, 0, 0)
        best_label = BodyLabel("Best of")
        best_label.setFixedWidth(100)
        self.best_spin = SpinBox()
        self.best_spin.setRange(1, 10)
        self.best_spin.setValue(1)
        self.best_spin.setFixedWidth(120)
        best_layout.addWidget(best_label)
        best_layout.addWidget(self.best_spin)
        best_layout.addStretch()
        self.adv_card.addGroup(
            FIF.CLOSE,
            "最佳候选数 (Best of)",
            "采样时考虑的候选数量，与温度参数配合使用，1 表示仅使用温度采样",
            best_widget
        )

        # 3. Temperature 独立组
        temp_widget = QWidget()
        temp_layout = QHBoxLayout(temp_widget)
        temp_layout.setContentsMargins(0, 0, 0, 0)
        temp_label = BodyLabel("Temperature")
        temp_label.setFixedWidth(100)
        self.temp_slider = Slider(Qt.Orientation.Horizontal)
        self.temp_slider.setRange(0, 100)
        self.temp_slider.setValue(0)
        self.temp_slider.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.temp_val = BodyLabel("0.0")
        self.temp_val.setFixedWidth(30)
        self.temp_val.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        temp_layout.addWidget(temp_label)
        temp_layout.addWidget(self.temp_slider, 1)
        temp_layout.addWidget(self.temp_val)
        self.adv_card.addGroup(
            FIF.CAR,
            "采样温度 (Temperature)",
            "控制随机性，0 为贪心解码，越大结果越多样但可能出错",
            temp_widget
        )

        # 其他选项组
        other_widget = QWidget()
        other_layout = QHBoxLayout(other_widget)
        other_layout.setContentsMargins(0, 0, 0, 0)
        other_layout.setSpacing(20)
        self.word_timestamps_cb = CheckBox("词级别时间戳")
        self.condition_cb = CheckBox("使用初始提示")
        self.compression_cb = CheckBox("压缩率过滤")
        self.word_timestamps_cb.setChecked(False)
        self.condition_cb.setChecked(True)
        self.compression_cb.setChecked(False)
        other_layout.addWidget(self.word_timestamps_cb)
        other_layout.addWidget(self.condition_cb)
        other_layout.addWidget(self.compression_cb)
        other_layout.addStretch()
        self.adv_card.addGroup(
            FIF.FILTER,
            "其他选项",
            "额外控制转录行为",
            other_widget
        )
        root.addWidget(self.adv_card)

        # 进度条
        self._progress = ProgressBar(self)
        self._progress.setVisible(False)
        self._progress_label = CaptionLabel("")
        self._progress_label.setVisible(False)
        progress_layout = QVBoxLayout()
        progress_layout.addWidget(self._progress)
        progress_layout.addWidget(self._progress_label)
        root.addLayout(progress_layout)

        # 开始按钮
        self._start_btn = PrimaryPushButton("开始转录", self)
        self._start_btn.setFixedHeight(48)
        font = self._start_btn.font()
        font.setBold(True)
        font.setPointSize(12)
        self._start_btn.setFont(font)
        root.addWidget(self._start_btn)

        # 历史记录卡片
        self.history_card = CardWidget(self)
        self.history_card.setBorderRadius(12)
        history_layout = QVBoxLayout(self.history_card)
        history_layout.setContentsMargins(20, 16, 20, 20)
        history_layout.setSpacing(12)
        history_layout.addWidget(StrongBodyLabel("历史记录", self))
        self.history_container = QVBoxLayout()
        self.history_container.setSpacing(8)
        history_layout.addLayout(self.history_container)
        self.history_card.setVisible(False)
        root.addWidget(self.history_card)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(scroll)

    def _setup_input_card(self):
        layout = QVBoxLayout(self.input_card)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(8)
        title_layout = QHBoxLayout()
        title_layout.addWidget(IconWidget(FIF.MUSIC, self))
        title_layout.addWidget(StrongBodyLabel("输入音频/视频"))
        title_layout.addStretch()
        layout.addLayout(title_layout)
        self._input_tag = BodyLabel("未选择文件")
        self._input_tag.setWordWrap(True)
        self._input_tag.setStyleSheet("color: #8a8a8a;")
        tag_layout = QHBoxLayout()
        tag_layout.addWidget(self._input_tag, stretch=1)
        tag_layout.addStretch()
        layout.addLayout(tag_layout)
        self._file_btn = PushButton("浏览文件", self)
        self._file_btn.setFixedWidth(100)
        layout.addWidget(self._file_btn)
        self._multi_hint = CaptionLabel("支持多文件批量转录", self)
        self._multi_hint.setStyleSheet("color: #8a8a8a;")
        layout.addWidget(self._multi_hint)

    def _setup_output_card(self):
        layout = QVBoxLayout(self.output_card)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(8)
        title_layout = QHBoxLayout()
        title_layout.addWidget(IconWidget(FIF.FOLDER, self))
        title_layout.addWidget(StrongBodyLabel("输出目录"))
        title_layout.addStretch()
        layout.addLayout(title_layout)
        self._dir_tag = BodyLabel("未选择")
        self._dir_tag.setWordWrap(True)
        self._dir_tag.setStyleSheet("color: #8a8a8a;")
        tag_layout = QHBoxLayout()
        tag_layout.addWidget(self._dir_tag, stretch=1)
        tag_layout.addStretch()
        layout.addLayout(tag_layout)
        self._dir_btn = PushButton("选择目录", self)
        self._dir_btn.setFixedWidth(100)
        layout.addWidget(self._dir_btn)

    def _connect_signals(self):
        self._file_btn.clicked.connect(self._pick_input)
        self._dir_btn.clicked.connect(self._pick_output)
        self._start_btn.clicked.connect(self._on_start)
        self.temp_slider.valueChanged.connect(
            lambda v: self.temp_val.setText(f"{v/100:.1f}")
        )

    def _pick_input(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "选择音频/视频文件", "",
            "媒体文件 (*.mp3 *.wav *.flac *.m4a *.ogg *.mp4 *.avi *.mkv);;所有文件 (*)"
        )
        if paths:
            self._input_paths = paths
            count = len(paths)
            display = Path(paths[0]).name if count == 1 else f"{count} 个文件"
            self._input_tag.setText(display)
            self._input_tag.setToolTip("\n".join(paths))
            InfoBar.success("已选择文件", f"共 {count} 个文件",
                            duration=1500, parent=self)

    def _pick_output(self):
        path = QFileDialog.getExistingDirectory(self, "选择输出目录")
        if path:
            self._output_dir = path
            display = path if len(path) <= 40 else path[:37] + "..."
            self._dir_tag.setText(display)
            self._dir_tag.setToolTip(path)

    def _on_start(self):
        if not self._input_paths:
            InfoBar.warning("缺少输入文件", "请先选择音频或视频文件", parent=self)
            return
        if not self._output_dir:
            InfoBar.warning("缺少输出目录", "请选择输出目录", parent=self)
            return
        InfoBar.info("演示模式", "UI 演示版未集成 Whisper 后端，正式版可实现批量转录与字幕生成。",
                     parent=self, duration=3000)
        self._add_demo_history()

    def _add_demo_history(self):
        from datetime import datetime
        if not self._input_paths:
            return
        filename = Path(self._input_paths[0]).name
        timestamp = datetime.now().strftime("%H:%M:%S")
        item = CardWidget(self)
        item.setBorderRadius(8)
        item_layout = QHBoxLayout(item)
        item_layout.setContentsMargins(12, 8, 12, 8)
        item_layout.setSpacing(12)
        info_layout = QVBoxLayout()
        info_layout.addWidget(StrongBodyLabel(filename))
        info_layout.addWidget(CaptionLabel(f"演示任务于 {timestamp}"))
        item_layout.addLayout(info_layout, stretch=1)
        folder_btn = ToolButton(FIF.FOLDER, self)
        folder_btn.setFixedSize(32, 32)
        folder_btn.clicked.connect(
            lambda: self._open_output_folder(self._output_dir))
        item_layout.addWidget(folder_btn)
        self.history_container.insertWidget(0, item)
        if self.history_container.count() > 10:
            last = self.history_container.itemAt(10).widget()
            self.history_container.removeWidget(last)
            last.deleteLater()
        self.history_card.setVisible(True)

    def _open_output_folder(self, output_dir):
        if output_dir and os.path.exists(output_dir):
            QDesktopServices.openUrl(QUrl.fromLocalFile(output_dir))
        else:
            InfoBar.warning("目录不存在", "输出目录已被移动或删除", parent=self)


def main():
    setTheme(Theme.DARK)
    setThemeColor(ACCENT)
    app = QApplication(sys.argv)
    window = WhisperUIDemo()
    window.resize(1100, 800)
    window.setWindowTitle("Whisper 语音转文字工作站 (UI Demo)")
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
