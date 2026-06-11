# widgets/subpage/subpage_whisper.py

import sys
import os
import re
from pathlib import Path
from PyQt6.QtCore import Qt, QUrl, QSize
from PyQt6.QtGui import QDesktopServices, QTextCursor
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QFileDialog, QFrame, QSizePolicy

from qfluentwidgets import (
    TitleLabel, BodyLabel, CaptionLabel, StrongBodyLabel,
    PrimaryPushButton, PushButton, ToolButton,
    ComboBox, Slider, CheckBox, SpinBox,
    ProgressBar, SmoothScrollArea, CardWidget, ExpandGroupSettingCard,
    IconWidget, InfoBar, InfoBarPosition, FluentIcon as FIF, TextEdit,
)

from workers.whisper_worker import WhisperWorker


class LogTextEdit(TextEdit):
    """支持彩色文本和超链接的日志控件"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptRichText(True)
        self.setReadOnly(True)

    def append_colored(self, html_text: str):
        """添加彩色 HTML 文本到日志"""
        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self.setTextCursor(cursor)

        # 检测并转换 URL 为超链接
        html_text = self._convert_urls_to_links(html_text)

        # 插入 HTML 并换行
        cursor.insertHtml(html_text + '<br>')
        self.ensureCursorVisible()

    def _convert_urls_to_links(self, text: str) -> str:
        """将文本中的 URL 转换为可点击的超链接"""
        url_pattern = r'(https?://[^\s<>"\'{}|\\^`\[\]]+)'

        def replace_url(match):
            url = match.group(1)
            display_url = url if len(
                url) <= 80 else url[:40] + '...' + url[-30:]
            return f'<a href="{url}" style="color:#4FC3F7; text-decoration:underline;">{display_url}</a>'

        return re.sub(url_pattern, replace_url, text)

    def mousePressEvent(self, event):
        """处理超链接点击"""
        cursor = self.cursorForPosition(event.pos())
        if cursor.charFormat().isAnchor():
            anchor = cursor.charFormat().anchorHref()
            if anchor:
                QDesktopServices.openUrl(QUrl(anchor))
                return
        super().mousePressEvent(event)


class WhisperWidget(QWidget):
    """Whisper 语音转录 UI 组件（包含 Worker 调用逻辑）"""

    def __init__(self, parent=None, device_options: dict = {}):
        super().__init__(parent)
        self.device_options = device_options
        self._input_paths = []
        self._output_dir = ""
        self._worker = None
        self._setup_ui()
        self._connect_signals()

    def get_params(self) -> dict:
        """获取当前 UI 参数"""
        lang_text = self.lang_combo.currentText()
        if lang_text == "自动检测":
            language = None
        else:
            lang_map = {
                "中文": "zh",
                "英文": "en",
                "日文": "ja",
                "韩文": "ko",
                "法文": "fr",
                "德文": "de",
                "西班牙文": "es",
                "俄文": "ru",
            }
            language = lang_map.get(lang_text, None)

        task_text = self.task_combo.currentText()
        task = "translate" if task_text == "翻译成英文" else "transcribe"

        fmt_text = self.fmt_combo.currentText()
        if fmt_text == "所有格式":
            output_format = "all"
        elif fmt_text == "txt (纯文本)":
            output_format = "txt"
        elif fmt_text == "srt (字幕)":
            output_format = "srt"
        else:
            output_format = "vtt"

        # 解析设备（与 Demucs 逻辑一致）
        device_text = self._device_combo.currentText()
        # 格式: "GPU名称 · index" 或 "CPU · cpu"
        if " · " in device_text:
            device = device_text.split(" · ")[1]
        else:
            device = "cpu"

        return {
            "input": self._input_paths,
            "output": self._output_dir,
            "model": self.model_combo.currentText(),
            "device": device,
            "language": language,
            "task": task,
            "output_format": output_format,
            "beam_size": self.beam_spin.value(),
            "best_of": self.best_spin.value(),
            "temperature": self.temp_slider.value() / 100.0,
            "word_timestamps": self.word_timestamps_cb.isChecked(),
            "condition_on_previous_text": self.condition_cb.isChecked(),
        }

    def set_running(self, running: bool):
        """设置运行状态，禁用/启用控件"""
        self._start_btn.setEnabled(not running)
        self._cancel_btn.setEnabled(running)  # 运行时启用终止按钮
        self._file_btn.setEnabled(not running)
        self._dir_btn.setEnabled(not running)
        self.model_combo.setEnabled(not running)
        self._device_combo.setEnabled(not running)
        self.lang_combo.setEnabled(not running)
        self.task_combo.setEnabled(not running)
        self.fmt_combo.setEnabled(not running)
        self.beam_spin.setEnabled(not running)
        self.best_spin.setEnabled(not running)
        self.temp_slider.setEnabled(not running)
        self.word_timestamps_cb.setEnabled(not running)
        self.condition_cb.setEnabled(not running)

    def set_progress(self, value: int, label: str = ""):
        """更新进度条"""
        if not self._progress.isVisible():
            self._progress.setVisible(True)
            self._progress_label.setVisible(True)
        self._progress.setValue(value)
        if label:
            self._progress_label.setText(label)

    def reset_progress(self):
        """重置进度条"""
        self._progress.setValue(0)
        self._progress.setVisible(False)
        self._progress_label.setVisible(False)

    def add_history_task(self, input_paths, output_dir):
        """添加历史记录"""
        from datetime import datetime

        if not input_paths:
            return

        if isinstance(input_paths, list):
            filename = Path(input_paths[0]).name
            if len(input_paths) > 1:
                filename = f"{filename} 等 {len(input_paths)} 个文件"
        else:
            filename = Path(input_paths).name

        timestamp = datetime.now().strftime("%H:%M:%S")

        item = CardWidget(self)
        item.setBorderRadius(8)
        item_layout = QHBoxLayout(item)
        item_layout.setContentsMargins(12, 8, 12, 8)
        item_layout.setSpacing(12)

        info_layout = QVBoxLayout()
        name_label = StrongBodyLabel(filename)
        time_label = CaptionLabel(f"转录完成于 {timestamp}")
        info_layout.addWidget(name_label)
        info_layout.addWidget(time_label)
        item_layout.addLayout(info_layout, stretch=1)

        folder_btn = ToolButton(FIF.FOLDER, self)
        folder_btn.setFixedSize(32, 32)
        folder_btn.clicked.connect(
            lambda: self._open_output_folder(output_dir))
        item_layout.addWidget(folder_btn)

        self.history_container.insertWidget(0, item)
        if self.history_container.count() > 10:
            last = self.history_container.itemAt(10).widget()
            self.history_container.removeWidget(last)
            last.deleteLater()

        self.history_card.setVisible(True)

    def _open_output_folder(self, output_dir):
        """打开输出目录"""
        if output_dir and os.path.exists(output_dir):
            QDesktopServices.openUrl(QUrl.fromLocalFile(output_dir))
        else:
            InfoBar.warning("目录不存在", "输出目录已被移动或删除", parent=self)

    def _on_start_clicked(self):
        """点击开始按钮 - 启动 Worker"""
        if not self._input_paths:
            InfoBar.warning("缺少输入文件", "请先选择音频或视频文件", parent=self)
            return
        if not self._output_dir:
            InfoBar.warning("缺少输出目录", "请选择输出目录", parent=self)
            return

        if self._worker and self._worker.isRunning():
            self._worker.cancel()
            self._worker.wait()

        params = self.get_params()

        # 清空日志
        self._log_text.clear()

        self.set_running(True)
        self.reset_progress()

        self._worker = WhisperWorker(params)
        self._worker.progress.connect(self.set_progress)
        self._worker.output.connect(self._log_text.append_colored)
        self._worker.finished.connect(self._on_worker_finished)
        self._worker.error.connect(self._on_worker_error)
        self._worker.start()

        self._log_text.append_colored(
            '<span style="color:#4FC3F7;">🚀 开始转录任务...</span>')

    def _on_cancel_clicked(self):
        """点击终止按钮 - 取消任务"""
        if self._worker and self._worker.isRunning():
            self._worker.cancel()
            self._log_text.append_colored(
                '<span style="color:#FF9800;">⚠️ 用户取消了转录任务</span>')
            self.set_running(False)
            self.reset_progress()
            InfoBar.warning("已取消", "转录任务已被用户取消", parent=self)

    def _on_worker_finished(self, output_dir: str):
        """转录完成回调"""
        self.set_progress(100, "完成！")
        self.reset_progress()
        self.set_running(False)

        self._log_text.append_colored(
            '<span style="color:#4CAF50;">✅ 转录完成！</span>')

        self.add_history_task(self._input_paths, output_dir)

        InfoBar.success(
            "转录完成",
            f"文件保存在 {output_dir}",
            parent=self
        )

    def _on_worker_error(self, error_msg: str):
        """转录错误回调"""
        self.reset_progress()
        self.set_running(False)
        self._log_text.append_colored(
            f'<span style="color:#F44336;">❌ 错误: {error_msg}</span>')
        InfoBar.error("转录错误", error_msg, parent=self)

    def _setup_ui(self):
        """设置 UI"""
        scroll = SmoothScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("""
            SmoothScrollArea {
                background: transparent;
                border: none;
            }
        """)
        scroll.viewport().setStyleSheet("background: transparent;")

        container = QWidget()
        container.setObjectName("container")
        container.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        container.setStyleSheet(
            "QWidget#container { background: transparent; }")
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

        # 模型与设备卡片（与 Demucs 布局一致）
        config_card = CardWidget(self)
        config_card.setBorderRadius(12)
        config_layout = QHBoxLayout(config_card)
        config_layout.setContentsMargins(20, 12, 20, 12)

        model_box = QVBoxLayout()
        model_box.addWidget(CaptionLabel("Whisper 模型"))
        self.model_combo = ComboBox()
        self.model_combo.addItems(
            ["tiny", "base", "small", "medium", "large", "large-v3"])
        self.model_combo.setCurrentText("small")
        self.model_combo.setFixedWidth(180)
        model_box.addWidget(self.model_combo)
        config_layout.addLayout(model_box)

        config_layout.addSpacing(30)

        device_box = QVBoxLayout()
        device_box.addWidget(CaptionLabel("计算设备"))
        self._device_combo = ComboBox()
        # 与 Demucs 相同的设备显示逻辑
        devices = []
        for drivername, driverindex in self.device_options.items():
            devices.append(f"{drivername} · {driverindex}")
        self._device_combo.addItems(devices)
        self._device_combo.setFixedWidth(180)
        device_box.addWidget(self._device_combo)
        config_layout.addLayout(device_box)

        config_layout.addStretch()
        root.addWidget(config_card)

        # 基础参数卡片
        basic_card = CardWidget(self)
        basic_card.setBorderRadius(12)
        basic_layout = QVBoxLayout(basic_card)
        basic_layout.setContentsMargins(20, 16, 20, 20)
        basic_layout.setSpacing(12)

        basic_layout.addWidget(self._section_title("转录参数", FIF.SETTING, self))

        lang_task_row = QHBoxLayout()
        lang_task_row.setSpacing(20)

        lang_widget = QWidget()
        lang_layout = QVBoxLayout(lang_widget)
        lang_layout.setContentsMargins(0, 0, 0, 0)
        lang_layout.addWidget(CaptionLabel("源语言"))
        self.lang_combo = ComboBox()
        self.lang_combo.addItems(
            ["自动检测", "中文", "英文", "日文", "韩文", "法文", "德文", "西班牙文", "俄文"])
        self.lang_combo.setFixedWidth(140)
        lang_layout.addWidget(self.lang_combo)
        lang_task_row.addWidget(lang_widget)

        task_widget = QWidget()
        task_layout = QVBoxLayout(task_widget)
        task_layout.setContentsMargins(0, 0, 0, 0)
        task_layout.addWidget(CaptionLabel("任务类型"))
        self.task_combo = ComboBox()
        self.task_combo.addItems(["转录 (原始语言)", "翻译成英文"])
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
        self.fmt_combo.addItems(
            ["txt (纯文本)", "srt (字幕)", "vtt (网页字幕)", "所有格式"])
        self.fmt_combo.setFixedWidth(150)
        fmt_layout.addWidget(self.fmt_combo)
        fmt_layout.addStretch()
        basic_layout.addWidget(fmt_widget)

        root.addWidget(basic_card)

        # 高级参数
        self.adv_card = ExpandGroupSettingCard(
            FIF.DEVELOPER_TOOLS,
            "高级参数",
            "调整转录的详细算法参数",
            parent=self
        )

        # Beam size
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

        # Best of
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

        # Temperature
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

        # 日志区域
        log_card = CardWidget(self)
        log_card.setBorderRadius(12)
        log_layout = QVBoxLayout(log_card)
        log_layout.setContentsMargins(16, 12, 16, 12)
        log_layout.setSpacing(8)

        log_title = QHBoxLayout()
        log_title.addWidget(IconWidget(FIF.HISTORY, self))
        log_title.addWidget(StrongBodyLabel("运行日志", self))
        log_title.addStretch()
        log_layout.addLayout(log_title)

        self._log_text = LogTextEdit(self)
        self._log_text.setMinimumHeight(200)
        self._log_text.setStyleSheet("""
            LogTextEdit {
                background: rgba(0, 0, 0, 0.3);
                border-radius: 8px;
                font-family: monospace;
                font-size: 12px;
                padding: 8px;
            }
        """)
        log_layout.addWidget(self._log_text)

        root.addWidget(log_card)

        # 按钮区域（开始 + 终止）
        button_layout = QHBoxLayout()
        button_layout.setSpacing(12)

        # 开始按钮
        self._start_btn = PrimaryPushButton("开始转录", self)
        self._start_btn.setFixedHeight(48)
        font = self._start_btn.font()
        font.setBold(True)
        font.setPointSize(12)
        self._start_btn.setFont(font)
        button_layout.addWidget(self._start_btn, 1)

        # 终止按钮
        self._cancel_btn = PushButton("终止转录", self)
        self._cancel_btn.setFixedHeight(48)
        self._cancel_btn.setEnabled(False)
        cancel_font = self._cancel_btn.font()
        cancel_font.setBold(True)
        cancel_font.setPointSize(12)
        self._cancel_btn.setFont(cancel_font)
        self._cancel_btn.setStyleSheet("""
            PushButton {
                background-color: #F44336;
                color: white;
                border-radius: 6px;
            }
            PushButton:hover {
                background-color: #D32F2F;
            }
            PushButton:pressed {
                background-color: #C62828;
            }
            PushButton:disabled {
                background-color: #555555;
                color: #888888;
            }
        """)
        button_layout.addWidget(self._cancel_btn, 1)

        root.addLayout(button_layout)

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

    def _section_title(self, text: str, icon=None, parent=None):
        """创建章节标题"""
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

    def _setup_input_card(self):
        """设置输入卡片"""
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
        """设置输出卡片"""
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
        """连接信号"""
        self._file_btn.clicked.connect(self._pick_input)
        self._dir_btn.clicked.connect(self._pick_output)
        self._start_btn.clicked.connect(self._on_start_clicked)
        self._cancel_btn.clicked.connect(self._on_cancel_clicked)
        self.temp_slider.valueChanged.connect(
            lambda v: self.temp_val.setText(f"{v/100:.1f}")
        )

    def _pick_input(self):
        """选择输入文件"""
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
        """选择输出目录"""
        path = QFileDialog.getExistingDirectory(self, "选择输出目录")
        if path:
            self._output_dir = path
            display = path if len(path) <= 40 else path[:37] + "..."
            self._dir_tag.setText(display)
            self._dir_tag.setToolTip(path)
