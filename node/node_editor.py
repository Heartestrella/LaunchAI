"""
node_editor.py
~~~~~~~~~~~~~~
主编辑器窗口：画布 + Shift+A 节点面板 + 属性面板 + 工具栏
"""

from node_canvas import NodeCanvas
from node_graph import NodeGraph
from node_registry import REGISTRY, CATEGORY_COLORS, PORT_COLORS, NodeDef
from qfluentwidgets import (
    setTheme, Theme, setThemeColor,
    FluentWindow, NavigationItemPosition,
    ElevatedCardWidget, CardWidget,
    TitleLabel, SubtitleLabel, BodyLabel, CaptionLabel, StrongBodyLabel,
    PrimaryPushButton, PushButton, TransparentPushButton, TransparentToolButton,
    ToolButton, LineEdit as FLineEdit, SearchLineEdit,
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
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


ACCENT = "#0078D4"


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
            fnt = QFont("Segoe UI", 11, QFont.Weight.Bold)
            cat_item.setFont(0, fnt)
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
#  属性面板
# ══════════════════════════════════════════════════════════════════════

class PropertyPanel(QWidget):
    """右侧属性面板，显示选中节点的参数。"""

    param_changed = pyqtSignal(str, str, object)  # iid, key, value

    def __init__(self, graph: NodeGraph, parent=None):
        super().__init__(parent)
        self.graph = graph
        self._iid: str | None = None
        self.setSizePolicy(QSizePolicy.Policy.Preferred,
                           QSizePolicy.Policy.Expanding)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        # 标题
        self._title_lbl = StrongBodyLabel("未选中节点", self)
        root.addWidget(self._title_lbl)
        root.addWidget(self._make_sep())

        # 端口信息
        self._port_area = QWidget(self)
        self._port_lay = QVBoxLayout(self._port_area)
        self._port_lay.setContentsMargins(0, 0, 0, 0)
        self._port_lay.setSpacing(4)
        root.addWidget(self._port_area)
        root.addWidget(self._make_sep())

        # 参数区
        self._param_scroll = SmoothScrollArea(self)
        self._param_scroll.setWidgetResizable(True)
        self._param_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._param_scroll.setStyleSheet("background:transparent;border:none;")
        self._param_container = QWidget()
        self._param_lay = QVBoxLayout(self._param_container)
        self._param_lay.setContentsMargins(0, 0, 0, 0)
        self._param_lay.setSpacing(6)
        self._param_scroll.setWidget(self._param_container)
        root.addWidget(self._param_scroll, 1)

        root.addStretch()

    def show_node(self, iid: str):
        self._iid = iid
        node = self.graph.nodes.get(iid)
        if not node:
            return

        nd = node.definition
        self._title_lbl.setText(f"{node.title}  [{iid}]")

        # 清空端口区
        for i in reversed(range(self._port_lay.count())):
            w = self._port_lay.itemAt(i).widget()
            if w:
                w.deleteLater()

        if nd:
            if nd.inputs:
                self._port_lay.addWidget(CaptionLabel("输入端口", self))
                for p in nd.inputs:
                    self._port_lay.addWidget(self._port_badge(p.label, p.type))
            if nd.outputs:
                self._port_lay.addWidget(CaptionLabel("输出端口", self))
                for p in nd.outputs:
                    self._port_lay.addWidget(self._port_badge(p.label, p.type))

        # 清空参数区
        for i in reversed(range(self._param_lay.count())):
            w = self._param_lay.itemAt(i).widget()
            if w:
                w.deleteLater()

        if node.params:
            self._param_lay.addWidget(CaptionLabel("参数", self))
            for key, val in node.params.items():
                row = QHBoxLayout()
                k_lbl = CaptionLabel(key, self)
                k_lbl.setFixedWidth(90)
                k_lbl.setStyleSheet("color:rgba(180,180,180,180);")
                edit = QLineEdit(str(val), self)
                edit.setStyleSheet("""
                    QLineEdit {
                        background:#1e1e1e;
                        border:1px solid rgba(255,255,255,0.12);
                        border-radius:5px;
                        color:#e0e0e0;
                        padding:3px 7px;
                        font-size:12px;
                    }
                    QLineEdit:focus{border-color:#0078D4;}
                """)
                # 捕获 key 到闭包

                def _make_handler(k):
                    def _h(text):
                        self.graph.set_param(iid, k, text)
                        self.param_changed.emit(iid, k, text)
                    return _h
                edit.textChanged.connect(_make_handler(key))
                row.addWidget(k_lbl)
                row.addWidget(edit, 1)
                w = QWidget(self)
                w.setLayout(row)
                self._param_lay.addWidget(w)

    def clear_selection(self):
        self._iid = None
        self._title_lbl.setText("未选中节点")
        for lay in (self._port_lay, self._param_lay):
            for i in reversed(range(lay.count())):
                w = lay.itemAt(i).widget()
                if w:
                    w.deleteLater()

    def _port_badge(self, label: str, ptype: str) -> QWidget:
        w = QWidget(self)
        lay = QHBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        dot = QLabel("●", w)
        color = PORT_COLORS.get(ptype, "#AAAAAA")
        dot.setStyleSheet(f"color:{color};font-size:10px;")
        lbl = BodyLabel(label, w)
        lbl.setStyleSheet("color:rgba(200,200,200,200);font-size:12px;")
        type_lbl = CaptionLabel(ptype, w)
        type_lbl.setStyleSheet(f"color:{color};font-size:10px;")
        lay.addWidget(dot)
        lay.addWidget(lbl)
        lay.addStretch()
        lay.addWidget(type_lbl)
        return w

    @staticmethod
    def _make_sep() -> QFrame:
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("background:rgba(255,255,255,0.08);max-height:1px;")
        return sep


# ══════════════════════════════════════════════════════════════════════
#  主编辑器页面
# ══════════════════════════════════════════════════════════════════════

class NodeEditorPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("NodeEditorPage")
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Expanding)

        self.graph = NodeGraph()
        self._spawn_pos_offset = 0   # 自动错开新节点位置

        self._build_ui()
        self._add_demo_nodes()

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
            "border-radius:8px;padding:1px 8px;font-size:11px;")
        tb_lay.addWidget(badge)
        tb_lay.addStretch()

        hint = QLabel(
            "Shift+A 添加节点  ·  右键删除连线  ·  Delete 删除节点  ·  滚轮缩放  ·  中键平移", toolbar)
        hint.setStyleSheet("color:rgba(150,150,150,140);font-size:11px;")
        tb_lay.addWidget(hint)
        tb_lay.addStretch()

        run_btn = PrimaryPushButton(FIF.PLAY, "执行计划", toolbar)
        run_btn.clicked.connect(self._run_plan)
        tb_lay.addWidget(run_btn)

        fit_btn = PushButton(FIF.FULL_SCREEN, "适应视图", toolbar)
        fit_btn.clicked.connect(lambda: self._canvas.fit_view())
        tb_lay.addWidget(fit_btn)

        clear_btn = PushButton(FIF.DELETE, "清空", toolbar)
        clear_btn.clicked.connect(self._clear_graph)
        tb_lay.addWidget(clear_btn)

        root.addWidget(toolbar)

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

        self._prop_panel = PropertyPanel(self.graph, prop_container)
        self._prop_panel.setStyleSheet("color:#e0e0e0;")
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

        self.graph.add_connection(n1.iid, "file_out", n2.iid, "audio_in")
        self.graph.add_connection(n1.iid, "file_out", n3.iid, "audio_in")
        self.graph.add_connection(n2.iid, "vocals",   n5.iid, "file_in")
        self.graph.add_connection(n3.iid, "transcript", n6.iid, "file_in")
        self.graph.add_connection(n7.iid, "file_out", n4.iid, "image_in")

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

    def _on_node_selected(self, iid: str):
        self._prop_panel.show_node(iid)

    def _on_node_deselected(self):
        self._prop_panel.clear_selection()

    def _on_graph_changed(self):
        pass  # 可在此触发自动保存 / undo 堆栈等

    def _run_plan(self):
        self.graph.print_execution_plan()
        InfoBar.success(
            title="已输出执行计划",
            content="请查看控制台",
            parent=self,
            position=InfoBarPosition.TOP_RIGHT,
            duration=2500,
        )

    def _clear_graph(self):
        for iid in list(self.graph.nodes.keys()):
            self.graph.remove_node(iid)
        self._prop_panel.clear_selection()
        self._canvas.update()


# ══════════════════════════════════════════════════════════════════════
#  独立运行入口
# ══════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    app = QApplication(sys.argv)
    setTheme(Theme.DARK)
    setThemeColor(ACCENT)

    win = QWidget()
    win.setWindowTitle("Node Editor — Demo")
    win.resize(1400, 900)
    lay = QVBoxLayout(win)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.addWidget(NodeEditorPage(win))
    win.show()

    sys.exit(app.exec())
