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

# ── 布局常量（对齐 LiteGraph 源码默认值） ─────────────────────────────
NODE_W = 210
NODE_HEADER = 30
PORT_ROW_H = 20
PORT_R = 4
PORT_PAD = 10
CORNER_R = 8
SHADOW_OFF = 6

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
C_SHADOW = QColor(0, 0, 0, 130)

C_TITLE_TEXT = QColor(255, 255, 255)
C_PORT_TEXT = QColor(204, 204, 204)
C_PORT_BORDER = QColor(0, 0, 0)
C_SEPARATOR = QColor(0, 0, 0, 80)

C_CUT_LINE = QColor(255, 80, 80, 220)


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

        self.setMinimumSize(800, 600)
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
    def node_rect(self, node: NodeInstance) -> QRectF:
        nd = node.definition
        n_ports = max(
            len(nd.inputs) if nd else 0,
            len(nd.outputs) if nd else 0
        )
        h = NODE_HEADER + max(n_ports, 1) * PORT_ROW_H + 8
        return QRectF(node.x, node.y, NODE_W, h)

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

        # 主体
        body = QPainterPath()
        body.addRoundedRect(nr, CORNER_R, CORNER_R)
        p.fillPath(body, C_NODE_BODY)

        # 标题栏（统一深灰，无分类色）
        hdr_rect = QRectF(nr.x(), nr.y(), nr.width(), NODE_HEADER)
        hdr_path = QPainterPath()
        hdr_path.addRoundedRect(hdr_rect, CORNER_R, CORNER_R)
        hdr_path.addRect(QRectF(hdr_rect.x(),
                                hdr_rect.y() + CORNER_R,
                                hdr_rect.width(),
                                hdr_rect.height() - CORNER_R))
        hdr_path = hdr_path.simplified().intersected(body)
        p.fillPath(hdr_path, C_NODE_HEADER)

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

        # 边框
        if selected:
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

            iid = self.hit_node(cp)
            if iid:
                if not (e.modifiers() & Qt.KeyboardModifier.ShiftModifier):
                    self._selected.clear()
                self._selected.add(iid)
                self._drag_node = iid
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

        if self._drag_node and self._drag_start:
            new_pos = cp - self._drag_start
            self.graph.move_node(self._drag_node, new_pos.x(), new_pos.y())
            self.update()

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

            self._drag_node = None
            self._drag_start = None
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
        if e.key() == Qt.Key.Key_Delete:
            for iid in list(self._selected):
                self.graph.remove_node(iid)
            self._selected.clear()
            self.node_deselected.emit()
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

    # ── 公共方法 ──────────────────────────────────────────────────────
    def fit_view(self):
        if not self.graph.nodes:
            return
        min_x = min(n.x for n in self.graph.nodes.values())
        min_y = min(n.y for n in self.graph.nodes.values())
        max_x = max(n.x + NODE_W for n in self.graph.nodes.values())
        max_y = max(n.y + 120 for n in self.graph.nodes.values())
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
