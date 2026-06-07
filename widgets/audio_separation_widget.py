import sys
from PyQt6.QtCore import Qt, pyqtSignal, QUrl
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QFileDialog
from PyQt6.QtGui import QFont, QDesktopServices
from qfluentwidgets import (
    BodyLabel, CaptionLabel, ToolButton, TitleLabel, StrongBodyLabel,
    PushButton, PrimaryPushButton, ComboBox, CheckBox,
    Slider, ProgressBar, CardWidget, SmoothScrollArea,
    setFont, FluentIcon as FIF, isDarkTheme,
    IconWidget, InfoBar, ExpandGroupSettingCard, SpinBox
)
from widgets.audio_waveform_widget import AudioWaveformWidget
import os


class AudioSeparationWidget(QWidget):
    separationRequested = pyqtSignal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        # self.setObjectName("audioSeparationInterface")
        self._input_path = ""
        self._output_dir = ""
        self._waveform_previews = []
        self._setup_ui()
        self._connect_signals()

    def _setup_ui(self):
        scroll = SmoothScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("""
    SmoothScrollArea {
        background: transparent;
        border: none;
    }
    QWidget#container {
        background: transparent;
    }
""")

        container = QWidget()
        container.setObjectName("container")
        scroll.setWidget(container)

        root = QVBoxLayout(container)
        root.setContentsMargins(32, 28, 32, 32)
        root.setSpacing(24)

        # 头部
        header = QHBoxLayout()
        icon_widget = IconWidget(FIF.MUSIC_FOLDER, self)
        icon_widget.setFixedSize(32, 32)
        title_label = TitleLabel("音频分离工作站", self)
        setFont(title_label, 24)
        header.addWidget(icon_widget)
        header.addWidget(title_label)
        header.addStretch()
        root.addLayout(header)

        desc = BodyLabel("使用 Demucs 模型将音乐分离成人声、鼓、贝斯和其他伴奏", self)
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
        model_box.addWidget(CaptionLabel("分离模型"))
        self._model_combo = ComboBox()
        self._model_combo.addItems(
            ["htdemucs (推荐)", "htdemucs_ft", "mdx_extra", "mdx"])
        self._model_combo.setCurrentText("htdemucs (推荐)")
        self._model_combo.setFixedWidth(180)
        model_box.addWidget(self._model_combo)
        config_layout.addLayout(model_box)

        config_layout.addSpacing(30)

        device_box = QVBoxLayout()
        device_box.addWidget(CaptionLabel("计算设备"))
        self._device_combo = ComboBox()
        self._device_combo.addItems(["cuda (NVIDIA GPU)", "cpu"])
        if sys.platform == "darwin":
            self._device_combo.addItem("mps (Apple Silicon)")
        self._device_combo.setCurrentText("cuda (NVIDIA GPU)")
        self._device_combo.setFixedWidth(180)
        device_box.addWidget(self._device_combo)
        config_layout.addLayout(device_box)

        config_layout.addStretch()
        root.addWidget(config_card)

        # 音轨选择
        tracks_card = CardWidget(self)
        tracks_card.setBorderRadius(12)
        tracks_layout = QVBoxLayout(tracks_card)
        tracks_layout.setContentsMargins(20, 16, 20, 20)

        tracks_layout.addWidget(StrongBodyLabel("输出音轨", self))
        tracks_grid = QHBoxLayout()
        tracks_grid.setSpacing(16)

        self._vocals_cb = CheckBox("人声", self)
        self._drums_cb = CheckBox("鼓", self)
        self._bass_cb = CheckBox("贝斯", self)
        self._other_cb = CheckBox("其他", self)
        for cb in (self._vocals_cb, self._drums_cb, self._bass_cb, self._other_cb):
            cb.setChecked(True)
            tracks_grid.addWidget(cb)

        tracks_layout.addLayout(tracks_grid)
        root.addWidget(tracks_card)

        # ========== 高级参数 - 使用 ExpandGroupSettingCard ==========
        self.adv_card = ExpandGroupSettingCard(
            FIF.DEVELOPER_TOOLS,
            "高级参数",
            "调整分离算法的详细参数 (移位量、分段长度等)",
            parent=self
        )

        # 第一组：移位量
        shifts_widget = QWidget()
        shifts_layout = QHBoxLayout(shifts_widget)
        shifts_layout.setContentsMargins(0, 0, 0, 0)
        shifts_layout.addWidget(BodyLabel("移位量 (shifts)"))
        shifts_layout.addStretch()
        self._shifts_sl = Slider(Qt.Orientation.Horizontal)
        self._shifts_sl.setRange(1, 8)
        self._shifts_sl.setValue(1)
        self._shifts_sl.setFixedWidth(200)
        self._shifts_val = BodyLabel("1")
        self._shifts_val.setFixedWidth(24)
        shifts_layout.addWidget(self._shifts_sl)
        shifts_layout.addWidget(self._shifts_val)
        shifts_layout.addWidget(BodyLabel("提高质量，降低速度"))
        self.adv_card.addGroup(
            FIF.SYNC,
            "移位量",
            "增大移位量可提高分离质量，但会降低处理速度",
            shifts_widget
        )

        # 第二组：分段长度
        seg_widget = QWidget()
        seg_layout = QHBoxLayout(seg_widget)
        seg_layout.setContentsMargins(0, 0, 0, 0)
        seg_layout.addWidget(BodyLabel("分段长度 (segment)"))
        seg_layout.addStretch()
        self._seg_sl = Slider(Qt.Orientation.Horizontal)
        self._seg_sl.setRange(1, 7)
        self._seg_sl.setValue(7)
        self._seg_sl.setFixedWidth(200)
        self._seg_val = BodyLabel("7")
        self._seg_val.setFixedWidth(24)
        seg_layout.addWidget(self._seg_sl)
        seg_layout.addWidget(self._seg_val)
        seg_layout.addWidget(BodyLabel("数值越大占用显存越多"))
        self.adv_card.addGroup(
            FIF.LAYOUT,
            "分段长度",
            "控制音频分段长度，影响显存占用",
            seg_widget
        )

        # 第三组：重叠率
        ov_widget = QWidget()
        ov_layout = QHBoxLayout(ov_widget)
        ov_layout.setContentsMargins(0, 0, 0, 0)
        ov_layout.addWidget(BodyLabel("重叠率 (overlap)"))
        ov_layout.addStretch()
        self._ov_sl = Slider(Qt.Orientation.Horizontal)
        self._ov_sl.setRange(0, 50)
        self._ov_sl.setValue(25)
        self._ov_sl.setFixedWidth(200)
        self._ov_val = BodyLabel("0.25")
        self._ov_val.setFixedWidth(32)
        ov_layout.addWidget(self._ov_sl)
        ov_layout.addWidget(self._ov_val)
        ov_layout.addWidget(BodyLabel("分段间重叠比例，过渡更平滑"))
        self.adv_card.addGroup(
            FIF.TRANSPARENT,
            "重叠率",
            "控制分段之间的重叠比例，提高平滑度",
            ov_widget
        )

        # 第四组：输出格式
        fmt_widget = QWidget()
        fmt_layout = QHBoxLayout(fmt_widget)
        fmt_layout.setContentsMargins(0, 0, 0, 0)
        fmt_layout.addWidget(BodyLabel("输出格式"))
        fmt_layout.addStretch()
        self._fmt_combo = ComboBox()
        self._fmt_combo.addItems(["wav (无损)", "flac (无损压缩)", "mp3 (320k)"])
        self._fmt_combo.setCurrentText("wav (无损)")
        self._fmt_combo.setFixedWidth(140)
        fmt_layout.addWidget(self._fmt_combo)
        fmt_layout.addWidget(BodyLabel("推荐 wav 保留最高质量"))
        self.adv_card.addGroup(
            FIF.DOCUMENT,
            "输出格式",
            "选择分离后音频的保存格式",
            fmt_widget
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
        self._start_btn = PrimaryPushButton("开始分离", self)
        self._start_btn.setFixedHeight(48)
        font = QFont()
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

        # 标题行
        title_layout = QHBoxLayout()
        title_layout.addWidget(StrongBodyLabel("历史记录", self))
        title_layout.addStretch()
        # 可选的清空按钮（暂不加）
        history_layout.addLayout(title_layout)

        # 历史任务容器
        self.history_container = QVBoxLayout()
        self.history_container.setSpacing(8)
        self.history_container.setContentsMargins(0, 0, 0, 0)
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
        title_layout.addWidget(StrongBodyLabel("输入音频"))
        title_layout.addStretch()
        layout.addLayout(title_layout)

        self._input_tag = BodyLabel("未选择文件")
        self._input_tag.setWordWrap(True)
        self._input_tag.setMaximumWidth(200)
        self._input_tag.setStyleSheet("color: #8a8a8a;")

        tag_layout = QHBoxLayout()
        tag_layout.addWidget(self._input_tag, stretch=1)
        tag_layout.addStretch()

        self._file_btn = PushButton("浏览文件", self)
        self._file_btn.setFixedWidth(100)

        layout.addLayout(tag_layout)
        layout.addWidget(self._file_btn)

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
        self._dir_tag.setMaximumWidth(200)
        self._dir_tag.setStyleSheet("color: #8a8a8a;")

        tag_layout = QHBoxLayout()
        tag_layout.addWidget(self._dir_tag, stretch=1)
        tag_layout.addStretch()

        self._dir_btn = PushButton("选择目录", self)
        self._dir_btn.setFixedWidth(100)

        layout.addLayout(tag_layout)
        layout.addWidget(self._dir_btn)

    def _connect_signals(self):
        self._file_btn.clicked.connect(self._pick_input)
        self._dir_btn.clicked.connect(self._pick_output)
        self._shifts_sl.valueChanged.connect(
            lambda v: self._shifts_val.setText(str(v)))
        self._seg_sl.valueChanged.connect(
            lambda v: self._seg_val.setText(str(v)))
        self._ov_sl.valueChanged.connect(
            lambda v: self._ov_val.setText(f"{v/100:.2f}"))
        self._start_btn.clicked.connect(self._on_start)

    def _pick_input(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择音频文件", "",
            "音频文件 (*.mp3 *.wav *.flac *.m4a *.ogg)")
        if path:
            self._input_path = path
            filename = path.split("/")[-1]
            # 如果文件名超过30个字符，截断并添加省略号
            if len(filename) > 30:
                display_name = filename[:27] + "..."
            else:
                display_name = filename
            self._input_tag.setText(display_name)
            self._input_tag.setToolTip(path)  # 鼠标悬停显示完整路径
            InfoBar.success("已选择文件", filename, duration=1500, parent=self)

    def _pick_output(self):
        path = QFileDialog.getExistingDirectory(self, "选择输出目录")
        if path:
            self._output_dir = path
            # 如果路径超过30个字符，截断并添加省略号
            if len(path) > 30:
                display_path = path[:27] + "..."
            else:
                display_path = path
            self._dir_tag.setText(display_path)
            self._dir_tag.setToolTip(path)

    def _on_start(self):
        if not self._input_path:
            InfoBar.warning("缺少输入文件", "请先选择音频文件", parent=self)
            return
        if not self._output_dir:
            InfoBar.warning("缺少输出目录", "请选择输出目录", parent=self)
            return
        self.separationRequested.emit(self.get_params())

    def get_params(self) -> dict:
        model_map = {
            "htdemucs (推荐)": "htdemucs",
            "htdemucs_ft": "htdemucs_ft",
            "mdx_extra": "mdx_extra",
            "mdx": "mdx"
        }
        device_text = self._device_combo.currentText()
        device = device_text.split()[0]
        fmt_text = self._fmt_combo.currentText()
        fmt = fmt_text.split()[0]
        segment_value = self._seg_sl.value()
        # if segment_value > 7.8:
        #     segment_value = 7
        segment_value = int(segment_value)
        return {
            "input": self._input_path,
            "output": self._output_dir,
            "model": model_map[self._model_combo.currentText()],
            "device": device,
            "tracks": {
                "vocals": self._vocals_cb.isChecked(),
                "drums": self._drums_cb.isChecked(),
                "bass": self._bass_cb.isChecked(),
                "other": self._other_cb.isChecked(),
            },
            "shifts": self._shifts_sl.value(),
            "segment": segment_value,
            "overlap": self._ov_sl.value() / 100,
            "format": fmt,
        }

    def set_progress(self, value: int, label: str = ""):
        if not self._progress.isVisible():
            self._progress.setVisible(True)
            self._progress_label.setVisible(True)
        self._progress.setValue(value)
        if label:
            self._progress_label.setText(label)

    def reset_progress(self):
        self._progress.setValue(0)
        self._progress.setVisible(False)
        self._progress_label.setVisible(False)

    def set_running(self, running: bool):
        self._start_btn.setEnabled(not running)
        self._file_btn.setEnabled(not running)
        self._dir_btn.setEnabled(not running)
        self._model_combo.setEnabled(not running)
        self._device_combo.setEnabled(not running)
        self._vocals_cb.setEnabled(not running)
        self._drums_cb.setEnabled(not running)
        self._bass_cb.setEnabled(not running)
        self._other_cb.setEnabled(not running)
        self._shifts_sl.setEnabled(not running)
        self._seg_sl.setEnabled(not running)
        self._ov_sl.setEnabled(not running)
        self._fmt_combo.setEnabled(not running)

    def add_history_task(self, input_path: str, output_dir: str):
        """添加一条历史任务记录"""
        from datetime import datetime
        import os

        # 提取文件名
        filename = os.path.basename(input_path)
        timestamp = datetime.now().strftime("%H:%M:%S")

        # 创建任务项卡片
        item = CardWidget(self)
        item.setBorderRadius(8)
        item_layout = QHBoxLayout(item)
        item_layout.setContentsMargins(12, 8, 12, 8)
        item_layout.setSpacing(12)

        # 信息区域
        info_layout = QVBoxLayout()
        info_layout.setSpacing(2)
        name_label = StrongBodyLabel(filename)
        name_label.setWordWrap(True)
        time_label = CaptionLabel(f"完成于 {timestamp}")
        info_layout.addWidget(name_label)
        info_layout.addWidget(time_label)
        item_layout.addLayout(info_layout, stretch=1)

        # 播放预览按钮
        play_btn = ToolButton(FIF.PLAY, self)
        play_btn.setFixedSize(32, 32)
        play_btn.setToolTip("打开波形预览")
        play_btn.clicked.connect(
            lambda: self._open_waveform_preview(output_dir))
        item_layout.addWidget(play_btn)

        # 文件夹按钮
        folder_btn = ToolButton(FIF.FOLDER, self)
        folder_btn.setFixedSize(32, 32)
        folder_btn.clicked.connect(
            lambda: self._open_output_folder(output_dir))
        item_layout.addWidget(folder_btn)

        # 添加到容器顶部（最新在上）
        self.history_container.insertWidget(0, item)

        # 限制最多显示10条，超出移除最后一条
        if self.history_container.count() > 10:
            last_item = self.history_container.itemAt(10).widget()
            self.history_container.removeWidget(last_item)
            last_item.deleteLater()

        # 显示历史卡片（如果之前隐藏）
        self.history_card.setVisible(True)

    def _open_output_folder(self, output_dir: str):
        """打开输出目录"""
        if output_dir and os.path.exists(output_dir):
            QDesktopServices.openUrl(QUrl.fromLocalFile(output_dir))
        else:
            InfoBar.warning("目录不存在", "输出目录已被移动或删除", parent=self)

    def _open_waveform_preview(self, output_dir: str):
        """打开波形预览窗口，默认不选取音频并将文件对话根目录设置为输出目录"""
        if not output_dir or not os.path.isdir(output_dir):
            InfoBar.warning("路径无效", "输出目录不存在或不是文件夹", parent=self)
            return

        preview = AudioWaveformWidget()
        preview.set_file_root_dir(output_dir)
        preview.disable_mic_recording()
        preview.show()
        self._waveform_previews.append(preview)
        preview.destroyed.connect(
            lambda _, p=preview: self._waveform_previews.remove(p)
        )

    def _on_start(self):
        if not self._input_path:
            InfoBar.warning("缺少输入文件", "请先选择音频文件", parent=self)
            return
        if not self._output_dir:
            InfoBar.warning("缺少输出目录", "请选择输出目录", parent=self)
            return

        # 发射信号，由主窗口处理
        self.separationRequested.emit(self.get_params())
