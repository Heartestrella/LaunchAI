# widgets/subpage/subpage_gptsovits.py
"""
GPT-SoVITS 语音合成 — WinUI 风格 · 单页不滚动

结构 (仅推理; 训练已下架):
  head (title + subtitle + status pill)
  → voice rack (拖放, 主参考 R + 辅助 +)
  → 参考文本 + 语种
  → 目标文本 + 语种 + 切分
  → GPT / SoVITS / 输出目录 (紧凑单行)
  → 采样参数 (top_k · top_p · temperature · speed)
  → 运行参数 (device · format)
  → progress + start / cancel + history chips

不再包含独立日志控件, 状态用 pill 反馈,
错误经 InfoBar 弹出, GPTSoVITSInferWorker 的 output 信号忽略即可.
参数字典与 GPTSoVITSInferWorker 完全保持不变.
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices, QDragEnterEvent, QDropEvent
from PyQt6.QtWidgets import (
    QAbstractItemView, QDialog, QDialogButtonBox, QFileDialog,
    QGridLayout, QHBoxLayout, QListWidget, QListWidgetItem,
    QSizePolicy, QVBoxLayout, QWidget,
)

from qfluentwidgets import (
    BodyLabel, CaptionLabel, ComboBox, DoubleSpinBox, IconWidget,
    InfoBar, PrimaryPushButton, ProgressBar, PushButton,
    SpinBox, StrongBodyLabel, TextEdit, TitleLabel,
    ToolButton,
    FluentIcon as FIF,
)

from utils import paths as _paths
from workers.gptsovits_worker import GPTSoVITSInferWorker


# ---------------------------------------------------------------------------
# 常量 (LANGUAGES / CUT_METHODS 需要与 GPT-SoVITS.inference_webui 完全一致)
# ---------------------------------------------------------------------------
AUDIO_EXTS = {".mp3", ".wav", ".flac", ".m4a", ".ogg"}
LIST_EXT = ".list"

LANGUAGES = ["中文", "英文", "日文", "粤语", "韩文",
             "中英混合", "日英混合", "粤英混合", "韩英混合",
             "多语种混合", "多语种混合(粤语)"]
CUT_METHODS = ["不切", "凑四句一切", "凑50字一切", "按中文句号。切",
               "按英文句号.切", "按标点符号切"]

# .list 每行: vocal_path|speaker|language|text
_LIST_LANG_MAP = {
    "zh":  "中文",  "en":  "英文",  "ja":  "日文",  "ko":  "韩文",  "yue": "粤语",
    "all_zh":  "中文",  "all_en":  "英文",
    "all_ja":  "日文",  "all_ko":  "韩文",  "all_yue": "粤语",
}

# 与 demucs 页保持一致的状态色板
_STATUS_STYLES = {
    "idle":    ("● 就绪",   "#4CAF50", "rgba(76,175,80,32)"),
    "running": ("● 合成中", "#FFA726", "rgba(255,167,38,36)"),
    "done":    ("● 完成",   "#42A5F5", "rgba(66,165,245,36)"),
    "error":   ("● 出错",   "#F44336", "rgba(244,67,54,36)"),
}


# ---------------------------------------------------------------------------
# helpers (与 subpage_demucs._param_row 对齐)
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
# .list 解析 (原样保留)
# ---------------------------------------------------------------------------
def parse_sovits_list_file(list_path: str) -> list[dict]:
    entries: list[dict] = []
    base_dir = os.path.dirname(os.path.abspath(list_path))
    try:
        with open(list_path, "r", encoding="utf-8") as f:
            raw_lines = f.readlines()
    except UnicodeDecodeError:
        with open(list_path, "r", encoding="gbk", errors="ignore") as f:
            raw_lines = f.readlines()

    for line in raw_lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("|")
        if len(parts) < 4:
            continue
        audio_raw, speaker, lang_code, text = (
            parts[0], parts[1], parts[2], "|".join(parts[3:]))
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


# ---------------------------------------------------------------------------
# ListEntryPickerDialog (从 .list 多选; 保留原逻辑)
# ---------------------------------------------------------------------------
class ListEntryPickerDialog(QDialog):
    def __init__(self, entries: list[dict], parent=None):
        super().__init__(parent)
        self.setWindowTitle("选择参考音频条目")
        self.resize(640, 460)
        self._entries = list(entries)
        self._selected: list[dict] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        self._summary_lbl = StrongBodyLabel("", self)
        layout.addWidget(self._summary_lbl)

        self._hint_lbl = CaptionLabel(
            "按住 Ctrl/Shift 多选;首条作主参考,其余作辅助参考做音色融合", self)
        self._hint_lbl.setStyleSheet("color: #8a8a8a;")
        layout.addWidget(self._hint_lbl)

        self._list = QListWidget(self)
        self._list.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection)
        self._list.setAlternatingRowColors(True)
        self._list.itemDoubleClicked.connect(self._on_accept)
        layout.addWidget(self._list, 1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        self._remap_btn = PushButton("重新选音频目录", self)
        self._remap_btn.setToolTip(
            ".list 中的音频路径若全部失效(跨机器场景)\n"
            "选择本地音频根目录,按文件名重新匹配")
        buttons.addButton(self._remap_btn, QDialogButtonBox.ButtonRole.ResetRole)
        self._select_all_btn = PushButton("全选可用", self)
        buttons.addButton(self._select_all_btn, QDialogButtonBox.ButtonRole.ResetRole)
        self._remap_btn.clicked.connect(self._on_remap_clicked)
        self._select_all_btn.clicked.connect(self._on_select_all_clicked)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._rebuild_list()

    def _rebuild_list(self):
        self._list.clear()
        total = len(self._entries)
        missing = sum(1 for e in self._entries if not e.get("exists", True))
        usable = total - missing
        summary = f".list 共 {total} 条,可用 {usable} 条"
        if missing:
            summary += f",缺失 {missing} 条(灰色不可选)"
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
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsSelectable
                                           & ~Qt.ItemFlag.ItemIsEnabled)
                item.setToolTip(f"音频文件不存在:\n{e['audio']}")
            self._list.addItem(item)
        if first_usable >= 0:
            self._list.setCurrentRow(first_usable)

    def _on_remap_clicked(self):
        audio_root = QFileDialog.getExistingDirectory(
            self, "选择音频根目录(按文件名重新匹配)", "")
        if not audio_root:
            return
        from utils.sovits_list import remap_entries_audio_root
        prev_usable = sum(1 for e in self._entries if e.get("exists"))
        new_entries = remap_entries_audio_root(self._entries, audio_root)
        new_usable = sum(1 for e in new_entries if e.get("exists"))
        self._entries = new_entries
        self._rebuild_list()
        InfoBar.success(
            "已重新匹配",
            f"按 {audio_root} 重匹配:可用 {prev_usable} → {new_usable}",
            parent=self,
        )

    def _on_select_all_clicked(self):
        for i in range(self._list.count()):
            item = self._list.item(i)
            if item.flags() & Qt.ItemFlag.ItemIsSelectable:
                item.setSelected(True)

    def _on_accept(self, *_):
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
            row = self._list.currentRow()
            if 0 <= row < len(self._entries):
                entry = self._entries[row]
                if entry.get("exists", True):
                    picked.append(entry)
        if picked:
            self._selected = picked
            self.accept()
            return
        self.reject()

    @property
    def selected(self) -> list[dict]:
        return self._selected


# ---------------------------------------------------------------------------
# StatusPill (与 subpage_demucs 一致)
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
# VoiceSlot — 单条参考音频. R=主参考, +=辅助参考 (WinUI 淡色)
# ---------------------------------------------------------------------------
class VoiceSlot(QWidget):
    removed = pyqtSignal(object)  # emits self

    def __init__(self, path: str, kind: str = "primary", parent=None):
        super().__init__(parent)
        self.path = path
        self.kind = kind  # "primary" | "aux"
        self.setFixedHeight(36)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(4, 0, 4, 0)
        lay.setSpacing(10)

        self._chip = BodyLabel("R" if kind == "primary" else "+", self)
        self._chip.setFixedSize(24, 24)
        self._chip.setAlignment(Qt.AlignmentFlag.AlignCenter)
        f = self._chip.font()
        f.setBold(True)
        self._chip.setFont(f)
        lay.addWidget(self._chip)

        self._name = BodyLabel(Path(path).name, self)
        self._name.setToolTip(path)
        lay.addWidget(self._name, 1)

        role = CaptionLabel(
            "主参考 · 决定音色" if kind == "primary" else "辅助 · 音色融合", self)
        role.setStyleSheet("color: #8a8a8a;")
        self._role = role
        lay.addWidget(role)

        size_lbl = CaptionLabel(self._size_str(), self)
        size_lbl.setStyleSheet("color: #8a8a8a;")
        lay.addWidget(size_lbl)

        self._del_btn = ToolButton(FIF.CLOSE, self)
        self._del_btn.setFixedSize(22, 22)
        self._del_btn.setToolTip("移除")
        self._del_btn.clicked.connect(lambda: self.removed.emit(self))
        lay.addWidget(self._del_btn)

        self._apply_kind()

    def _size_str(self) -> str:
        try:
            kb = os.path.getsize(self.path) / 1024
            if kb < 1024:
                return f"{kb:.1f} KB"
            return f"{kb/1024:.1f} MB"
        except OSError:
            return ""

    def set_kind(self, kind: str):
        self.kind = kind
        self._chip.setText("R" if kind == "primary" else "+")
        self._role.setText(
            "主参考 · 决定音色" if kind == "primary" else "辅助 · 音色融合")
        self._apply_kind()

    def _apply_kind(self):
        if self.kind == "primary":
            # WinUI accent 绿
            self._chip.setStyleSheet(
                "color: #4CAF50;"
                " background: rgba(76,175,80,28);"
                " border-radius: 4px;")
        else:
            self._chip.setStyleSheet(
                "color: #8a8a8a;"
                " background: rgba(255,255,255,12);"
                " border-radius: 4px;")


# ---------------------------------------------------------------------------
# VoiceRack — VoiceSlot 容器 + add-row + 拖放 (紧凑 WinUI 风格)
# ---------------------------------------------------------------------------
class VoiceRack(QWidget):
    changed = pyqtSignal()
    listImportRequested = pyqtSignal(str)  # 收到 .list 文件路径
    add_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setObjectName("voiceRack")
        self.setAcceptDrops(True)
        self._slots: list[VoiceSlot] = []
        self._hover = False

        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 6, 12, 6)
        lay.setSpacing(0)
        self._slots_lay = lay

        self._add_row = self._build_add_row()
        lay.addWidget(self._add_row)

        self._refresh_style()

    def _build_add_row(self) -> QWidget:
        row = QWidget(self)
        row.setFixedHeight(36)
        row.setCursor(Qt.CursorShape.PointingHandCursor)
        lay = QHBoxLayout(row)
        lay.setContentsMargins(4, 0, 4, 0)
        lay.setSpacing(10)

        chip = IconWidget(FIF.ADD, row)
        chip.setFixedSize(20, 20)
        lay.addWidget(chip)

        title = BodyLabel("添加参考音频 · 拖入或点击", row)
        lay.addWidget(title)
        lay.addStretch()

        hint = CaptionLabel("wav · mp3 · flac  |  或选择 .list", row)
        hint.setStyleSheet("color: #8a8a8a;")
        lay.addWidget(hint)

        row.mousePressEvent = self._on_add_clicked  # type: ignore[assignment]
        return row

    def _refresh_style(self):
        if self._hover:
            border = "1.2px solid rgba(76,175,80,150)"
            bg = "rgba(76,175,80,18)"
        elif self._slots:
            border = "1px solid rgba(255,255,255,32)"
            bg = "rgba(255,255,255,8)"
        else:
            border = "1.5px dashed rgba(255,255,255,55)"
            bg = "rgba(255,255,255,6)"
        self.setStyleSheet(
            f"QWidget#voiceRack {{"
            f"  background: {bg}; border: {border};"
            f"  border-radius: 12px;"
            f"}}"
        )

    # ---- 拖放 ----
    def dragEnterEvent(self, e: QDragEnterEvent):
        if not e.mimeData().hasUrls():
            e.ignore(); return
        for url in e.mimeData().urls():
            suf = Path(url.toLocalFile()).suffix.lower()
            if suf in AUDIO_EXTS or suf == LIST_EXT:
                e.acceptProposedAction()
                self._hover = True
                self._refresh_style()
                return
        e.ignore()

    def dragLeaveEvent(self, e):
        self._hover = False
        self._refresh_style()
        super().dragLeaveEvent(e)

    def dropEvent(self, e: QDropEvent):
        list_paths, audio_paths = [], []
        for url in e.mimeData().urls():
            p = url.toLocalFile()
            suf = Path(p).suffix.lower()
            if suf == LIST_EXT:
                list_paths.append(p)
            elif suf in AUDIO_EXTS:
                audio_paths.append(p)
        self._hover = False
        self._refresh_style()
        if list_paths:
            self.listImportRequested.emit(list_paths[0])
            e.acceptProposedAction()
            return
        if audio_paths:
            self.add_paths(audio_paths)
            e.acceptProposedAction()

    def _on_add_clicked(self, _ev):
        # 只是转发信号, 让 GPTSoVITSWidget 弹文件对话框
        self.add_requested.emit()

    # ---- 操作 ----
    def add_paths(self, paths: list[str]):
        added = 0
        for p in paths:
            if not p or any(s.path == p for s in self._slots):
                continue
            slot = VoiceSlot(p, kind="primary" if not self._slots else "aux",
                             parent=self)
            slot.removed.connect(self._on_slot_removed)
            self._slots.append(slot)
            self._insert_slot_widget(slot)
            added += 1
        if added:
            self._refresh_style()
            self.changed.emit()

    def _insert_slot_widget(self, slot: VoiceSlot):
        # 保证 add_row 始终在最底
        idx = self._slots_lay.indexOf(self._add_row)
        if idx == -1:
            self._slots_lay.addWidget(slot)
        else:
            self._slots_lay.insertWidget(idx, slot)
            # 加分隔线
            sep = QWidget(self)
            sep.setFixedHeight(1)
            sep.setStyleSheet("background: rgba(255,255,255,20);")
            self._slots_lay.insertWidget(idx, sep)

    def _on_slot_removed(self, slot: VoiceSlot):
        if slot not in self._slots:
            return
        i = self._slots_lay.indexOf(slot)
        # 删掉紧跟其后的分隔线(如果有)
        if i >= 0 and i + 1 < self._slots_lay.count():
            nxt = self._slots_lay.itemAt(i + 1).widget()
            if nxt and nxt is not self._add_row:
                self._slots_lay.removeWidget(nxt)
                nxt.deleteLater()
        # 如果自己是最底的 slot, 顺带删掉自己上面那条分隔线
        elif i > 0:
            prev = self._slots_lay.itemAt(i - 1).widget()
            if prev and prev is not self._add_row and not isinstance(prev, VoiceSlot):
                self._slots_lay.removeWidget(prev)
                prev.deleteLater()
        self._slots_lay.removeWidget(slot)
        slot.deleteLater()
        self._slots.remove(slot)
        # 保证 slots 中第一个仍是 primary
        for j, s in enumerate(self._slots):
            s.set_kind("primary" if j == 0 else "aux")
        self._refresh_style()
        self.changed.emit()

    def clear(self):
        while self._slots:
            self._on_slot_removed(self._slots[0])

    def primary_path(self) -> str:
        return self._slots[0].path if self._slots else ""

    def aux_paths(self) -> list[str]:
        return [s.path for s in self._slots[1:]]

    def all_paths(self) -> list[str]:
        return [s.path for s in self._slots]

    def is_empty(self) -> bool:
        return not self._slots


# ---------------------------------------------------------------------------
# GPTSoVITSWidget (主界面, WinUI 风格, 单页不滚动)
# ---------------------------------------------------------------------------
class GPTSoVITSWidget(QWidget):
    def __init__(self, parent=None, device_options: dict = None):
        super().__init__(parent)
        self.device_options = device_options or {}
        self._gpt_path = ""
        self._sovits_path = ""
        self._output_dir = _paths.output_dir("gptsovits")
        self._worker: GPTSoVITSInferWorker | None = None
        self._last_target_text = ""
        self._setup_ui()
        self._connect_signals()

    # ------------------------------------------------------------------
    # 布局 (两列: 左输入 · 右控件, 底部动作条)
    # ------------------------------------------------------------------
    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 10, 28, 10)
        root.setSpacing(10)

        # ---- 头部 ----
        root.addLayout(self._build_head())

        # ---- 模型条 (全宽, 顶部) ----
        root.addWidget(self._build_models_bar())

        # ---- 双列主体 ----
        body = QHBoxLayout()
        body.setSpacing(24)
        body.addLayout(self._build_left_col(), 62)
        body.addLayout(self._build_right_col(), 38)
        root.addLayout(body, 1)

        # ---- 底部动作条 ----
        root.addLayout(self._build_action_bar())

    # ------- head -------
    def _build_head(self) -> QHBoxLayout:
        head = QHBoxLayout()
        head.setSpacing(10)
        head_col = QVBoxLayout()
        head_col.setContentsMargins(0, 0, 0, 0)
        head_col.setSpacing(0)
        title = TitleLabel("语音合成", self)
        subtitle = CaptionLabel(
            "GPT-SoVITS · 3-10 秒参考音频 → 任意文本 · 音色克隆", self)
        subtitle.setStyleSheet("color: #8a8a8a;")
        head_col.addWidget(title)
        head_col.addWidget(subtitle)
        head.addLayout(head_col)
        head.addStretch()
        self._status_pill = StatusPill(self)
        head.addWidget(self._status_pill,
                       alignment=Qt.AlignmentFlag.AlignVCenter)
        return head

    # ------- left column: input surface -------
    def _build_left_col(self) -> QVBoxLayout:
        col = QVBoxLayout()
        col.setSpacing(12)

        # 参考音频 · rack
        col.addWidget(self._section_label("参考音频"))
        self._rack = VoiceRack(self)
        col.addWidget(self._rack)

        # 参考文本 (语种在同一行右侧)
        ref_head = QHBoxLayout()
        ref_head.setSpacing(10)
        ref_head.addWidget(self._section_label("参考文本"))
        ref_head.addStretch()
        ref_hint = CaptionLabel("与首条参考音频内容一致", self)
        ref_hint.setStyleSheet("color: #8a8a8a;")
        ref_head.addWidget(ref_hint)
        ref_head.addSpacing(8)
        self.ref_lang_combo = ComboBox()
        self.ref_lang_combo.addItems(LANGUAGES)
        self.ref_lang_combo.setCurrentText("中文")
        self.ref_lang_combo.setFixedWidth(130)
        ref_head.addWidget(self.ref_lang_combo)
        col.addLayout(ref_head)

        self._ref_text_edit = TextEdit(self)
        self._ref_text_edit.setPlaceholderText(
            "例: 这是一段用于克隆音色的参考语音, 语速自然, 情绪平稳.")
        self._ref_text_edit.setFixedHeight(64)
        col.addWidget(self._ref_text_edit)

        # 目标文本 (语种 + 切分同行右侧)
        tgt_head = QHBoxLayout()
        tgt_head.setSpacing(10)
        tgt_head.addWidget(self._section_label("目标文本"))
        tgt_head.addStretch()
        self.cut_combo = ComboBox()
        self.cut_combo.addItems(CUT_METHODS)
        self.cut_combo.setCurrentText("凑四句一切")
        self.cut_combo.setFixedWidth(160)
        tgt_head.addWidget(self.cut_combo)
        tgt_head.addSpacing(4)
        self.target_lang_combo = ComboBox()
        self.target_lang_combo.addItems(LANGUAGES)
        self.target_lang_combo.setCurrentText("中文")
        self.target_lang_combo.setFixedWidth(130)
        tgt_head.addWidget(self.target_lang_combo)
        col.addLayout(tgt_head)

        self._target_text_edit = TextEdit(self)
        self._target_text_edit.setPlaceholderText(
            "输入要合成的文本; 长文本会按上面的切分策略分段.")
        self._target_text_edit.setMinimumHeight(160)
        self._target_text_edit.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        col.addWidget(self._target_text_edit, 1)

        return col

    # ------- right column: two control panels (模型条已上提到顶部) -------
    def _build_right_col(self) -> QVBoxLayout:
        col = QVBoxLayout()
        col.setSpacing(14)

        col.addWidget(self._build_sampler_panel())
        col.addWidget(self._build_runtime_panel())
        col.addStretch(1)
        return col

    # ------- 顶部模型条: 全宽横排 [GPT][SoVITS][输出] -------
    def _build_models_bar(self) -> QWidget:
        bar = QWidget(self)
        bar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        bar.setObjectName("modelsBar")
        bar.setStyleSheet(
            "QWidget#modelsBar {"
            "  background: rgba(255,255,255,10);"
            "  border: 1px solid rgba(255,255,255,20);"
            "  border-radius: 10px;"
            "}"
        )
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(14, 8, 14, 8)
        lay.setSpacing(14)

        gpt_tile, self._gpt_tag, self._gpt_btn = self._model_tile(
            "GPT 模型", "未选择 (.ckpt)", "选择 .ckpt")
        lay.addWidget(gpt_tile, 1)

        lay.addWidget(self._bar_divider())

        sov_tile, self._sovits_tag, self._sovits_btn = self._model_tile(
            "SoVITS 模型", "未选择 (.pth)", "选择 .pth")
        lay.addWidget(sov_tile, 1)

        lay.addWidget(self._bar_divider())

        out_tile, self._out_tag, self._out_btn = self._model_tile(
            "输出目录", self._display_path(self._output_dir), "浏览")
        self._out_tag.setToolTip(self._output_dir)
        lay.addWidget(out_tile, 1)

        return bar

    def _model_tile(self, title: str, placeholder: str, btn_text: str):
        """一个 tile: caption 标题 + [tag stretch] [PushButton]. 返回 (widget, tag_label, btn)."""
        tile = QWidget(self)
        v = QVBoxLayout(tile)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(4)

        cap = CaptionLabel(title, tile)
        cap.setStyleSheet("color: #8a8a8a;")
        v.addWidget(cap)

        row = QHBoxLayout()
        row.setSpacing(8)
        tag = BodyLabel(placeholder, tile)
        tag.setStyleSheet("color: #8a8a8a;")
        tag.setMinimumWidth(0)
        row.addWidget(tag, 1)
        btn = PushButton(btn_text, tile)
        btn.setFixedHeight(30)
        btn.setMinimumWidth(96)
        row.addWidget(btn)
        v.addLayout(row)
        return tile, tag, btn

    def _bar_divider(self) -> QWidget:
        d = QWidget(self)
        d.setFixedWidth(1)
        d.setStyleSheet("background: rgba(255,255,255,22);")
        return d

    def _build_sampler_panel(self) -> QWidget:
        panel, layout = self._side_panel("采样参数")

        self.topk_spin = SpinBox()
        self.topk_spin.setRange(1, 100)
        self.topk_spin.setValue(15)
        self.topp_spin = DoubleSpinBox()
        self.topp_spin.setRange(0.1, 1.0)
        self.topp_spin.setSingleStep(0.05)
        self.topp_spin.setDecimals(2)
        self.topp_spin.setValue(1.0)
        self.temp_spin = DoubleSpinBox()
        self.temp_spin.setRange(0.1, 2.0)
        self.temp_spin.setSingleStep(0.05)
        self.temp_spin.setDecimals(2)
        self.temp_spin.setValue(1.0)
        self.speed_spin = DoubleSpinBox()
        self.speed_spin.setRange(0.5, 2.0)
        self.speed_spin.setSingleStep(0.05)
        self.speed_spin.setDecimals(2)
        self.speed_spin.setValue(1.0)

        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(8)
        pairs = [
            ("top_k",       self.topk_spin,  0, 0),
            ("top_p",       self.topp_spin,  0, 1),
            ("temperature", self.temp_spin,  1, 0),
            ("speed",       self.speed_spin, 1, 1),
        ]
        for name, ctrl, r, c in pairs:
            lbl = CaptionLabel(name)
            lbl.setStyleSheet("color: #8a8a8a;")
            cell = QVBoxLayout()
            cell.setContentsMargins(0, 0, 0, 0)
            cell.setSpacing(2)
            cell.addWidget(lbl)
            cell.addWidget(ctrl)
            grid.addLayout(cell, r, c)
            grid.setColumnStretch(c, 1)
        layout.addLayout(grid)

        return panel

    def _build_runtime_panel(self) -> QWidget:
        panel, layout = self._side_panel("运行")

        self._device_combo = ComboBox()
        for name, idx in self.device_options.items():
            self._device_combo.addItem(f"{name} · {idx}")
        self.fmt_combo = ComboBox()
        self.fmt_combo.addItems(["wav", "flac"])

        def _row(label: str, w: QWidget) -> QHBoxLayout:
            r = QHBoxLayout()
            r.setSpacing(10)
            lb = BodyLabel(label)
            lb.setFixedWidth(72)
            r.addWidget(lb)
            r.addWidget(w, 1)
            return r

        layout.addLayout(_row("计算设备", self._device_combo))
        layout.addLayout(_row("输出格式", self.fmt_combo))

        hint = CaptionLabel("首选 GPU 才实用", self)
        hint.setStyleSheet("color: #8a8a8a;")
        layout.addWidget(hint)

        return panel

    # ------- action bar (progress + buttons + history) -------
    def _build_action_bar(self) -> QVBoxLayout:
        wrap = QVBoxLayout()
        wrap.setSpacing(6)

        self._progress = ProgressBar(self)
        self._progress.setValue(0)
        self._progress_label = CaptionLabel("等待开始", self)
        self._progress_label.setStyleSheet("color: #8a8a8a;")
        wrap.addWidget(self._progress)
        wrap.addWidget(self._progress_label)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)
        self._start_btn = PrimaryPushButton("开 始 合 成", self)
        self._start_btn.setFixedHeight(40)
        self._start_btn.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        f = self._start_btn.font()
        f.setBold(True); f.setPointSize(12)
        self._start_btn.setFont(f)
        btn_row.addWidget(self._start_btn, 1)

        self._cancel_btn = PushButton("取 消", self)
        self._cancel_btn.setFixedSize(120, 40)
        self._cancel_btn.setEnabled(False)
        btn_row.addWidget(self._cancel_btn)
        wrap.addLayout(btn_row)

        # 历史 chips
        self._history_wrap = QWidget(self)
        hw = QHBoxLayout(self._history_wrap)
        hw.setContentsMargins(0, 2, 0, 0)
        hw.setSpacing(10)
        h_lbl = CaptionLabel("最近", self._history_wrap)
        h_lbl.setStyleSheet("color: #8a8a8a;")
        hw.addWidget(h_lbl)
        self._history_row = QHBoxLayout()
        self._history_row.setSpacing(8)
        hw.addLayout(self._history_row)
        hw.addStretch()
        self._history_wrap.setVisible(False)
        wrap.addWidget(self._history_wrap)

        return wrap

    # ------- primitives -------
    def _section_label(self, text: str) -> StrongBodyLabel:
        lbl = StrongBodyLabel(text, self)
        return lbl

    def _side_panel(self, title: str) -> tuple[QWidget, QVBoxLayout]:
        """右侧控件面板: 浅底 + hairline 圆角 + 标题.
        返回 (panel_widget, inner_layout).  """
        panel = QWidget(self)
        panel.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        panel.setObjectName("sidePanel")
        panel.setStyleSheet(
            "QWidget#sidePanel {"
            "  background: rgba(255,255,255,10);"
            "  border: 1px solid rgba(255,255,255,20);"
            "  border-radius: 10px;"
            "}"
        )
        outer = QVBoxLayout(panel)
        outer.setContentsMargins(14, 12, 14, 12)
        outer.setSpacing(10)
        head = StrongBodyLabel(title, panel)
        outer.addWidget(head)
        inner = QVBoxLayout()
        inner.setSpacing(8)
        outer.addLayout(inner)
        return panel, inner

    # ------------------------------------------------------------------
    # 信号
    # ------------------------------------------------------------------
    def _connect_signals(self):
        self._gpt_btn.clicked.connect(self._pick_gpt)
        self._sovits_btn.clicked.connect(self._pick_sovits)
        self._out_btn.clicked.connect(self._pick_output_dir)
        self._start_btn.clicked.connect(self._on_start_clicked)
        self._cancel_btn.clicked.connect(self._on_cancel_clicked)

        # voice rack
        self._rack.add_requested.connect(self._pick_ref_audio)
        self._rack.listImportRequested.connect(self._import_list_file)

    # ------------------------------------------------------------------
    # 参数
    # ------------------------------------------------------------------
    def get_params(self) -> dict:
        device_text = self._device_combo.currentText()
        device = device_text.split(" · ", 1)[1] if " · " in device_text else "cpu"
        fmt = self.fmt_combo.currentText()
        out_dir = self._output_dir or _paths.output_dir("gptsovits")
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = os.path.join(out_dir, f"gptsovits_{ts}.{fmt}")
        return {
            "gpt_model":       self._gpt_path,
            "sovits_model":    self._sovits_path,
            "ref_audio":       self._rack.primary_path(),
            "aux_ref_audios":  self._rack.aux_paths(),
            "ref_text":        self._ref_text_edit.toPlainText().strip(),
            "ref_language":    self.ref_lang_combo.currentText(),
            "target_text":     self._target_text_edit.toPlainText().strip(),
            "target_language": self.target_lang_combo.currentText(),
            "output":          out_path,
            "how_to_cut":      self.cut_combo.currentText(),
            "top_k":           self.topk_spin.value(),
            "top_p":           self.topp_spin.value(),
            "temperature":     self.temp_spin.value(),
            "speed":           self.speed_spin.value(),
            "device":          device,
        }

    # ------------------------------------------------------------------
    # 状态控制
    # ------------------------------------------------------------------
    def set_running(self, running: bool):
        self._start_btn.setEnabled(not running)
        self._cancel_btn.setEnabled(running)
        for w in (self._gpt_btn, self._sovits_btn, self._out_btn,
                  self._device_combo, self.ref_lang_combo, self.target_lang_combo,
                  self.cut_combo, self.fmt_combo, self.topk_spin, self.topp_spin,
                  self.temp_spin, self.speed_spin, self._ref_text_edit,
                  self._target_text_edit, self._rack):
            w.setEnabled(not running)
        self._status_pill.set_state("running" if running else "idle")

    def set_progress(self, value: int, label: str = ""):
        self._progress.setValue(int(value))
        if label:
            self._progress_label.setText(label)

    def reset_progress(self):
        self._progress.setValue(0)
        self._progress_label.setText("等待开始")

    # ------------------------------------------------------------------
    # 历史 chips
    # ------------------------------------------------------------------
    def add_history_task(self, target_text: str, output_path: str):
        snippet = (target_text or "(无文本)").strip().replace("\n", " ")
        if len(snippet) > 22:
            snippet = snippet[:19] + "…"
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
        lbl = CaptionLabel(snippet, chip)
        lbl.setToolTip(
            f"{target_text}\n完成于 {datetime.now().strftime('%H:%M:%S')}\n输出: {output_path}")
        cl.addWidget(lbl)
        btn = ToolButton(FIF.FOLDER, chip)
        btn.setFixedSize(22, 22)
        btn.setToolTip(f"打开 {os.path.dirname(output_path)}")
        btn.clicked.connect(
            lambda _=False, d=os.path.dirname(output_path):
                self._open_output_folder(d))
        cl.addWidget(btn)
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

    # ------------------------------------------------------------------
    # Worker 控制
    # ------------------------------------------------------------------
    def _on_start_clicked(self):
        if not self._gpt_path:
            InfoBar.warning("缺少 GPT 模型", "请选择 .ckpt 文件", parent=self); return
        if not self._sovits_path:
            InfoBar.warning("缺少 SoVITS 模型", "请选择 .pth 文件", parent=self); return
        if self._rack.is_empty():
            InfoBar.warning("缺少参考音频",
                            "请选择 3-10 秒的参考音频 (或从 .list 导入)",
                            parent=self); return
        if not self._ref_text_edit.toPlainText().strip():
            InfoBar.warning("缺少参考文本", "请填写参考音频对应的文本", parent=self); return
        if not self._target_text_edit.toPlainText().strip():
            InfoBar.warning("缺少目标文本", "请填写要合成的文本", parent=self); return
        if not self._output_dir:
            InfoBar.warning("缺少输出目录", "请选择输出目录", parent=self); return

        if self._worker and self._worker.isRunning():
            self._worker.cancel()
            self._worker.wait()

        params = self.get_params()
        self.reset_progress()
        self.set_running(True)
        self.set_progress(0, "准备中...")

        self._last_target_text = params["target_text"]
        self._worker = GPTSoVITSInferWorker(params)
        self._worker.progress.connect(self.set_progress)
        # worker.output 走 InfoBar / 进度标签, 不再单独渲染
        self._worker.finished.connect(self._on_worker_finished)
        self._worker.error.connect(self._on_worker_error)
        self._worker.start()

    def _on_cancel_clicked(self):
        if self._worker and self._worker.isRunning():
            self._worker.cancel()
            self.set_running(False)
            self.reset_progress()
            InfoBar.warning("已取消", "合成任务已被用户取消", parent=self)

    def _on_worker_finished(self, output_path: str):
        self.set_progress(100, "完成 · 已写入文件")
        self.set_running(False)
        self._status_pill.set_state("done")
        self.add_history_task(self._last_target_text, output_path)
        InfoBar.success("合成完成", f"文件已保存:\n{output_path}", parent=self)

    def _on_worker_error(self, msg: str):
        self.reset_progress()
        self.set_running(False)
        self._status_pill.set_state("error")
        InfoBar.error("合成错误", msg, parent=self)

    # ------------------------------------------------------------------
    # 文件对话框
    # ------------------------------------------------------------------
    def _pick_gpt(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择 GPT 模型 (.ckpt)", "",
            "GPT 权重 (*.ckpt);;所有文件 (*)")
        if path:
            self._gpt_path = path
            self._gpt_tag.setText(Path(path).name)
            self._gpt_tag.setToolTip(path)
            self._gpt_tag.setStyleSheet("color: #4CAF50;")

    def _pick_sovits(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择 SoVITS 模型 (.pth)", "",
            "SoVITS 权重 (*.pth);;所有文件 (*)")
        if path:
            self._sovits_path = path
            self._sovits_tag.setText(Path(path).name)
            self._sovits_tag.setToolTip(path)
            self._sovits_tag.setStyleSheet("color: #4CAF50;")

    def _pick_ref_audio(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "选择参考音频 (可多选) 或 .list 数据集", "",
            "音频或数据集 (*.wav *.mp3 *.flac *.list);;"
            "音频文件 (*.wav *.mp3 *.flac);;"
            "数据集列表 (*.list);;所有文件 (*)")
        if not paths:
            return
        list_files = [p for p in paths if p.lower().endswith(".list")]
        if list_files:
            self._import_list_file(list_files[0])
            return
        # 追加, 不覆盖 —— 用户可能想加几条 aux ref
        self._rack.add_paths(paths)

    def _import_list_file(self, path: str):
        try:
            entries = parse_sovits_list_file(path)
        except Exception as e:
            InfoBar.error("解析失败", f".list 解析出错: {e}", parent=self)
            return
        if not entries:
            InfoBar.warning(
                "未找到可用条目",
                "该 .list 文件没有可识别的记录 (字段格式不符)",
                parent=self)
            return
        if not any(e.get("exists") for e in entries):
            InfoBar.warning(
                "音频文件均不存在",
                f"解析到 {len(entries)} 条记录, 但音频路径全部失效. "
                f"可点对话框左下角\"重新选音频目录\"按 basename 匹配.",
                parent=self)
        dlg = ListEntryPickerDialog(entries, parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted or not dlg.selected:
            return
        picked = dlg.selected
        first = picked[0]
        if first["text"]:
            self._ref_text_edit.setPlainText(first["text"])
        if first["lang"]:
            idx = self.ref_lang_combo.findText(first["lang"])
            if idx >= 0:
                self.ref_lang_combo.setCurrentIndex(idx)
        # .list 一般是一次性打的 batch, 清空当前 rack 再加
        self._rack.clear()
        self._rack.add_paths([e["audio"] for e in picked])

    def _pick_output_dir(self):
        path = QFileDialog.getExistingDirectory(
            self, "选择输出目录", self._output_dir or "")
        if not path:
            return
        self._output_dir = path
        self._out_tag.setText(self._display_path(path))
        self._out_tag.setToolTip(path)
        self._out_tag.setStyleSheet("color: #4CAF50;")

    def _display_path(self, path: str) -> str:
        return path if len(path) <= 60 else path[:57] + "…"
