# widgets/subpage/subpage_audiocraft.py
"""Audiocraft 工作站 —— MusicGen + AudioGen 两个 Tab。"""

import os
import re
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QDesktopServices, QTextCursor
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFileDialog, QSizePolicy, QStackedWidget,
)
from qfluentwidgets import (
    TitleLabel, BodyLabel, CaptionLabel, StrongBodyLabel,
    PrimaryPushButton, PushButton, ToolButton,
    ComboBox, Slider, SpinBox, DoubleSpinBox,
    ProgressBar, SmoothScrollArea, CardWidget, ExpandGroupSettingCard,
    IconWidget, InfoBar, FluentIcon as FIF, TextEdit, Pivot, PlainTextEdit,
)

from workers.audiocraft_worker import AudiocraftWorker
from utils import paths as _paths


# ---------------------------------------------------------------------------
# 共用：彩色日志 + 章节标题
# ---------------------------------------------------------------------------
class LogTextEdit(TextEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptRichText(True)
        self.setReadOnly(True)

    def append_colored(self, html_text: str):
        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self.setTextCursor(cursor)
        html_text = self._convert_urls_to_links(html_text)
        if "下载进度" in html_text:
            # 进度消息：覆盖当前行，避免下载进度刷屏（与 subpage_switch_pages 一致）
            cursor.movePosition(
                QTextCursor.MoveOperation.StartOfLine, QTextCursor.MoveMode.KeepAnchor)
            cursor.removeSelectedText()
            cursor.insertHtml(html_text)
        else:
            cursor.insertHtml(html_text + "<br>")
        self.ensureCursorVisible()

    def _convert_urls_to_links(self, text: str) -> str:
        url_pattern = r'(https?://[^\s<>"\'{}|\\^`\[\]]+)'

        def replace_url(match):
            url = match.group(1)
            display_url = url if len(url) <= 80 else url[:40] + "..." + url[-30:]
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
    lay.addWidget(StrongBodyLabel(text, row))
    lay.addStretch()
    return row


# ---------------------------------------------------------------------------
# 基类：MusicGen / AudioGen 共享的 UI 骨架
# ---------------------------------------------------------------------------
class _AudiocraftTabBase(QWidget):
    """两个 Tab 的共享框架：提示词卡 + 模型/设备卡 + 高级参数 + 日志 + 历史。"""

    task_name = ""              # "musicgen" or "audiogen"，子类填
    task_display = ""           # 中文名
    default_model = ""
    model_choices: list[str] = []
    section_icon = FIF.MUSIC
    supports_melody = False     # 仅 MusicGen-melody 用得上

    def __init__(self, parent=None, device_options: dict = None):
        super().__init__(parent)
        self.device_options = device_options or {}
        self._output_dir = _paths.output_dir("audiocraft")
        self._melody_path = ""
        self._worker = None
        self._setup_ui()
        self._connect_signals()

    # ------------------------------------------------------------------ API
    def get_params(self) -> dict:
        device_text = self._device_combo.currentText()
        device = device_text.split(" · ")[1] if " · " in device_text else "cpu"
        return {
            "task": self.task_name,
            "model": self.model_combo.currentText(),
            "device": device,
            "prompts": self.prompt_edit.toPlainText(),
            "melody": self._melody_path if self.supports_melody else "",
            "output": self._output_dir,
            "output_format": self.fmt_combo.currentText(),
            "duration": float(self.duration_spin.value()),
            "top_k": int(self.topk_spin.value()),
            "top_p": float(self.topp_spin.value()),
            "temperature": float(self.temp_spin.value()),
            "cfg_coef": float(self.cfg_spin.value()),
        }

    def set_running(self, running: bool):
        self._start_btn.setEnabled(not running)
        self._cancel_btn.setEnabled(running)
        for w in (
            self.prompt_edit, self.model_combo, self._device_combo,
            self.fmt_combo, self.duration_spin, self.topk_spin,
            self.topp_spin, self.temp_spin, self.cfg_spin,
            self._dir_btn,
        ):
            w.setEnabled(not running)
        if self.supports_melody:
            self._melody_btn.setEnabled(not running)
            self._melody_clear_btn.setEnabled(not running)

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

    def add_history_task(self, output_dir: str):
        timestamp = datetime.now().strftime("%H:%M:%S")
        first_prompt = next(
            (line for line in self.prompt_edit.toPlainText().splitlines() if line.strip()),
            "(无)"
        )
        item = CardWidget(self)
        item.setBorderRadius(8)
        lay = QHBoxLayout(item)
        lay.setContentsMargins(12, 8, 12, 8)
        lay.setSpacing(12)
        info = QVBoxLayout()
        info.addWidget(StrongBodyLabel(first_prompt[:80]))
        info.addWidget(CaptionLabel(f"{self.task_display} · {timestamp}"))
        lay.addLayout(info, stretch=1)
        folder_btn = ToolButton(FIF.FOLDER, self)
        folder_btn.setFixedSize(32, 32)
        folder_btn.clicked.connect(lambda: self._open_output_folder(output_dir))
        lay.addWidget(folder_btn)
        self.history_container.insertWidget(0, item)
        if self.history_container.count() > 10:
            last = self.history_container.itemAt(10).widget()
            self.history_container.removeWidget(last)
            last.deleteLater()
        self.history_card.setVisible(True)

    # ---------------------------------------------------------------- slots
    def _open_output_folder(self, output_dir):
        if output_dir and os.path.exists(output_dir):
            QDesktopServices.openUrl(QUrl.fromLocalFile(output_dir))
        else:
            InfoBar.warning("目录不存在", "输出目录已被移动或删除", parent=self)

    def _on_start_clicked(self):
        text = self.prompt_edit.toPlainText().strip()
        if not text:
            InfoBar.warning("缺少提示词", "请至少填写一条提示词", parent=self)
            return
        if self._worker and self._worker.isRunning():
            self._worker.cancel()
            self._worker.wait()

        params = self.get_params()
        self._log_text.clear()
        self.set_running(True)
        self.reset_progress()

        self._worker = AudiocraftWorker(params)
        self._worker.progress.connect(self.set_progress)
        self._worker.output.connect(self._log_text.append_colored)
        self._worker.finished.connect(self._on_worker_finished)
        self._worker.error.connect(self._on_worker_error)
        self._worker.start()

        self._log_text.append_colored(
            f'<span style="color:#4FC3F7;">🚀 开始 {self.task_display} 生成任务...</span>')

    def _on_cancel_clicked(self):
        if self._worker and self._worker.isRunning():
            self._worker.cancel()
            self._log_text.append_colored(
                '<span style="color:#FF9800;">⚠️ 用户取消了生成任务</span>')
            self.set_running(False)
            self.reset_progress()
            InfoBar.warning("已取消", "生成任务已被用户取消", parent=self)

    def _on_worker_finished(self, output_dir: str):
        self.set_progress(100, "完成！")
        self.reset_progress()
        self.set_running(False)
        self._log_text.append_colored(
            '<span style="color:#4CAF50;">✅ 生成完成！</span>')
        self.add_history_task(output_dir)
        InfoBar.success("生成完成", f"文件保存在 {output_dir}", parent=self)

    def _on_worker_error(self, error_msg: str):
        self.reset_progress()
        self.set_running(False)
        self._log_text.append_colored(
            f'<span style="color:#F44336;">❌ 错误: {error_msg}</span>')
        InfoBar.error("生成错误", error_msg, parent=self)

    def _pick_output(self):
        path = QFileDialog.getExistingDirectory(self, "选择输出目录", self._output_dir)
        if path:
            self._output_dir = path
            display = path if len(path) <= 60 else path[:30] + "..." + path[-25:]
            self._dir_tag.setText(display)
            self._dir_tag.setToolTip(path)

    def _pick_melody(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择旋律音频", "",
            "音频文件 (*.wav *.mp3 *.flac *.m4a *.ogg);;所有文件 (*)"
        )
        if path:
            self._melody_path = path
            self._melody_tag.setText(Path(path).name)
            self._melody_tag.setToolTip(path)

    def _clear_melody(self):
        self._melody_path = ""
        self._melody_tag.setText("未选择 (可选)")
        self._melody_tag.setToolTip("")

    # ----------------------------------------------------------------- UI
    def _setup_ui(self):
        scroll = SmoothScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet(
            "SmoothScrollArea { background: transparent; border: none; }")
        scroll.viewport().setStyleSheet("background: transparent;")

        container = QWidget()
        container.setObjectName(f"{self.task_name}Container")
        container.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        container.setStyleSheet(
            f"QWidget#{self.task_name}Container {{ background: transparent; }}")
        scroll.setWidget(container)

        root = QVBoxLayout(container)
        root.setContentsMargins(16, 12, 16, 16)
        root.setSpacing(20)

        # ---------------- 提示词卡 ----------------
        prompt_card = CardWidget(self)
        prompt_card.setBorderRadius(12)
        p_layout = QVBoxLayout(prompt_card)
        p_layout.setContentsMargins(20, 16, 20, 16)
        p_layout.setSpacing(8)
        p_layout.addWidget(_section_title("提示词 (Prompt)", self.section_icon, self))
        p_hint = CaptionLabel(
            "每行一条提示词，多行将批量生成。例如：8-bit upbeat chiptune；"
            "lo-fi hip hop with mellow piano"
        )
        p_hint.setStyleSheet("color: #8a8a8a;")
        p_layout.addWidget(p_hint)
        self.prompt_edit = PlainTextEdit(self)
        self.prompt_edit.setPlaceholderText("请输入提示词，每行一条…")
        self.prompt_edit.setMinimumHeight(110)
        p_layout.addWidget(self.prompt_edit)
        root.addWidget(prompt_card)

        # ---------------- 模型 + 设备 + 输出 卡 ----------------
        cfg_card = CardWidget(self)
        cfg_card.setBorderRadius(12)
        cfg_layout = QHBoxLayout(cfg_card)
        cfg_layout.setContentsMargins(20, 12, 20, 12)
        cfg_layout.setSpacing(20)

        model_box = QVBoxLayout()
        model_box.addWidget(CaptionLabel("模型"))
        self.model_combo = ComboBox()
        self.model_combo.addItems(self.model_choices)
        if self.default_model in self.model_choices:
            self.model_combo.setCurrentText(self.default_model)
        self.model_combo.setFixedWidth(180)
        model_box.addWidget(self.model_combo)
        cfg_layout.addLayout(model_box)

        device_box = QVBoxLayout()
        device_box.addWidget(CaptionLabel("计算设备"))
        self._device_combo = ComboBox()
        devices = [f"{n} · {i}" for n, i in self.device_options.items()]
        self._device_combo.addItems(devices)
        self._device_combo.setFixedWidth(200)
        device_box.addWidget(self._device_combo)
        cfg_layout.addLayout(device_box)

        fmt_box = QVBoxLayout()
        fmt_box.addWidget(CaptionLabel("输出格式"))
        self.fmt_combo = ComboBox()
        self.fmt_combo.addItems(["wav", "mp3"])
        self.fmt_combo.setFixedWidth(120)
        fmt_box.addWidget(self.fmt_combo)
        cfg_layout.addLayout(fmt_box)

        cfg_layout.addStretch()
        root.addWidget(cfg_card)

        # ---------------- 输出目录 (+ melody) ----------------
        out_card = CardWidget(self)
        out_card.setBorderRadius(12)
        out_layout = QVBoxLayout(out_card)
        out_layout.setContentsMargins(20, 16, 20, 16)
        out_layout.setSpacing(10)
        out_layout.addWidget(_section_title("输出目录", FIF.FOLDER, self))

        dir_row = QHBoxLayout()
        self._dir_tag = BodyLabel(self._output_dir, self)
        self._dir_tag.setStyleSheet("color: #8a8a8a;")
        self._dir_tag.setWordWrap(True)
        self._dir_btn = PushButton("选择目录", self)
        self._dir_btn.setFixedWidth(100)
        dir_row.addWidget(self._dir_tag, 1)
        dir_row.addWidget(self._dir_btn)
        out_layout.addLayout(dir_row)

        if self.supports_melody:
            out_layout.addSpacing(6)
            out_layout.addWidget(_section_title("旋律提示 (Melody, 可选)", FIF.MUSIC, self))
            mel_hint = CaptionLabel(
                "仅 MusicGen-melody 模型有效，会以这段旋律为条件生成。"
            )
            mel_hint.setStyleSheet("color: #8a8a8a;")
            out_layout.addWidget(mel_hint)
            mel_row = QHBoxLayout()
            self._melody_tag = BodyLabel("未选择 (可选)", self)
            self._melody_tag.setStyleSheet("color: #8a8a8a;")
            self._melody_btn = PushButton("浏览", self)
            self._melody_btn.setFixedWidth(80)
            self._melody_clear_btn = PushButton("清除", self)
            self._melody_clear_btn.setFixedWidth(80)
            mel_row.addWidget(self._melody_tag, 1)
            mel_row.addWidget(self._melody_btn)
            mel_row.addWidget(self._melody_clear_btn)
            out_layout.addLayout(mel_row)

        root.addWidget(out_card)

        # ---------------- 基础参数：时长 ----------------
        basic_card = CardWidget(self)
        basic_card.setBorderRadius(12)
        basic_layout = QVBoxLayout(basic_card)
        basic_layout.setContentsMargins(20, 16, 20, 20)
        basic_layout.setSpacing(16)
        basic_layout.addWidget(_section_title("基础参数", FIF.SETTING, self))

        dur_row = QHBoxLayout()
        dur_label = BodyLabel("生成时长 (秒)")
        dur_label.setFixedWidth(140)
        self.duration_spin = DoubleSpinBox()
        self.duration_spin.setRange(1.0, 120.0)
        self.duration_spin.setSingleStep(1.0)
        self.duration_spin.setDecimals(1)
        self.duration_spin.setValue(8.0)
        self.duration_spin.setFixedWidth(140)
        dur_hint = CaptionLabel(
            "MusicGen 默认 30s 上限；超过后内部用 extend_stride 拼接，质量可能下降"
        )
        dur_hint.setStyleSheet("color: #8a8a8a;")
        dur_row.addWidget(dur_label)
        dur_row.addWidget(self.duration_spin)
        dur_row.addSpacing(12)
        dur_row.addWidget(dur_hint)
        dur_row.addStretch()
        basic_layout.addLayout(dur_row)

        root.addWidget(basic_card)

        # ---------------- 高级参数 ----------------
        self.adv_card = ExpandGroupSettingCard(
            FIF.DEVELOPER_TOOLS,
            "采样参数",
            "调节随机性 / 条件强度，常用就改 temperature 和 cfg_coef",
            parent=self,
        )

        # top_k
        topk_w = QWidget()
        topk_l = QHBoxLayout(topk_w)
        topk_l.setContentsMargins(0, 0, 0, 0)
        tk_label = BodyLabel("Top-k")
        tk_label.setFixedWidth(100)
        self.topk_spin = SpinBox()
        self.topk_spin.setRange(0, 2000)
        self.topk_spin.setValue(250)
        self.topk_spin.setFixedWidth(120)
        topk_l.addWidget(tk_label)
        topk_l.addWidget(self.topk_spin)
        topk_l.addStretch()
        self.adv_card.addGroup(
            FIF.FILTER, "Top-k",
            "采样时只考虑概率最高的 K 个 token，0 表示不裁剪",
            topk_w,
        )

        # top_p
        topp_w = QWidget()
        topp_l = QHBoxLayout(topp_w)
        topp_l.setContentsMargins(0, 0, 0, 0)
        tp_label = BodyLabel("Top-p")
        tp_label.setFixedWidth(100)
        self.topp_spin = DoubleSpinBox()
        self.topp_spin.setRange(0.0, 1.0)
        self.topp_spin.setSingleStep(0.05)
        self.topp_spin.setDecimals(2)
        self.topp_spin.setValue(0.0)
        self.topp_spin.setFixedWidth(120)
        topp_l.addWidget(tp_label)
        topp_l.addWidget(self.topp_spin)
        topp_l.addStretch()
        self.adv_card.addGroup(
            FIF.FILTER, "Top-p (nucleus)",
            "累计概率截断；与 top_k 二选一启用即可，0 表示禁用",
            topp_w,
        )

        # temperature
        temp_w = QWidget()
        temp_l = QHBoxLayout(temp_w)
        temp_l.setContentsMargins(0, 0, 0, 0)
        t_label = BodyLabel("Temperature")
        t_label.setFixedWidth(100)
        self.temp_spin = DoubleSpinBox()
        self.temp_spin.setRange(0.1, 3.0)
        self.temp_spin.setSingleStep(0.05)
        self.temp_spin.setDecimals(2)
        self.temp_spin.setValue(1.0)
        self.temp_spin.setFixedWidth(120)
        temp_l.addWidget(t_label)
        temp_l.addWidget(self.temp_spin)
        temp_l.addStretch()
        self.adv_card.addGroup(
            FIF.CAR, "Temperature",
            "越大越随机；过高会失去结构，过低会单调",
            temp_w,
        )

        # cfg_coef
        cfg_w = QWidget()
        cfg_l = QHBoxLayout(cfg_w)
        cfg_l.setContentsMargins(0, 0, 0, 0)
        c_label = BodyLabel("CFG coef")
        c_label.setFixedWidth(100)
        self.cfg_spin = DoubleSpinBox()
        self.cfg_spin.setRange(1.0, 10.0)
        self.cfg_spin.setSingleStep(0.1)
        self.cfg_spin.setDecimals(2)
        self.cfg_spin.setValue(3.0)
        self.cfg_spin.setFixedWidth(120)
        cfg_l.addWidget(c_label)
        cfg_l.addWidget(self.cfg_spin)
        cfg_l.addStretch()
        self.adv_card.addGroup(
            FIF.PIN, "Classifier-Free Guidance",
            "提示词条件强度。常用 3.0；越大越贴提示词但更容易过饱和",
            cfg_w,
        )

        root.addWidget(self.adv_card)

        # ---------------- 进度条 ----------------
        self._progress = ProgressBar(self)
        self._progress.setVisible(False)
        self._progress_label = CaptionLabel("")
        self._progress_label.setVisible(False)
        prog_layout = QVBoxLayout()
        prog_layout.addWidget(self._progress)
        prog_layout.addWidget(self._progress_label)
        root.addLayout(prog_layout)

        # ---------------- 日志 ----------------
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

        # ---------------- 按钮 ----------------
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(12)
        self._start_btn = PrimaryPushButton(f"开始生成 ({self.task_display})", self)
        self._start_btn.setFixedHeight(48)
        f = self._start_btn.font(); f.setBold(True); f.setPointSize(12)
        self._start_btn.setFont(f)
        btn_layout.addWidget(self._start_btn, 1)

        self._cancel_btn = PushButton("终止生成", self)
        self._cancel_btn.setFixedHeight(48)
        self._cancel_btn.setEnabled(False)
        cf = self._cancel_btn.font(); cf.setBold(True); cf.setPointSize(12)
        self._cancel_btn.setFont(cf)
        self._cancel_btn.setStyleSheet("""
            PushButton {
                background-color: #F44336;
                color: white;
                border-radius: 6px;
            }
            PushButton:hover { background-color: #D32F2F; }
            PushButton:pressed { background-color: #C62828; }
            PushButton:disabled {
                background-color: #555555;
                color: #888888;
            }
        """)
        btn_layout.addWidget(self._cancel_btn, 1)
        root.addLayout(btn_layout)

        # ---------------- 历史 ----------------
        self.history_card = CardWidget(self)
        self.history_card.setBorderRadius(12)
        h_layout = QVBoxLayout(self.history_card)
        h_layout.setContentsMargins(20, 16, 20, 20)
        h_layout.setSpacing(12)
        h_layout.addWidget(StrongBodyLabel("历史记录", self))
        self.history_container = QVBoxLayout()
        self.history_container.setSpacing(8)
        h_layout.addLayout(self.history_container)
        self.history_card.setVisible(False)
        root.addWidget(self.history_card)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(scroll)

    def _connect_signals(self):
        self._dir_btn.clicked.connect(self._pick_output)
        self._start_btn.clicked.connect(self._on_start_clicked)
        self._cancel_btn.clicked.connect(self._on_cancel_clicked)
        if self.supports_melody:
            self._melody_btn.clicked.connect(self._pick_melody)
            self._melody_clear_btn.clicked.connect(self._clear_melody)


# ---------------------------------------------------------------------------
# MusicGen / AudioGen 子类
# ---------------------------------------------------------------------------
class MusicGenTab(_AudiocraftTabBase):
    task_name = "musicgen"
    task_display = "MusicGen"
    default_model = "small"
    model_choices = ["small", "medium", "large", "melody", "stereo-small", "stereo-medium"]
    section_icon = FIF.MUSIC
    supports_melody = True


class AudioGenTab(_AudiocraftTabBase):
    task_name = "audiogen"
    task_display = "AudioGen"
    default_model = "medium"
    model_choices = ["medium"]
    section_icon = FIF.MEGAPHONE if hasattr(FIF, "MEGAPHONE") else FIF.MUSIC
    supports_melody = False


# ---------------------------------------------------------------------------
# 顶层 AudiocraftWidget —— Pivot 两 Tab
# ---------------------------------------------------------------------------
class AudiocraftWidget(QWidget):
    """Audiocraft 工作站：MusicGen + AudioGen 双 Tab"""

    def __init__(self, parent=None, device_options: dict = None):
        super().__init__(parent)
        self.device_options = device_options or {}
        self._setup_ui()

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(32, 28, 32, 16)
        root.setSpacing(12)

        header = QHBoxLayout()
        icon = IconWidget(FIF.MUSIC, self)
        icon.setFixedSize(32, 32)
        header.addWidget(icon)
        header.addWidget(TitleLabel("Audiocraft 工作站", self))
        header.addStretch()
        root.addLayout(header)

        self.pivot = Pivot(self)
        self.stacked = QStackedWidget(self)

        self.musicgen_tab = MusicGenTab(self, device_options=self.device_options)
        self.audiogen_tab = AudioGenTab(self, device_options=self.device_options)

        self._add_pivot_item("musicgen", "MusicGen · 音乐生成", self.musicgen_tab)
        self._add_pivot_item("audiogen", "AudioGen · 音效生成", self.audiogen_tab)

        self.stacked.setCurrentWidget(self.musicgen_tab)
        self.pivot.setCurrentItem("musicgen")

        root.addWidget(self.pivot)
        root.addWidget(self.stacked, 1)

    def _add_pivot_item(self, route: str, text: str, widget: QWidget):
        self.stacked.addWidget(widget)
        self.pivot.addItem(
            routeKey=route,
            text=text,
            onClick=lambda *_args, w=widget: self.stacked.setCurrentWidget(w),
        )
