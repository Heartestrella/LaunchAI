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
    ToolButton, LineEdit as FLineEdit, ComboBox,
    ProgressBar, SmoothScrollArea,
    InfoBar, InfoBarPosition,
    FluentIcon as FIF,
    IconWidget, isDarkTheme,
)
from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QFrame, QSizePolicy, QScrollArea,
    QLineEdit, QSplitter, QTreeWidget, QTreeWidgetItem,
    QDockWidget, QMainWindow, QAbstractItemView
)
from PyQt6.QtGui import (
    QColor, QFont, QKeySequence, QShortcut, QIcon
)
from PyQt6.QtCore import (
    Qt, QPoint, QRectF, QPropertyAnimation, QEasingCurve,
    QTimer, pyqtSignal
)
from PyQt6.QtWidgets import QFileDialog, QDialog, QDialogButtonBox, QVBoxLayout, QListWidget, QListWidgetItem
import json
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 用户配置里记录"上次打开的 .node 文件"
from utils.configer import get_field, set_field
_LAST_FILE_KEY = "node_editor.last_file"
_NODE_FILE_FILTER = "节点图 (*.node *.json);;所有文件 (*)"


ACCENT = "#0078D4"
_FILE_PARAM_KEYS = {"path", "file", "filepath", "filename", "input"}
_DIR_PARAM_KEYS = {"directory", "dir", "folder", "out_dir", "output_dir"}
_DEVICE_PARAM_KEYS = {"device", "gpu", "gpu_id"}
_FILE_PARAM_KEYS = {"path", "file", "filepath", "filename", "input"}
_DIR_PARAM_KEYS = {"directory", "dir", "folder", "out_dir", "output_dir"}
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

        name = CaptionLabel(key, row)
        name.setStyleSheet("color:rgba(255,255,255,0.65);")
        lay.addWidget(name)

        key_low = key.lower()

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

        self.graph = NodeGraph()
        self._spawn_pos_offset = 0   # 自动错开新节点位置
        self._runner: GraphWorker | None = None
        # 当前关联文件路径；None = 新建未保存
        self._current_file: str | None = None

        self._build_ui()
        # 启动加载策略：用户配置里有"上次打开的文件"就读；读不到才回退到 demo
        self._auto_load_on_start()

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
            "Shift+A 添加节点  ·  右键删除连线  ·  Delete 删除节点  ·  滚轮缩放  ·  中键平移", toolbar)
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

        # ── 主体：画布 + 右侧属性面板 ────────────────────────────────
        splitter = QSplitter(Qt.Orientation.Horizontal, self)
        splitter.setHandleWidth(1)
        splitter.setStyleSheet(
            "QSplitter::handle{background:rgba(255,255,255,0.08);}")

        # 画布
        self._canvas = NodeCanvas(self.graph, splitter)
        self._canvas.node_selected.connect(self._on_node_selected)
        self._canvas.node_deselected.connect(self._on_node_deselected)
        self._canvas.graph_changed.connect(self._on_graph_changed)
        splitter.addWidget(self._canvas)

        # 属性面板
        prop_container = QFrame(splitter)
        prop_container.setFixedWidth(260)
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

        self._prop_panel = PropertyPanel(
            self.graph, self.cuda_drivers, prop_container)
        self._prop_panel.setStyleSheet("color:#e0e0e0;")
        self._prop_panel.param_changed.connect(self._on_param_changed)
        pc_lay.addWidget(self._prop_panel)

        splitter.addWidget(prop_container)
        splitter.setSizes([1200, 260])
        root.addWidget(splitter, 1)

        # ── Shift+A 节点面板 ──────────────────────────────────────────
        self._picker = NodePickerPanel(self)
        self._picker.node_chosen.connect(self._spawn_node)

        # 快捷键
        sc = QShortcut(QKeySequence("Shift+A"), self)
        sc.activated.connect(self._open_picker)

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
        else:
            for conn in self.graph.connections.values():
                if conn.src_iid == iid:
                    dst = self.graph.nodes.get(conn.dst_iid)
                    if dst and dst.def_id == "preview":
                        path = self._canvas.resolve_preview_path(dst)
                        self._canvas.update_preview(dst.iid, path)

    def _on_node_selected(self, iid: str):
        self._prop_panel.show_node(iid)

    def _on_node_deselected(self):
        self._prop_panel.clear_selection()

    def _on_graph_changed(self):
        self._canvas.refresh_all_previews()

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
        if self._runner is None or not self._runner.isRunning():
            return
        # 防止反复点击
        self._run_btn.setEnabled(False)
        self._run_btn.setText("终止中…")
        self._runner.cancel()
        InfoBar.warning(
            title="正在终止",
            content="已请求取消，等待当前节点收尾…",
            parent=self,
            position=InfoBarPosition.TOP_RIGHT,
            duration=2000,
        )

    def _run_plan(self):
        # 控制台打印一份计划（保留旧行为，便于排查）
        self.graph.print_execution_plan()

        # 启动 GraphWorker
        self._runner = GraphWorker(self.graph, parent=self)
        self._runner.output.connect(self._on_runner_output)
        self._runner.progress.connect(self._on_runner_progress)
        self._runner.finished.connect(self._on_runner_finished)
        self._runner.error.connect(self._on_runner_error)
        self._runner.start()

        self._set_run_btn_running(True)
        InfoBar.info(
            title="开始执行",
            content="节点图已提交后台执行，日志见控制台",
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

    def _on_runner_finished(self, results: dict):
        info(f"节点图执行结束，输出节点数: {len(results)}")
        InfoBar.success(
            title="执行完成",
            content=f"共 {len(results)} 个节点产出结果",
            parent=self,
            position=InfoBarPosition.TOP_RIGHT,
            duration=3000,
        )
        # 把执行结果中的实际输出路径写回到下游 preview 节点的 params["path"]，
        # 这样 NodeCanvas.resolve_preview_path 才能找到 demucs / whisper /
        # realesrgan 等中间节点的产物。
        for iid, node in self.graph.nodes.items():
            if node.def_id != "preview":
                continue
            resolved = None
            for conn in self.graph.connections.values():
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
        self._canvas.refresh_all_previews()
        self._runner = None
        self._run_btn.setEnabled(True)
        self._set_run_btn_running(False)

    def _on_runner_error(self, msg: str):
        log_error(f"节点图执行失败: {msg}")
        # 区分用户取消 vs 真正错误
        if "取消" in msg:
            InfoBar.warning(
                title="已终止",
                content=msg,
                parent=self,
                position=InfoBarPosition.TOP_RIGHT,
                duration=2500,
            )
        else:
            InfoBar.error(
                title="执行失败",
                content=msg,
                parent=self,
                position=InfoBarPosition.TOP_RIGHT,
                duration=4000,
            )
        self._runner = None
        self._run_btn.setEnabled(True)
        self._set_run_btn_running(False)

    def _clear_graph(self):
        self._canvas.clear_all_previews()
        for iid in list(self.graph.nodes.keys()):
            self.graph.remove_node(iid)
        self._prop_panel.clear_selection()
        self._canvas.update()

    # ── 文件 IO ──────────────────────────────────────────────────────

    def _auto_load_on_start(self):
        """启动时按用户配置里的 last_file 自动加载；失败则回退到 demo 节点。"""
        last = get_field(_LAST_FILE_KEY)
        if last and isinstance(last, str) and os.path.isfile(last):
            try:
                self._load_from_path(last)
                return
            except Exception as e:
                warning(f"加载上次的节点文件失败: {e}")
        # 没有上次记录或加载失败 —— 保留旧的 demo 行为兜底
        self._add_demo_nodes()

    def _load_from_path(self, path: str):
        """从指定路径加载 .node 文件到当前图。"""
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        # 清掉现有状态（含 canvas 预览），再加载
        self._canvas.clear_all_previews()
        self.graph.load_from_dict(data)
        self._prop_panel.clear_selection()
        self._canvas.update()
        self._canvas.refresh_all_previews()
        QTimer.singleShot(100, self._canvas.fit_view)

        self._current_file = path
        set_field(_LAST_FILE_KEY, path)
        info(f"已加载节点文件: {path}")

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

        self._current_file = path
        set_field(_LAST_FILE_KEY, path)
        info(f"节点文件已保存: {path}")
        InfoBar.success(
            title="已保存",
            content=os.path.basename(path),
            parent=self,
            position=InfoBarPosition.TOP_RIGHT,
            duration=2000,
        )


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
