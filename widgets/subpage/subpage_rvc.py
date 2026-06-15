# widgets/subpage/subpage_rvc.py

import os
import re
from pathlib import Path
from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QDesktopServices, QTextCursor
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QFileDialog, QSizePolicy,
    QStackedWidget,
)

from qfluentwidgets import (
    TitleLabel, BodyLabel, CaptionLabel, StrongBodyLabel, SubtitleLabel,
    PrimaryPushButton, PushButton, ToolButton,
    ComboBox, Slider, SpinBox, DoubleSpinBox, CheckBox,
    ProgressBar, SmoothScrollArea, CardWidget, ExpandGroupSettingCard,
    IconWidget, InfoBar, FluentIcon as FIF, TextEdit, Pivot,
)

from workers.rvc_worker import RVCInferWorker, RVCRealtimeWorker


class LogTextEdit(TextEdit):
    """支持彩色文本和超链接的日志控件"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptRichText(True)
        self.setReadOnly(True)

    def append_colored(self, html_text: str):
        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self.setTextCursor(cursor)

        html_text = self._convert_urls_to_links(html_text)
        cursor.insertHtml(html_text + '<br>')
        self.ensureCursorVisible()

    def _convert_urls_to_links(self, text: str) -> str:
        url_pattern = r'(https?://[^\s<>"\'{}|\\^`\[\]]+)'

        def replace_url(match):
            url = match.group(1)
            display_url = url if len(
                url) <= 80 else url[:40] + '...' + url[-30:]
            return f'<a href="{url}" style="color:#4FC3F7; text-decoration:underline;">{display_url}</a>'

        return re.sub(url_pattern, replace_url, text)

    def mousePressEvent(self, event):
        cursor = self.cursorForPosition(event.pos())
        if cursor.charFormat().isAnchor():
            anchor = cursor.charFormat().anchorHref()
            if anchor:
                QDesktopServices.openUrl(QUrl(anchor))
                return
        super().mousePressEvent(event)


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


# =====================================================================
# 批量推理 Tab —— 原有 UI 整块搬入，后端改为 RVCInferWorker
# =====================================================================
class BatchInferTab(QWidget):
    def __init__(self, parent=None, device_options: dict = None):
        super().__init__(parent)
        self.device_options = device_options or {}
        self._input_paths = []
        self._output_dir = ""
        self._model_path = ""
        self._index_path = ""
        self._worker = None
        self._setup_ui()
        self._connect_signals()

    def get_params(self) -> dict:
        device_text = self._device_combo.currentText()
        if " · " in device_text:
            device = device_text.split(" · ")[1]
        else:
            device = "cpu"

        return {
            "input": self._input_paths,
            "output": self._output_dir,
            "model_path": self._model_path,
            "index_path": self._index_path,
            "device": device,
            "f0_method": self.f0_combo.currentText(),
            "transpose": self.transpose_spin.value(),
            "index_rate": self.index_rate_slider.value() / 100.0,
            "filter_radius": self.filter_radius_spin.value(),
            "resample_sr": int(self.resample_combo.currentText().split(" ")[0])
            if self.resample_combo.currentText() != "不重采样" else 0,
            "rms_mix_rate": self.rms_slider.value() / 100.0,
            "protect": self.protect_slider.value() / 100.0,
            "split_infer": self.split_infer_cb.isChecked(),
            "format": self.fmt_combo.currentText(),
        }

    def set_running(self, running: bool):
        self._start_btn.setEnabled(not running)
        self._cancel_btn.setEnabled(running)
        self._file_btn.setEnabled(not running)
        self._dir_btn.setEnabled(not running)
        self._model_btn.setEnabled(not running)
        self._index_btn.setEnabled(not running)
        self._device_combo.setEnabled(not running)
        self.f0_combo.setEnabled(not running)
        self.transpose_spin.setEnabled(not running)
        self.index_rate_slider.setEnabled(not running)
        self.filter_radius_spin.setEnabled(not running)
        self.resample_combo.setEnabled(not running)
        self.rms_slider.setEnabled(not running)
        self.protect_slider.setEnabled(not running)
        self.split_infer_cb.setEnabled(not running)
        self.fmt_combo.setEnabled(not running)

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

    def add_history_task(self, input_paths, output_dir):
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
        time_label = CaptionLabel(f"推理完成于 {timestamp}")
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
        if output_dir and os.path.exists(output_dir):
            QDesktopServices.openUrl(QUrl.fromLocalFile(output_dir))
        else:
            InfoBar.warning("目录不存在", "输出目录已被移动或删除", parent=self)

    def _on_start_clicked(self):
        if not self._input_paths:
            InfoBar.warning("缺少输入文件", "请先选择音频文件", parent=self)
            return
        if not self._output_dir:
            InfoBar.warning("缺少输出目录", "请选择输出目录", parent=self)
            return
        if not self._model_path:
            InfoBar.warning("缺少模型", "请选择 .pth 模型文件", parent=self)
            return

        if self._worker and self._worker.isRunning():
            self._worker.cancel()
            self._worker.wait()

        params = self.get_params()

        self._log_text.clear()
        self.set_running(True)
        self.reset_progress()

        self._worker = RVCInferWorker(params)
        self._worker.progress.connect(self.set_progress)
        self._worker.output.connect(self._log_text.append_colored)
        self._worker.finished.connect(self._on_worker_finished)
        self._worker.error.connect(self._on_worker_error)
        self._worker.start()

        self._log_text.append_colored(
            '<span style="color:#4FC3F7;">🚀 开始 RVC 批量推理任务...</span>')

    def _on_cancel_clicked(self):
        if self._worker and self._worker.isRunning():
            self._worker.cancel()
            self._log_text.append_colored(
                '<span style="color:#FF9800;">⚠️ 用户取消了推理任务</span>')
            self.set_running(False)
            self.reset_progress()
            InfoBar.warning("已取消", "推理任务已被用户取消", parent=self)

    def _on_worker_finished(self, output_dir: str):
        self.set_progress(100, "完成！")
        self.reset_progress()
        self.set_running(False)

        self._log_text.append_colored(
            '<span style="color:#4CAF50;">✅ 推理完成！</span>')

        self.add_history_task(self._input_paths, output_dir)

        InfoBar.success(
            "推理完成",
            f"文件保存在 {output_dir}",
            parent=self
        )

    def _on_worker_error(self, error_msg: str):
        self.reset_progress()
        self.set_running(False)
        self._log_text.append_colored(
            f'<span style="color:#F44336;">❌ 错误: {error_msg}</span>')
        InfoBar.error("推理错误", error_msg, parent=self)

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
        """)
        scroll.viewport().setStyleSheet("background: transparent;")

        container = QWidget()
        container.setObjectName("batchContainer")
        container.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        container.setStyleSheet(
            "QWidget#batchContainer { background: transparent; }")
        scroll.setWidget(container)

        root = QVBoxLayout(container)
        root.setContentsMargins(16, 12, 16, 16)
        root.setSpacing(20)

        # 输入输出
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

        # 模型与索引
        model_card = CardWidget(self)
        model_card.setBorderRadius(12)
        model_layout = QVBoxLayout(model_card)
        model_layout.setContentsMargins(20, 16, 20, 16)
        model_layout.setSpacing(12)
        model_layout.addWidget(_section_title("说话人模型", FIF.PEOPLE, self))

        pth_row = QHBoxLayout()
        pth_label = BodyLabel("模型 (.pth)")
        pth_label.setFixedWidth(100)
        self._model_tag = BodyLabel("未选择")
        self._model_tag.setStyleSheet("color: #8a8a8a;")
        self._model_btn = PushButton("浏览", self)
        self._model_btn.setFixedWidth(80)
        pth_row.addWidget(pth_label)
        pth_row.addWidget(self._model_tag, 1)
        pth_row.addWidget(self._model_btn)
        model_layout.addLayout(pth_row)

        idx_row = QHBoxLayout()
        idx_label = BodyLabel("索引 (.index)")
        idx_label.setFixedWidth(100)
        self._index_tag = BodyLabel("未选择 (可选)")
        self._index_tag.setStyleSheet("color: #8a8a8a;")
        self._index_btn = PushButton("浏览", self)
        self._index_btn.setFixedWidth(80)
        idx_row.addWidget(idx_label)
        idx_row.addWidget(self._index_tag, 1)
        idx_row.addWidget(self._index_btn)
        model_layout.addLayout(idx_row)

        root.addWidget(model_card)

        # 设备 + F0 + 输出格式
        config_card = CardWidget(self)
        config_card.setBorderRadius(12)
        config_layout = QHBoxLayout(config_card)
        config_layout.setContentsMargins(20, 12, 20, 12)

        device_box = QVBoxLayout()
        device_box.addWidget(CaptionLabel("计算设备"))
        self._device_combo = ComboBox()
        devices = []
        for drivername, driverindex in self.device_options.items():
            devices.append(f"{drivername} · {driverindex}")
        self._device_combo.addItems(devices)
        self._device_combo.setFixedWidth(200)
        device_box.addWidget(self._device_combo)
        config_layout.addLayout(device_box)

        config_layout.addSpacing(30)

        f0_box = QVBoxLayout()
        f0_box.addWidget(CaptionLabel("音高提取算法 (F0)"))
        self.f0_combo = ComboBox()
        # Applio 支持的 F0 算法（已移除 rmvpe+，Applio 不识别）
        self.f0_combo.addItems(["rmvpe", "crepe", "harvest", "pm"])
        self.f0_combo.setCurrentText("rmvpe")
        self.f0_combo.setFixedWidth(140)
        f0_box.addWidget(self.f0_combo)
        config_layout.addLayout(f0_box)

        config_layout.addSpacing(30)

        fmt_box = QVBoxLayout()
        fmt_box.addWidget(CaptionLabel("输出格式"))
        self.fmt_combo = ComboBox()
        self.fmt_combo.addItems(["wav", "flac", "mp3"])
        self.fmt_combo.setFixedWidth(120)
        fmt_box.addWidget(self.fmt_combo)
        config_layout.addLayout(fmt_box)

        config_layout.addStretch()
        root.addWidget(config_card)

        # 基础参数
        basic_card = CardWidget(self)
        basic_card.setBorderRadius(12)
        basic_layout = QVBoxLayout(basic_card)
        basic_layout.setContentsMargins(20, 16, 20, 20)
        basic_layout.setSpacing(16)
        basic_layout.addWidget(_section_title("基础参数", FIF.SETTING, self))

        trans_row = QHBoxLayout()
        trans_label = BodyLabel("变调 (半音)")
        trans_label.setFixedWidth(120)
        self.transpose_spin = SpinBox()
        self.transpose_spin.setRange(-24, 24)
        self.transpose_spin.setValue(0)
        self.transpose_spin.setFixedWidth(120)
        trans_hint = CaptionLabel("男→女约 +12，女→男约 -12")
        trans_hint.setStyleSheet("color: #8a8a8a;")
        trans_row.addWidget(trans_label)
        trans_row.addWidget(self.transpose_spin)
        trans_row.addSpacing(12)
        trans_row.addWidget(trans_hint)
        trans_row.addStretch()
        basic_layout.addLayout(trans_row)

        idx_row = QHBoxLayout()
        idx_rate_label = BodyLabel("检索特征占比")
        idx_rate_label.setFixedWidth(120)
        self.index_rate_slider = Slider(Qt.Orientation.Horizontal)
        self.index_rate_slider.setRange(0, 100)
        self.index_rate_slider.setValue(75)
        self.index_rate_slider.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.index_rate_val = BodyLabel("0.75")
        self.index_rate_val.setFixedWidth(40)
        self.index_rate_val.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        idx_row.addWidget(idx_rate_label)
        idx_row.addWidget(self.index_rate_slider, 1)
        idx_row.addWidget(self.index_rate_val)
        basic_layout.addLayout(idx_row)

        root.addWidget(basic_card)

        # 高级参数
        self.adv_card = ExpandGroupSettingCard(
            FIF.DEVELOPER_TOOLS,
            "高级参数",
            "调整推理的详细算法参数",
            parent=self
        )

        filter_widget = QWidget()
        filter_layout = QHBoxLayout(filter_widget)
        filter_layout.setContentsMargins(0, 0, 0, 0)
        filter_label = BodyLabel("Filter radius")
        filter_label.setFixedWidth(100)
        self.filter_radius_spin = SpinBox()
        self.filter_radius_spin.setRange(0, 7)
        self.filter_radius_spin.setValue(3)
        self.filter_radius_spin.setFixedWidth(120)
        filter_layout.addWidget(filter_label)
        filter_layout.addWidget(self.filter_radius_spin)
        filter_layout.addStretch()
        self.adv_card.addGroup(
            FIF.FILTER,
            "中值滤波半径 (Filter radius)",
            "对 harvest 提取的音高做中值滤波，>=3 可有效降噪",
            filter_widget
        )

        resample_widget = QWidget()
        resample_layout = QHBoxLayout(resample_widget)
        resample_layout.setContentsMargins(0, 0, 0, 0)
        resample_label = BodyLabel("Resample SR")
        resample_label.setFixedWidth(100)
        self.resample_combo = ComboBox()
        self.resample_combo.addItems(
            ["不重采样", "16000 Hz", "32000 Hz", "40000 Hz", "44100 Hz", "48000 Hz"])
        self.resample_combo.setFixedWidth(160)
        resample_layout.addWidget(resample_label)
        resample_layout.addWidget(self.resample_combo)
        resample_layout.addStretch()
        self.adv_card.addGroup(
            FIF.MUSIC,
            "后处理重采样率",
            "对输出做重采样，0 表示不重采样",
            resample_widget
        )

        rms_widget = QWidget()
        rms_layout = QHBoxLayout(rms_widget)
        rms_layout.setContentsMargins(0, 0, 0, 0)
        rms_label = BodyLabel("RMS mix rate")
        rms_label.setFixedWidth(100)
        self.rms_slider = Slider(Qt.Orientation.Horizontal)
        self.rms_slider.setRange(0, 100)
        self.rms_slider.setValue(25)
        self.rms_slider.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.rms_val = BodyLabel("0.25")
        self.rms_val.setFixedWidth(40)
        self.rms_val.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        rms_layout.addWidget(rms_label)
        rms_layout.addWidget(self.rms_slider, 1)
        rms_layout.addWidget(self.rms_val)
        self.adv_card.addGroup(
            FIF.VOLUME,
            "音量包络融合 (RMS mix)",
            "0 = 完全使用模型音量，1 = 完全沿用原音频音量",
            rms_widget
        )

        protect_widget = QWidget()
        protect_layout = QHBoxLayout(protect_widget)
        protect_layout.setContentsMargins(0, 0, 0, 0)
        protect_label = BodyLabel("Protect")
        protect_label.setFixedWidth(100)
        self.protect_slider = Slider(Qt.Orientation.Horizontal)
        self.protect_slider.setRange(0, 50)
        self.protect_slider.setValue(33)
        self.protect_slider.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.protect_val = BodyLabel("0.33")
        self.protect_val.setFixedWidth(40)
        self.protect_val.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        protect_layout.addWidget(protect_label)
        protect_layout.addWidget(self.protect_slider, 1)
        protect_layout.addWidget(self.protect_val)
        self.adv_card.addGroup(
            FIF.CERTIFICATE,
            "清辅音保护 (Protect)",
            "降低清辅音被替换为元音的概率，0.5 = 禁用，越小保护越强（增加推理耗时）",
            protect_widget
        )

        split_widget = QWidget()
        split_layout = QHBoxLayout(split_widget)
        split_layout.setContentsMargins(0, 0, 0, 0)
        self.split_infer_cb = CheckBox("启用分段推理")
        self.split_infer_cb.setChecked(False)
        split_layout.addWidget(self.split_infer_cb)
        split_layout.addStretch()
        self.adv_card.addGroup(
            FIF.CUT,
            "按静音分段推理 (Split infer)",
            "将长音频按静音切片后分别推理，可降低显存占用、避免长音频伪影",
            split_widget
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

        # 日志
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

        # 按钮
        button_layout = QHBoxLayout()
        button_layout.setSpacing(12)

        self._start_btn = PrimaryPushButton("开始推理", self)
        self._start_btn.setFixedHeight(48)
        font = self._start_btn.font()
        font.setBold(True)
        font.setPointSize(12)
        self._start_btn.setFont(font)
        button_layout.addWidget(self._start_btn, 1)

        self._cancel_btn = PushButton("终止推理", self)
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

        # 历史记录
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
        title_layout.addWidget(StrongBodyLabel("输入音频"))
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

        self._multi_hint = CaptionLabel("支持多文件批量推理", self)
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
        self._model_btn.clicked.connect(self._pick_model)
        self._index_btn.clicked.connect(self._pick_index)
        self._start_btn.clicked.connect(self._on_start_clicked)
        self._cancel_btn.clicked.connect(self._on_cancel_clicked)
        self.index_rate_slider.valueChanged.connect(
            lambda v: self.index_rate_val.setText(f"{v/100:.2f}")
        )
        self.rms_slider.valueChanged.connect(
            lambda v: self.rms_val.setText(f"{v/100:.2f}")
        )
        self.protect_slider.valueChanged.connect(
            lambda v: self.protect_val.setText(f"{v/100:.2f}")
        )

    def _pick_input(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "选择音频文件", "",
            "音频文件 (*.wav *.mp3 *.flac *.m4a *.ogg);;所有文件 (*)"
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

    def _pick_model(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择 RVC 模型 (.pth)", "", "RVC 模型 (*.pth);;所有文件 (*)"
        )
        if path:
            self._model_path = path
            display = Path(path).name
            self._model_tag.setText(display)
            self._model_tag.setToolTip(path)
            self._model_tag.setStyleSheet("color: #4CAF50;")

    def _pick_index(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择检索索引 (.index)", "", "检索索引 (*.index);;所有文件 (*)"
        )
        if path:
            self._index_path = path
            display = Path(path).name
            self._index_tag.setText(display)
            self._index_tag.setToolTip(path)
            self._index_tag.setStyleSheet("color: #4CAF50;")


# =====================================================================
# 实时变声 Tab
# =====================================================================
class RealtimeTab(QWidget):
    def __init__(self, parent=None, device_options: dict = None):
        super().__init__(parent)
        self.device_options = device_options or {}
        self._model_path = ""
        self._index_path = ""
        self._worker = None
        self._setup_ui()
        self._connect_signals()
        self._populate_audio_devices()

    def _setup_ui(self):
        scroll = SmoothScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("""
            SmoothScrollArea { background: transparent; border: none; }
        """)
        scroll.viewport().setStyleSheet("background: transparent;")

        container = QWidget()
        container.setObjectName("realtimeContainer")
        container.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        container.setStyleSheet(
            "QWidget#realtimeContainer { background: transparent; }")
        scroll.setWidget(container)

        root = QVBoxLayout(container)
        root.setContentsMargins(16, 12, 16, 16)
        root.setSpacing(20)

        # 模型卡片
        model_card = CardWidget(self)
        model_card.setBorderRadius(12)
        model_layout = QVBoxLayout(model_card)
        model_layout.setContentsMargins(20, 16, 20, 16)
        model_layout.setSpacing(12)
        model_layout.addWidget(_section_title("说话人模型", FIF.PEOPLE, self))

        pth_row = QHBoxLayout()
        pth_label = BodyLabel("模型 (.pth)")
        pth_label.setFixedWidth(100)
        self._model_tag = BodyLabel("未选择")
        self._model_tag.setStyleSheet("color: #8a8a8a;")
        self._model_btn = PushButton("浏览", self)
        self._model_btn.setFixedWidth(80)
        pth_row.addWidget(pth_label)
        pth_row.addWidget(self._model_tag, 1)
        pth_row.addWidget(self._model_btn)
        model_layout.addLayout(pth_row)

        idx_row = QHBoxLayout()
        idx_label = BodyLabel("索引 (.index)")
        idx_label.setFixedWidth(100)
        self._index_tag = BodyLabel("未选择 (可选)")
        self._index_tag.setStyleSheet("color: #8a8a8a;")
        self._index_btn = PushButton("浏览", self)
        self._index_btn.setFixedWidth(80)
        idx_row.addWidget(idx_label)
        idx_row.addWidget(self._index_tag, 1)
        idx_row.addWidget(self._index_btn)
        model_layout.addLayout(idx_row)

        root.addWidget(model_card)

        # 设备卡片
        device_card = CardWidget(self)
        device_card.setBorderRadius(12)
        dev_layout = QVBoxLayout(device_card)
        dev_layout.setContentsMargins(20, 16, 20, 16)
        dev_layout.setSpacing(12)
        dev_layout.addWidget(_section_title("音频设备", FIF.MICROPHONE, self))

        # 计算设备
        compute_row = QHBoxLayout()
        compute_label = BodyLabel("计算设备")
        compute_label.setFixedWidth(100)
        self._device_combo = ComboBox()
        for drivername, driverindex in self.device_options.items():
            self._device_combo.addItem(f"{drivername} · {driverindex}")
        self._device_combo.setFixedWidth(280)
        compute_row.addWidget(compute_label)
        compute_row.addWidget(self._device_combo)
        compute_row.addStretch()
        dev_layout.addLayout(compute_row)

        # 输入声卡
        in_row = QHBoxLayout()
        in_label = BodyLabel("输入声卡")
        in_label.setFixedWidth(100)
        self._input_combo = ComboBox()
        self._input_combo.setMinimumWidth(280)
        self._refresh_in_btn = ToolButton(FIF.SYNC, self)
        self._refresh_in_btn.setFixedSize(32, 32)
        self._refresh_in_btn.setToolTip("刷新声卡列表")
        in_row.addWidget(in_label)
        in_row.addWidget(self._input_combo, 1)
        in_row.addWidget(self._refresh_in_btn)
        dev_layout.addLayout(in_row)

        # 输出声卡
        out_row = QHBoxLayout()
        out_label = BodyLabel("输出声卡")
        out_label.setFixedWidth(100)
        self._output_combo = ComboBox()
        self._output_combo.setMinimumWidth(280)
        self._refresh_out_btn = ToolButton(FIF.SYNC, self)
        self._refresh_out_btn.setFixedSize(32, 32)
        self._refresh_out_btn.setToolTip("刷新声卡列表")
        out_row.addWidget(out_label)
        out_row.addWidget(self._output_combo, 1)
        out_row.addWidget(self._refresh_out_btn)
        dev_layout.addLayout(out_row)

        self._device_hint = CaptionLabel("提示: 使用虚拟声卡（如 VB-Cable）可把变声后的音频送入直播 / 通话软件", self)
        self._device_hint.setStyleSheet("color: #8a8a8a;")
        dev_layout.addWidget(self._device_hint)

        root.addWidget(device_card)

        # 推理参数卡片
        param_card = CardWidget(self)
        param_card.setBorderRadius(12)
        param_layout = QVBoxLayout(param_card)
        param_layout.setContentsMargins(20, 16, 20, 16)
        param_layout.setSpacing(12)
        param_layout.addWidget(_section_title("推理参数", FIF.SETTING, self))

        grid = QGridLayout()
        grid.setHorizontalSpacing(20)
        grid.setVerticalSpacing(12)

        grid.addWidget(BodyLabel("变调 (半音)"), 0, 0)
        self.pitch_spin = SpinBox()
        self.pitch_spin.setRange(-24, 24)
        self.pitch_spin.setValue(0)
        self.pitch_spin.setFixedWidth(120)
        grid.addWidget(self.pitch_spin, 0, 1)

        grid.addWidget(BodyLabel("F0 算法"), 0, 2)
        self.f0_combo = ComboBox()
        # Applio 实时模块仅支持 fcpe / rmvpe；fcpe 更轻量，推荐用于实时
        self.f0_combo.addItems(["fcpe", "rmvpe"])
        self.f0_combo.setCurrentText("fcpe")
        self.f0_combo.setFixedWidth(140)
        grid.addWidget(self.f0_combo, 0, 3)

        grid.addWidget(BodyLabel("块时长 (s)"), 1, 0)
        self.block_spin = DoubleSpinBox()
        self.block_spin.setRange(0.05, 2.0)
        self.block_spin.setSingleStep(0.05)
        # Applio 3.6.x 实时默认 ~0.15s 块，更小则 GPU 顶不住
        self.block_spin.setValue(0.15)
        self.block_spin.setDecimals(2)
        self.block_spin.setFixedWidth(120)
        grid.addWidget(self.block_spin, 1, 1)

        grid.addWidget(BodyLabel("交叉淡入 (s)"), 1, 2)
        self.crossfade_spin = DoubleSpinBox()
        self.crossfade_spin.setRange(0.01, 0.5)
        self.crossfade_spin.setSingleStep(0.01)
        self.crossfade_spin.setValue(0.05)
        self.crossfade_spin.setDecimals(2)
        self.crossfade_spin.setFixedWidth(120)
        grid.addWidget(self.crossfade_spin, 1, 3)

        grid.addWidget(BodyLabel("额外缓冲 (s)"), 2, 0)
        self.extra_spin = DoubleSpinBox()
        self.extra_spin.setRange(0.1, 5.0)
        self.extra_spin.setSingleStep(0.1)
        # Applio 实时默认 0.5s 即可，2.5s 会显著增加每帧推理量
        self.extra_spin.setValue(0.5)
        self.extra_spin.setDecimals(1)
        self.extra_spin.setFixedWidth(120)
        grid.addWidget(self.extra_spin, 2, 1)

        grid.addWidget(BodyLabel("Index rate"), 2, 2)
        self.index_rate_spin = DoubleSpinBox()
        self.index_rate_spin.setRange(0.0, 1.0)
        self.index_rate_spin.setSingleStep(0.05)
        self.index_rate_spin.setValue(0.75)
        self.index_rate_spin.setDecimals(2)
        self.index_rate_spin.setFixedWidth(120)
        grid.addWidget(self.index_rate_spin, 2, 3)

        grid.addWidget(BodyLabel("Protect"), 3, 0)
        self.protect_spin = DoubleSpinBox()
        self.protect_spin.setRange(0.0, 0.5)
        self.protect_spin.setSingleStep(0.01)
        self.protect_spin.setValue(0.33)
        self.protect_spin.setDecimals(2)
        self.protect_spin.setFixedWidth(120)
        grid.addWidget(self.protect_spin, 3, 1)

        grid.addWidget(BodyLabel("RMS mix"), 3, 2)
        self.rms_spin = DoubleSpinBox()
        self.rms_spin.setRange(0.0, 1.0)
        self.rms_spin.setSingleStep(0.05)
        self.rms_spin.setValue(0.25)
        self.rms_spin.setDecimals(2)
        self.rms_spin.setFixedWidth(120)
        grid.addWidget(self.rms_spin, 3, 3)

        grid.addWidget(BodyLabel("输入增益 (%)"), 4, 0)
        self.input_gain_spin = SpinBox()
        self.input_gain_spin.setRange(0, 400)
        self.input_gain_spin.setSingleStep(10)
        self.input_gain_spin.setValue(100)
        self.input_gain_spin.setFixedWidth(120)
        grid.addWidget(self.input_gain_spin, 4, 1)

        grid.addWidget(BodyLabel("输出增益 (%)"), 4, 2)
        self.output_gain_spin = SpinBox()
        self.output_gain_spin.setRange(0, 400)
        self.output_gain_spin.setSingleStep(10)
        self.output_gain_spin.setValue(150)
        self.output_gain_spin.setFixedWidth(120)
        grid.addWidget(self.output_gain_spin, 4, 3)

        param_layout.addLayout(grid)
        root.addWidget(param_card)

        # 状态 + 按钮
        action_row = QHBoxLayout()
        action_row.setSpacing(16)
        self._status_dot = BodyLabel("●", self)
        self._status_dot.setStyleSheet(
            "color: #555555; font-size: 18px;")
        self._status_label = BodyLabel("未启动", self)
        action_row.addWidget(self._status_dot)
        action_row.addWidget(self._status_label)
        action_row.addStretch()

        self._start_btn = PrimaryPushButton("开始变声", self)
        self._start_btn.setFixedHeight(40)
        self._start_btn.setMinimumWidth(120)
        action_row.addWidget(self._start_btn)

        self._stop_btn = PushButton("停止变声", self)
        self._stop_btn.setFixedHeight(40)
        self._stop_btn.setMinimumWidth(120)
        self._stop_btn.setEnabled(False)
        self._stop_btn.setStyleSheet("""
            PushButton {
                background-color: #F44336;
                color: white;
                border-radius: 6px;
            }
            PushButton:hover { background-color: #D32F2F; }
            PushButton:pressed { background-color: #C62828; }
            PushButton:disabled { background-color: #555555; color: #888888; }
        """)
        action_row.addWidget(self._stop_btn)
        root.addLayout(action_row)

        # 日志
        log_card = CardWidget(self)
        log_card.setBorderRadius(12)
        log_layout = QVBoxLayout(log_card)
        log_layout.setContentsMargins(16, 12, 16, 12)
        log_layout.setSpacing(8)
        log_layout.addWidget(_section_title("运行日志", FIF.HISTORY, self))
        self._log_text = LogTextEdit(self)
        self._log_text.setMinimumHeight(180)
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

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(scroll)

    def _connect_signals(self):
        self._model_btn.clicked.connect(self._pick_model)
        self._index_btn.clicked.connect(self._pick_index)
        self._refresh_in_btn.clicked.connect(self._populate_audio_devices)
        self._refresh_out_btn.clicked.connect(self._populate_audio_devices)
        self._start_btn.clicked.connect(self._on_start_clicked)
        self._stop_btn.clicked.connect(self._on_stop_clicked)

    def _populate_audio_devices(self):
        """枚举声卡。sounddevice 由 Applio 依赖带入，
        Applio 未安装时 import 会失败，给出友好提示。"""
        try:
            import sounddevice as sd
        except Exception as e:
            self._log_text.append_colored(
                f'<span style="color:#FF9800;">⚠️ 无法枚举声卡: {e}</span>')
            self._log_text.append_colored(
                '<span style="color:#FF9800;">请先完成 Applio 安装（依赖 sounddevice）</span>')
            return

        self._input_combo.clear()
        self._output_combo.clear()
        try:
            devices = sd.query_devices()
        except Exception as e:
            self._log_text.append_colored(
                f'<span style="color:#F44336;">声卡枚举失败: {e}</span>')
            return

        for idx, info in enumerate(devices):
            name = info.get("name", f"device {idx}")
            if info.get("max_input_channels", 0) > 0:
                self._input_combo.addItem(f"[{idx}] {name}", userData=idx)
            if info.get("max_output_channels", 0) > 0:
                self._output_combo.addItem(f"[{idx}] {name}", userData=idx)

        # 默认值
        try:
            default_in, default_out = sd.default.device
            for combo, default_idx in ((self._input_combo, default_in),
                                       (self._output_combo, default_out)):
                for i in range(combo.count()):
                    if combo.itemData(i) == default_idx:
                        combo.setCurrentIndex(i)
                        break
        except Exception:
            pass

    def _pick_model(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择 RVC 模型 (.pth)", "", "RVC 模型 (*.pth);;所有文件 (*)"
        )
        if path:
            self._model_path = path
            self._model_tag.setText(Path(path).name)
            self._model_tag.setToolTip(path)
            self._model_tag.setStyleSheet("color: #4CAF50;")

    def _pick_index(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择检索索引 (.index)", "", "检索索引 (*.index);;所有文件 (*)"
        )
        if path:
            self._index_path = path
            self._index_tag.setText(Path(path).name)
            self._index_tag.setToolTip(path)
            self._index_tag.setStyleSheet("color: #4CAF50;")

    def _selected_device(self) -> str:
        text = self._device_combo.currentText()
        if " · " in text:
            return text.split(" · ", 1)[1]
        return "cpu"

    def _set_running(self, running: bool):
        self._start_btn.setEnabled(not running)
        self._stop_btn.setEnabled(running)
        self._model_btn.setEnabled(not running)
        self._index_btn.setEnabled(not running)
        self._device_combo.setEnabled(not running)
        self._input_combo.setEnabled(not running)
        self._output_combo.setEnabled(not running)
        self._refresh_in_btn.setEnabled(not running)
        self._refresh_out_btn.setEnabled(not running)

    def _set_status(self, state: str):
        colors = {
            "stopped": ("#555555", "未启动"),
            "starting": ("#FF9800", "启动中..."),
            "running": ("#4CAF50", "运行中"),
            "error":   ("#F44336", "出错"),
        }
        color, label = colors.get(state, ("#555555", state))
        self._status_dot.setStyleSheet(
            f"color: {color}; font-size: 18px;")
        self._status_label.setText(label)

    def _on_start_clicked(self):
        if not self._model_path:
            InfoBar.warning("缺少模型", "请选择 .pth 模型文件", parent=self)
            return
        in_idx = self._input_combo.currentData()
        out_idx = self._output_combo.currentData()
        if in_idx is None or out_idx is None:
            InfoBar.warning("缺少声卡", "请选择输入和输出声卡（点刷新）", parent=self)
            return

        if self._worker and self._worker.isRunning():
            self._worker.stop()
            self._worker.wait()

        params = {
            "model_path":     self._model_path,
            "index_path":     self._index_path,
            "input_device":   int(in_idx),
            "output_device":  int(out_idx),
            "pitch":          self.pitch_spin.value(),
            "f0_method":      self.f0_combo.currentText(),
            "block_time":     self.block_spin.value(),
            "crossfade_time": self.crossfade_spin.value(),
            "extra_time":     self.extra_spin.value(),
            "index_rate":     self.index_rate_spin.value(),
            "protect":        self.protect_spin.value(),
            "rms_mix_rate":   self.rms_spin.value(),
            "input_gain":     self.input_gain_spin.value() / 100.0,
            "output_gain":    self.output_gain_spin.value() / 100.0,
            "device":         self._selected_device(),
        }

        self._log_text.clear()
        self._set_running(True)
        self._set_status("starting")

        self._worker = RVCRealtimeWorker(params)
        self._worker.status_changed.connect(self._on_status_changed)
        self._worker.output.connect(self._log_text.append_colored)
        self._worker.error.connect(self._on_worker_error)
        self._worker.finished.connect(self._on_worker_thread_finished)
        self._worker.start()

        self._log_text.append_colored(
            '<span style="color:#4FC3F7;">🎙️ 启动实时变声...</span>')

    def _on_stop_clicked(self):
        if self._worker and self._worker.isRunning():
            self._log_text.append_colored(
                '<span style="color:#FF9800;">⏹️ 正在停止...</span>')
            self._worker.stop()

    def _on_status_changed(self, state: str):
        self._set_status(state)
        if state == "running":
            InfoBar.success("已启动", "实时变声运行中", duration=2000, parent=self)
        elif state == "stopped":
            self._set_running(False)
        elif state == "error":
            self._set_running(False)

    def _on_worker_error(self, msg: str):
        self._set_status("error")
        self._set_running(False)
        self._log_text.append_colored(
            f'<span style="color:#F44336;">❌ {msg}</span>')
        InfoBar.error("实时变声错误", msg, parent=self)

    def _on_worker_thread_finished(self):
        # QThread.finished 是兜底
        if not self._stop_btn.isEnabled():
            return
        self._set_running(False)


# =====================================================================
# 模型训练 Tab —— 占位入口
# =====================================================================
class TrainingTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(40, 60, 40, 40)
        layout.setSpacing(20)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        icon = IconWidget(FIF.DEVELOPER_TOOLS, self)
        icon.setFixedSize(80, 80)
        layout.addWidget(icon, alignment=Qt.AlignmentFlag.AlignCenter)

        title = SubtitleLabel("RVC 模型训练 · 开发中", self)
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        desc = BodyLabel(
            "训练模块将基于 Applio core.py 的 preprocess / extract / train 三阶段流水线实现：\n"
            "1. 预处理：重采样到目标采样率、按静音切片\n"
            "2. 特征提取：F0 + 说话人 embedding\n"
            "3. 训练：GAN 主循环 + checkpoint 保存\n\n"
            "后续版本开放，敬请期待。",
            self
        )
        desc.setAlignment(Qt.AlignmentFlag.AlignCenter)
        desc.setWordWrap(True)
        desc.setStyleSheet("color: #8a8a8a;")
        layout.addWidget(desc)

        btn = PrimaryPushButton("开始训练", self)
        btn.setFixedSize(180, 44)
        btn.setEnabled(False)
        layout.addWidget(btn, alignment=Qt.AlignmentFlag.AlignCenter)

        layout.addStretch()


# =====================================================================
# 顶层 RVCWidget —— 3-Tab 容器
# =====================================================================
class RVCWidget(QWidget):
    """AI 变声 / 翻唱 工作站
    Tabs: 批量推理 · 实时变声 · 模型训练
    """

    def __init__(self, parent=None, device_options: dict = None):
        super().__init__(parent)
        self.device_options = device_options or {}
        self._setup_ui()

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(32, 28, 32, 16)
        root.setSpacing(12)

        # Header
        header = QHBoxLayout()
        icon_widget = IconWidget(FIF.ALBUM, self)
        icon_widget.setFixedSize(32, 32)
        title_label = TitleLabel("AI 变声 / 翻唱 工作站", self)
        header.addWidget(icon_widget)
        header.addWidget(title_label)
        header.addStretch()
        root.addLayout(header)

        # Pivot 切换条
        self.pivot = Pivot(self)
        self.stacked = QStackedWidget(self)

        self.batch_tab = BatchInferTab(self, device_options=self.device_options)
        self.realtime_tab = RealtimeTab(self, device_options=self.device_options)
        self.training_tab = TrainingTab(self)

        self._add_pivot_item("batch", "批量推理", self.batch_tab)
        self._add_pivot_item("realtime", "实时变声", self.realtime_tab)
        self._add_pivot_item("training", "模型训练", self.training_tab)

        self.stacked.setCurrentWidget(self.batch_tab)
        self.pivot.setCurrentItem("batch")

        root.addWidget(self.pivot)
        root.addWidget(self.stacked, 1)

    def _add_pivot_item(self, route: str, text: str, widget: QWidget):
        self.stacked.addWidget(widget)
        self.pivot.addItem(
            routeKey=route,
            text=text,
            onClick=lambda *_args, w=widget: self.stacked.setCurrentWidget(w),
        )
