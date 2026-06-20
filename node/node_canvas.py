"""
node_canvas.py
~~~~~~~~~~~~~~
节点画布 QWidget —— ComfyUI / LiteGraph 风格。

交互：
  - 左键拖输出端口 → 拉新连线
  - 左键拖输入端口 → 摘下已有连线，重新连
  - 右键单击连线附近 → 删除该连线
  - 右键拖动 → 画切刀，松开切断所有相交连线
  - Delete → 删除选中节点
  - 中键 / 左键拖空白 → 平移
  - 滚轮 → 缩放
"""

from __future__ import annotations
from typing import TYPE_CHECKING

from PyQt6.QtCore import (
    Qt, QPointF, QRectF, QSizeF, pyqtSignal, QPoint
)
from PyQt6.QtGui import (
    QPainter, QPen, QBrush, QColor, QFont, QFontMetrics,
    QPainterPath, QCursor, QKeyEvent, QWheelEvent,
    QMouseEvent, QPaintEvent,
)
from PyQt6.QtWidgets import QWidget, QApplication

from node.node_registry import PORT_COLORS, REGISTRY
from node.node_graph import NodeGraph, NodeInstance, Connection
from node.node_preview import (
    create_preview, detect_file_type, PREVIEW_W, _is_binary_file,
)

# ── 布局常量（对齐 LiteGraph 源码默认值） ─────────────────────────────
NODE_W = 210
NODE_HEADER = 30
PORT_ROW_H = 20
PORT_R = 4
PORT_PAD = 10
CORNER_R = 8
SHADOW_OFF = 6
PREVIEW_H = 140
PREVIEW_PAD = 6
RESIZE_HANDLE = 10        # 右下角拖拽热区尺寸(画布坐标)
MIN_NODE_W = 140
MIN_NODE_H = 60

# FONT_TITLE = QFont("Arial", 11, QFont.Weight.Bold)
# FONT_PORT = QFont("Arial", 9)

# ── 配色 ──────────────────────────────────────────────────────────────
C_BG = QColor(35, 35, 35)
C_GRID_MINOR = QColor(45, 45, 45)
C_GRID_MAJOR = QColor(55, 55, 55)

C_NODE_BODY = QColor(53, 53, 53)
C_NODE_HEADER = QColor(45, 45, 45)
C_NODE_BORDER = QColor(0, 0, 0)
C_NODE_SELECTED = QColor(255, 255, 255)
C_NODE_EXECUTING = QColor(76, 220, 100)   # ComfyUI 风格运行中绿色高亮
C_SHADOW = QColor(0, 0, 0, 130)

C_TITLE_TEXT = QColor(255, 255, 255)
C_PORT_TEXT = QColor(204, 204, 204)
C_PORT_BORDER = QColor(0, 0, 0)
C_SEPARATOR = QColor(0, 0, 0, 80)

C_CUT_LINE = QColor(255, 80, 80, 220)

# text_note 专属：sticky-note 暖色 与数据节点区分开
C_NOTE_BODY = QColor(70, 64, 46)
C_NOTE_HEADER = QColor(60, 56, 42)
C_NOTE_TEXT = QColor(245, 225, 160)
NOTE_PAD_X = 10
NOTE_PAD_Y = 8
NOTE_MIN_BODY_H = 40


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


class NodeCanvas(QWidget):
    """节点画布 (ComfyUI / LiteGraph 风格)。"""

    node_selected = pyqtSignal(str)
    node_deselected = pyqtSignal()
    graph_changed = pyqtSignal()

    def __init__(self, graph: NodeGraph, parent=None):
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)

        self.graph = graph

        self._offset = QPointF(0, 0)
        self._scale = 1.0

        self._selected:   set[str] = set()
        self._drag_node:  str | None = None
        self._drag_start: QPointF | None = None
        # 多选拖拽：记录拖动起点的画布坐标 + 每个被选中节点拖动前的位置
        # 拖动时所有选中节点按同一 delta 平移
        self._drag_anchor: QPointF | None = None
        self._drag_origins: dict[str, tuple[float, float]] = {}

        # 连线拖拽
        self._wire_src_iid:  str | None = None
        self._wire_src_port: str | None = None
        self._wire_cur:      QPointF | None = None
        self._wire_hover:    tuple | None = None

        # 平移
        self._pan_start:  QPoint | None = None
        self._pan_offset: QPointF | None = None

        # 切刀
        self._cut_start: QPointF | None = None
        self._cut_end:   QPointF | None = None

        # 缩放节点
        self._resize_node:   str | None = None
        self._resize_start:  QPointF | None = None
        self._resize_orig_w: float = 0
        self._resize_orig_h: float = 0

        # 正在执行的节点 iid (ComfyUI 风格绿色外框)
        self._executing_iid: str | None = None

        # 预览 overlay
        # 音/视频/图片走 widget overlay；文本类直接缓存内容,在 _draw_node
        # 里用 QPainter 画 —— 这样字体/缩放与其它节点元素一致。
        self._preview_widgets: dict[str, QWidget] = {}
        self._preview_paths: dict[str, str] = {}
        self._preview_text: dict[str, str] = {}

        self.setMinimumSize(800, 600)
        # 让画布在被点击后接收键盘事件 否则 X/Shift+D/Ctrl+A 不会到 keyPressEvent
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._set_style()

    # ── 样式 ──────────────────────────────────────────────────────────
    def _set_style(self):
        self.setStyleSheet("NodeCanvas{background:#232323;}")

    # ── 坐标转换 ──────────────────────────────────────────────────────
    def to_canvas(self, screen: QPointF) -> QPointF:
        return (screen - self._offset) / self._scale

    def to_screen(self, canvas: QPointF) -> QPointF:
        return canvas * self._scale + self._offset

    # ── 节点几何 ──────────────────────────────────────────────────────
    def _auto_node_size(self, node: NodeInstance) -> tuple[float, float]:
        nd = node.definition
        # text_note / text_input：宽度由用户决定 高度根据文本折行自动计算
        # 区别仅在端口行占位 —— text_input 顶部要留一行给 text_out 端口
        if node.def_id in ("text_note", "text_input"):
            w = node.w if node.w > 0 else NODE_W
            body_w = max(40, w - NOTE_PAD_X * 2)
            text = str(node.params.get("text", "")) or " "
            fm = QFontMetrics(QApplication.font())
            rect = fm.boundingRect(
                0, 0, int(body_w), 100000,
                int(Qt.TextFlag.TextWordWrap), text,
            )
            text_h = max(NOTE_MIN_BODY_H, rect.height() + NOTE_PAD_Y * 2)
            # text_input 顶部多一个端口行
            port_h = PORT_ROW_H if node.def_id == "text_input" else 0
            return float(w), float(NODE_HEADER + port_h + text_h)

        n_ports = max(
            len(nd.inputs) if nd else 0,
            len(nd.outputs) if nd else 0
        )
        w = NODE_W
        h = NODE_HEADER + max(n_ports, 1) * PORT_ROW_H + 8
        if node.def_id == "preview":
            h += PREVIEW_H + PREVIEW_PAD
        return w, h

    def node_rect(self, node: NodeInstance) -> QRectF:
        aw, ah = self._auto_node_size(node)
        w = node.w if node.w > 0 else aw
        if node.def_id in ("text_note", "text_input"):
            # 高度始终跟随文本 用户改 node.h 不生效（避免文字被裁断）
            h = ah
        else:
            h = node.h if node.h > 0 else ah
        return QRectF(node.x, node.y, w, h)

    def port_pos(self, node: NodeInstance, port_name: str, is_output: bool) -> QPointF | None:
        nd = node.definition
        if nd is None:
            return None
        ports = nd.outputs if is_output else nd.inputs
        idx = next((i for i, p in enumerate(ports)
                   if p.name == port_name), None)
        if idx is None:
            return None
        nr = self.node_rect(node)
        y = nr.y() + NODE_HEADER + idx * PORT_ROW_H + PORT_ROW_H / 2 + 2
        x = nr.right() if is_output else nr.left()
        return QPointF(x, y)

    def hit_port(self, canvas_pos: QPointF) -> tuple[str, str, bool] | None:
        for iid, node in self.graph.nodes.items():
            nd = node.definition
            if nd is None:
                continue
            for is_out, ports in ((True, nd.outputs), (False, nd.inputs)):
                for port in ports:
                    pp = self.port_pos(node, port.name, is_out)
                    if pp and (canvas_pos - pp).manhattanLength() < PORT_R * 3:
                        return (iid, port.name, is_out)
        return None

    def hit_node(self, canvas_pos: QPointF) -> str | None:
        for iid, node in reversed(list(self.graph.nodes.items())):
            if self.node_rect(node).contains(canvas_pos):
                return iid
        return None

    def hit_resize(self, canvas_pos: QPointF) -> str | None:
        for iid, node in reversed(list(self.graph.nodes.items())):
            nr = self.node_rect(node)
            grip = QRectF(nr.right() - RESIZE_HANDLE,
                          nr.bottom() - RESIZE_HANDLE,
                          RESIZE_HANDLE, RESIZE_HANDLE)
            if grip.contains(canvas_pos):
                return iid
        return None

    def _find_connection_to_input(self, iid: str, port_name: str):
        """查找连到指定输入端口的连线，返回 (cid, conn) 或 None。"""
        for cid, conn in self.graph.connections.items():
            if conn.dst_iid == iid and conn.dst_port == port_name:
                return cid, conn
        return None

    # ── 绘制 ──────────────────────────────────────────────────────────
    def paintEvent(self, e: QPaintEvent):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)

        # 背景 + 网格
        p.fillRect(self.rect(), C_BG)
        self._draw_grid(p)

        p.save()
        p.translate(self._offset)
        p.scale(self._scale, self._scale)

        # 连线
        for conn in self.graph.connections.values():
            self._draw_connection(p, conn)

        # 拖拽连线预览
        if self._wire_src_iid and self._wire_cur:
            src_node = self.graph.nodes.get(self._wire_src_iid)
            if src_node:
                sp = self.port_pos(src_node, self._wire_src_port, True)
                if sp:
                    self._draw_wire(p, sp, self._wire_cur,
                                    QColor(220, 220, 220, 200), dashed=True)

        # 节点
        for node in self.graph.nodes.values():
            self._draw_node_shadow(p, node)
        for node in self.graph.nodes.values():
            self._draw_node(p, node)

        # 切刀线（画布坐标系内，跟随平移缩放）
        if self._cut_start is not None and self._cut_end is not None:
            pen = QPen(C_CUT_LINE, 2.0 / self._scale, Qt.PenStyle.DashLine)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawLine(self._cut_start, self._cut_end)

        p.restore()
        self._sync_preview_overlays()

    # ── 网格 ──────────────────────────────────────────────────────────
    def _draw_grid(self, p: QPainter):
        """LiteGraph 风格：细线网格 + 每 5 格一条主线。"""
        step = 25.0 * self._scale
        if step < 5:
            return

        w, h = self.width(), self.height()
        ox = self._offset.x() % step
        oy = self._offset.y() % step

        major_every = 5
        start_x_idx = int(-self._offset.x() // step)
        start_y_idx = int(-self._offset.y() // step)

        # 细线
        p.setPen(QPen(C_GRID_MINOR, 1))
        x, idx = ox, start_x_idx
        while x < w:
            if idx % major_every != 0:
                p.drawLine(int(x), 0, int(x), h)
            x += step
            idx += 1

        y, idx = oy, start_y_idx
        while y < h:
            if idx % major_every != 0:
                p.drawLine(0, int(y), w, int(y))
            y += step
            idx += 1

        # 主线
        p.setPen(QPen(C_GRID_MAJOR, 1))
        x, idx = ox, start_x_idx
        while x < w:
            if idx % major_every == 0:
                p.drawLine(int(x), 0, int(x), h)
            x += step
            idx += 1
        y, idx = oy, start_y_idx
        while y < h:
            if idx % major_every == 0:
                p.drawLine(0, int(y), w, int(y))
            y += step
            idx += 1

    # ── 节点 ──────────────────────────────────────────────────────────
    def _draw_node_shadow(self, p: QPainter, node: NodeInstance):
        nr = self.node_rect(node)
        shadow = QPainterPath()
        shadow.addRoundedRect(
            nr.adjusted(2, 4, SHADOW_OFF, SHADOW_OFF),
            CORNER_R, CORNER_R
        )
        p.fillPath(shadow, C_SHADOW)

    def _draw_node(self, p: QPainter, node: NodeInstance):
        nd = node.definition
        selected = node.iid in self._selected
        nr = self.node_rect(node)
        is_note = node.def_id == "text_note"
        body_color = C_NOTE_BODY if is_note else C_NODE_BODY
        header_color = C_NOTE_HEADER if is_note else C_NODE_HEADER

        # 主体
        body = QPainterPath()
        body.addRoundedRect(nr, CORNER_R, CORNER_R)
        p.fillPath(body, body_color)

        # 标题栏（统一深灰，无分类色）
        hdr_rect = QRectF(nr.x(), nr.y(), nr.width(), NODE_HEADER)
        hdr_path = QPainterPath()
        hdr_path.addRoundedRect(hdr_rect, CORNER_R, CORNER_R)
        hdr_path.addRect(QRectF(hdr_rect.x(),
                                hdr_rect.y() + CORNER_R,
                                hdr_rect.width(),
                                hdr_rect.height() - CORNER_R))
        hdr_path = hdr_path.simplified().intersected(body)
        p.fillPath(hdr_path, header_color)

        # 标题与内容分隔线
        p.setPen(QPen(C_SEPARATOR, 1))
        p.drawLine(
            QPointF(nr.x() + 1, nr.y() + NODE_HEADER),
            QPointF(nr.right() - 1, nr.y() + NODE_HEADER)
        )

        # 标题文字
        # p.setFont(FONT_TITLE)
        p.setPen(C_TITLE_TEXT)
        p.drawText(
            QRectF(nr.x() + 12, nr.y(), nr.width() - 24, NODE_HEADER),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            node.title
        )

        # text_note：把 params["text"] 折行画进 body 区域
        if is_note:
            text = str(node.params.get("text", ""))
            body_rect = QRectF(
                nr.x() + NOTE_PAD_X,
                nr.y() + NODE_HEADER + NOTE_PAD_Y,
                max(0.0, nr.width() - NOTE_PAD_X * 2),
                max(0.0, nr.height() - NODE_HEADER - NOTE_PAD_Y * 2),
            )
            p.setPen(C_NOTE_TEXT)
            p.drawText(
                body_rect,
                int(Qt.AlignmentFlag.AlignTop
                    | Qt.AlignmentFlag.AlignLeft
                    | Qt.TextFlag.TextWordWrap),
                text,
            )

        # text_input：在 text_out 端口下方画 params["text"]
        if node.def_id == "text_input":
            text = str(node.params.get("text", ""))
            top = nr.y() + NODE_HEADER + PORT_ROW_H + 4
            body_rect = QRectF(
                nr.x() + NOTE_PAD_X,
                top,
                max(0.0, nr.width() - NOTE_PAD_X * 2),
                max(0.0, nr.bottom() - top - NOTE_PAD_Y),
            )
            p.setPen(C_PORT_TEXT)
            p.drawText(
                body_rect,
                int(Qt.AlignmentFlag.AlignTop
                    | Qt.AlignmentFlag.AlignLeft
                    | Qt.TextFlag.TextWordWrap),
                text,
            )

        # 边框
        executing = node.iid == self._executing_iid
        if executing:
            # 运行中节点：粗绿色外框 优先级高于选中态 (ComfyUI 风格)
            pen = QPen(C_NODE_EXECUTING, 3.0)
        elif selected:
            pen = QPen(C_NODE_SELECTED, 1.5)
        else:
            pen = QPen(C_NODE_BORDER, 1.0)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(body)

        if nd is None:
            return

        # 端口
        for idx, port in enumerate(nd.inputs):
            y = nr.y() + NODE_HEADER + idx * PORT_ROW_H + PORT_ROW_H / 2 + 2
            self._draw_port(p, nr.x(), y, port.type)
            # p.setFont(FONT_PORT)
            p.setPen(C_PORT_TEXT)
            p.drawText(
                QRectF(nr.x() + PORT_PAD, y - PORT_ROW_H / 2,
                       nr.width() - PORT_PAD * 2, PORT_ROW_H),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                port.label
            )

        for idx, port in enumerate(nd.outputs):
            y = nr.y() + NODE_HEADER + idx * PORT_ROW_H + PORT_ROW_H / 2 + 2
            self._draw_port(p, nr.right(), y, port.type)
            # p.setFont(FONT_PORT)
            p.setPen(C_PORT_TEXT)
            p.drawText(
                QRectF(nr.x() + PORT_PAD, y - PORT_ROW_H / 2,
                       nr.width() - PORT_PAD * 2, PORT_ROW_H),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                port.label
            )

        # preview 节点 + 缓存了文本内容：在画布坐标系里画 (跟随缩放、用系统字体)
        # 跟 widget overlay 那种"字体不变 盒子在变"的失真表现做切割
        if node.def_id == "preview" and node.iid in self._preview_text:
            area = self._preview_area(node)
            if area.width() > 8 and area.height() > 8:
                bg_path = QPainterPath()
                bg_path.addRoundedRect(area, 4, 4)
                p.fillPath(bg_path, QColor(0x2B, 0x2B, 0x2B))
                p.setPen(QColor(224, 224, 224))
                p.drawText(
                    area.adjusted(6, 4, -6, -4),
                    int(Qt.AlignmentFlag.AlignTop
                        | Qt.AlignmentFlag.AlignLeft
                        | Qt.TextFlag.TextWordWrap),
                    self._preview_text[node.iid],
                )

        # 右下角缩放手柄
        gx = nr.right() - 2
        gy = nr.bottom() - 2
        p.setPen(QPen(QColor(160, 160, 160, 100), 1))
        for i in range(3):
            off = (i + 1) * 3
            p.drawLine(QPointF(gx - off, gy), QPointF(gx, gy - off))

    def _draw_port(self, p: QPainter, cx: float, cy: float, port_type: str):
        color = QColor(PORT_COLORS.get(port_type, "#AAAAAA"))
        p.setBrush(QBrush(color))
        p.setPen(QPen(C_PORT_BORDER, 1.0))
        p.drawEllipse(QPointF(cx, cy), PORT_R, PORT_R)

    # ── 连线 ──────────────────────────────────────────────────────────
    def _draw_connection(self, p: QPainter, conn: Connection):
        src_node = self.graph.nodes.get(conn.src_iid)
        dst_node = self.graph.nodes.get(conn.dst_iid)
        if not src_node or not dst_node:
            return
        sp = self.port_pos(src_node, conn.src_port, True)
        dp = self.port_pos(dst_node, conn.dst_port, False)
        if not sp or not dp:
            return

        src_def = src_node.definition
        port_def = next((pp for pp in src_def.outputs if pp.name == conn.src_port), None) \
            if src_def else None
        color = QColor(PORT_COLORS.get(
            port_def.type if port_def else "any", "#AAAAAA"))
        self._draw_wire(p, sp, dp, color)

    def _draw_wire(self, p: QPainter, sp: QPointF, dp: QPointF,
                   color: QColor, dashed: bool = False):
        dist = max(50.0, abs(dp.x() - sp.x()) * 0.5)
        cp1 = QPointF(sp.x() + dist, sp.y())
        cp2 = QPointF(dp.x() - dist, dp.y())

        path = QPainterPath(sp)
        path.cubicTo(cp1, cp2, dp)

        pen = QPen(color, 3.0, Qt.PenStyle.SolidLine,
                   Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        if dashed:
            pen.setStyle(Qt.PenStyle.DashLine)
            pen.setWidthF(2.0)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(path)

    # ── 切刀工具 ──────────────────────────────────────────────────────
    @staticmethod
    def _segments_intersect(p1: QPointF, p2: QPointF,
                            p3: QPointF, p4: QPointF) -> bool:
        """判断线段 p1-p2 与 p3-p4 是否相交。"""
        def ccw(a, b, c):
            return (c.y() - a.y()) * (b.x() - a.x()) > \
                   (b.y() - a.y()) * (c.x() - a.x())
        return (ccw(p1, p3, p4) != ccw(p2, p3, p4)
                and ccw(p1, p2, p3) != ccw(p1, p2, p4))

    def _bezier_samples(self, sp: QPointF, dp: QPointF, n: int = 24):
        """采样贝塞尔曲线为多段折线。"""
        dist = max(50.0, abs(dp.x() - sp.x()) * 0.5)
        cp1 = QPointF(sp.x() + dist, sp.y())
        cp2 = QPointF(dp.x() - dist, dp.y())
        pts = []
        for i in range(n + 1):
            t = i / n
            u = 1 - t
            x = (u**3 * sp.x() + 3 * u**2 * t * cp1.x()
                 + 3 * u * t**2 * cp2.x() + t**3 * dp.x())
            y = (u**3 * sp.y() + 3 * u**2 * t * cp1.y()
                 + 3 * u * t**2 * cp2.y() + t**3 * dp.y())
            pts.append(QPointF(x, y))
        return pts

    def _cut_intersecting(self, p1: QPointF, p2: QPointF) -> int:
        """删除所有与切线相交的连线，返回数量。"""
        to_remove = []
        for cid, conn in list(self.graph.connections.items()):
            src_node = self.graph.nodes.get(conn.src_iid)
            dst_node = self.graph.nodes.get(conn.dst_iid)
            if not src_node or not dst_node:
                continue
            sp = self.port_pos(src_node, conn.src_port, True)
            dp = self.port_pos(dst_node, conn.dst_port, False)
            if not sp or not dp:
                continue
            pts = self._bezier_samples(sp, dp)
            for a, b in zip(pts, pts[1:]):
                if self._segments_intersect(p1, p2, a, b):
                    to_remove.append(cid)
                    break
        for cid in to_remove:
            self.graph.remove_connection(cid)
        return len(to_remove)

    # ── 事件 ──────────────────────────────────────────────────────────
    def wheelEvent(self, e: QWheelEvent):
        delta = e.angleDelta().y()
        factor = 1.12 if delta > 0 else 1 / 1.12
        center = QPointF(e.position())
        self._offset = center + (self._offset - center) * factor
        self._scale = max(0.2, min(3.0, self._scale * factor))
        self.update()

    def mousePressEvent(self, e: QMouseEvent):
        cp = self.to_canvas(QPointF(e.position()))

        if e.button() == Qt.MouseButton.MiddleButton:
            self._pan_start = e.pos()
            self._pan_offset = QPointF(self._offset)
            return

        if e.button() == Qt.MouseButton.LeftButton:
            hit = self.hit_port(cp)
            if hit:
                iid, port_name, is_out = hit
                if is_out:
                    # 从输出端口拉新线
                    self._wire_src_iid = iid
                    self._wire_src_port = port_name
                    self._wire_cur = cp
                    return
                else:
                    # 从输入端口摘下已有连线，重新拉
                    found = self._find_connection_to_input(iid, port_name)
                    if found:
                        cid, conn = found
                        src_iid, src_port = conn.src_iid, conn.src_port
                        self.graph.remove_connection(cid)
                        self.graph_changed.emit()
                        self._wire_src_iid = src_iid
                        self._wire_src_port = src_port
                        self._wire_cur = cp
                        self.update()
                        return

            # 缩放手柄优先于节点拖拽
            resize_iid = self.hit_resize(cp)
            if resize_iid:
                node = self.graph.nodes[resize_iid]
                nr = self.node_rect(node)
                self._resize_node = resize_iid
                self._resize_start = cp
                self._resize_orig_w = nr.width()
                self._resize_orig_h = nr.height()
                if resize_iid not in self._selected:
                    self._selected.clear()
                    self._selected.add(resize_iid)
                    self.node_selected.emit(resize_iid)
                self.update()
                return

            iid = self.hit_node(cp)
            if iid:
                additive = bool(e.modifiers() & (
                    Qt.KeyboardModifier.ShiftModifier
                    | Qt.KeyboardModifier.ControlModifier))
                if additive:
                    # Ctrl/Shift+click: toggle 当前节点 已选则移出 否则加入
                    if iid in self._selected:
                        self._selected.discard(iid)
                        if self._selected:
                            self.node_selected.emit(next(iter(self._selected)))
                        else:
                            self.node_deselected.emit()
                        self.update()
                        return  # 取消选中时不开始拖拽
                    self._selected.add(iid)
                else:
                    # 点到已选节点：保留整组选择 直接进入群组拖拽
                    # 点到未选节点：清空再选这一个
                    if iid not in self._selected:
                        self._selected.clear()
                        self._selected.add(iid)
                # 群组拖拽：记录每个被选节点的起始位置
                self._drag_node = iid
                self._drag_anchor = cp
                self._drag_origins = {
                    sid: (self.graph.nodes[sid].x, self.graph.nodes[sid].y)
                    for sid in self._selected if sid in self.graph.nodes
                }
                # 旧字段保留 兼容仍可能在别处引用 _drag_start 的代码
                self._drag_start = cp - QPointF(self.graph.nodes[iid].x,
                                                self.graph.nodes[iid].y)
                self.node_selected.emit(iid)
            else:
                self._selected.clear()
                self.node_deselected.emit()
                self._pan_start = e.pos()
                self._pan_offset = QPointF(self._offset)

            self.update()

        elif e.button() == Qt.MouseButton.RightButton:
            # 右键开始切刀（点击就是单点；拖动形成切线）
            self._cut_start = cp
            self._cut_end = cp
            self.update()

    def mouseMoveEvent(self, e: QMouseEvent):
        cp = self.to_canvas(QPointF(e.position()))

        if self._cut_start is not None:
            self._cut_end = cp
            self.update()
            return

        if self._pan_start is not None:
            delta = QPointF(e.pos() - self._pan_start)
            self._offset = self._pan_offset + delta
            self.update()
            return

        if self._wire_src_iid is not None:
            self._wire_cur = cp
            self._wire_hover = self.hit_port(cp)
            self.update()
            return

        if self._resize_node is not None and self._resize_start is not None:
            delta = cp - self._resize_start
            node = self.graph.nodes.get(self._resize_node)
            if node:
                aw, ah = self._auto_node_size(node)
                new_w = max(aw, self._resize_orig_w + delta.x())
                new_h = max(ah, self._resize_orig_h + delta.y())
                node.w = new_w
                node.h = new_h
            self.update()
            return

        if self._drag_node and self._drag_anchor is not None and self._drag_origins:
            # 群组拖拽：所有选中节点按同一 delta 平移
            delta = cp - self._drag_anchor
            for sid, (ox, oy) in self._drag_origins.items():
                self.graph.move_node(sid, ox + delta.x(), oy + delta.y())
            self.update()
            return

        # 空闲时更新光标
        if self.hit_resize(cp):
            self.setCursor(QCursor(Qt.CursorShape.SizeFDiagCursor))
        else:
            self.setCursor(QCursor(Qt.CursorShape.ArrowCursor))

    def mouseReleaseEvent(self, e: QMouseEvent):
        cp = self.to_canvas(QPointF(e.position()))

        if e.button() == Qt.MouseButton.MiddleButton:
            self._pan_start = None
            return

        if e.button() == Qt.MouseButton.LeftButton:
            self._pan_start = None

            if self._wire_src_iid is not None:
                hit = self.hit_port(cp)
                if hit:
                    dst_iid, dst_port, is_out = hit
                    if not is_out:
                        conn = self.graph.add_connection(
                            self._wire_src_iid, self._wire_src_port,
                            dst_iid, dst_port
                        )
                        if conn:
                            self.graph_changed.emit()

                self._wire_src_iid = None
                self._wire_src_port = None
                self._wire_cur = None
                self._wire_hover = None

            self._resize_node = None
            self._resize_start = None
            # 拖拽真的发生过位置变化才发 graph_changed (避免单击空跑)
            if self._drag_node and self._drag_anchor is not None:
                moved = False
                for sid, (ox, oy) in self._drag_origins.items():
                    node = self.graph.nodes.get(sid)
                    if node and (node.x != ox or node.y != oy):
                        moved = True
                        break
                if moved:
                    self.graph_changed.emit()
            self._drag_node = None
            self._drag_start = None
            self._drag_anchor = None
            self._drag_origins = {}
            self.update()

        elif e.button() == Qt.MouseButton.RightButton:
            if self._cut_start is not None and self._cut_end is not None:
                # 短距离视为单击 → 删除附近一根连线
                if (self._cut_end - self._cut_start).manhattanLength() < 6:
                    self._try_delete_connection(self._cut_start)
                else:
                    # 切刀：删除所有相交连线
                    n = self._cut_intersecting(self._cut_start, self._cut_end)
                    if n > 0:
                        self.graph_changed.emit()
                self._cut_start = None
                self._cut_end = None
                self.update()

    def keyPressEvent(self, e: QKeyEvent):
        mods = e.modifiers()
        ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
        shift = bool(mods & Qt.KeyboardModifier.ShiftModifier)

        # Delete / X：删除选中（Blender 风格双绑定）
        if e.key() in (Qt.Key.Key_Delete, Qt.Key.Key_X) and not ctrl:
            self._delete_selected()
            return

        # Shift+D：复制选中（节点 + 内部连线 + 全量 params）
        if e.key() == Qt.Key.Key_D and shift and not ctrl:
            self._duplicate_selected()
            return

        # Ctrl+A：全选
        if e.key() == Qt.Key.Key_A and ctrl and not shift:
            self._select_all()
            return

        super().keyPressEvent(e)

    # ── 选中节点的高层操作 ───────────────────────────────────────────
    def _delete_selected(self):
        if not self._selected:
            return
        for iid in list(self._selected):
            self.remove_preview(iid)
            self.graph.remove_node(iid)
        self._selected.clear()
        self.node_deselected.emit()
        self.graph_changed.emit()
        self.update()

    def _select_all(self):
        all_iids = set(self.graph.nodes.keys())
        if not all_iids or all_iids == self._selected:
            return
        self._selected = all_iids
        # 属性面板显示其中一个（用任意一个 焦点节点不再有特殊地位）
        self.node_selected.emit(next(iter(all_iids)))
        self.update()

    def _duplicate_selected(self):
        """Shift+D：复制当前选中的节点。

        - 节点位置整体偏移 +30/+30 与原节点错开 防止重叠
        - params 深拷贝 (含路径/数值/枚举等所有字段)
        - 仅复制"两端都在选中集合里"的连线 跨界连线不复制
        - 复制完后选中集合切到新节点 方便用户立刻继续拖拽
        """
        if not self._selected:
            return
        import copy
        OFFSET_X, OFFSET_Y = 30.0, 30.0

        iid_map: dict[str, str] = {}
        for sid in list(self._selected):
            orig = self.graph.nodes.get(sid)
            if orig is None:
                continue
            new_node = self.graph.add_node(
                orig.def_id,
                orig.x + OFFSET_X,
                orig.y + OFFSET_Y,
            )
            # 保留用户调过的尺寸
            new_node.w = orig.w
            new_node.h = orig.h
            # params 深拷贝（防止以后 params 里出现 list/dict 时共享引用）
            new_node.params = copy.deepcopy(orig.params)
            iid_map[sid] = new_node.iid

        if not iid_map:
            return

        # 复制两端都在选中集合里的连线
        for conn in list(self.graph.connections.values()):
            if conn.src_iid in iid_map and conn.dst_iid in iid_map:
                self.graph.add_connection(
                    iid_map[conn.src_iid], conn.src_port,
                    iid_map[conn.dst_iid], conn.dst_port,
                )

        # 切到新节点
        self._selected = set(iid_map.values())
        self.node_selected.emit(next(iter(self._selected)))
        self.graph_changed.emit()
        self.update()

    def _try_delete_connection(self, cp: QPointF):
        THRESHOLD = 8.0
        for cid, conn in list(self.graph.connections.items()):
            src_node = self.graph.nodes.get(conn.src_iid)
            dst_node = self.graph.nodes.get(conn.dst_iid)
            if not src_node or not dst_node:
                continue
            sp = self.port_pos(src_node, conn.src_port, True)
            dp = self.port_pos(dst_node, conn.dst_port, False)
            if not sp or not dp:
                continue
            if self._point_near_bezier(cp, sp, dp, THRESHOLD):
                self.graph.remove_connection(cid)
                self.graph_changed.emit()
                self.update()
                return

    @staticmethod
    def _point_near_bezier(pt: QPointF, sp: QPointF, dp: QPointF,
                           threshold: float) -> bool:
        dist = max(50.0, abs(dp.x() - sp.x()) * 0.5)
        cp1 = QPointF(sp.x() + dist, sp.y())
        cp2 = QPointF(dp.x() - dist, dp.y())
        for t in [i / 20 for i in range(21)]:
            bx = (_lerp(_lerp(sp.x(), cp1.x(), t), _lerp(cp1.x(), cp2.x(), t), t) * (1 - t)
                  + _lerp(_lerp(cp1.x(), cp2.x(), t), _lerp(cp2.x(), dp.x(), t), t) * t)
            by = (_lerp(_lerp(sp.y(), cp1.y(), t), _lerp(cp1.y(), cp2.y(), t), t) * (1 - t)
                  + _lerp(_lerp(cp1.y(), cp2.y(), t), _lerp(cp2.y(), dp.y(), t), t) * t)
            if (pt - QPointF(bx, by)).manhattanLength() < threshold:
                return True
        return False

    # ── 预览 overlay ────────────────────────────────────────────────
    def _preview_area(self, node: NodeInstance) -> QRectF:
        nd = node.definition
        n_ports = max(len(nd.inputs) if nd else 0,
                      len(nd.outputs) if nd else 0)
        nr = self.node_rect(node)
        top = nr.y() + NODE_HEADER + max(n_ports, 1) * PORT_ROW_H + 8 + PREVIEW_PAD
        return QRectF(nr.x() + PREVIEW_PAD, top,
                      nr.width() - PREVIEW_PAD * 2,
                      nr.bottom() - top - PREVIEW_PAD)

    def _sync_preview_overlays(self):
        alive = set()
        for iid, node in self.graph.nodes.items():
            if node.def_id != "preview":
                continue
            alive.add(iid)
            w = self._preview_widgets.get(iid)
            if w is None:
                continue
            if self._scale < 0.35:
                w.hide()
                continue
            area = self._preview_area(node)
            tl = self.to_screen(QPointF(area.x(), area.y()))
            br = self.to_screen(QPointF(area.right(), area.bottom()))
            sw = max(20, int(br.x() - tl.x()))
            sh = max(20, int(br.y() - tl.y()))
            w.setGeometry(int(tl.x()), int(tl.y()), sw, sh)
            w.show()

        stale = (set(self._preview_widgets.keys())
                 | set(self._preview_text.keys())) - alive
        for iid in stale:
            self.remove_preview(iid)

    def update_preview(self, iid: str, path: str | None):
        if path and path == self._preview_paths.get(iid):
            return
        self.remove_preview(iid)
        if not path:
            return

        # 文本类预览：直接缓存内容 由 _draw_node 用 QPainter 画
        # 不走 QTextEdit overlay —— overlay 的字体不随画布缩放,缩放后会模糊
        ftype = detect_file_type(path)
        is_text = ftype == "text"
        if not is_text and ftype == "unknown":
            try:
                is_text = not _is_binary_file(path)
            except Exception:
                is_text = False
        if is_text:
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read(2048)
            except Exception:
                content = "(无法读取)"
            self._preview_text[iid] = content
            self._preview_paths[iid] = path
            self.update()
            return

        w = create_preview(path, self)
        if w is None:
            return
        if not getattr(w, "INTERACTIVE", False):
            w.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._preview_widgets[iid] = w
        self._preview_paths[iid] = path
        self.update()

    def remove_preview(self, iid: str):
        w = self._preview_widgets.pop(iid, None)
        self._preview_paths.pop(iid, None)
        self._preview_text.pop(iid, None)
        if w is not None:
            if hasattr(w, "cleanup"):
                w.cleanup()
            w.hide()
            w.deleteLater()

    def resolve_preview_path(self, node: NodeInstance) -> str | None:
        path = node.params.get("path", "").strip()
        if path:
            return path
        nd = node.definition
        if nd is None:
            return None
        for port in nd.inputs:
            for conn in self.graph.connections.values():
                if conn.dst_iid == node.iid and conn.dst_port == port.name:
                    src = self.graph.nodes.get(conn.src_iid)
                    if src:
                        for k in ("path", "file", "filepath"):
                            v = src.params.get(k, "")
                            if v and isinstance(v, str) and v.strip():
                                return v.strip()
        return None

    def refresh_all_previews(self):
        for iid, node in self.graph.nodes.items():
            if node.def_id != "preview":
                continue
            path = self.resolve_preview_path(node)
            self.update_preview(iid, path)

    def clear_all_previews(self):
        for iid in list(self._preview_widgets.keys()):
            self.remove_preview(iid)

    # ── 公共方法 ──────────────────────────────────────────────────────
    def set_executing_node(self, iid: str | None):
        """设置当前正在执行的节点 iid;空串/None 表示无节点在执行。"""
        new_iid = iid or None
        if new_iid == self._executing_iid:
            return
        self._executing_iid = new_iid
        self.update()

    def fit_view(self):
        if not self.graph.nodes:
            return
        rects = [self.node_rect(n) for n in self.graph.nodes.values()]
        min_x = min(r.x() for r in rects)
        min_y = min(r.y() for r in rects)
        max_x = max(r.right() for r in rects)
        max_y = max(r.bottom() for r in rects)
        pad = 60
        cx = (min_x + max_x) / 2
        cy = (min_y + max_y) / 2
        sw, sh = self.width(), self.height()
        scale_x = (sw - 2 * pad) / max(1, max_x - min_x)
        scale_y = (sh - 2 * pad) / max(1, max_y - min_y)
        self._scale = max(0.2, min(1.5, min(scale_x, scale_y)))
        self._offset = QPointF(sw / 2 - cx * self._scale,
                               sh / 2 - cy * self._scale)
        self.update()
