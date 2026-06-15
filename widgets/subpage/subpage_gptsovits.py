# widgets/subpage/subpage_gptsovits.py

import os
import re
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QDesktopServices, QTextCursor
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFileDialog,
    QStackedWidget,
)

from qfluentwidgets import (
    TitleLabel, BodyLabel, CaptionLabel, StrongBodyLabel, SubtitleLabel,
    PrimaryPushButton, PushButton, ToolButton,
    ComboBox, SpinBox, DoubleSpinBox,
    ProgressBar, SmoothScrollArea, CardWidget, ExpandGroupSettingCard,
    IconWidget, InfoBar, FluentIcon as FIF, TextEdit, Pivot,
)

from workers.gptsovits_worker import GPTSoVITSInferWorker


# GPT-SoVITS inference_webui 内部 i18n 默认就是中文字符串
LANGUAGES = ["中文", "英文", "日文", "中英混", "日英混", "多语种混"]
CUT_METHODS = ["不切", "凑四句一切", "凑50字一切", "按中文句号切",
               "按英文句号切", "按标点符号切"]


class LogTextEdit(TextEdit):
    """支持彩色文本与超链接的日志控件"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptRichText(True)
        self.setReadOnly(True)

    def append_colored(self, html_text: str):
        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self.setTextCursor(cursor)
        html_text = self._convert_urls_to_links(html_text)
        cursor.insertHtml(html_text + "<br>")
        self.ensureCursorVisible()

    def _convert_urls_to_links(self, text: str) -> str:
        pattern = r'(https?://[^\s<>"\'{}|\\^`\[\]]+)'

        def repl(m):
            url = m.group(1)
            disp = url if len(url) <= 80 else url[:40] + "..." + url[-30:]
            return f'<a href="{url}" style="color:#4FC3F7; text-decoration:underline;">{disp}</a>'

        return re.sub(pattern, repl, text)

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
# TTS 推理 Tab
# =====================================================================
class TTSInferTab(QWidget):
    def __init__(self, parent=None, device_options: dict = None):
        super().__init__(parent)
        self.device_options = device_options or {}
        self._gpt_path = ""
        self._sovits_path = ""
        self._ref_audio_path = ""
        self._output_dir = ""
        self._worker = None
        self._setup_ui()
        self._connect_signals()

    # ------------------------------------------------------------------
    # 公共：参数 / 状态
    # ------------------------------------------------------------------
    def get_params(self) -> dict:
        device_text = self._device_combo.currentText()
        if " · " in device_text:
            device = device_text.split(" · ", 1)[1]
        else:
            device = "cpu"

        ref_text = self._ref_text_edit.toPlainText().strip()
        target_text = self._target_text_edit.toPlainText().strip()
        fmt = self.fmt_combo.currentText()

        out_dir = self._output_dir or os.path.join(os.getcwd(), "gptsovits_output")
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = os.path.join(out_dir, f"gptsovits_{ts}.{fmt}")

        return {
            "gpt_model":       self._gpt_path,
            "sovits_model":    self._sovits_path,
            "ref_audio":       self._ref_audio_path,
            "ref_text":        ref_text,
            "ref_language":    self.ref_lang_combo.currentText(),
            "target_text":     target_text,
            "target_language": self.target_lang_combo.currentText(),
            "output":          out_path,
            "how_to_cut":      self.cut_combo.currentText(),
            "top_k":           self.topk_spin.value(),
            "top_p":           self.topp_spin.value(),
            "temperature":     self.temp_spin.value(),
            "speed":           self.speed_spin.value(),
            "device":          device,
        }

    def set_running(self, running: bool):
        self._start_btn.setEnabled(not running)
        self._cancel_btn.setEnabled(running)
        for w in (self._gpt_btn, self._sovits_btn, self._ref_btn,
                  self._out_btn, self._device_combo,
                  self.ref_lang_combo, self.target_lang_combo, self.cut_combo,
                  self.fmt_combo, self.topk_spin, self.topp_spin,
                  self.temp_spin, self.speed_spin,
                  self._ref_text_edit, self._target_text_edit):
            w.setEnabled(not running)

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

    def add_history_task(self, target_text: str, output_path: str):
        snippet = target_text.strip().replace("\n", " ")
        if len(snippet) > 40:
            snippet = snippet[:40] + "..."
        timestamp = datetime.now().strftime("%H:%M:%S")

        item = CardWidget(self)
        item.setBorderRadius(8)
        layout = QHBoxLayout(item)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(12)

        info_layout = QVBoxLayout()
        info_layout.addWidget(StrongBodyLabel(snippet or "(无文本)"))
        info_layout.addWidget(CaptionLabel(f"合成于 {timestamp} → {Path(output_path).name}"))
        layout.addLayout(info_layout, stretch=1)

        folder_btn = ToolButton(FIF.FOLDER, self)
        folder_btn.setFixedSize(32, 32)
        folder_btn.clicked.connect(
            lambda: self._open_output_folder(os.path.dirname(output_path)))
        layout.addWidget(folder_btn)

        self.history_container.insertWidget(0, item)
        if self.history_container.count() > 10:
            last = self.history_container.itemAt(10).widget()
            self.history_container.removeWidget(last)
            last.deleteLater()
        self.history_card.setVisible(True)

    def _open_output_folder(self, output_dir: str):
        if output_dir and os.path.exists(output_dir):
            QDesktopServices.openUrl(QUrl.fromLocalFile(output_dir))
        else:
            InfoBar.warning("目录不存在", "输出目录已被移动或删除", parent=self)

    # ------------------------------------------------------------------
    # Worker 控制
    # ------------------------------------------------------------------
    def _on_start_clicked(self):
        if not self._gpt_path:
            InfoBar.warning("缺少 GPT 模型", "请选择 .ckpt 文件", parent=self)
            return
        if not self._sovits_path:
            InfoBar.warning("缺少 SoVITS 模型", "请选择 .pth 文件", parent=self)
            return
        if not self._ref_audio_path:
            InfoBar.warning("缺少参考音频", "请选择 3~10 秒的参考 wav", parent=self)
            return
        if not self._ref_text_edit.toPlainText().strip():
            InfoBar.warning("缺少参考文本", "请填写参考音频对应的文本", parent=self)
            return
        if not self._target_text_edit.toPlainText().strip():
            InfoBar.warning("缺少目标文本", "请填写要合成的文本", parent=self)
            return
        if not self._output_dir:
            InfoBar.warning("缺少输出目录", "请选择输出目录", parent=self)
            return

        if self._worker and self._worker.isRunning():
            self._worker.cancel()
            self._worker.wait()

        params = self.get_params()
        self._log_text.clear()
        self.set_running(True)
        self.reset_progress()
        self.set_progress(0, "准备中...")

        self._last_target_text = params["target_text"]
        self._worker = GPTSoVITSInferWorker(params)
        self._worker.progress.connect(self.set_progress)
        self._worker.output.connect(self._log_text.append_colored)
        self._worker.finished.connect(self._on_worker_finished)
        self._worker.error.connect(self._on_worker_error)
        self._worker.start()

        self._log_text.append_colored(
            '<span style="color:#4FC3F7;">🚀 启动 GPT-SoVITS TTS 合成...</span>')

    def _on_cancel_clicked(self):
        if self._worker and self._worker.isRunning():
            self._worker.cancel()
            self._log_text.append_colored(
                '<span style="color:#FF9800;">⚠️ 用户取消了合成任务</span>')
            self.set_running(False)
            self.reset_progress()
            InfoBar.warning("已取消", "合成任务已被用户取消", parent=self)

    def _on_worker_finished(self, output_path: str):
        self.set_progress(100, "完成！")
        self.reset_progress()
        self.set_running(False)
        self._log_text.append_colored(
            '<span style="color:#4CAF50;">✅ 合成完成</span>')
        self.add_history_task(self._last_target_text, output_path)
        InfoBar.success("合成完成", f"文件已保存:\n{output_path}", parent=self)

    def _on_worker_error(self, msg: str):
        self.reset_progress()
        self.set_running(False)
        self._log_text.append_colored(
            f'<span style="color:#F44336;">❌ 错误: {msg}</span>')
        InfoBar.error("合成错误", msg, parent=self)

    # ------------------------------------------------------------------
    # UI 搭建
    # ------------------------------------------------------------------
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
        container.setObjectName("ttsContainer")
        container.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        container.setStyleSheet(
            "QWidget#ttsContainer { background: transparent; }")
        scroll.setWidget(container)

        root = QVBoxLayout(container)
        root.setContentsMargins(16, 12, 16, 16)
        root.setSpacing(20)

        # --- 模型卡 ---
        model_card = CardWidget(self)
        model_card.setBorderRadius(12)
        m_lay = QVBoxLayout(model_card)
        m_lay.setContentsMargins(20, 16, 20, 16)
        m_lay.setSpacing(12)
        m_lay.addWidget(_section_title("说话人模型", FIF.PEOPLE, self))

        gpt_row = QHBoxLayout()
        gpt_label = BodyLabel("GPT (.ckpt)")
        gpt_label.setFixedWidth(110)
        self._gpt_tag = BodyLabel("未选择")
        self._gpt_tag.setStyleSheet("color: #8a8a8a;")
        self._gpt_btn = PushButton("浏览", self)
        self._gpt_btn.setFixedWidth(80)
        gpt_row.addWidget(gpt_label)
        gpt_row.addWidget(self._gpt_tag, 1)
        gpt_row.addWidget(self._gpt_btn)
        m_lay.addLayout(gpt_row)

        sov_row = QHBoxLayout()
        sov_label = BodyLabel("SoVITS (.pth)")
        sov_label.setFixedWidth(110)
        self._sovits_tag = BodyLabel("未选择")
        self._sovits_tag.setStyleSheet("color: #8a8a8a;")
        self._sovits_btn = PushButton("浏览", self)
        self._sovits_btn.setFixedWidth(80)
        sov_row.addWidget(sov_label)
        sov_row.addWidget(self._sovits_tag, 1)
        sov_row.addWidget(self._sovits_btn)
        m_lay.addLayout(sov_row)

        root.addWidget(model_card)

        # --- 参考音频卡 ---
        ref_card = CardWidget(self)
        ref_card.setBorderRadius(12)
        r_lay = QVBoxLayout(ref_card)
        r_lay.setContentsMargins(20, 16, 20, 16)
        r_lay.setSpacing(12)
        r_lay.addWidget(_section_title("参考音频（3~10 秒）", FIF.MICROPHONE, self))

        ra_row = QHBoxLayout()
        ra_label = BodyLabel("参考音频")
        ra_label.setFixedWidth(110)
        self._ref_tag = BodyLabel("未选择")
        self._ref_tag.setStyleSheet("color: #8a8a8a;")
        self._ref_btn = PushButton("浏览", self)
        self._ref_btn.setFixedWidth(80)
        ra_row.addWidget(ra_label)
        ra_row.addWidget(self._ref_tag, 1)
        ra_row.addWidget(self._ref_btn)
        r_lay.addLayout(ra_row)

        rl_row = QHBoxLayout()
        rl_label = BodyLabel("参考语种")
        rl_label.setFixedWidth(110)
        self.ref_lang_combo = ComboBox()
        self.ref_lang_combo.addItems(LANGUAGES)
        self.ref_lang_combo.setCurrentText("中文")
        self.ref_lang_combo.setFixedWidth(160)
        rl_row.addWidget(rl_label)
        rl_row.addWidget(self.ref_lang_combo)
        rl_row.addStretch()
        r_lay.addLayout(rl_row)

        rt_row = QVBoxLayout()
        rt_row.addWidget(BodyLabel("参考文本（与参考音频内容一致）"))
        self._ref_text_edit = TextEdit(self)
        self._ref_text_edit.setPlaceholderText("例如: 这是一段用于克隆音色的参考语音。")
        self._ref_text_edit.setFixedHeight(70)
        rt_row.addWidget(self._ref_text_edit)
        r_lay.addLayout(rt_row)

        root.addWidget(ref_card)

        # --- 目标文本卡 ---
        tgt_card = CardWidget(self)
        tgt_card.setBorderRadius(12)
        t_lay = QVBoxLayout(tgt_card)
        t_lay.setContentsMargins(20, 16, 20, 16)
        t_lay.setSpacing(12)
        t_lay.addWidget(_section_title("目标合成文本", FIF.EDIT, self))

        tl_row = QHBoxLayout()
        tl_label = BodyLabel("目标语种")
        tl_label.setFixedWidth(110)
        self.target_lang_combo = ComboBox()
        self.target_lang_combo.addItems(LANGUAGES)
        self.target_lang_combo.setCurrentText("中文")
        self.target_lang_combo.setFixedWidth(160)

        cut_label = BodyLabel("切分策略")
        cut_label.setFixedWidth(70)
        self.cut_combo = ComboBox()
        self.cut_combo.addItems(CUT_METHODS)
        self.cut_combo.setCurrentText("凑四句一切")
        self.cut_combo.setFixedWidth(160)

        tl_row.addWidget(tl_label)
        tl_row.addWidget(self.target_lang_combo)
        tl_row.addSpacing(20)
        tl_row.addWidget(cut_label)
        tl_row.addWidget(self.cut_combo)
        tl_row.addStretch()
        t_lay.addLayout(tl_row)

        self._target_text_edit = TextEdit(self)
        self._target_text_edit.setPlaceholderText("输入要合成的文本，长文本将按切分策略分段处理。")
        self._target_text_edit.setMinimumHeight(120)
        t_lay.addWidget(self._target_text_edit)

        root.addWidget(tgt_card)

        # --- 设备 + 输出 ---
        conf_card = CardWidget(self)
        conf_card.setBorderRadius(12)
        c_lay = QVBoxLayout(conf_card)
        c_lay.setContentsMargins(20, 16, 20, 16)
        c_lay.setSpacing(12)
        c_lay.addWidget(_section_title("运行配置", FIF.SETTING, self))

        d_row = QHBoxLayout()
        d_label = BodyLabel("计算设备")
        d_label.setFixedWidth(110)
        self._device_combo = ComboBox()
        for name, idx in self.device_options.items():
            self._device_combo.addItem(f"{name} · {idx}")
        self._device_combo.setFixedWidth(280)

        fmt_label = BodyLabel("输出格式")
        fmt_label.setFixedWidth(70)
        self.fmt_combo = ComboBox()
        self.fmt_combo.addItems(["wav", "flac"])
        self.fmt_combo.setFixedWidth(100)

        d_row.addWidget(d_label)
        d_row.addWidget(self._device_combo)
        d_row.addSpacing(20)
        d_row.addWidget(fmt_label)
        d_row.addWidget(self.fmt_combo)
        d_row.addStretch()
        c_lay.addLayout(d_row)

        o_row = QHBoxLayout()
        o_label = BodyLabel("输出目录")
        o_label.setFixedWidth(110)
        self._out_tag = BodyLabel("未选择")
        self._out_tag.setStyleSheet("color: #8a8a8a;")
        self._out_btn = PushButton("选择目录", self)
        self._out_btn.setFixedWidth(100)
        o_row.addWidget(o_label)
        o_row.addWidget(self._out_tag, 1)
        o_row.addWidget(self._out_btn)
        c_lay.addLayout(o_row)

        root.addWidget(conf_card)

        # --- 高级参数 ---
        self.adv_card = ExpandGroupSettingCard(
            FIF.DEVELOPER_TOOLS, "高级参数",
            "调节采样温度与解码细节", parent=self
        )

        topk_w = QWidget()
        topk_l = QHBoxLayout(topk_w)
        topk_l.setContentsMargins(0, 0, 0, 0)
        topk_l.addWidget(BodyLabel("top_k"))
        self.topk_spin = SpinBox()
        self.topk_spin.setRange(1, 100)
        self.topk_spin.setValue(15)
        self.topk_spin.setFixedWidth(120)
        topk_l.addStretch()
        topk_l.addWidget(self.topk_spin)
        self.adv_card.addGroup(FIF.FILTER, "Top-K", "采样候选数，越大越随机", topk_w)

        topp_w = QWidget()
        topp_l = QHBoxLayout(topp_w)
        topp_l.setContentsMargins(0, 0, 0, 0)
        topp_l.addWidget(BodyLabel("top_p"))
        self.topp_spin = DoubleSpinBox()
        self.topp_spin.setRange(0.1, 1.0)
        self.topp_spin.setSingleStep(0.05)
        self.topp_spin.setDecimals(2)
        self.topp_spin.setValue(1.0)
        self.topp_spin.setFixedWidth(120)
        topp_l.addStretch()
        topp_l.addWidget(self.topp_spin)
        self.adv_card.addGroup(FIF.FILTER, "Top-P", "核采样概率截断，1.0 = 不截断", topp_w)

        temp_w = QWidget()
        temp_l = QHBoxLayout(temp_w)
        temp_l.setContentsMargins(0, 0, 0, 0)
        temp_l.addWidget(BodyLabel("temperature"))
        self.temp_spin = DoubleSpinBox()
        self.temp_spin.setRange(0.1, 2.0)
        self.temp_spin.setSingleStep(0.05)
        self.temp_spin.setDecimals(2)
        self.temp_spin.setValue(1.0)
        self.temp_spin.setFixedWidth(120)
        temp_l.addStretch()
        temp_l.addWidget(self.temp_spin)
        self.adv_card.addGroup(FIF.FILTER, "Temperature", "采样温度，>1 更随机, <1 更稳定", temp_w)

        speed_w = QWidget()
        speed_l = QHBoxLayout(speed_w)
        speed_l.setContentsMargins(0, 0, 0, 0)
        speed_l.addWidget(BodyLabel("speed"))
        self.speed_spin = DoubleSpinBox()
        self.speed_spin.setRange(0.5, 2.0)
        self.speed_spin.setSingleStep(0.05)
        self.speed_spin.setDecimals(2)
        self.speed_spin.setValue(1.0)
        self.speed_spin.setFixedWidth(120)
        speed_l.addStretch()
        speed_l.addWidget(self.speed_spin)
        self.adv_card.addGroup(FIF.SPEED_HIGH, "语速", "合成音频的语速倍率", speed_w)

        root.addWidget(self.adv_card)

        # --- 进度 ---
        self._progress = ProgressBar(self)
        self._progress.setVisible(False)
        self._progress_label = CaptionLabel("")
        self._progress_label.setVisible(False)
        prog_layout = QVBoxLayout()
        prog_layout.addWidget(self._progress)
        prog_layout.addWidget(self._progress_label)
        root.addLayout(prog_layout)

        # --- 日志 ---
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

        # --- 按钮 ---
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(12)
        self._start_btn = PrimaryPushButton("开始合成", self)
        self._start_btn.setFixedHeight(48)
        font = self._start_btn.font()
        font.setBold(True)
        font.setPointSize(12)
        self._start_btn.setFont(font)
        btn_layout.addWidget(self._start_btn, 1)

        self._cancel_btn = PushButton("终止合成", self)
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
            PushButton:hover { background-color: #D32F2F; }
            PushButton:pressed { background-color: #C62828; }
            PushButton:disabled { background-color: #555555; color: #888888; }
        """)
        btn_layout.addWidget(self._cancel_btn, 1)
        root.addLayout(btn_layout)

        # --- 历史 ---
        self.history_card = CardWidget(self)
        self.history_card.setBorderRadius(12)
        h_lay = QVBoxLayout(self.history_card)
        h_lay.setContentsMargins(20, 16, 20, 20)
        h_lay.setSpacing(12)
        h_lay.addWidget(StrongBodyLabel("历史记录", self))
        self.history_container = QVBoxLayout()
        self.history_container.setSpacing(8)
        h_lay.addLayout(self.history_container)
        self.history_card.setVisible(False)
        root.addWidget(self.history_card)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(scroll)

    def _connect_signals(self):
        self._gpt_btn.clicked.connect(self._pick_gpt)
        self._sovits_btn.clicked.connect(self._pick_sovits)
        self._ref_btn.clicked.connect(self._pick_ref_audio)
        self._out_btn.clicked.connect(self._pick_output_dir)
        self._start_btn.clicked.connect(self._on_start_clicked)
        self._cancel_btn.clicked.connect(self._on_cancel_clicked)

    def _pick_gpt(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择 GPT 模型 (.ckpt)", "", "GPT 权重 (*.ckpt);;所有文件 (*)")
        if path:
            self._gpt_path = path
            self._gpt_tag.setText(Path(path).name)
            self._gpt_tag.setToolTip(path)
            self._gpt_tag.setStyleSheet("color: #4CAF50;")

    def _pick_sovits(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择 SoVITS 模型 (.pth)", "", "SoVITS 权重 (*.pth);;所有文件 (*)")
        if path:
            self._sovits_path = path
            self._sovits_tag.setText(Path(path).name)
            self._sovits_tag.setToolTip(path)
            self._sovits_tag.setStyleSheet("color: #4CAF50;")

    def _pick_ref_audio(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择参考音频", "",
            "音频文件 (*.wav *.mp3 *.flac);;所有文件 (*)")
        if path:
            self._ref_audio_path = path
            self._ref_tag.setText(Path(path).name)
            self._ref_tag.setToolTip(path)
            self._ref_tag.setStyleSheet("color: #4CAF50;")

    def _pick_output_dir(self):
        path = QFileDialog.getExistingDirectory(self, "选择输出目录")
        if path:
            self._output_dir = path
            display = path if len(path) <= 60 else path[:30] + "..." + path[-27:]
            self._out_tag.setText(display)
            self._out_tag.setToolTip(path)
            self._out_tag.setStyleSheet("color: #4CAF50;")


# =====================================================================
# 模型训练 Tab —— 占位
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

        title = SubtitleLabel("GPT-SoVITS 模型训练 · 开发中", self)
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        desc = BodyLabel(
            "训练模块将串联 GPT-SoVITS 的 1A/1B/1C 三阶段数据预处理 + GPT/SoVITS 两阶段训练：\n"
            "1A: 文本/特征/语义提取  1B: GPT 训练  1C: SoVITS 训练\n\n"
            "数据准备建议使用「音频分离 - Demucs」+「语音识别 - Whisper」组合。\n"
            "敬请期待。",
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
# 顶层 GPTSoVITSWidget
# =====================================================================
class GPTSoVITSWidget(QWidget):
    """GPT-SoVITS 工作站
    Tabs: TTS 推理 · 模型训练
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
        icon_widget = IconWidget(FIF.MEGAPHONE, self)
        icon_widget.setFixedSize(32, 32)
        title_label = TitleLabel("GPT-SoVITS 语音合成工作站", self)
        header.addWidget(icon_widget)
        header.addWidget(title_label)
        header.addStretch()
        root.addLayout(header)

        # Pivot
        self.pivot = Pivot(self)
        self.stacked = QStackedWidget(self)

        self.tts_tab = TTSInferTab(self, device_options=self.device_options)
        self.training_tab = TrainingTab(self)

        self._add_pivot_item("tts", "TTS 推理", self.tts_tab)
        self._add_pivot_item("training", "模型训练", self.training_tab)

        self.stacked.setCurrentWidget(self.tts_tab)
        self.pivot.setCurrentItem("tts")

        root.addWidget(self.pivot)
        root.addWidget(self.stacked, 1)

    def _add_pivot_item(self, route: str, text: str, widget: QWidget):
        self.stacked.addWidget(widget)
        self.pivot.addItem(
            routeKey=route, text=text,
            onClick=lambda *_args, w=widget: self.stacked.setCurrentWidget(w),
        )
