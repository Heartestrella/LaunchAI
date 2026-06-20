"""
node_editor.py
~~~~~~~~~~~~~~
主编辑器窗口：画布 + Shift+A 节点面板 + 属性面板 + 工具栏
"""

from node.node_canvas import NodeCanvas
from node.node_graph import NodeGraph
from node.node_registry import REGISTRY, CATEGORY_COLORS, PORT_COLORS, NodeDef
from node.node_worker import GraphWorker
from logger import info, warning, error as log_error
from qfluentwidgets import (
    setTheme, Theme, setThemeColor,
    FluentWindow, NavigationItemPosition,
    ElevatedCardWidget, CardWidget,
    TitleLabel, SubtitleLabel, BodyLabel, CaptionLabel, StrongBodyLabel,
    PrimaryPushButton, PushButton, TransparentPushButton, TransparentToolButton,
    ToolButton, LineEdit as FLineEdit, PlainTextEdit, ComboBox,
    SpinBox, DoubleSpinBox, CheckBox,
    ProgressBar, SmoothScrollArea,
    InfoBar, InfoBarPosition,
    FluentIcon as FIF,
    IconWidget, isDarkTheme,
    TabBar, TabCloseButtonDisplayMode,
)
from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QFrame, QSizePolicy, QScrollArea,
    QLineEdit, QSplitter, QTreeWidget, QTreeWidgetItem,
    QDockWidget, QMainWindow, QAbstractItemView, QStackedWidget,
)
from PyQt6.QtGui import (
    QColor, QFont, QKeySequence, QShortcut, QIcon
)
from PyQt6.QtCore import (
    Qt, QPoint, QRectF, QPropertyAnimation, QEasingCurve,
    QTimer, QThread, pyqtSignal
)
from PyQt6.QtWidgets import QFileDialog, QDialog, QDialogButtonBox, QVBoxLayout, QListWidget, QListWidgetItem
import json
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 用户配置里记录"上次打开的 .node 文件"
from utils.configer import get_field, set_field
_LAST_FILE_KEY = "node_editor.last_file"
# 整个 tab 会话快照（含未保存的图）—— 重启后还原所有打开的 tab
_SESSION_KEY = "node_editor.session"
_NODE_FILE_FILTER = "节点图 (*.node *.json);;所有文件 (*)"
_UNSAVED_SUFFIX = " (unsaved)"


ACCENT = "#0078D4"

# 参数键 → 中文释义 用于属性面板的字段标签 显示成 "english · 中文"
# 没列出的键退回展示原英文键 不强求每个新参数都翻译
_PARAM_LABELS_CN = {
    # ── 通用 / 基础 ────────────────────────────────────────────────
    "path":            "文件路径",
    "file":            "文件",
    "filepath":        "文件路径",
    "filename":        "文件名",
    "input":           "输入",
    "directory":       "输出目录",
    "dir":             "目录",
    "folder":          "目录",
    "out_dir":         "输出目录",
    "output_dir":      "输出目录",
    "audio_dir":       "音频根目录",
    "glob":            "通配符",
    "text":            "文本内容",
    "target_format":   "目标格式",
    "device":          "计算设备",
    "gpu":             "GPU",
    "gpu_id":          "GPU 编号",
    "model":           "模型",
    "format":          "输出格式",
    "fmt":             "输出格式",
    # ── demucs ──────────────────────────────────────────────────
    "shifts":          "随机偏移次数",
    "overlap":         "段重叠率",
    "segment":         "切片长度 (秒)",
    # ── whisper ─────────────────────────────────────────────────
    "language":        "语种",
    "task":            "任务 (转录/翻译)",
    # ── RVC ─────────────────────────────────────────────────────
    "model_path":      "模型路径 (.pth)",
    "index_path":      "检索索引 (.index)",
    "f0_method":       "音高提取算法",
    "transpose":       "变调 (半音)",
    "index_rate":      "检索特征占比",
    "filter_radius":   "中值滤波半径",
    "resample_sr":     "重采样率",
    "rms_mix_rate":    "音量包络融合",
    "protect":         "清辅音保护",
    "split_infer":     "按静音分段推理",
    # ── sovits .list ────────────────────────────────────────────
    "list_path":       ".list 数据集路径",
    "entry_index":     "条目索引 (-1 = 首项)",
    # ── GPT-SoVITS ──────────────────────────────────────────────
    "gpt_model":       "GPT 模型 (.ckpt)",
    "sovits_model":    "SoVITS 模型 (.pth)",
    "ref_audio":       "参考音频",
    "ref_text":        "参考文本",
    "target_text":     "目标文本",
    "ref_language":    "参考语种",
    "target_language": "目标语种",
    "how_to_cut":      "切分策略",
    "top_k":           "Top-K 采样",
    "top_p":           "Top-P 采样",
    "temperature":     "温度",
    "speed":           "语速",
    # ── audio_merge ─────────────────────────────────────────────
    "mode":            "合并模式",
    "weights":         "各路权重",
    "volume_a":        "音量 A",
    "volume_b":        "音量 B",
    # ── Real-ESRGAN ─────────────────────────────────────────────
    "scale":           "放大倍数",
    "tile":            "切片大小",
    # ── image_resize ────────────────────────────────────────────
    "width":           "宽度",
    "height":          "高度",
    "keep_ratio":      "保持宽高比",
    # ── text_note / text_input ─────────────────────────────────
    "note":            "备注",
    "description":     "描述",
    "prompt":          "提示词",
}


def _param_display_label(key: str) -> str:
    """属性面板字段名：``english · 中文`` 没翻译的键就显示原英文。"""
    cn = _PARAM_LABELS_CN.get(key)
    return f"{key} · {cn}" if cn else key


def _format_music_hit(hit: dict) -> str:
    """把 search_netease/search_bilibili 返回的单条 dict 渲染成下拉显示文本。
    无副作用 仅供 PropertyPanel.music_fetch 行使用。
    """
    src = (hit.get("source") or "").lower()
    if src == "bilibili":
        title = hit.get("title", "") or "(无标题)"
        author = hit.get("author", "") or ""
        dur = hit.get("duration", "") or ""
        tail = " / ".join(x for x in (author, dur) if x)
        return f"{title}  —  {tail}" if tail else title
    # netease (默认)
    name = hit.get("name", "") or "(无名)"
    artists = hit.get("artists", "") or ""
    album = hit.get("album", "") or ""
    tail = " / ".join(x for x in (artists, album) if x)
    return f"{name}  —  {tail}" if tail else name


_FILE_PARAM_KEYS = {"path", "file", "filepath", "filename", "input", "list_path"}
_DIR_PARAM_KEYS = {"directory", "dir", "folder", "out_dir", "output_dir", "audio_dir"}
_DEVICE_PARAM_KEYS = {"device", "gpu", "gpu_id"}
_FILE_PARAM_KEYS = {"path", "file", "filepath", "filename", "input", "list_path"}
_DIR_PARAM_KEYS = {"directory", "dir", "folder", "out_dir", "output_dir", "audio_dir"}
# 多行文本参数 —— 渲染为 PlainTextEdit 而非单行 LineEdit
# 适用于 prompt / 参考文本 / 目标文本 / 注释 等长内容
_MULTILINE_TEXT_PARAM_KEYS = {"text", "ref_text", "target_text",
                              "prompt", "note", "description"}

# 隐藏参数键 —— 属性面板不渲染这些行 (作为节点内部状态由专属 UI 写入)
# 例如 music_fetch 的 selected_* 由 keyword 行下方的"获取"下拉直接读写
_HIDDEN_PARAM_KEYS = {
    "selected_keyword", "selected_source",
    "selected_id", "selected_title",
}

# 已知数值参数的合理范围 (min, max, step) —— 与各子页 UI 保持一致
# 落到这里的键名会渲染为 SpinBox / DoubleSpinBox 并应用对应范围
# 没列出的数值参数仍按类型给通用兜底范围
_NUMERIC_PARAM_RANGES = {
    # GPT-SoVITS（与 subpage_gptsovits 高级参数卡一致）
    "top_k":         (1, 100, 1),
    "top_p":         (0.1, 1.0, 0.05),
    "temperature":   (0.1, 2.0, 0.05),
    "speed":         (0.5, 2.0, 0.05),
    # Demucs
    "shifts":        (1, 10, 1),
    "overlap":       (0.0, 0.99, 0.05),
    "segment":       (1, 7, 1),       # htdemucs 类 Transformer 最大 7.8s
    # Real-ESRGAN
    "scale":         (1, 8, 1),
    "tile":          (0, 2048, 32),
    # RVC
    "transpose":     (-24, 24, 1),
    "index_rate":    (0.0, 1.0, 0.05),
    "filter_radius": (0, 7, 1),
    "resample_sr":   (0, 48000, 1000),
    "rms_mix_rate":  (0.0, 1.0, 0.05),
    "protect":       (0.0, 0.5, 0.05),
    # 图像
    "width":         (1, 16384, 1),
    "height":        (1, 16384, 1),
    # 通用
    # entry_index = -1 是 sovits_list_input 节点的"自动取首个可用"语义
    "entry_index":   (-1, 9999, 1),
    "fps":           (1, 120, 1),
    "volume_a":      (0.0, 4.0, 0.1),
    "volume_b":      (0.0, 4.0, 0.1),
}
# ══════════════════════════════════════════════════════════════════════
#  Shift+A 节点选择弹出面板
# ══════════════════════════════════════════════════════════════════════


class NodePickerPanel(QWidget):
    """Shift+A 触发的节点选择器，点击即创建节点。"""

    node_chosen = pyqtSignal(str)   # def_id

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedWidth(280)
        self.setMaximumHeight(480)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # 容器卡片
        card = QFrame(self)
        card.setStyleSheet("""
            QFrame {
                background: #252526;
                border-radius: 12px;
                border: 1px solid rgba(255,255,255,0.1);
            }
        """)
        card_lay = QVBoxLayout(card)
        card_lay.setContentsMargins(12, 12, 12, 12)
        card_lay.setSpacing(8)

        # 搜索框
        self._search = QLineEdit(card)
        self._search.setPlaceholderText("搜索节点…")
        self._search.setStyleSheet("""
            QLineEdit {
                background: #1e1e1e;
                border: 1px solid rgba(255,255,255,0.15);
                border-radius: 6px;
                color: #e0e0e0;
                padding: 6px 10px;
                font-size: 13px;
            }
            QLineEdit:focus { border-color: #0078D4; }
        """)
        self._search.textChanged.connect(self._filter)
        card_lay.addWidget(self._search)

        # 节点树
        self._tree = QTreeWidget(card)
        self._tree.setHeaderHidden(True)
        self._tree.setIndentation(16)
        self._tree.setStyleSheet("""
            QTreeWidget {
                background: transparent;
                border: none;
                color: #e0e0e0;
                font-size: 12px;
                outline: none;
            }
            QTreeWidget::item {
                padding: 4px 6px;
                border-radius: 4px;
            }
            QTreeWidget::item:hover { background: rgba(255,255,255,0.06); }
            QTreeWidget::item:selected { background: rgba(0,120,212,0.3); }
            QTreeWidget::branch { background: transparent; }
        """)
        self._tree.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection)
        self._tree.itemClicked.connect(self._on_item_click)
        card_lay.addWidget(self._tree)

        # 底部提示
        hint = QLabel("↑↓ 导航  Enter 确认  Esc 关闭", card)
        hint.setStyleSheet("color:rgba(150,150,150,140);font-size:11px;")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        card_lay.addWidget(hint)

        root.addWidget(card)
        self._build_tree("")

    def show_at(self, global_pos: QPoint):
        self._search.clear()
        self._build_tree("")
        self._search.setFocus()
        self.move(global_pos)
        self.show()

    def _build_tree(self, query: str):
        self._tree.clear()
        by_cat = REGISTRY.by_category()
        q = query.strip().lower()

        for cat, defs in by_cat.items():
            matched = [d for d in defs
                       if not q or q in d.title.lower() or q in d.id.lower()]
            if not matched:
                continue

            cat_item = QTreeWidgetItem([cat])
            cat_item.setFlags(cat_item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            color = CATEGORY_COLORS.get(cat, "#555555")
            cat_item.setForeground(0, QColor(color))
            # fnt = QFont("Segoe UI", 11, QFont.Weight.Bold)
            # cat_item.setFont(0, fnt)
            self._tree.addTopLevelItem(cat_item)

            for nd in matched:
                child = QTreeWidgetItem([nd.title])
                child.setData(0, Qt.ItemDataRole.UserRole, nd.id)
                child.setForeground(0, QColor(200, 200, 200))
                child.setToolTip(0, nd.id)
                cat_item.addChild(child)

            cat_item.setExpanded(True)

    def _filter(self, text: str):
        self._build_tree(text)

    def _on_item_click(self, item: QTreeWidgetItem, col: int):
        def_id = item.data(0, Qt.ItemDataRole.UserRole)
        if def_id:
            self.node_chosen.emit(def_id)
            self.hide()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape:
            self.hide()
        elif e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            items = self._tree.selectedItems()
            if items:
                self._on_item_click(items[0], 0)
        else:
            super().keyPressEvent(e)


# ══════════════════════════════════════════════════════════════════════
#  属性面板 (WinUI 3 / Fluent 风格)
# ══════════════════════════════════════════════════════════════════════

class PortBadge(QFrame):
    """Pill 风格的端口徽章：圆点 + 标签 + 类型 tag。"""

    def __init__(self, label: str, ptype: str, parent=None):
        super().__init__(parent)
        self.setFixedHeight(30)
        color = PORT_COLORS.get(ptype, "#AAAAAA")

        self.setStyleSheet(f"""
            PortBadge {{
                background: rgba(255,255,255,0.04);
                border: 1px solid rgba(255,255,255,0.06);
                border-radius: 6px;
            }}
        """)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 0, 8, 0)
        lay.setSpacing(8)

        dot = QLabel(self)
        dot.setFixedSize(8, 8)
        dot.setStyleSheet(
            f"background:{color};border-radius:4px;border:1px solid rgba(0,0,0,0.4);"
        )
        lay.addWidget(dot)

        name = BodyLabel(label, self)
        lay.addWidget(name)
        lay.addStretch()

        type_tag = CaptionLabel(ptype, self)
        type_tag.setStyleSheet(
            f"color:{color};"
            f"background:rgba(255,255,255,0.05);"
            f"border:1px solid {color}55;"
            f"border-radius:4px;"
            f"padding:1px 6px;"
        )
        lay.addWidget(type_tag)


class SectionCard(CardWidget):
    """带标题的分组卡片。"""

    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(14, 12, 14, 12)
        self._lay.setSpacing(8)

        self._title = CaptionLabel(title.upper(), self)
        self._title.setStyleSheet(
            "color:rgba(255,255,255,0.55);"
            "letter-spacing:1px;"
            "font-weight:600;"
        )
        self._lay.addWidget(self._title)

    def add(self, w: QWidget):
        self._lay.addWidget(w)

    def clear_items(self):
        # 保留标题（index 0），清掉其它
        while self._lay.count() > 1:
            item = self._lay.takeAt(1)
            w = item.widget()
            if w:
                w.deleteLater()


class _ElidedComboBox(ComboBox):
    """qfluentwidgets.ComboBox 选中长项时会 adjustSize() 把按钮撑得跟项一样宽
    属性面板列宽有限 这里把按钮宽度钳住 + 显示用 Qt.ElideRight 截尾
    完整文本保留在 toolTip 里 鼠标悬停可看 下拉菜单仍是完整内容
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        # 给箭头预留 ~22px 实际文本可用宽度 = width - 22 - 内边距
        self._arrow_reserve = 26

    def setText(self, text: str):
        # 父类: QPushButton.setText + self.adjustSize()
        # 这里换成: 按当前可用宽度做 elide 不调 adjustSize 让外层 layout 决定宽度
        self._full_text = text or ""
        self.setToolTip(self._full_text)
        self._apply_elide()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._apply_elide()

    def _apply_elide(self):
        full = getattr(self, "_full_text", "") or self.text()
        if not full:
            from PyQt6.QtWidgets import QPushButton as _QPB
            _QPB.setText(self, "")
            return
        avail = max(20, self.width() - self._arrow_reserve - 16)
        fm = self.fontMetrics()
        elided = fm.elidedText(full, Qt.TextElideMode.ElideRight, avail)
        from PyQt6.QtWidgets import QPushButton as _QPB
        _QPB.setText(self, elided)

    def sizeHint(self):
        # 默认 sizeHint 会用 text 撑宽度 这里给一个合理上限交给 layout
        sh = super().sizeHint()
        sh.setWidth(min(sh.width(), 260))
        return sh

    def minimumSizeHint(self):
        sh = super().minimumSizeHint()
        # 让外层能把宽度收到属性面板列宽内 不被按钮的最小宽度顶住
        sh.setWidth(min(sh.width(), 80))
        return sh


class _MusicSearchThread(QThread):
    """属性面板里"音乐获取"节点的关键词搜索后台线程。
    与 widgets/subpage/subpage_materials.py::MaterialsSearchWorker 同源
    单独一份是为了让 node_editor 不依赖那个 subpage 类。
    """

    results = pyqtSignal(list)   # list[dict]
    error   = pyqtSignal(str)

    def __init__(self, source: str, keyword: str, parent=None):
        super().__init__(parent)
        self.source = (source or "netease").strip().lower()
        self.keyword = (keyword or "").strip()

    def run(self):
        try:
            from utils.material_fetcher import (
                search_netease, search_bilibili,
            )
            if self.source == "bilibili":
                rs = search_bilibili(self.keyword, limit=20)
            else:
                rs = search_netease(self.keyword, limit=20,
                                    drop_instrumental=True)
            self.results.emit(rs or [])
        except Exception as e:
            self.error.emit(f"搜索失败: {e}")


class PropertyPanel(QWidget):
    """右侧属性面板 (WinUI 3 风格)。"""

    param_changed = pyqtSignal(str, str, object)   # iid, key, value

    def __init__(self, graph: NodeGraph, cuda_drivers: dict[str, str] | None = None, parent=None):
        super().__init__(parent)
        self.graph = graph
        self.cuda_drivers = cuda_drivers or {"cpu": "cpu"}
        self._iid: str | None = None
        self.setSizePolicy(QSizePolicy.Policy.Preferred,
                           QSizePolicy.Policy.Expanding)

        # ── 滚动容器 ──────────────────────────────────────────────────
        self._scroll = SmoothScrollArea(self)
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setStyleSheet("background:transparent;border:none;")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self._scroll)

        body = QWidget()
        self._scroll.setWidget(body)

        self._root = QVBoxLayout(body)
        self._root.setContentsMargins(14, 14, 14, 14)
        self._root.setSpacing(10)

        # ── 头部：节点名 + IID ────────────────────────────────────────
        self._header_card = CardWidget(self)
        h_lay = QVBoxLayout(self._header_card)
        h_lay.setContentsMargins(14, 12, 14, 12)
        h_lay.setSpacing(2)

        self._title_lbl = StrongBodyLabel("未选中节点", self._header_card)
        self._iid_lbl = CaptionLabel("", self._header_card)
        self._iid_lbl.setStyleSheet("color:rgba(255,255,255,0.45);")

        h_lay.addWidget(self._title_lbl)
        h_lay.addWidget(self._iid_lbl)
        self._root.addWidget(self._header_card)

        # ── 端口卡片 ──────────────────────────────────────────────────
        self._inputs_card = SectionCard("输入端口", self)
        self._outputs_card = SectionCard("输出端口", self)
        self._params_card = SectionCard("参数", self)

        self._root.addWidget(self._inputs_card)
        self._root.addWidget(self._outputs_card)
        self._root.addWidget(self._params_card)
        self._root.addStretch(1)

        # 默认隐藏（无选中）
        self._set_cards_visible(False)

    # ── 公共方法 ──────────────────────────────────────────────────────
    def set_graph(self, graph: NodeGraph):
        """切换底层节点图（多 tab 文档场景）。"""
        self.graph = graph
        self.clear_selection()

    def show_node(self, iid: str):
        self._iid = iid
        node = self.graph.nodes.get(iid)
        if not node:
            return

        nd = node.definition
        self._title_lbl.setText(node.title)
        self._iid_lbl.setText(f"ID  ·  {iid}")

        # 端口
        self._inputs_card.clear_items()
        self._outputs_card.clear_items()

        if nd and nd.inputs:
            for p in nd.inputs:
                self._inputs_card.add(PortBadge(p.label, p.type, self))
            self._inputs_card.setVisible(True)
        else:
            self._inputs_card.setVisible(False)

        if nd and nd.outputs:
            for p in nd.outputs:
                self._outputs_card.add(PortBadge(p.label, p.type, self))
            self._outputs_card.setVisible(True)
        else:
            self._outputs_card.setVisible(False)

        # 参数
        self._params_card.clear_items()
        if node.params:
            for key, val in node.params.items():
                # 隐藏内部状态键 (例如 music_fetch.selected_* 由 keyword 行的"获取"控件维护)
                if key in _HIDDEN_PARAM_KEYS:
                    continue
                self._params_card.add(self._make_param_row(iid, key, val))
            self._params_card.setVisible(True)
        else:
            self._params_card.setVisible(False)

        self._header_card.setVisible(True)

    def clear_selection(self):
        self._iid = None
        self._title_lbl.setText("未选中节点")
        self._iid_lbl.setText("")
        self._inputs_card.clear_items()
        self._outputs_card.clear_items()
        self._params_card.clear_items()
        self._set_cards_visible(False)

    # ── 内部 ──────────────────────────────────────────────────────────
    def _set_cards_visible(self, visible: bool):
        self._inputs_card.setVisible(visible)
        self._outputs_card.setVisible(visible)
        self._params_card.setVisible(visible)

    def _make_param_row(self, iid: str, key: str, val) -> QWidget:
        row = QWidget(self)
        lay = QVBoxLayout(row)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)

        name = CaptionLabel(_param_display_label(key), row)
        name.setStyleSheet("color:rgba(255,255,255,0.65);")
        lay.addWidget(name)

        key_low = key.lower()

        # ── music_fetch.keyword 特例：LineEdit + 获取按钮 + 候选下拉 ───
        # 普通节点的 keyword 不命中,只有 music_fetch 这一种节点会走到这
        node = self.graph.nodes.get(iid)
        if node and node.def_id == "music_fetch" and key == "keyword":
            return self._make_music_fetch_keyword_row(iid, key, val, row, lay)

        # ── 计算设备：下拉框 ─────────────────────────────────────────
        if key_low in _DEVICE_PARAM_KEYS and self.cuda_drivers:
            combo = ComboBox(row)
            combo.setSizePolicy(QSizePolicy.Policy.Expanding,
                                QSizePolicy.Policy.Fixed)
            # 维护"显示名 → 设备字符串"的有序映射
            items = list(self.cuda_drivers.items())   # [(display, value), ...]
            combo.addItems([disp for disp, _ in items])

            # 选中当前值（按 value 反查 display）
            cur_val = str(val)
            cur_disp = next(
                (disp for disp, v in items if v == cur_val),
                items[0][0]
            )
            combo.setCurrentText(cur_disp)
            # 如果当前值不在列表里，立刻同步一次（让 graph 与 UI 一致）
            if cur_val not in [v for _, v in items]:
                sync_val = items[0][1]
                self.graph.set_param(iid, key, sync_val)
                self.param_changed.emit(iid, key, sync_val)

            def _on_device_changed(idx, k=key, _items=items):
                value = _items[idx][1]
                self.graph.set_param(iid, k, value)
                self.param_changed.emit(iid, k, value)
            combo.currentIndexChanged.connect(_on_device_changed)

            lay.addWidget(combo)
            return row

        # ── 静态枚举（NodeDef.param_choices）：下拉框 ────────────────
        node = self.graph.nodes.get(iid)
        nd = node.definition if node else None
        choices = nd.param_choices.get(key) if nd else None
        if choices:
            combo = ComboBox(row)
            combo.setSizePolicy(QSizePolicy.Policy.Expanding,
                                QSizePolicy.Policy.Fixed)
            disp = [str(c) for c in choices]
            combo.addItems(disp)
            cur = str(val) if val is not None else ""
            if cur in disp:
                combo.setCurrentText(cur)
            else:
                # 当前值不在选项中：同步成首项
                combo.setCurrentIndex(0)
                self.graph.set_param(iid, key, choices[0])
                self.param_changed.emit(iid, key, choices[0])

            def _on_choice_changed(idx, k=key, _choices=choices):
                value = _choices[idx]
                self.graph.set_param(iid, k, value)
                self.param_changed.emit(iid, k, value)
            combo.currentIndexChanged.connect(_on_choice_changed)

            lay.addWidget(combo)
            return row

        # ── 布尔 / 整数 / 浮点：CheckBox / SpinBox / DoubleSpinBox ───
        # 注意 bool 是 int 子类 必须先判 bool
        if isinstance(val, bool):
            chk = CheckBox(row)
            chk.setChecked(val)

            def _on_bool_changed(state, k=key):
                value = bool(state)
                self.graph.set_param(iid, k, value)
                self.param_changed.emit(iid, k, value)
            chk.stateChanged.connect(_on_bool_changed)

            lay.addWidget(chk)
            return row

        if isinstance(val, int):
            rng = _NUMERIC_PARAM_RANGES.get(key_low)
            vmin, vmax, step = rng if rng else (-99999, 99999, 1)
            sb = SpinBox(row)
            sb.setRange(int(vmin), int(vmax))
            sb.setSingleStep(int(step))
            sb.setValue(int(val))
            sb.setSizePolicy(QSizePolicy.Policy.Expanding,
                             QSizePolicy.Policy.Fixed)

            def _on_int_changed(value, k=key):
                v = int(value)
                self.graph.set_param(iid, k, v)
                self.param_changed.emit(iid, k, v)
            sb.valueChanged.connect(_on_int_changed)

            lay.addWidget(sb)
            return row

        if isinstance(val, float):
            rng = _NUMERIC_PARAM_RANGES.get(key_low)
            vmin, vmax, step = rng if rng else (-9999.0, 9999.0, 0.1)
            # 根据步长推导小数位 0.05 → 2 位 0.1 → 1 位
            if   step >= 1:    decimals = 0
            elif step >= 0.1:  decimals = 1
            elif step >= 0.01: decimals = 2
            else:              decimals = 4

            dsb = DoubleSpinBox(row)
            dsb.setRange(float(vmin), float(vmax))
            dsb.setSingleStep(float(step))
            dsb.setDecimals(decimals)
            dsb.setValue(float(val))
            dsb.setSizePolicy(QSizePolicy.Policy.Expanding,
                              QSizePolicy.Policy.Fixed)

            def _on_float_changed(value, k=key):
                v = float(value)
                self.graph.set_param(iid, k, v)
                self.param_changed.emit(iid, k, v)
            dsb.valueChanged.connect(_on_float_changed)

            lay.addWidget(dsb)
            return row

        # ── 多行文本：PlainTextEdit ──────────────────────────────────
        if key_low in _MULTILINE_TEXT_PARAM_KEYS:
            tedit = PlainTextEdit(row)
            tedit.setPlainText(str(val) if val is not None else "")
            tedit.setSizePolicy(QSizePolicy.Policy.Expanding,
                                QSizePolicy.Policy.Fixed)
            # 4 行高度起步 用户拖窗口或长文本时滚动条接管
            fm = tedit.fontMetrics()
            tedit.setFixedHeight(fm.lineSpacing() * 4 + 18)

            def _on_text_changed(_te=tedit, k=key):
                value = _te.toPlainText()
                self.graph.set_param(iid, k, value)
                self.param_changed.emit(iid, k, value)
            tedit.textChanged.connect(_on_text_changed)

            lay.addWidget(tedit)
            return row

        # ── 文件 / 目录：LineEdit + 浏览按钮 ─────────────────────────
        is_file = key_low in _FILE_PARAM_KEYS
        is_dir = key_low in _DIR_PARAM_KEYS

        edit = FLineEdit(row)
        edit.setText(str(val))
        edit.setClearButtonEnabled(True)

        def _h(text, k=key):
            self.graph.set_param(iid, k, text)
            self.param_changed.emit(iid, k, text)
        edit.textChanged.connect(_h)

        if is_file or is_dir:
            wrap = QWidget(row)
            wlay = QHBoxLayout(wrap)
            wlay.setContentsMargins(0, 0, 0, 0)
            wlay.setSpacing(4)
            wlay.addWidget(edit, 1)

            browse = ToolButton(FIF.FOLDER, wrap)
            browse.setFixedSize(30, 30)
            browse.setToolTip("选择文件" if is_file else "选择目录")
            wlay.addWidget(browse)

            def _browse():
                cur = edit.text().strip()
                if is_file:
                    node = self.graph.nodes.get(iid)
                    title = node.title if node else ""
                    tl = title.lower()
                    if "音频" in title or "audio" in tl:
                        flt = "音频 (*.mp3 *.wav *.flac *.m4a *.ogg);;所有文件 (*)"
                    elif "图像" in title or "image" in tl:
                        flt = "图像 (*.png *.jpg *.jpeg *.webp *.bmp);;所有文件 (*)"
                    elif "视频" in title or "video" in tl:
                        flt = "视频 (*.mp4 *.mov *.mkv *.avi);;所有文件 (*)"
                    else:
                        flt = "所有文件 (*)"
                    path, _ = QFileDialog.getOpenFileName(
                        self, "选择文件", cur, flt)
                    if path:
                        edit.setText(path)
                else:
                    d = QFileDialog.getExistingDirectory(
                        self, "选择目录", cur)
                    if d:
                        edit.setText(d)
            browse.clicked.connect(_browse)
            lay.addWidget(wrap)
        else:
            lay.addWidget(edit)

        return row

    # ── music_fetch 专属：关键字行 + 获取按钮 + 候选下拉 ─────────────
    def _make_music_fetch_keyword_row(self, iid: str, key: str, val,
                                      row: QWidget, lay: QVBoxLayout) -> QWidget:
        """构建 music_fetch.keyword 的特殊属性行:
        - 第一行: LineEdit(关键字) + 获取按钮
        - 第二行: 候选下拉 (空时禁用)
        选择下拉后把 selected_id/title/source/keyword 写回 node.params;
        改了关键字或下拉里 source 跟当前不一致则清掉选择 回退 fetch_first_match
        """
        # ── 第一行 ────────────────────────────────────────────────
        wrap = QWidget(row)
        wlay = QHBoxLayout(wrap)
        wlay.setContentsMargins(0, 0, 0, 0)
        wlay.setSpacing(4)

        edit = FLineEdit(wrap)
        edit.setText(str(val))
        edit.setClearButtonEnabled(True)
        edit.setPlaceholderText("歌名 / 视频关键词")
        wlay.addWidget(edit, 1)

        fetch_btn = PushButton("获取", wrap)
        fetch_btn.setFixedHeight(30)
        fetch_btn.setToolTip("按当前关键字 + 数据源在线搜索 填充下方候选列表")
        wlay.addWidget(fetch_btn)

        lay.addWidget(wrap)

        # ── 第二行 ────────────────────────────────────────────────
        combo = _ElidedComboBox(row)
        combo.setSizePolicy(QSizePolicy.Policy.Expanding,
                            QSizePolicy.Policy.Fixed)
        combo.setPlaceholderText('点"获取"加载候选 (留空=自动取第一条)')
        combo.setEnabled(False)
        # 显式钳住最大宽度 保证 layout 不会因子项太长被撑开
        combo.setMaximumWidth(320)
        lay.addWidget(combo)

        # 状态:本次 fetch 返回的原始数据 与 combo 索引对齐
        # 持有在闭包里 避免再起一个属性挂到 PropertyPanel
        state: dict = {"hits": [], "syncing": False}

        # ── 关键字写回 graph ─────────────────────────────────────
        def _on_keyword_changed(text, k=key):
            self.graph.set_param(iid, k, text)
            self.param_changed.emit(iid, k, text)
            # 关键字一改就视作之前选的那一条作废 让执行器回退 fetch_first_match
            self._clear_music_fetch_selection(iid)
            # 候选列表是基于旧关键字的 也清掉避免误选
            state["hits"] = []
            state["syncing"] = True
            combo.clear()
            combo.setEnabled(False)
            state["syncing"] = False
        edit.textChanged.connect(_on_keyword_changed)

        # ── 还原已保存的选择 (打开文件 / 选中节点时) ──────────────
        node = self.graph.nodes.get(iid)
        saved_id = str((node.params.get("selected_id") if node else "") or "")
        saved_title = str((node.params.get("selected_title") if node else "") or "")
        saved_kw = str((node.params.get("selected_keyword") if node else "") or "")
        saved_src = str((node.params.get("selected_source") if node else "") or "")
        cur_src = str((node.params.get("source") if node else "") or "netease")
        # 只有当 (关键字, 数据源) 与保存时一致 才认为这条选择仍然有效
        if saved_id and saved_title and saved_kw == str(val) and saved_src == cur_src:
            state["syncing"] = True
            combo.addItem(f"[已保存] {saved_title}")
            combo.setEnabled(True)
            combo.setCurrentIndex(0)
            state["syncing"] = False
            # 占位 hits[0] 为 None：选这个项时不重写 selected_* (它们已经是对的)
            state["hits"] = [None]

        # ── 获取按钮 ─────────────────────────────────────────────
        # 同时只跑一个搜索线程 旧的覆盖
        active_thread: dict = {"t": None}

        def _on_fetched(rs, _combo=combo, _state=state):
            fetch_btn.setEnabled(True)
            fetch_btn.setText("获取")
            _state["syncing"] = True
            _combo.clear()
            _state["hits"] = list(rs or [])
            if not _state["hits"]:
                _combo.setEnabled(False)
                _state["syncing"] = False
                InfoBar.warning(
                    title="无结果",
                    content="未搜到匹配项 试试换个关键字 / 数据源",
                    duration=2500, parent=self,
                ).show()
                return
            for it in _state["hits"]:
                _combo.addItem(_format_music_hit(it))
            _combo.setEnabled(True)
            _combo.setCurrentIndex(0)
            _state["syncing"] = False
            # 默认选中第一条 同步写回 selected_*
            _commit_selection(0)

        def _on_search_err(msg):
            fetch_btn.setEnabled(True)
            fetch_btn.setText("获取")
            InfoBar.error(
                title="搜索失败",
                content=msg, duration=3000, parent=self,
            ).show()

        def _on_fetch_click():
            kw = edit.text().strip()
            if not kw:
                InfoBar.warning(
                    title="关键字为空",
                    content="先填关键字再点获取",
                    duration=2000, parent=self,
                ).show()
                return
            node = self.graph.nodes.get(iid)
            src = str((node.params.get("source") if node else "") or "netease")
            fetch_btn.setEnabled(False)
            fetch_btn.setText("搜索中…")
            t = _MusicSearchThread(src, kw, self)
            t.results.connect(_on_fetched)
            t.error.connect(_on_search_err)
            # 跑完自己回收 不然 QThread 对象会泄漏
            t.finished.connect(t.deleteLater)
            active_thread["t"] = t
            t.start()
        fetch_btn.clicked.connect(_on_fetch_click)

        # ── 选择变化 → 写回 selected_* ─────────────────────────
        def _commit_selection(idx):
            if state["syncing"]:
                return
            if idx < 0 or idx >= len(state["hits"]):
                return
            hit = state["hits"][idx]
            if hit is None:
                # 占位的"[已保存]" 项 — 不改 graph (selected_* 已经是对的)
                return
            node = self.graph.nodes.get(iid)
            if not node:
                return
            src = str((node.params.get("source") or "netease"))
            if src == "bilibili":
                sid = str(hit.get("bvid") or "")
                title = f"{hit.get('title','')} - {hit.get('author','')}".strip(" -")
            else:
                sid = str(hit.get("id") or "")
                title = f"{hit.get('name','')} - {hit.get('artists','')}".strip(" -")
            kw = edit.text().strip()
            self._set_music_fetch_selection(iid, kw, src, sid, title)

        combo.currentIndexChanged.connect(_commit_selection)

        return row

    # ── 写 / 清 selected_* 的小工具 ─────────────────────────────────
    def _set_music_fetch_selection(self, iid: str,
                                   keyword: str, source: str,
                                   sid: str, title: str):
        pairs = (
            ("selected_keyword", keyword),
            ("selected_source",  source),
            ("selected_id",      sid),
            ("selected_title",   title),
        )
        for k, v in pairs:
            self.graph.set_param(iid, k, v)
            self.param_changed.emit(iid, k, v)

    def _clear_music_fetch_selection(self, iid: str):
        for k in ("selected_keyword", "selected_source",
                  "selected_id", "selected_title"):
            self.graph.set_param(iid, k, "")
            self.param_changed.emit(iid, k, "")


# ══════════════════════════════════════════════════════════════════════
#  Tab 文档容器
# ══════════════════════════════════════════════════════════════════════


class _TabRenameEdit(FLineEdit):
    """双击 tab 标题时叠加上去的临时内联编辑器。

    - Enter / 失焦 → 提交新名
    - Esc          → 取消
    - emit accepted(text) 之后自动 deleteLater
    """

    accepted = pyqtSignal(str)   # 提交的新名,空字符串视为取消

    def __init__(self, initial: str, parent=None):
        super().__init__(parent)
        self.setText(initial)
        self.selectAll()
        self._done = False
        self.editingFinished.connect(self._finish_accept)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape:
            self._finish("")
            return
        super().keyPressEvent(e)

    def _finish_accept(self):
        self._finish(self.text().strip())

    def _finish(self, text: str):
        if self._done:
            return
        self._done = True
        self.accepted.emit(text)
        self.deleteLater()


class _Doc:
    """一个 tab 对应一份独立的节点图会话。"""

    __slots__ = ("route_key", "graph", "canvas",
                 "current_file", "runner", "title", "dirty")

    def __init__(self, route_key: str, graph: NodeGraph, canvas,
                 title: str, current_file: str | None = None,
                 dirty: bool = False):
        self.route_key = route_key
        self.graph = graph
        self.canvas = canvas
        self.current_file = current_file
        self.runner: GraphWorker | None = None
        self.title = title
        # 与磁盘上的 current_file 是否有差异；无文件且非空也算 dirty
        self.dirty = dirty


# ══════════════════════════════════════════════════════════════════════
#  主编辑器页面
# ══════════════════════════════════════════════════════════════════════


class NodeEditorPage(QWidget):
    def __init__(self, cuda_drivers: dict, parent=None):
        super().__init__(parent)
        self.cuda_drivers = cuda_drivers
        self.setObjectName("NodeEditorPage")
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Expanding)

        self._spawn_pos_offset = 0   # 自动错开新节点位置
        self._docs: list[_Doc] = []
        self._doc_seq = 0            # 用于生成唯一 routeKey + "未命名 N" 标题
        # 切 tab 时短暂屏蔽 currentChanged 反复触发
        self._switching_tab = False
        # 加载 session / 文件期间不要把程序化的图变更标记为 dirty
        self._loading = False

        self._build_ui()
        # session 持久化：所有变更都 debounce 500ms 后写入配置
        self._save_session_timer = QTimer(self)
        self._save_session_timer.setSingleShot(True)
        self._save_session_timer.timeout.connect(self._save_session)
        # 启动加载策略：用户配置里有"上次打开的文件"就读；读不到才回退到 demo
        self._auto_load_on_start()

    # ── 活动文档代理 ─────────────────────────────────────────────────
    @property
    def _active(self) -> _Doc | None:
        idx = self._tabbar.currentIndex() if hasattr(self, "_tabbar") else -1
        if 0 <= idx < len(self._docs):
            return self._docs[idx]
        return None

    @property
    def graph(self) -> NodeGraph:
        """活动文档的 graph（无文档时返回一个空图,防止外部空引用）。"""
        d = self._active
        return d.graph if d else NodeGraph()

    @property
    def _canvas(self):
        d = self._active
        return d.canvas if d else None

    @property
    def _runner(self) -> GraphWorker | None:
        d = self._active
        return d.runner if d else None

    @_runner.setter
    def _runner(self, value: GraphWorker | None):
        d = self._active
        if d:
            d.runner = value

    @property
    def _current_file(self) -> str | None:
        d = self._active
        return d.current_file if d else None

    @_current_file.setter
    def _current_file(self, value: str | None):
        d = self._active
        if d:
            d.current_file = value

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── 工具栏 ────────────────────────────────────────────────────
        toolbar = QWidget(self)
        toolbar.setFixedHeight(48)
        toolbar.setStyleSheet(
            "background:#1e1e1e;border-bottom:1px solid rgba(255,255,255,0.08);")
        tb_lay = QHBoxLayout(toolbar)
        tb_lay.setContentsMargins(16, 0, 16, 0)
        tb_lay.setSpacing(8)

        title = QLabel("节点编辑器", toolbar)
        title.setStyleSheet("color:#e0e0e0;font-size:15px;font-weight:bold;")
        tb_lay.addWidget(title)

        badge = QLabel("实验性", toolbar)
        badge.setStyleSheet(
            "background:rgba(247,183,49,0.2);color:#F7B731;"
            "border:1px solid rgba(247,183,49,0.4);"
            "border-radius:4px;padding:1px 6px;font-size:10px;")
        tb_lay.addWidget(badge)
        tb_lay.addStretch()

        hint = QLabel(
            "Shift+A 添加  ·  Shift+D 复制  ·  X/Del 删除  ·  "
            "Ctrl/Shift+点击 多选  ·  Ctrl+A 全选  ·  右键删除连线  ·  "
            "滚轮缩放  ·  中键平移", toolbar)
        hint.setStyleSheet("color:rgba(150,150,150,140);font-size:11px;")
        tb_lay.addWidget(hint)
        tb_lay.addStretch()

        self._run_btn = PrimaryPushButton(FIF.PLAY, "执行计划", toolbar)
        self._run_btn.clicked.connect(self._on_run_btn_click)
        tb_lay.addWidget(self._run_btn)

        fit_btn = PushButton(FIF.FULL_SCREEN, "适应视图", toolbar)
        fit_btn.clicked.connect(lambda: self._canvas.fit_view())
        tb_lay.addWidget(fit_btn)

        clear_btn = PushButton(FIF.DELETE, "清空", toolbar)
        clear_btn.clicked.connect(self._clear_graph)
        tb_lay.addWidget(clear_btn)

        # ── 文件操作 ─────────────────────────────────────────────────
        open_btn = PushButton(FIF.FOLDER, "打开", toolbar)
        open_btn.setToolTip("打开 .node 文件 (Ctrl+O)")
        open_btn.clicked.connect(self._open_file)
        tb_lay.addWidget(open_btn)

        save_btn = PushButton(FIF.SAVE, "保存", toolbar)
        save_btn.setToolTip("保存当前节点图 (Ctrl+S)")
        save_btn.clicked.connect(self._save_file)
        tb_lay.addWidget(save_btn)

        save_as_btn = PushButton(FIF.SAVE_AS, "另存为", toolbar)
        save_as_btn.setToolTip("另存为 .node 文件 (Ctrl+Shift+S)")
        save_as_btn.clicked.connect(self._save_file_as)
        tb_lay.addWidget(save_as_btn)

        root.addWidget(toolbar)

        # 文件操作快捷键（保留原有 Shift+A）
        QShortcut(QKeySequence("Ctrl+O"), self).activated.connect(self._open_file)
        QShortcut(QKeySequence("Ctrl+S"), self).activated.connect(self._save_file)
        QShortcut(QKeySequence("Ctrl+Shift+S"), self).activated.connect(self._save_file_as)
        QShortcut(QKeySequence("Ctrl+T"), self).activated.connect(self._new_tab)
        QShortcut(QKeySequence("Ctrl+W"), self).activated.connect(
            lambda: self._close_tab(self._tabbar.currentIndex())
        )

        # ── 文档 TabBar ─────────────────────────────────────────────
        self._tabbar = TabBar(self)
        self._tabbar.setMovable(True)
        self._tabbar.setScrollable(True)
        self._tabbar.setTabMaximumWidth(220)
        self._tabbar.setCloseButtonDisplayMode(TabCloseButtonDisplayMode.ALWAYS)
        self._tabbar.setStyleSheet(
            "TabBar{background:#1e1e1e;"
            "border-bottom:1px solid rgba(255,255,255,0.08);}"
        )
        self._tabbar.tabAddRequested.connect(self._new_tab)
        self._tabbar.tabCloseRequested.connect(self._close_tab)
        self._tabbar.currentChanged.connect(self._on_tab_changed)
        self._tabbar.tabBarDoubleClicked.connect(self._begin_rename_tab)
        # 拖动 tab 重排时同步 _docs 顺序，否则 session 持久化的顺序会错位
        self._tabbar.tabMoved.connect(self._on_tab_moved)
        root.addWidget(self._tabbar)

        # ── 主体：画布栈 + 右侧属性面板 ──────────────────────────────
        splitter = QSplitter(Qt.Orientation.Horizontal, self)
        splitter.setHandleWidth(1)
        splitter.setStyleSheet(
            "QSplitter::handle{background:rgba(255,255,255,0.08);}")

        # 画布栈：每个 tab 一个 NodeCanvas 实例
        self._canvas_stack = QStackedWidget(splitter)
        splitter.addWidget(self._canvas_stack)

        # 属性面板
        # 用 setMinimumWidth 而非 setFixedWidth 这样 QSplitter 的拖拽手柄能生效
        # 用户拖宽以适配像 gptsovits 这种 5 端口 + 多参数的复杂节点
        prop_container = QFrame(splitter)
        prop_container.setMinimumWidth(300)
        prop_container.setStyleSheet(
            "background:#1e1e1e;border-left:1px solid rgba(255,255,255,0.08);")
        pc_lay = QVBoxLayout(prop_container)
        pc_lay.setContentsMargins(0, 0, 0, 0)

        prop_title = QLabel("属性", prop_container)
        prop_title.setFixedHeight(36)
        prop_title.setStyleSheet(
            "color:#e0e0e0;font-size:13px;font-weight:bold;"
            "padding-left:14px;border-bottom:1px solid rgba(255,255,255,0.08);")
        prop_title.setAlignment(
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        pc_lay.addWidget(prop_title)

        # PropertyPanel 与活动 doc 共享：tab 切换时通过 set_graph 换底层图
        self._prop_panel = PropertyPanel(
            NodeGraph(), self.cuda_drivers, prop_container)
        self._prop_panel.setStyleSheet("color:#e0e0e0;")
        self._prop_panel.param_changed.connect(self._on_param_changed)
        pc_lay.addWidget(self._prop_panel)

        splitter.addWidget(prop_container)
        # 拖拽时让画布优先扩张 属性面板保持当前宽度
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 0)
        splitter.setSizes([1200, 360])
        root.addWidget(splitter, 1)

        # ── Shift+A 节点面板 ──────────────────────────────────────────
        self._picker = NodePickerPanel(self)
        self._picker.node_chosen.connect(self._spawn_node)

        # 快捷键
        sc = QShortcut(QKeySequence("Shift+A"), self)
        sc.activated.connect(self._open_picker)

    # ── Tab 管理 ──────────────────────────────────────────────────────
    def _create_doc(self, title: str, current_file: str | None = None) -> _Doc:
        """构造一个新的 _Doc(graph + canvas),注册到 TabBar 与 QStackedWidget。"""
        self._doc_seq += 1
        route_key = f"doc_{self._doc_seq}"

        graph = NodeGraph()
        canvas = NodeCanvas(graph, self._canvas_stack)
        canvas.node_selected.connect(self._on_node_selected)
        canvas.node_deselected.connect(self._on_node_deselected)
        canvas.graph_changed.connect(self._on_graph_changed)
        self._canvas_stack.addWidget(canvas)

        doc = _Doc(route_key, graph, canvas, title, current_file)
        self._docs.append(doc)
        self._tabbar.addTab(route_key, title, FIF.DOCUMENT)
        return doc

    def _new_tab(self):
        """TabBar 的 + 按钮 / Ctrl+T 触发：新建一份空白节点图,自动切到新页。"""
        doc = self._create_doc(self._next_untitled_name())
        idx = self._docs.index(doc)
        self._tabbar.setCurrentIndex(idx)
        # TabBar.setCurrentIndex 不发 currentChanged,得手动切画布栈
        self._on_tab_changed(idx)
        self._schedule_session_save()

    def _next_untitled_name(self) -> str:
        used = {d.title for d in self._docs}
        i = 1
        while f"未命名 {i}" in used:
            i += 1
        return f"未命名 {i}"

    def _close_tab(self, index: int):
        if not (0 <= index < len(self._docs)):
            return
        doc = self._docs[index]

        # 若 doc 仍在执行,先取消
        if doc.runner is not None and doc.runner.isRunning():
            try:
                doc.runner.cancel()
            except Exception:
                pass

        self._canvas_stack.removeWidget(doc.canvas)
        doc.canvas.deleteLater()
        self._docs.pop(index)
        self._tabbar.removeTab(index)

        # 至少保留一个 tab
        if not self._docs:
            self._new_tab()
        else:
            # TabBar.removeTab 会自动调整 currentIndex,这里同步一下显示
            self._on_tab_changed(self._tabbar.currentIndex())
        self._schedule_session_save()

    def _on_tab_changed(self, index: int):
        if self._switching_tab:
            return
        if not (0 <= index < len(self._docs)):
            return
        doc = self._docs[index]
        self._canvas_stack.setCurrentWidget(doc.canvas)
        self._prop_panel.set_graph(doc.graph)
        self._refresh_run_button()

    def _set_doc_title(self, doc: _Doc, title: str):
        doc.title = title
        self._refresh_tab_text(doc)

    def _refresh_tab_text(self, doc: _Doc):
        """根据 doc.title 与 doc.dirty 刷新 tab 上显示的文本。"""
        if doc not in self._docs:
            return
        idx = self._docs.index(doc)
        display = doc.title + _UNSAVED_SUFFIX if doc.dirty else doc.title
        self._tabbar.tabItem(idx).setText(display)

    def _mark_dirty(self, doc: _Doc | None = None):
        """把 doc 标为未保存；session 加载期间忽略，避免误标。"""
        if self._loading:
            return
        doc = doc or self._active
        if doc is None:
            return
        if not doc.dirty:
            doc.dirty = True
            self._refresh_tab_text(doc)
        self._schedule_session_save()

    def _mark_clean(self, doc: _Doc | None = None):
        doc = doc or self._active
        if doc is None:
            return
        if doc.dirty:
            doc.dirty = False
            self._refresh_tab_text(doc)
        self._schedule_session_save()

    def _schedule_session_save(self):
        if self._loading:
            return
        timer = getattr(self, "_save_session_timer", None)
        if timer is not None:
            timer.start(500)

    def _on_tab_moved(self, src: int, dst: int):
        if not (0 <= src < len(self._docs)) or not (0 <= dst < len(self._docs)):
            return
        if src == dst:
            return
        doc = self._docs.pop(src)
        self._docs.insert(dst, doc)
        self._schedule_session_save()

    def _begin_rename_tab(self, index: int):
        """双击 tab 触发的内联重命名。

        Parent 必须在 TabBar 之外（这里用 NodeEditorPage 自身）—— 不然
        TabItem.mouseDoubleClickEvent 里的合成 mousePress 会触发
        TabItem.setSelected -> raise_()，把刚 raise 上去的编辑器再次压回底部，
        造成"打字时看不到光标也看不到字"的现象。
        """
        if not (0 <= index < len(self._docs)):
            return
        # 用 QTimer 推到事件循环下一帧，避开双击事件链尾巴上的合成 press 抢焦点
        QTimer.singleShot(0, lambda i=index: self._spawn_rename_editor(i))

    def _spawn_rename_editor(self, index: int):
        if not (0 <= index < len(self._docs)):
            return
        doc = self._docs[index]
        item = self._tabbar.tabItem(index)

        edit = _TabRenameEdit(doc.title, self)
        # 把 TabItem 的左上角换算成 NodeEditorPage 坐标
        top_left = item.mapTo(self, QPoint(0, 0))
        rect = item.geometry()
        # 留出 4px 边距,右侧再让出 32px 给关闭按钮
        edit.setGeometry(
            top_left.x() + 4, top_left.y() + 4,
            max(60, rect.width() - 36), rect.height() - 8,
        )
        edit.setStyleSheet(
            "QLineEdit{background:#2d2d2d;color:#e0e0e0;"
            "border:1px solid #0078D4;border-radius:4px;"
            "padding:2px 6px;font-size:12px;}"
        )
        edit.accepted.connect(
            lambda new_title, _d=doc: self._commit_rename(_d, new_title))
        edit.show()
        edit.raise_()
        edit.setFocus()

    def _commit_rename(self, doc: _Doc, new_title: str):
        new_title = new_title.strip()
        # 空 / 未变 / doc 已关闭 都视为无操作
        if not new_title or new_title == doc.title or doc not in self._docs:
            return
        self._set_doc_title(doc, new_title)
        self._schedule_session_save()

    def _refresh_run_button(self):
        running = self._runner is not None and self._runner.isRunning()
        self._run_btn.setEnabled(True)
        self._set_run_btn_running(running)

    # ── Demo 节点 ──────────────────────────────────────────────────────

    def _add_demo_nodes(self):
        n1 = self.graph.add_node("file_input",   80,  80)
        n2 = self.graph.add_node("demucs",      340,  60)
        n3 = self.graph.add_node("whisper",     340, 320)
        n4 = self.graph.add_node("realesrgan",  340, 560)
        n5 = self.graph.add_node("file_output", 620, 180)
        n6 = self.graph.add_node("file_output", 620, 420)
        n7 = self.graph.add_node("file_input",   80, 560)
        n8 = self.graph.add_node("preview",     620, 560)

        self.graph.add_connection(n1.iid, "file_out", n2.iid, "audio_in")
        self.graph.add_connection(n1.iid, "file_out", n3.iid, "audio_in")
        self.graph.add_connection(n2.iid, "vocals",   n5.iid, "file_in")
        self.graph.add_connection(n3.iid, "transcript", n6.iid, "file_in")
        self.graph.add_connection(n7.iid, "file_out", n4.iid, "image_in")
        self.graph.add_connection(n4.iid, "image_out", n8.iid, "input")

        QTimer.singleShot(100, self._canvas.fit_view)

    # ── 交互 ──────────────────────────────────────────────────────────

    def _open_picker(self):
        center = self.mapToGlobal(
            QPoint(self.width() // 2 - 140, self.height() // 2 - 240)
        )
        self._picker.show_at(center)

    def _spawn_node(self, def_id: str):
        # 在画布中心附近散开放置
        cx = self.width() / 2 / self._canvas._scale - \
            self._canvas._offset.x() / self._canvas._scale
        cy = self.height() / 2 / self._canvas._scale - \
            self._canvas._offset.y() / self._canvas._scale
        offset = self._spawn_pos_offset * 20
        self._spawn_pos_offset = (self._spawn_pos_offset + 1) % 10
        node = self.graph.add_node(def_id, cx - 100 + offset, cy - 60 + offset)
        self._canvas.update()
        self._mark_dirty()
        InfoBar.success(
            title="已添加", content=f"{node.title}",
            parent=self, position=InfoBarPosition.BOTTOM_RIGHT, duration=1500
        )

    def _on_param_changed(self, iid: str, key: str, value):
        node = self.graph.nodes.get(iid)
        if not node:
            return
        if node.def_id == "preview":
            path = self._canvas.resolve_preview_path(node)
            self._canvas.update_preview(iid, path)
        elif node.def_id in ("text_note", "text_input"):
            # 文本变化要重画节点 高度可能跟着折行变
            self._canvas.update()
        else:
            for conn in self.graph.connections.values():
                if conn.src_iid == iid:
                    dst = self.graph.nodes.get(conn.dst_iid)
                    if dst and dst.def_id == "preview":
                        path = self._canvas.resolve_preview_path(dst)
                        self._canvas.update_preview(dst.iid, path)
        self._mark_dirty()

    def _on_node_selected(self, iid: str):
        self._prop_panel.show_node(iid)

    def _on_node_deselected(self):
        self._prop_panel.clear_selection()

    def _on_graph_changed(self):
        self._canvas.refresh_all_previews()
        self._mark_dirty()

    def _on_run_btn_click(self):
        """按钮在执行/终止两态间切换。"""
        if self._runner is not None and self._runner.isRunning():
            self._cancel_run()
        else:
            self._run_plan()

    def _set_run_btn_running(self, running: bool):
        if running:
            self._run_btn.setIcon(FIF.CLOSE)
            self._run_btn.setText("终止")
        else:
            self._run_btn.setIcon(FIF.PLAY)
            self._run_btn.setText("执行计划")

    def _cancel_run(self):
        doc = self._active
        if doc is None or doc.runner is None or not doc.runner.isRunning():
            return
        # 防止反复点击
        self._run_btn.setEnabled(False)
        self._run_btn.setText("终止中…")
        doc.runner.cancel()
        InfoBar.warning(
            title="正在终止",
            content="已请求取消，等待当前节点收尾…",
            parent=self,
            position=InfoBarPosition.TOP_RIGHT,
            duration=2000,
        )

    def _run_plan(self):
        doc = self._active
        if doc is None:
            return
        # 控制台打印一份计划（保留旧行为，便于排查）
        doc.graph.print_execution_plan()

        # 启动 GraphWorker —— 把 doc 绑进回调,切 tab 不影响命中目标
        runner = GraphWorker(doc.graph, parent=self)
        runner.output.connect(self._on_runner_output)
        runner.progress.connect(self._on_runner_progress)
        runner.finished.connect(
            lambda r, _d=doc: self._on_runner_finished(_d, r))
        runner.error.connect(
            lambda m, _d=doc: self._on_runner_error(_d, m))
        # 节点级运行高亮:把当前正在执行的节点 iid 推给对应画布(切 tab 也不丢)
        # tab 关闭后 canvas 会被 deleteLater 此时不能再调用其方法 故先检查 doc 是否还活着
        runner.executing_node.connect(
            lambda iid, _d=doc: _d.canvas.set_executing_node(iid)
            if _d in self._docs else None)
        runner.start()
        doc.runner = runner

        self._set_run_btn_running(True)
        InfoBar.info(
            title="开始执行",
            content=f"{doc.title}: 节点图已提交后台执行，日志见控制台",
            parent=self,
            position=InfoBarPosition.TOP_RIGHT,
            duration=2000,
        )

    # ── GraphWorker 信号回调 ──────────────────────────────────────────
    @staticmethod
    def _strip_html(text: str) -> str:
        import re
        return re.sub(r"<[^>]+>", "", text)

    def _on_runner_output(self, line: str):
        info(self._strip_html(line))

    def _on_runner_progress(self, percent: int, status: str):
        info(f"[{percent}%] {status}")

    def _on_runner_finished(self, doc: "_Doc", results: dict):
        doc.runner = None
        # tab 已被关闭：canvas 已 deleteLater,只记日志,不再触碰 UI
        if doc not in self._docs:
            info(f"节点图执行结束（{doc.title}），但 tab 已关闭,跳过 UI 更新")
            return
        info(f"节点图执行结束（{doc.title}），输出节点数: {len(results)}")
        InfoBar.success(
            title="执行完成",
            content=f"{doc.title}: 共 {len(results)} 个节点产出结果",
            parent=self,
            position=InfoBarPosition.TOP_RIGHT,
            duration=3000,
        )
        # 把执行结果中的实际输出路径写回到下游 preview 节点的 params["path"]，
        # 这样 NodeCanvas.resolve_preview_path 才能找到 demucs / whisper /
        # realesrgan 等中间节点的产物。
        for iid, node in doc.graph.nodes.items():
            if node.def_id != "preview":
                continue
            resolved = None
            for conn in doc.graph.connections.values():
                if conn.dst_iid != iid:
                    continue
                src_outs = results.get(conn.src_iid, {})
                val = src_outs.get(conn.src_port)
                path = getattr(val, "path", None)
                if path:
                    resolved = path
                    break
            if resolved:
                node.params["path"] = resolved
        doc.canvas.refresh_all_previews()
        if doc is self._active:
            self._run_btn.setEnabled(True)
            self._set_run_btn_running(False)

    def _on_runner_error(self, doc: "_Doc", msg: str):
        doc.runner = None
        if doc not in self._docs:
            log_error(f"节点图执行失败（{doc.title}，tab 已关闭）: {msg}")
            return
        log_error(f"节点图执行失败（{doc.title}）: {msg}")
        # 区分用户取消 vs 真正错误
        if "取消" in msg:
            InfoBar.warning(
                title="已终止",
                content=f"{doc.title}: {msg}",
                parent=self,
                position=InfoBarPosition.TOP_RIGHT,
                duration=2500,
            )
        else:
            InfoBar.error(
                title="执行失败",
                content=f"{doc.title}: {msg}",
                parent=self,
                position=InfoBarPosition.TOP_RIGHT,
                duration=4000,
            )
        if doc is self._active:
            self._run_btn.setEnabled(True)
            self._set_run_btn_running(False)

    def _clear_graph(self):
        self._canvas.clear_all_previews()
        for iid in list(self.graph.nodes.keys()):
            self.graph.remove_node(iid)
        self._prop_panel.clear_selection()
        self._canvas.update()
        self._mark_dirty()

    # ── 文件 IO ──────────────────────────────────────────────────────

    def _auto_load_on_start(self):
        """启动时优先还原完整 session（所有 tab + 未保存的图）。

        优先级：session → last_file → demo 节点。
        """
        if self._restore_session():
            return

        # 始终至少有一个 tab
        self._create_doc(self._next_untitled_name())
        self._tabbar.setCurrentIndex(0)
        self._on_tab_changed(0)

        last = get_field(_LAST_FILE_KEY)
        if last and isinstance(last, str) and os.path.isfile(last):
            try:
                self._load_from_path(last)
                return
            except Exception as e:
                warning(f"加载上次的节点文件失败: {e}")
        # 没有上次记录或加载失败 —— 保留旧的 demo 行为兜底
        self._add_demo_nodes()

    def _restore_session(self) -> bool:
        """从配置里恢复所有 tab；返回 True 表示已成功还原至少一个 tab。"""
        sess = get_field(_SESSION_KEY)
        if not isinstance(sess, dict):
            return False
        tabs = sess.get("tabs")
        if not isinstance(tabs, list) or not tabs:
            return False

        self._loading = True
        try:
            restored = 0
            for entry in tabs:
                if not isinstance(entry, dict):
                    continue
                title = entry.get("title") or self._next_untitled_name()
                current_file = entry.get("current_file")
                if current_file is not None and not isinstance(current_file, str):
                    current_file = None
                graph_data = entry.get("graph")
                dirty = bool(entry.get("dirty", False))

                doc = self._create_doc(title, current_file)
                if isinstance(graph_data, dict):
                    try:
                        doc.graph.load_from_dict(graph_data)
                    except Exception as e:
                        warning(f"还原 tab '{title}' 的节点图失败: {e}")
                # 文件丢失：保持 graph 不动；标 dirty 提示用户另存
                if current_file and not os.path.isfile(current_file):
                    dirty = True
                doc.dirty = dirty
                self._refresh_tab_text(doc)
                restored += 1

            if restored == 0:
                return False

            cur = sess.get("current_index", 0)
            if not isinstance(cur, int) or not (0 <= cur < len(self._docs)):
                cur = 0
            self._tabbar.setCurrentIndex(cur)
            self._on_tab_changed(cur)

            # 还原后让 canvas 各自适应视图 + 渲染预览
            for doc in self._docs:
                doc.canvas.refresh_all_previews()
            active = self._active
            if active is not None:
                QTimer.singleShot(100, active.canvas.fit_view)
            return True
        finally:
            self._loading = False

    def _save_session(self):
        """把当前所有 tab 的快照写入配置（含未保存的图）。"""
        if self._loading:
            return
        tabs = []
        for doc in self._docs:
            try:
                graph_data = doc.graph.to_dict()
            except Exception as e:
                warning(f"序列化 tab '{doc.title}' 节点图失败: {e}")
                graph_data = None
            tabs.append({
                "title": doc.title,
                "current_file": doc.current_file,
                "dirty": bool(doc.dirty),
                "graph": graph_data,
            })
        try:
            cur = self._tabbar.currentIndex()
        except Exception:
            cur = 0
        set_field(_SESSION_KEY, {
            "tabs": tabs,
            "current_index": cur,
        })

    def _load_from_path(self, path: str):
        """加载 .node 文件。若活动 doc 是空白未保存,就地加载；否则新开 tab。"""
        doc = self._active
        if doc is None or len(doc.graph.nodes) > 0 or doc.current_file is not None:
            doc = self._create_doc(os.path.basename(path), path)
            self._tabbar.setCurrentIndex(self._docs.index(doc))
            self._on_tab_changed(self._docs.index(doc))

        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        # 清掉现有状态（含 canvas 预览），再加载；期间屏蔽 dirty 标记
        self._loading = True
        try:
            doc.canvas.clear_all_previews()
            doc.graph.load_from_dict(data)
            self._prop_panel.set_graph(doc.graph)
            doc.canvas.update()
            doc.canvas.refresh_all_previews()
            QTimer.singleShot(100, doc.canvas.fit_view)

            doc.current_file = path
            doc.dirty = False
            self._set_doc_title(doc, os.path.basename(path))
        finally:
            self._loading = False
        set_field(_LAST_FILE_KEY, path)
        info(f"已加载节点文件: {path}")
        self._schedule_session_save()

    def _open_file(self):
        # 默认目录：当前文件所在目录 → 上次文件所在目录 → cwd
        start_dir = ""
        if self._current_file and os.path.isfile(self._current_file):
            start_dir = os.path.dirname(self._current_file)
        else:
            last = get_field(_LAST_FILE_KEY)
            if last and os.path.isfile(last):
                start_dir = os.path.dirname(last)

        path, _ = QFileDialog.getOpenFileName(
            self, "打开节点文件", start_dir, _NODE_FILE_FILTER)
        if not path:
            return
        try:
            self._load_from_path(path)
            InfoBar.success(
                title="已打开",
                content=os.path.basename(path),
                parent=self,
                position=InfoBarPosition.TOP_RIGHT,
                duration=2000,
            )
        except Exception as e:
            log_error(f"打开节点文件失败: {e}")
            InfoBar.error(
                title="打开失败",
                content=str(e),
                parent=self,
                position=InfoBarPosition.TOP_RIGHT,
                duration=3500,
            )

    def _save_file(self):
        # 没关联文件 → 走另存为
        if not self._current_file:
            self._save_file_as()
            return
        self._save_to_path(self._current_file)

    def _save_file_as(self):
        start_dir = ""
        if self._current_file:
            start_dir = self._current_file
        else:
            last = get_field(_LAST_FILE_KEY)
            if last:
                start_dir = os.path.dirname(last) if os.path.isfile(last) else ""
            if not start_dir:
                start_dir = "untitled.node"

        path, _ = QFileDialog.getSaveFileName(
            self, "另存为", start_dir, _NODE_FILE_FILTER)
        if not path:
            return
        # 用户没写扩展名时补 .node（保持 JSON 内容不变）
        if not os.path.splitext(path)[1]:
            path += ".node"
        self._save_to_path(path)

    def _save_to_path(self, path: str):
        try:
            data = self.graph.to_dict()
            os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            log_error(f"保存节点文件失败: {e}")
            InfoBar.error(
                title="保存失败",
                content=str(e),
                parent=self,
                position=InfoBarPosition.TOP_RIGHT,
                duration=3500,
            )
            return

        doc = self._active
        if doc:
            doc.current_file = path
            doc.dirty = False
            self._set_doc_title(doc, os.path.basename(path))
        set_field(_LAST_FILE_KEY, path)
        info(f"节点文件已保存: {path}")
        InfoBar.success(
            title="已保存",
            content=os.path.basename(path),
            parent=self,
            position=InfoBarPosition.TOP_RIGHT,
            duration=2000,
        )
        self._schedule_session_save()


# ══════════════════════════════════════════════════════════════════════
#  独立运行入口
# ══════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    app = QApplication(sys.argv)
    setTheme(Theme.DARK)
    # setThemeColor(ACCENT)

    win = QWidget()
    win.setWindowTitle("Node Editor — Demo")
    win.resize(1400, 900)
    lay = QVBoxLayout(win)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.addWidget(NodeEditorPage(win))
    win.show()

    sys.exit(app.exec())
