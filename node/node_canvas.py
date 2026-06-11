"""
node_canvas.py
~~~~~~~~~~~~~~
节点画布 QWidget —— ComfyUI 风格。
"""

from __future__ import annotations
import math
from typing import TYPE_CHECKING

from PyQt6.QtCore import (
    Qt, QPointF, QRectF, QSizeF, pyqtSignal, QPoint
)
from PyQt6.QtGui import (
    QPainter, QPen, QBrush, QColor, QFont, QFontMetrics,
    QPainterPath, QCursor, QKeyEvent, QWheelEvent,
    QMouseEvent, QPaintEvent, QLinearGradient,
)
from PyQt6.QtWidgets import QWidget, QApplication

from node_registry import PORT_COLORS, REGISTRY
from node_graph import NodeGraph, NodeInstance, Connection

# ── 布局常量 ──────────────────────────────────────────────────────────
NODE_W = 220        # 节点宽度
NODE_HEADER = 26         # 标题栏高度
PORT_ROW_H = 22         # 每行端口高度
PORT_R = 5          # 端口圆半径
PORT_PAD = 12         # 端口到左/右边缘距离
CORNER_R = 6          # 节点圆角（ComfyUI 偏小）
SHADOW_OFF = 4          # 阴影偏移

FONT_TITLE = QFont("Segoe UI", 9, QFont.Weight.DemiBold)
FONT_PORT = QFont("Segoe UI", 8)
FONT_CATEGORY = QFont("Segoe UI", 7)

# ── 配色（ComfyUI 风格） ──────────────────────────────────────────────
C_BG = QColor(35, 35, 35)
C_GRID_DOT = QColor(55, 55, 55)
C_NODE_BODY = QColor(53, 53, 53, 235)
C_NODE_BODY_ALT = QColor(45, 45, 45, 235)   # slot 交替行
C_NODE_BORDER = QColor(20, 20, 20)
C_NODE_BORDER_HI = QColor(255, 204, 0)        # 选中：ComfyUI 黄
C_HEADER_OVERLAY = QColor(0, 0, 0, 60)        # 头部上加一层暗色叠加
C_TEXT = QColor(220, 220, 220)
C_TEXT_DIM = QColor(170, 170, 170)
C_SHADOW = QColor(0, 0, 0, 110)
C_SEPARATOR = QColor(0, 0, 0, 70)


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


class NodeCanvas(QWidget):
    """节点画布主控件 (ComfyUI 风格)。"""

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

        self._wire_src_iid:  str | None = None
        self._wire_src_port: str | None = None
        self._wire_cur:      QPointF | None = None
        self._wire_hover:    tuple | None = None

        self._pan_start:  QPoint | None = None
        self._pan_offset: QPointF | None = None

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
        h = NODE_HEADER + max(n_ports, 1) * PORT_ROW_H + 6
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
        y = nr.y() + NODE_HEADER + idx * PORT_ROW_H + PORT_ROW_H / 2
        # 端口正好压在节点边线上（ComfyUI 风格）
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
                    if pp and (canvas_pos - pp).manhattanLength() < PORT_R * 2.4:
                        return (iid, port.name, is_out)
        return None

    def hit_node(self, canvas_pos: QPointF) -> str | None:
        for iid, node in reversed(list(self.graph.nodes.items())):
            if self.node_rect(node).contains(canvas_pos):
                return iid
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
                                    QColor(220, 220, 220, 180), dashed=True)

        # 节点（先画阴影再画本体，避免阴影互相覆盖正确顺序）
        for node in self.graph.nodes.values():
            self._draw_node_shadow(p, node)
        for node in self.graph.nodes.values():
            self._draw_node(p, node)

        p.restore()

    def _draw_grid(self, p: QPainter):
        """点状网格，ComfyUI 风格更暗更稀。"""
        spacing = 30 * self._scale
        if spacing < 6:
            return
        p.setPen(QPen(C_GRID_DOT, 1.2))
        ox = self._offset.x() % spacing
        oy = self._offset.y() % spacing
        x = ox
        while x < self.width():
            y = oy
            while y < self.height():
                p.drawPoint(int(x), int(y))
                y += spacing
            x += spacing

    def _draw_node_shadow(self, p: QPainter, node: NodeInstance):
        nr = self.node_rect(node)
        shadow = QPainterPath()
        shadow.addRoundedRect(
            nr.adjusted(SHADOW_OFF, SHADOW_OFF, SHADOW_OFF, SHADOW_OFF),
            CORNER_R, CORNER_R
        )
        p.fillPath(shadow, C_SHADOW)

    def _draw_node(self, p: QPainter, node: NodeInstance):
        nd = node.definition
        selected = node.iid in self._selected
        nr = self.node_rect(node)

        # 节点本体
        body = QPainterPath()
        body.addRoundedRect(nr, CORNER_R, CORNER_R)
        p.fillPath(body, C_NODE_BODY)

        # slot 行交替底色（端口区）
        n_rows = max(
            len(nd.inputs) if nd else 0,
            len(nd.outputs) if nd else 0,
            1
        )
        for i in range(n_rows):
            if i % 2 == 1:
                row_rect = QRectF(
                    nr.x() + 1,
                    nr.y() + NODE_HEADER + i * PORT_ROW_H,
                    nr.width() - 2,
                    PORT_ROW_H
                )
                row_path = QPainterPath()
                row_path.addRect(row_rect)
                row_path = row_path.intersected(body)
                p.fillPath(row_path, C_NODE_BODY_ALT)

        # 标题栏 —— 纯色块 + 顶部渐变高光
        hdr_rect = QRectF(nr.x(), nr.y(), nr.width(), NODE_HEADER)
        hdr_path = QPainterPath()
        hdr_path.addRoundedRect(hdr_rect, CORNER_R, CORNER_R)
        hdr_path.addRect(hdr_rect.adjusted(0, CORNER_R, 0, 0))
        hdr_path = hdr_path.intersected(body)

        color_str = nd.color if nd else "#555555"
        base = QColor(color_str)
        grad = QLinearGradient(hdr_rect.topLeft(), hdr_rect.bottomLeft())
        grad.setColorAt(0.0, base.lighter(118))
        grad.setColorAt(1.0, base.darker(108))
        p.fillPath(hdr_path, QBrush(grad))
        # 暗色叠加层让饱和度更克制
        p.fillPath(hdr_path, C_HEADER_OVERLAY)

        # 标题与正文分隔线
        p.setPen(QPen(C_SEPARATOR, 1))
        p.drawLine(
            QPointF(nr.x() + 1, nr.y() + NODE_HEADER),
            QPointF(nr.right() - 1, nr.y() + NODE_HEADER)
        )

        # 标题文字
        p.setFont(FONT_TITLE)
        p.setPen(QColor(245, 245, 245))
        p.drawText(
            QRectF(nr.x() + 10, nr.y(), nr.width() - 20, NODE_HEADER),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            node.title
        )

        # 边框（选中时高亮黄）
        if selected:
            p.setPen(QPen(C_NODE_BORDER_HI, 1.8))
        else:
            p.setPen(QPen(C_NODE_BORDER, 1.0))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(body)

        if nd is None:
            return

        # 端口
        for idx, port in enumerate(nd.inputs):
            y = nr.y() + NODE_HEADER + idx * PORT_ROW_H + PORT_ROW_H / 2
            self._draw_port(p, nr.x(), y, port.type, False)
            p.setFont(FONT_PORT)
            p.setPen(C_TEXT)
            p.drawText(
                QRectF(nr.x() + PORT_PAD, y - PORT_ROW_H / 2,
                       nr.width() - PORT_PAD * 2, PORT_ROW_H),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                port.label
            )

        for idx, port in enumerate(nd.outputs):
            y = nr.y() + NODE_HEADER + idx * PORT_ROW_H + PORT_ROW_H / 2
            self._draw_port(p, nr.right(), y, port.type, True)
            p.setFont(FONT_PORT)
            p.setPen(C_TEXT)
            p.drawText(
                QRectF(nr.x() + PORT_PAD, y - PORT_ROW_H / 2,
                       nr.width() - PORT_PAD * 2, PORT_ROW_H),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                port.label
            )

    def _draw_port(self, p: QPainter, cx: float, cy: float,
                   port_type: str, is_output: bool):
        color = QColor(PORT_COLORS.get(port_type, "#AAAAAA"))
        # 外圈深色描边 + 内圈纯色（ComfyUI 端口经典样式）
        p.setBrush(QBrush(color))
        p.setPen(QPen(QColor(15, 15, 15), 1.2))
        p.drawEllipse(QPointF(cx, cy), PORT_R, PORT_R)
        # 中心高光点
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(color.lighter(150)))
        p.drawEllipse(QPointF(cx, cy), PORT_R * 0.35, PORT_R * 0.35)

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
        # 控制点距离按水平距离自适应，连线更柔和
        dx = max(40.0, abs(dp.x() - sp.x()) * 0.5)
        cp1 = QPointF(sp.x() + dx, sp.y())
        cp2 = QPointF(dp.x() - dx, dp.y())

        path = QPainterPath(sp)
        path.cubicTo(cp1, cp2, dp)

        # 外发光（让线条更醒目）
        if not dashed:
            glow = QColor(color)
            glow.setAlpha(70)
            p.setPen(QPen(glow, 5.0, Qt.PenStyle.SolidLine,
                          Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(path)

        pen = QPen(color, 2.6, Qt.PenStyle.SolidLine,
                   Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        if dashed:
            pen.setStyle(Qt.PenStyle.DashLine)
            pen.setWidthF(2.0)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(path)

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
                    self._wire_src_iid = iid
                    self._wire_src_port = port_name
                    self._wire_cur = cp
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
            self._try_delete_connection(cp)

    def mouseMoveEvent(self, e: QMouseEvent):
        cp = self.to_canvas(QPointF(e.position()))

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
        dx = max(40.0, abs(dp.x() - sp.x()) * 0.5)
        cp1 = QPointF(sp.x() + dx, sp.y())
        cp2 = QPointF(dp.x() - dx, dp.y())
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
