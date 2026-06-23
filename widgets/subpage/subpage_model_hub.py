"""
widgets/subpage/subpage_model_hub.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
「找模型」子页面 —— RVC / GPT-SoVITS 角色模型搜索 + 下载。

UX 仿 ``subpage_materials``：两级视图 QStackedWidget 切换

  page 0  搜索视图   头部 + 横幅 + 搜索行 (类型 + 关键词) + 卡片列表
  page 1  详情视图   返回 + 元信息 + 文件清单 (勾选) + 下载日志 + 进度条

后端走 ``utils.model_hub`` + ``workers.model_hub_worker`` —— 不直接
访问网络。下载目标目录由 ``utils.paths.model_dir(kind)`` 决定
最终落 ``data/models/<rvc|gptsovits>/<repo>/``。

设计取舍
========
- 类型只暴露 **RVC / GPT-SoVITS** 两个 SegmentedWidget 项
  其他 (Whisper / Demucs / IOPaint / Audiocraft) 已有专用安装入口 不混入。
- 默认源是 HuggingFace 公共 API
  仓库非常多但模型作者命名混乱 详情页给文件清单让用户自己选 `.pth` / `.ckpt` / `.index`。
- 体积大的训练副产物 (logs/ eval/ ...) 由 model_hub 静默过滤掉
  避免列出来误下 100GB。
"""

from __future__ import annotations

import os
from typing import Optional

from PyQt6.QtCore import Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices, QFont, QTextCursor
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QStackedWidget,
    QSizePolicy, QFrame,
)
from qfluentwidgets import (
    BodyLabel, CaptionLabel, TitleLabel, StrongBodyLabel,
    PushButton, PrimaryPushButton, LineEdit, ToolButton,
    ProgressBar, CardWidget, SmoothScrollArea, IconWidget,
    InfoBar, InfoBarPosition, IndeterminateProgressBar,
    SegmentedWidget, CheckBox, TextEdit,
    FluentIcon as FIF,
)

from workers.model_hub_worker import (
    ModelSearchWorker, ModelListFilesWorker, ModelDownloadWorker,
)
from utils import model_hub as _mh
from utils import paths as _paths
from logger import info, error


_BANNER_TEXT = (
    "⚠️ 本功能只代理 Hugging Face 公开仓库的搜索/下载 模型权利与许可证归原作者所有 "
    "请阅读各仓库 README 与 LICENSE 合规使用 不得用于侵犯他人声音权益。"
)

# 类型对应的中文显示
_KIND_LABELS = [
    ("rvc",       "RVC 声音模型"),
    ("gptsovits", "GPT-SoVITS 音色模型"),
]

# 源 → (显示名, 卡片角标背景色, 文字色) 颜色挑常见品牌色弱化版
_SOURCE_BADGE = {
    "huggingface": ("HF",     "rgba(255, 211,  77, 0.20)", "#FFB300"),
    "modelscope":  ("MS",     "rgba(  0, 188, 212, 0.20)", "#26C6DA"),
    "bilibili":    ("B站",    "rgba(252, 124, 184, 0.20)", "#FB7299"),
}

# 卡片副信息里 "下载/播放" 单位标签的两套文案
_METRIC_LABEL = {
    "bilibili": "▶",       # 播放
}
_DEFAULT_METRIC = "⬇"       # 下载


def _fmt_size(n: int) -> str:
    if not n or n <= 0:
        return "—"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}" if isinstance(n, float) else f"{n} {unit}"
        n = n / 1024
    return f"{n:.1f} PB"


def _fmt_num(n: int) -> str:
    if n is None:
        return "—"
    if n >= 1_000_000:
        return f"{n/1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n/1_000:.1f}K"
    return str(n)


# ══════════════════════════════════════════════════════════════════════
#  日志控件 (复用 subpage_switch_pages 的覆盖逻辑)
# ══════════════════════════════════════════════════════════════════════

class _LogTextEdit(TextEdit):
    """支持「下载进度」前缀单行覆盖的只读 HTML 日志区"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptRichText(True)
        self.setReadOnly(True)

    def append_html(self, html: str):
        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self.setTextCursor(cursor)
        if "下载进度" in html:
            cursor.movePosition(
                QTextCursor.MoveOperation.StartOfLine,
                QTextCursor.MoveMode.KeepAnchor,
            )
            cursor.removeSelectedText()
            cursor.insertHtml(html)
        else:
            cursor.insertHtml(html + "<br>")
        self.ensureCursorVisible()


# ══════════════════════════════════════════════════════════════════════
#  结果卡片
# ══════════════════════════════════════════════════════════════════════

class _ResultCard(CardWidget):
    """单条仓库结果 整卡可点 触发详情页

    左侧：源徽章 (HF / MS / B站) 颜色与品牌色对齐
    中间：标题 + 副信息 (作者 · 热度 · 时间)
    右侧：↗ 浏览器按钮 + 查看 → 进详情
    """

    clicked_detail  = pyqtSignal(dict)
    clicked_browser = pyqtSignal(dict)

    def __init__(self, item: dict, parent=None):
        super().__init__(parent)
        self.setBorderRadius(10)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._item = item
        src = item.get("source", "")

        root = QHBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 12)
        root.setSpacing(12)

        # 源徽章
        badge_text, bg, fg = _SOURCE_BADGE.get(src, (src or "?", "#444", "#ccc"))
        badge = BodyLabel(badge_text, self)
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge.setFixedSize(44, 28)
        badge.setStyleSheet(
            f"background:{bg}; color:{fg}; border-radius:6px; "
            "font-weight:bold; padding:2px 4px;"
        )
        root.addWidget(badge)

        info_box = QVBoxLayout()
        info_box.setSpacing(2)

        title_lbl = StrongBodyLabel(item.get("title") or item.get("id", ""), self)
        title_lbl.setWordWrap(False)
        info_box.addWidget(title_lbl)

        sub_parts = []
        if item.get("author"):
            sub_parts.append(f"作者 {item['author']}")
        if src != "bilibili":
            # B站 没有 like 字段 没必要塞 0
            sub_parts.append(f"⭐ {_fmt_num(item.get('likes', 0))}")
        metric_glyph = _METRIC_LABEL.get(src, _DEFAULT_METRIC)
        sub_parts.append(f"{metric_glyph} {_fmt_num(item.get('downloads', 0))}")
        if item.get("last_modified"):
            sub_parts.append(item["last_modified"][:10])
        if src == "bilibili" and (item.get("extra") or {}).get("duration"):
            sub_parts.append((item["extra"]).get("duration", ""))
        sub_lbl = CaptionLabel("  ·  ".join(sub_parts), self)
        info_box.addWidget(sub_lbl)

        tags = item.get("tags") or []
        if tags:
            tag_lbl = CaptionLabel(
                "标签: " + ", ".join(str(t) for t in tags[:6]), self)
            tag_lbl.setStyleSheet("color: #888888;")
            info_box.addWidget(tag_lbl)

        root.addLayout(info_box, 1)

        # ↗ 直接打开浏览器
        self.browse_btn = ToolButton(FIF.LINK, self)
        self.browse_btn.setToolTip("在浏览器中打开")
        self.browse_btn.setFixedSize(32, 32)
        self.browse_btn.clicked.connect(self._fire_browser)
        root.addWidget(self.browse_btn)

        # 查看详情 (B 站直接说是去看视频)
        self.go_btn = PushButton(
            "看视频 →" if src == "bilibili" else "查看 →", self)
        self.go_btn.setFixedWidth(100)
        self.go_btn.clicked.connect(self._fire_detail)
        root.addWidget(self.go_btn)

    def _fire_detail(self):
        self.clicked_detail.emit(self._item)

    def _fire_browser(self):
        self.clicked_browser.emit(self._item)

    def mouseReleaseEvent(self, ev):
        super().mouseReleaseEvent(ev)
        # 子部件 (↗ / 查看) 点击会先收到事件 这里只兜底空白区点击
        if ev.button() == Qt.MouseButton.LeftButton:
            child = self.childAt(ev.pos())
            if child in (self.browse_btn, self.go_btn):
                return
            self._fire_detail()


# ══════════════════════════════════════════════════════════════════════
#  详情视图
# ══════════════════════════════════════════════════════════════════════

class _DetailView(QWidget):
    """返回 + 元信息 + 文件清单 (CheckBox) + 下载控制 + 进度/日志"""

    back_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._item: dict = {}
        self._files: list[dict] = []
        self._file_checks: list[tuple[CheckBox, dict]] = []
        self._list_worker: Optional[ModelListFilesWorker] = None
        self._dl_worker: Optional[ModelDownloadWorker] = None

        self._setup_ui()

    def _setup_ui(self):
        scroll = SmoothScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("""
SmoothScrollArea { background: transparent; border: none; }
QWidget#modelHubDetailContainer { background: transparent; }
""")
        container = QWidget()
        container.setObjectName("modelHubDetailContainer")
        scroll.setWidget(container)

        root = QVBoxLayout(container)
        root.setContentsMargins(32, 24, 32, 24)
        root.setSpacing(16)

        # 顶部 返回 + 标题
        top = QHBoxLayout()
        top.setSpacing(10)
        self.back_btn = ToolButton(FIF.RETURN, self)
        self.back_btn.setToolTip("返回搜索结果")
        self.back_btn.setFixedSize(36, 36)
        self.back_btn.clicked.connect(self.back_requested.emit)
        top.addWidget(self.back_btn)

        self.title_lbl = TitleLabel("模型详情", self)
        f = QFont(); f.setPointSize(18); f.setBold(True)
        self.title_lbl.setFont(f)
        top.addWidget(self.title_lbl, 1)

        self.open_btn = PushButton("打开仓库主页", self, FIF.LINK)
        self.open_btn.setFixedWidth(140)
        self.open_btn.clicked.connect(self._open_url)
        top.addWidget(self.open_btn)

        self.open_dir_btn = PushButton("打开下载目录", self, FIF.FOLDER)
        self.open_dir_btn.setFixedWidth(140)
        self.open_dir_btn.clicked.connect(self._open_dest_dir)
        top.addWidget(self.open_dir_btn)

        root.addLayout(top)

        # 元信息
        self.meta_lbl = CaptionLabel("", self)
        self.meta_lbl.setWordWrap(True)
        root.addWidget(self.meta_lbl)

        self.desc_lbl = BodyLabel("", self)
        self.desc_lbl.setWordWrap(True)
        self.desc_lbl.setVisible(False)
        root.addWidget(self.desc_lbl)

        # 文件清单卡
        self.files_card = CardWidget(self)
        self.files_card.setBorderRadius(10)
        fcl = QVBoxLayout(self.files_card)
        fcl.setContentsMargins(16, 12, 16, 12)
        fcl.setSpacing(8)

        head = QHBoxLayout()
        head.setSpacing(10)
        head.addWidget(StrongBodyLabel("文件清单", self.files_card))
        head.addStretch()
        self.select_all_btn = PushButton("全选模型权重", self.files_card)
        self.select_all_btn.clicked.connect(self._select_model_files)
        head.addWidget(self.select_all_btn)
        self.clear_sel_btn = PushButton("清空选择", self.files_card)
        self.clear_sel_btn.clicked.connect(self._clear_selection)
        head.addWidget(self.clear_sel_btn)
        fcl.addLayout(head)

        self.files_hint = CaptionLabel(
            "勾选要下载的文件 下载到 data/models/… 子目录。", self.files_card)
        self.files_hint.setStyleSheet("color: #888888;")
        fcl.addWidget(self.files_hint)

        self.files_container = QVBoxLayout()
        self.files_container.setSpacing(2)
        fcl.addLayout(self.files_container)

        self.files_status = CaptionLabel("正在列文件…", self.files_card)
        self.files_status.setStyleSheet("color: #888888;")
        fcl.addWidget(self.files_status)

        root.addWidget(self.files_card)

        # B 站专用面板 (没有文件列表 一键跳网页)
        self.bili_card = CardWidget(self)
        self.bili_card.setBorderRadius(10)
        bl = QVBoxLayout(self.bili_card)
        bl.setContentsMargins(20, 16, 20, 16)
        bl.setSpacing(10)
        bl.addWidget(StrongBodyLabel("B 站视频", self.bili_card))
        self.bili_hint = BodyLabel(
            "B 站不直接托管模型权重 模型链接通常埋在视频简介或评论里 "
            "点下面按钮跳到视频页面 自己抓网盘/Hugging Face 直链。",
            self.bili_card)
        self.bili_hint.setWordWrap(True)
        bl.addWidget(self.bili_hint)
        bili_btn_row = QHBoxLayout()
        bili_btn_row.setSpacing(10)
        self.bili_open_btn = PrimaryPushButton(
            "打开 B 站视频", self.bili_card, FIF.LINK)
        self.bili_open_btn.setFixedWidth(180)
        self.bili_open_btn.clicked.connect(self._open_url)
        bili_btn_row.addWidget(self.bili_open_btn)
        bili_btn_row.addStretch()
        bl.addLayout(bili_btn_row)
        self.bili_card.setVisible(False)
        root.addWidget(self.bili_card)

        # 下载控制 (仅 HF / MS 可见)
        self.dl_ctl_widget = QWidget(self)
        ctl = QHBoxLayout(self.dl_ctl_widget)
        ctl.setContentsMargins(0, 0, 0, 0)
        ctl.setSpacing(10)
        self.download_btn = PrimaryPushButton("下载选中", self, FIF.DOWNLOAD)
        self.download_btn.setFixedWidth(160)
        self.download_btn.clicked.connect(self._start_download)
        ctl.addWidget(self.download_btn)
        self.cancel_btn = PushButton("取消", self, FIF.CANCEL)
        self.cancel_btn.setFixedWidth(96)
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self._cancel_download)
        ctl.addWidget(self.cancel_btn)
        ctl.addStretch()
        self.total_lbl = CaptionLabel("已选 0 个文件 · 0 B", self)
        ctl.addWidget(self.total_lbl)
        root.addWidget(self.dl_ctl_widget)

        # 进度
        self.progress = ProgressBar(self)
        self.progress.setValue(0)
        root.addWidget(self.progress)

        self.indet = IndeterminateProgressBar(self)
        self.indet.setVisible(False)
        root.addWidget(self.indet)

        # 日志
        self.log_view = _LogTextEdit(self)
        self.log_view.setMinimumHeight(180)
        self.log_view.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        root.addWidget(self.log_view, 1)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

    # ── 状态机 ────────────────────────────────────────────────────────

    def start(self, item: dict):
        """从搜索页跳进来时调"""
        self.release()
        self._item = item
        self._files = []
        self._file_checks = []
        src = item.get("source", "")
        self.title_lbl.setText(item.get("title") or item.get("id", ""))

        # 元信息行 B 站用播放/时长 其它源用 ⭐/⬇
        meta_parts = []
        if item.get("author"):
            meta_parts.append(f"作者 {item['author']}")
        if src == "bilibili":
            meta_parts.append(f"▶ {_fmt_num(item.get('downloads', 0))} 播放")
            dur = (item.get("extra") or {}).get("duration", "")
            if dur:
                meta_parts.append(f"时长 {dur}")
        else:
            meta_parts.append(f"⭐ {_fmt_num(item.get('likes', 0))}")
            meta_parts.append(f"⬇ {_fmt_num(item.get('downloads', 0))}")
            if item.get("last_modified"):
                meta_parts.append(f"最后更新 {item['last_modified'][:10]}")
        if src:
            meta_parts.append(f"来源 {dict(_SOURCE_BADGE).get(src, (src,))[0]}")
        self.meta_lbl.setText("  ·  ".join(meta_parts))

        desc = item.get("description") or ""
        if desc.strip() and src != "bilibili":
            self.desc_lbl.setText(desc.strip())
            self.desc_lbl.setVisible(True)
        else:
            self.desc_lbl.setVisible(False)

        # 公共重置
        self._clear_file_checks()
        self.progress.setValue(0)
        self.download_btn.setEnabled(False)
        self.cancel_btn.setEnabled(False)
        self.log_view.clear()

        if src == "bilibili":
            # 分支：B 站只展示「打开视频」 不去拉文件清单
            self.files_card.setVisible(False)
            self.dl_ctl_widget.setVisible(False)
            self.progress.setVisible(False)
            self.indet.setVisible(False)
            self.log_view.setVisible(False)
            self.open_dir_btn.setVisible(False)
            self.bili_card.setVisible(True)
            return

        # HF / ModelScope 走原下载流程
        self.files_card.setVisible(True)
        self.dl_ctl_widget.setVisible(True)
        self.progress.setVisible(True)
        self.log_view.setVisible(True)
        self.open_dir_btn.setVisible(True)
        self.bili_card.setVisible(False)

        self.files_status.setText("正在列文件…")
        self.indet.setVisible(True)
        dest_preview = os.path.join(
            _paths.model_dir(item.get("kind", "rvc")),
            (item.get("id") or "").replace("/", "_"),
        )
        self.log_view.append_html(
            f'<span style="color:#888;">下载目录预览: {dest_preview}</span>')

        self._list_worker = ModelListFilesWorker(item, self)
        self._list_worker.files.connect(self._on_files)
        self._list_worker.error.connect(self._on_list_error)
        self._list_worker.start()

    def _on_files(self, files: list):
        self._files = files
        self.indet.setVisible(False)
        if not files:
            self.files_status.setText("仓库为空 或无可识别的文件")
            return
        # 按 (is_model 在前, path 字母序) 排序
        files_sorted = sorted(
            files,
            key=lambda f: (0 if f.get("is_model") else 1 if f.get("is_aux") else 2,
                           f.get("path") or "")
        )
        for f in files_sorted:
            self._add_file_row(f)
        # 默认勾上所有 is_model 文件 帮用户一步到位
        self._select_model_files()
        n_model = sum(1 for f in files if f.get("is_model"))
        n_total = len(files)
        self.files_status.setText(
            f"共 {n_total} 个文件 其中 {n_model} 个被识别为模型权重 (.pth / .ckpt / .index)")

    def _on_list_error(self, msg: str):
        self.indet.setVisible(False)
        self.files_status.setText(msg)
        InfoBar.error(
            "列文件失败", msg,
            position=InfoBarPosition.TOP_RIGHT, duration=5000,
            parent=self.window(),
        )

    def _add_file_row(self, f: dict):
        row = QFrame(self.files_card)
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, 2, 0, 2)
        lay.setSpacing(10)

        path = f.get("path", "")
        cb = CheckBox(path, row)
        if f.get("is_model"):
            cb.setStyleSheet("color: #4CAF50;")
        elif f.get("is_aux"):
            cb.setStyleSheet("color: #FFC107;")
        else:
            cb.setStyleSheet("color: #888888;")
        cb.stateChanged.connect(self._update_total)
        lay.addWidget(cb, 1)

        size_lbl = CaptionLabel(_fmt_size(int(f.get("size") or 0)), row)
        size_lbl.setFixedWidth(80)
        size_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        lay.addWidget(size_lbl)

        self.files_container.addWidget(row)
        self._file_checks.append((cb, f))

    def _clear_file_checks(self):
        while self.files_container.count():
            it = self.files_container.takeAt(0)
            w = it.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        self._file_checks = []
        self._update_total()

    def _select_model_files(self):
        for cb, f in self._file_checks:
            cb.setChecked(bool(f.get("is_model")))
        self._update_total()

    def _clear_selection(self):
        for cb, _f in self._file_checks:
            cb.setChecked(False)
        self._update_total()

    def _selected_files(self) -> list[dict]:
        return [f for cb, f in self._file_checks if cb.isChecked()]

    def _update_total(self, *_):
        sel = self._selected_files()
        size = sum(int(f.get("size") or 0) for f in sel)
        self.total_lbl.setText(f"已选 {len(sel)} 个文件 · {_fmt_size(size)}")
        self.download_btn.setEnabled(bool(sel) and (self._dl_worker is None or not self._dl_worker.isRunning()))

    def _open_url(self):
        url = self._item.get("url") or ""
        if url:
            QDesktopServices.openUrl(QUrl(url))

    def _open_dest_dir(self):
        if not self._item:
            return
        try:
            kind = self._item.get("kind", "rvc")
            base = _paths.model_dir(kind)
            sub = (self._item.get("id") or "").replace("/", "_")
            path = os.path.join(base, sub)
            if not os.path.isdir(path):
                # 没下载过 直接打开 data/models/<kind>/
                path = base
            QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.abspath(path)))
        except Exception as e:
            InfoBar.error(
                "打开目录失败", str(e),
                position=InfoBarPosition.TOP_RIGHT, duration=4000,
                parent=self.window(),
            )

    # ── 下载 ──────────────────────────────────────────────────────────

    def _start_download(self):
        sel = self._selected_files()
        if not sel:
            InfoBar.warning(
                "未选择文件", "请勾选至少一个文件",
                position=InfoBarPosition.TOP_RIGHT, duration=3000,
                parent=self.window(),
            )
            return
        if self._dl_worker and self._dl_worker.isRunning():
            return

        self.progress.setValue(0)
        self.download_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self.log_view.append_html(
            f'<span style="color:#4FC3F7;">开始下载 {len(sel)} 个文件…</span>')

        self._dl_worker = ModelDownloadWorker(self._item, sel, parent=self)
        self._dl_worker.progress.connect(self._on_dl_progress)
        self._dl_worker.output.connect(self._on_dl_output)
        self._dl_worker.finished.connect(self._on_dl_finished)
        self._dl_worker.error.connect(self._on_dl_error)
        self._dl_worker.start()

    def _cancel_download(self):
        if self._dl_worker and self._dl_worker.isRunning():
            self._dl_worker.cancel()
            self.log_view.append_html(
                '<span style="color:#FFAB91;">已请求取消…</span>')

    def _on_dl_progress(self, pct: int, text: str):
        self.progress.setValue(max(0, min(100, int(pct))))

    def _on_dl_output(self, html: str):
        self.log_view.append_html(html)

    def _on_dl_finished(self, last_path: str):
        self.progress.setValue(100)
        self.download_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        self._update_total()
        info(f"[model_hub] 下载完成: {last_path}")
        InfoBar.success(
            "下载完成", os.path.basename(last_path) or "ok",
            position=InfoBarPosition.TOP_RIGHT, duration=3500,
            parent=self.window(),
        )

    def _on_dl_error(self, msg: str):
        self.download_btn.setEnabled(bool(self._selected_files()))
        self.cancel_btn.setEnabled(False)
        self.log_view.append_html(
            f'<span style="color:#EF5350;">{msg}</span>')
        error(f"[model_hub] {msg}")
        InfoBar.error(
            "下载失败", msg,
            position=InfoBarPosition.TOP_RIGHT, duration=5000,
            parent=self.window(),
        )

    def release(self):
        """切走时取消正在跑的搜索/下载 不强行等"""
        for w_name in ("_list_worker", "_dl_worker"):
            w = getattr(self, w_name, None)
            if w and w.isRunning():
                try:
                    if hasattr(w, "cancel"):
                        w.cancel()
                except Exception:
                    pass
                w.requestInterruption()
                w.wait(1500)
            setattr(self, w_name, None)


# ══════════════════════════════════════════════════════════════════════
#  主页面
# ══════════════════════════════════════════════════════════════════════

class ModelHubWidget(QWidget):
    """「找模型」子页面 顶层是 QStackedWidget(搜索 / 详情)"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("modelHubInterface")
        self._search_worker: Optional[ModelSearchWorker] = None
        self._current_kind: str = "rvc"

        self._setup_ui()

    def _setup_ui(self):
        self._stack = QStackedWidget(self)
        self._search_view = self._build_search_view()
        self._detail_view = _DetailView(self)
        self._detail_view.back_requested.connect(self._exit_detail)
        self._stack.addWidget(self._search_view)
        self._stack.addWidget(self._detail_view)
        self._stack.setCurrentWidget(self._search_view)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self._stack)

    def _build_search_view(self) -> QWidget:
        view = QWidget(self)
        scroll = SmoothScrollArea(view)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("""
SmoothScrollArea { background: transparent; border: none; }
QWidget#modelHubContainer { background: transparent; }
""")
        container = QWidget()
        container.setObjectName("modelHubContainer")
        scroll.setWidget(container)

        root = QVBoxLayout(container)
        root.setContentsMargins(32, 28, 32, 32)
        root.setSpacing(20)

        # 头部
        header = QHBoxLayout()
        icon = IconWidget(
            FIF.CLOUD_DOWNLOAD if hasattr(FIF, "CLOUD_DOWNLOAD") else FIF.DOWNLOAD,
            view)
        icon.setFixedSize(32, 32)
        title = TitleLabel("找模型", view)
        f = QFont(); f.setPointSize(20); f.setBold(True)
        title.setFont(f)
        header.addWidget(icon)
        header.addWidget(title)
        header.addStretch()
        root.addLayout(header)

        desc = BodyLabel(
            "Hugging Face / ModelScope 拉模型权重 B 站找作者发布的视频 (跳网页) "
            "三家默认并行混合搜索 按下载/播放热度降序排。下载到 data/models/<rvc|gptsovits>/。",
            view)
        desc.setWordWrap(True)
        root.addWidget(desc)

        # 横幅
        banner = CardWidget(view)
        banner.setBorderRadius(8)
        banner.setStyleSheet(
            "CardWidget { background: rgba(255, 152, 0, 0.10); "
            "border: 1px solid rgba(255, 152, 0, 0.45); }"
        )
        bn = QHBoxLayout(banner)
        bn.setContentsMargins(16, 10, 16, 10)
        bn.setSpacing(10)
        bn_lbl = BodyLabel(_BANNER_TEXT, banner)
        bn_lbl.setWordWrap(True)
        bn.addWidget(bn_lbl, 1)
        root.addWidget(banner)

        # 搜索行：类型 SegmentedWidget + 关键词 + 搜索按钮
        search_card = CardWidget(view)
        search_card.setBorderRadius(10)
        sc_layout = QVBoxLayout(search_card)
        sc_layout.setContentsMargins(16, 12, 16, 12)
        sc_layout.setSpacing(10)

        seg_row = QHBoxLayout()
        seg_row.setSpacing(10)
        seg_lbl = CaptionLabel("模型类型", view)
        seg_lbl.setFixedWidth(60)
        seg_row.addWidget(seg_lbl)
        self._seg = SegmentedWidget(view)
        for k, label in _KIND_LABELS:
            # onClick 会传一个 bool(checked) 进来 不吞掉就会把 key 默认值顶替成 True
            self._seg.addItem(
                routeKey=k, text=label,
                onClick=lambda *_args, key=k: self._on_kind_changed(key),
            )
        self._seg.setCurrentItem("rvc")
        seg_row.addWidget(self._seg)
        seg_row.addStretch()
        sc_layout.addLayout(seg_row)

        # 数据源筛选 默认三家全选 → 默认混合搜索
        src_row = QHBoxLayout()
        src_row.setSpacing(12)
        src_row_lbl = CaptionLabel("数据源", view)
        src_row_lbl.setFixedWidth(60)
        src_row.addWidget(src_row_lbl)
        self._src_checks: dict[str, CheckBox] = {}
        for key, label in _mh.SOURCES:
            cb = CheckBox(label, view)
            cb.setChecked(True)
            self._src_checks[key] = cb
            src_row.addWidget(cb)
        src_row.addStretch()
        sc_layout.addLayout(src_row)

        kw_row = QHBoxLayout()
        kw_row.setSpacing(10)
        self._kw_edit = LineEdit(view)
        self._kw_edit.setPlaceholderText(
            "输入角色 / 模型名（可为空 用类型默认词搜热门 回车 = 搜索）")
        self._kw_edit.returnPressed.connect(self._on_search_clicked)
        kw_row.addWidget(self._kw_edit, 1)
        self._search_btn = PrimaryPushButton("搜索", view, FIF.SEARCH)
        self._search_btn.setFixedWidth(120)
        self._search_btn.clicked.connect(self._on_search_clicked)
        kw_row.addWidget(self._search_btn)
        sc_layout.addLayout(kw_row)

        root.addWidget(search_card)

        # 状态
        self._status_lbl = CaptionLabel("", view)
        self._status_lbl.setVisible(False)
        root.addWidget(self._status_lbl)

        # 结果区
        self._results_card = CardWidget(view)
        self._results_card.setBorderRadius(10)
        results_layout = QVBoxLayout(self._results_card)
        results_layout.setContentsMargins(16, 12, 16, 16)
        results_layout.setSpacing(8)
        results_layout.addWidget(StrongBodyLabel("搜索结果 (点击查看文件清单)", view))
        self._results_container = QVBoxLayout()
        self._results_container.setSpacing(8)
        results_layout.addLayout(self._results_container)
        results_layout.addStretch()
        self._results_card.setVisible(False)
        root.addWidget(self._results_card)

        root.addStretch()

        v = QVBoxLayout(view)
        v.setContentsMargins(0, 0, 0, 0)
        v.addWidget(scroll)
        return view

    # ── 搜索 ──────────────────────────────────────────────────────────

    def _on_kind_changed(self, kind: str):
        self._current_kind = kind

    def _selected_sources(self) -> list[str]:
        sel = [k for k, cb in self._src_checks.items() if cb.isChecked()]
        return sel or list(_mh.SOURCE_KEYS)   # 一个都没勾 → 兜底全选

    def _on_search_clicked(self):
        kw = self._kw_edit.text().strip()
        sources = self._selected_sources()
        # 允许空关键词 走默认 hint（如 "RVC" / "GPT-SoVITS"）
        if self._search_worker and self._search_worker.isRunning():
            self._search_worker.requestInterruption()
            self._search_worker.wait(500)

        self._clear_results()
        kind_label = dict(_KIND_LABELS).get(self._current_kind, self._current_kind)
        src_label = " + ".join(
            dict(_mh.SOURCES).get(s, s) for s in sources)
        self._status_lbl.setText(
            f"搜索 [{src_label}] {kind_label}: {kw or '(默认热门)'} …")
        self._status_lbl.setVisible(True)
        self._search_btn.setEnabled(False)
        self._search_btn.setText("搜索中…")

        self._search_worker = ModelSearchWorker(
            self._current_kind, kw, sources=sources, parent=self)
        self._search_worker.results.connect(self._on_results)
        self._search_worker.error.connect(self._on_search_error)
        self._search_worker.finished.connect(self._on_search_done)
        self._search_worker.start()

    def _on_search_done(self):
        self._search_btn.setEnabled(True)
        self._search_btn.setText("搜索")

    def _on_search_error(self, msg: str):
        self._status_lbl.setText(msg)
        InfoBar.error(
            "搜索失败", msg,
            position=InfoBarPosition.TOP_RIGHT, duration=5000,
            parent=self.window(),
        )
        error(f"[model_hub] {msg}")

    def _on_results(self, items: list):
        if not items:
            self._status_lbl.setText("无结果 换个关键词试试")
            self._results_card.setVisible(False)
            return
        # 按源分类的简短统计 便于用户判断哪家在贡献
        counts: dict[str, int] = {}
        for it in items:
            counts[it.get("source", "")] = counts.get(it.get("source", ""), 0) + 1
        breakdown = " · ".join(
            f"{dict(_mh.SOURCES).get(s, s)} {n}"
            for s, n in counts.items() if s
        )
        self._status_lbl.setText(f"找到 {len(items)} 条结果 ({breakdown})")
        for it in items:
            card = _ResultCard(it, self)
            card.clicked_detail.connect(self._enter_detail)
            card.clicked_browser.connect(self._open_in_browser)
            self._results_container.addWidget(card)
        self._results_card.setVisible(True)

    def _open_in_browser(self, item: dict):
        url = item.get("url") or ""
        if url:
            QDesktopServices.openUrl(QUrl(url))

    def _clear_results(self):
        while self._results_container.count():
            it = self._results_container.takeAt(0)
            w = it.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        self._results_card.setVisible(False)

    # ── 详情切换 ──────────────────────────────────────────────────────

    def _enter_detail(self, item: dict):
        # item 的 kind 一定是搜索时的 kind 这里不再覆盖
        self._detail_view.start(item)
        self._stack.setCurrentWidget(self._detail_view)

    def _exit_detail(self):
        self._detail_view.release()
        self._stack.setCurrentWidget(self._search_view)
