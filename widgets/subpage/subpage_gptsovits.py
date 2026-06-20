# widgets/subpage/subpage_gptsovits.py

import os
import re
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QDesktopServices, QTextCursor
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFileDialog,
    QStackedWidget, QDialog, QDialogButtonBox,
    QListWidget, QListWidgetItem, QAbstractItemView,
)

from qfluentwidgets import (
    TitleLabel, BodyLabel, CaptionLabel, StrongBodyLabel, SubtitleLabel,
    PrimaryPushButton, PushButton, ToolButton,
    ComboBox, SpinBox, DoubleSpinBox,
    ProgressBar, SmoothScrollArea, CardWidget, ExpandGroupSettingCard,
    IconWidget, InfoBar, FluentIcon as FIF, TextEdit, Pivot,
)

from utils import paths as _paths


# GPT-SoVITS .list 文件每行格式：vocal_path|speaker_name|language|text
# 语言代码 → UI 下拉框使用的中文名（dict_language_v2 的字面值）
_LIST_LANG_MAP = {
    "zh":  "中文",
    "en":  "英文",
    "ja":  "日文",
    "ko":  "韩文",
    "yue": "粤语",
    "all_zh":  "中文",
    "all_en":  "英文",
    "all_ja":  "日文",
    "all_ko":  "韩文",
    "all_yue": "粤语",
}


def parse_sovits_list_file(list_path: str) -> list[dict]:
    """解析 GPT-SoVITS 数据集 .list 文件。

    每行格式: ``vocal_path|speaker_name|language|text``

    - 字段不足 4 段或纯注释/空行会被跳过；
    - 路径若为相对路径，按 .list 所在目录解析；
    - **音频是否存在**只作为标记保留在 ``exists`` 字段里，不再过滤掉。
      让 UI 自己决定怎么提示缺文件的条目（一般是灰显不可选）。

    Returns:
        ``[{"audio": abs_path, "speaker": str, "lang": cn_name,
            "text": str, "exists": bool}, ...]``
    """
    entries: list[dict] = []
    base_dir = os.path.dirname(os.path.abspath(list_path))
    try:
        with open(list_path, "r", encoding="utf-8") as f:
            raw_lines = f.readlines()
    except UnicodeDecodeError:
        # 极少数 .list 是 GBK 编码（早期 Windows 工具产出）
        with open(list_path, "r", encoding="gbk", errors="ignore") as f:
            raw_lines = f.readlines()

    for line in raw_lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("|")
        if len(parts) < 4:
            continue
        audio_raw, speaker, lang_code, text = parts[0], parts[1], parts[2], "|".join(parts[3:])

        audio_path = audio_raw.strip().replace("\\", "/")
        if not os.path.isabs(audio_path):
            audio_path = os.path.normpath(os.path.join(base_dir, audio_path))

        entries.append({
            "audio":   audio_path,
            "speaker": speaker.strip(),
            "lang":    _LIST_LANG_MAP.get(lang_code.strip().lower(), ""),
            "text":    text.strip(),
            "exists":  os.path.isfile(audio_path),
        })
    return entries


class ListEntryPickerDialog(QDialog):
    """从 .list 文件多条参考音频中选若干条做音色融合。

    多选：Ctrl/Shift 多选或点「全选可用」按钮；首条作主参考（决定回填的
    参考文本/语种），其余作为辅助参考喂给 GPT-SoVITS 的 inp_refs。
    存在的条目正常显示并可选；找不到音频文件的条目灰显且不可选。

    左下角"重新选音频目录"按钮：
        .list 跨机器使用时音频绝对路径通常全部失效。
        点击后让用户选本地音频根目录，按 basename 重新匹配。
        匹配逻辑见 utils.sovits_list.remap_entries_audio_root。
    """

    def __init__(self, entries: list[dict], parent=None):
        super().__init__(parent)
        self.setWindowTitle("选择参考音频条目")
        self.resize(640, 460)

        # 拷贝一份 —— remap 时会替换 self._entries，不污染调用方传入的 list
        self._entries = list(entries)
        self._selected: list[dict] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        # 摘要 / 列表 做成成员变量，remap 后 _rebuild_list 直接更新
        self._summary_lbl = StrongBodyLabel("", self)
        layout.addWidget(self._summary_lbl)

        self._hint_lbl = CaptionLabel(
            "按住 Ctrl/Shift 多选；首条作主参考，其余作辅助参考做音色融合", self)
        self._hint_lbl.setStyleSheet("color: #8a8a8a;")
        layout.addWidget(self._hint_lbl)

        self._list = QListWidget(self)
        self._list.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection)
        self._list.setAlternatingRowColors(True)
        self._list.itemDoubleClicked.connect(self._on_accept)
        layout.addWidget(self._list, 1)

        # 按钮区：左侧"重新选音频目录" + "全选可用" 右侧 OK/Cancel
        # QDialogButtonBox 用 ResetRole 把自定义按钮放到左边，跨平台行为一致
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        self._remap_btn = PushButton("重新选音频目录", self)
        self._remap_btn.setToolTip(
            ".list 中的音频路径若全部失效（跨机器场景）\n"
            "选择本地音频根目录，按文件名重新匹配")
        buttons.addButton(
            self._remap_btn,
            QDialogButtonBox.ButtonRole.ResetRole,
        )
        self._select_all_btn = PushButton("全选可用", self)
        self._select_all_btn.setToolTip("一次性选中所有音频存在的条目")
        buttons.addButton(
            self._select_all_btn,
            QDialogButtonBox.ButtonRole.ResetRole,
        )
        self._remap_btn.clicked.connect(self._on_remap_clicked)
        self._select_all_btn.clicked.connect(self._on_select_all_clicked)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        # 首次渲染
        self._rebuild_list()

    def _rebuild_list(self):
        """按当前 self._entries 重建摘要 + 列表。remap 后也走这里，
        保证渲染逻辑只有一份。
        """
        self._list.clear()

        total   = len(self._entries)
        missing = sum(1 for e in self._entries if not e.get("exists", True))
        usable  = total - missing

        summary = f".list 共 {total} 条，可用 {usable} 条"
        if missing:
            summary += f"，缺失 {missing} 条（灰色不可选）"
        self._summary_lbl.setText(summary)

        first_usable = -1
        for i, e in enumerate(self._entries):
            text_preview = e["text"]
            if len(text_preview) > 60:
                text_preview = text_preview[:60] + "…"
            label = (f"[{e['speaker'] or '?'} · {e['lang'] or '?'}]  "
                     f"{text_preview}")
            if not e.get("exists", True):
                label = "⚠ 缺失  " + label
            item = QListWidgetItem(label)
            if e.get("exists", True):
                item.setToolTip(f"{e['audio']}\n\n{e['text']}")
                if first_usable < 0:
                    first_usable = i
            else:
                # 灰显 + 不可选，避免用户误选到无法读取的音频
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsSelectable
                                           & ~Qt.ItemFlag.ItemIsEnabled)
                item.setToolTip(f"音频文件不存在:\n{e['audio']}")
            self._list.addItem(item)
        if first_usable >= 0:
            self._list.setCurrentRow(first_usable)

    def _on_remap_clicked(self):
        """让用户选音频根目录，按 basename 重新匹配后刷新列表。"""
        audio_root = QFileDialog.getExistingDirectory(
            self, "选择音频根目录（按文件名重新匹配）", "")
        if not audio_root:
            return
        # 延迟 import：避免 subpage 顶层多一条依赖
        from utils.sovits_list import remap_entries_audio_root
        prev_usable = sum(1 for e in self._entries if e.get("exists"))
        new_entries = remap_entries_audio_root(self._entries, audio_root)
        new_usable = sum(1 for e in new_entries if e.get("exists"))
        self._entries = new_entries
        self._rebuild_list()
        InfoBar.success(
            "已重新匹配",
            f"按 {audio_root} 重匹配：可用 {prev_usable} → {new_usable}",
            parent=self,
        )

    def _on_select_all_clicked(self):
        """选中所有 exists=True 的条目；灰条目本就 ItemIsSelectable 已关，不会被选上。"""
        for i in range(self._list.count()):
            item = self._list.item(i)
            if item.flags() & Qt.ItemFlag.ItemIsSelectable:
                item.setSelected(True)

    def _on_accept(self, *_):
        # 双击会带入 itemDoubleClicked 信号的 QListWidgetItem 实参；
        # 双击灰条目时 selectedItems() 仍可能为空，单独走 currentItem() 兜底
        picked: list[dict] = []
        seen_rows: set[int] = set()
        for item in self._list.selectedItems():
            row = self._list.row(item)
            if row in seen_rows:
                continue
            seen_rows.add(row)
            if 0 <= row < len(self._entries):
                entry = self._entries[row]
                if entry.get("exists", True):
                    picked.append(entry)
        if not picked:
            # 例如双击灰条目：fallback 到 currentRow
            row = self._list.currentRow()
            if 0 <= row < len(self._entries):
                entry = self._entries[row]
                if entry.get("exists", True):
                    picked.append(entry)
        if picked:
            self._selected = picked
            self.accept()
            return
        # 没有有效选择就不关闭，让用户重新选
        self.reject()

    @property
    def selected(self) -> list[dict]:
        """返回用户选中的所有有效条目；首条作主参考。空列表代表未选/全无效。"""
        return self._selected

from workers.gptsovits_worker import GPTSoVITSInferWorker


# GPT-SoVITS inference_webui 内部 i18n 默认就是中文字符串
# 必须与 GPT-SoVITS inference_webui.dict_language_v2 / how_to_cut 的字面值一致，
# 否则推理时 dict_language[text_language] 会 KeyError，how_to_cut 会静默不切。
LANGUAGES = ["中文", "英文", "日文", "粤语", "韩文",
             "中英混合", "日英混合", "粤英混合", "韩英混合",
             "多语种混合", "多语种混合(粤语)"]
CUT_METHODS = ["不切", "凑四句一切", "凑50字一切", "按中文句号。切",
               "按英文句号.切", "按标点符号切"]


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
        # 多参考音频：约定第一项是主参考（喂 ref_wav_path + 决定文本/语种回填），
        # 其余作为 inp_refs 喂给 GPT-SoVITS 做音色融合（v3/v4 模型会被静默忽略）
        self._ref_audio_paths: list[str] = []
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

        out_dir = self._output_dir or _paths.output_dir("gptsovits")
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = os.path.join(out_dir, f"gptsovits_{ts}.{fmt}")

        # 主参考兜 ref_audio；辅助参考列表透传给 worker → runner → inp_refs
        main_ref = self._ref_audio_paths[0] if self._ref_audio_paths else ""
        aux_refs = list(self._ref_audio_paths[1:])

        return {
            "gpt_model":       self._gpt_path,
            "sovits_model":    self._sovits_path,
            "ref_audio":       main_ref,
            "aux_ref_audios":  aux_refs,
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
        if not self._ref_audio_paths:
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
        # getOpenFileNames 支持多选 wav/mp3/flac 做音色融合；
        # .list 走单选 → 解析 → 多选 dialog
        paths, _ = QFileDialog.getOpenFileNames(
            self, "选择参考音频（可多选）或 .list 数据集", "",
            "音频或数据集 (*.wav *.mp3 *.flac *.list);;"
            "音频文件 (*.wav *.mp3 *.flac);;"
            "数据集列表 (*.list);;所有文件 (*)")
        if not paths:
            return

        # .list 与音频混选意义不大；若用户挑了 .list 就只看第一份 .list
        list_files = [p for p in paths if p.lower().endswith(".list")]
        if list_files:
            new_paths = self._import_list_file(list_files[0])
            if not new_paths:
                return
        else:
            new_paths = paths

        self._ref_audio_paths = list(new_paths)
        self._refresh_ref_tag()

    def _import_list_file(self, path: str) -> list[str]:
        """解析 .list，弹多选 dialog，返回选中的音频路径列表（首条作主参考）。
        过程中顺手回填参考文本与参考语种。
        """
        try:
            entries = parse_sovits_list_file(path)
        except Exception as e:
            InfoBar.error("解析失败", f".list 解析出错: {e}", parent=self)
            return []
        if not entries:
            # 一行都没解析成功 —— 格式问题
            InfoBar.warning(
                "未找到可用条目",
                "该 .list 文件没有可识别的记录（字段格式不符）",
                parent=self,
            )
            return []
        if not any(e.get("exists") for e in entries):
            # 解析得到条目，但音频全都对不上路径 —— 弹窗里依然显示，
            # 同时给一条警告解释问题，避免用户陷入"看到列表却全灰"的困惑
            InfoBar.warning(
                "音频文件均不存在",
                f"解析到 {len(entries)} 条记录，但音频路径全部失效。"
                f"可点击对话框左下角\"重新选音频目录\"按 basename 重新匹配。",
                parent=self,
            )

        dlg = ListEntryPickerDialog(entries, parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted or not dlg.selected:
            return []
        picked = dlg.selected  # list[dict]
        # 首条决定回填的参考文本和参考语种（用户仍可手动改）
        first = picked[0]
        if first["text"]:
            self._ref_text_edit.setPlainText(first["text"])
        if first["lang"]:
            idx = self.ref_lang_combo.findText(first["lang"])
            if idx >= 0:
                self.ref_lang_combo.setCurrentIndex(idx)
        return [e["audio"] for e in picked]

    def _refresh_ref_tag(self):
        """按 self._ref_audio_paths 更新 _ref_tag 显示与 ToolTip。"""
        if not self._ref_audio_paths:
            self._ref_tag.setText("未选择")
            self._ref_tag.setToolTip("")
            self._ref_tag.setStyleSheet("color: #8a8a8a;")
            return
        main = self._ref_audio_paths[0]
        extra = len(self._ref_audio_paths) - 1
        label = Path(main).name + (f"（+{extra} 辅助参考）" if extra else "")
        self._ref_tag.setText(label)
        # ToolTip 一行一个绝对路径，方便用户校对
        self._ref_tag.setToolTip("\n".join(self._ref_audio_paths))
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
