"""
widgets/subpage/subpage_iopaint.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
IOPaint 图像修复页面 —— 单画布 + PS 风工具集 + 前后对比滑块。

模块划分：
  - ToolPalette          : 左侧竖向工具条（笔刷/橡皮/矩形/魔棒/撤销/重做/对比）
  - MaskCanvas           : 中央 QGraphicsView，承载图像 + 蒙版 + 笔迹预览
  - IOPaintParamPanel    : 右侧参数面板
  - IOPaintPage          : 顶层 QWidget，串起上述三块 + 推理流水线
"""

from __future__ import annotations

import os
import sys
from collections import deque
from pathlib import Path
from typing import Optional

import numpy as np

from PyQt6.QtCore import (
    Qt, QPoint, QPointF, QRect, QRectF, QSize,
    pyqtSignal, QEvent
)
from PyQt6.QtGui import (
    QColor, QImage, QPixmap, QPainter, QPainterPath, QPen, QBrush,
    QKeySequence, QShortcut, QMouseEvent, QWheelEvent, QKeyEvent,
    QDragEnterEvent, QDropEvent, QCursor
)
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFrame, QSizePolicy,
    QFileDialog, QLabel, QGraphicsView, QGraphicsScene,
    QGraphicsPixmapItem, QGraphicsPathItem, QGraphicsRectItem,
    QButtonGroup, QSplitter, QSpinBox
)

from qfluentwidgets import (
    ElevatedCardWidget, TitleLabel, BodyLabel, CaptionLabel,
    StrongBodyLabel, PrimaryPushButton, PushButton, TransparentPushButton,
    TransparentToolButton, ToolButton, ComboBox, Slider, SwitchButton,
    LineEdit, ProgressBar, SmoothScrollArea, InfoBar, InfoBarPosition,
    FluentIcon as FIF, IconWidget,
)

from workers.iopaint_worker import (
    MODELS, HD_STRATEGIES, SAM_MODELS, DEFAULT_OUT_DIR, patch_iopaint_page
)
from utils.configer import get_field, set_field
from logger import info, warning, debug, error


# ── 常量（与其它页保持一致） ────────────────────────────────────────
ACCENT = "#0078D4"
SUCCESS = "#0DB37E"
WARNING = "#F7B731"
DANGER = "#FC5C65"

MODEL_DESC = {
    "lama":  "LaMa · 通用大型遮罩修复，最快最稳",
    "ldm":   "LDM · 潜空间扩散，可调步数，速度较慢",
    "mat":   "MAT · 适合人像 / 复杂结构",
    "fcf":   "FcF · 面向高分辨率",
    "manga": "Manga · 黑白漫画专用",
    "zits":  "ZITS · 结构线引导修复",
}

OUT_FORMATS = ["png", "jpg", "webp"]


# ── 工具函数 ────────────────────────────────────────────────────────
def _separator() -> QFrame:
    sep = QFrame()
    sep.setFrameShape(QFrame.Shape.HLine)
    sep.setStyleSheet("background:rgba(128,128,128,40);max-height:1px;")
    return sep


def _section_title(text: str, icon=None, parent=None) -> QWidget:
    row = QWidget(parent)
    lay = QHBoxLayout(row)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(8)
    if icon:
        ico = IconWidget(icon, row)
        ico.setFixedSize(16, 16)
        lay.addWidget(ico)
    lay.addWidget(StrongBodyLabel(text, row))
    lay.addStretch()
    return row


def _badge(text: str, color: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setStyleSheet(
        f"QLabel{{background:{color}22;color:{color};border:1px solid {color}55;"
        f"border-radius:8px;padding:1px 8px;font-size:11px;font-weight:600;}}"
    )
    return lbl


def _no_scrollbar(area):
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    try:
        vb = area.verticalScrollBar()
        hb = area.horizontalScrollBar()
        if vb is not None:
            vb.setMaximumWidth(0)
            vb.setStyleSheet("QScrollBar{width:0px;background:transparent;}")
        if hb is not None:
            hb.setMaximumHeight(0)
            hb.setStyleSheet("QScrollBar{height:0px;background:transparent;}")
    except Exception:
        pass


def _safe_fluent_icon(*candidates):
    """qfluentwidgets 在不同版本里图标名不一定都存在，挑第一个存在的。"""
    for name in candidates:
        ic = getattr(FIF, name, None)
        if ic is not None:
            return ic
    return FIF.SETTING


# ══════════════════════════════════════════════════════════════════════
#  ToolPalette —— 左侧工具栏
# ══════════════════════════════════════════════════════════════════════
class ToolPalette(QWidget):
    tool_changed = pyqtSignal(str)
    undo_requested = pyqtSignal()
    redo_requested = pyqtSignal()
    clear_requested = pyqtSignal()
    brush_size_changed = pyqtSignal(int)
    opacity_changed = pyqtSignal(int)
    compare_toggled = pyqtSignal(bool)
    apply_seg_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(190)
        self.setSizePolicy(QSizePolicy.Policy.Fixed,
                           QSizePolicy.Policy.Expanding)
        self.setStyleSheet(
            "ToolPalette{background:rgba(128,128,128,10);"
            "border-radius:10px;}"
        )

        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 12, 10, 12)
        lay.setSpacing(10)

        # ── 工具按钮组 ─────────────────────────────────────────────
        lay.addWidget(StrongBodyLabel("工具", self))
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)

        def _add_tool(key: str, label: str, icon_names: tuple):
            btn = ToolButton(_safe_fluent_icon(*icon_names), self)
            btn.setCheckable(True)
            btn.setFixedSize(40, 40)
            btn.setToolTip(label)
            btn.clicked.connect(lambda _=False, k=key: self._select_tool(k))
            self._group.addButton(btn)
            return btn

        tools_row1 = QHBoxLayout()
        tools_row1.setSpacing(6)
        self._btn_brush = _add_tool("brush", "画笔（B）",
                                    ("BRUSH", "EDIT"))
        self._btn_eraser = _add_tool("eraser", "套索 · 圈选填充（E）",
                                     ("ROUTE", "PENCIL_INK", "EDIT"))
        tools_row1.addWidget(self._btn_brush)
        tools_row1.addWidget(self._btn_eraser)
        lay.addLayout(tools_row1)

        tools_row2 = QHBoxLayout()
        tools_row2.setSpacing(6)
        self._btn_rect = _add_tool("rect", "矩形选区填充（R）",
                                   ("LAYOUT", "ZOOM"))
        self._btn_sam = _add_tool("sam", "魔棒 · 点击抠图（W）",
                                  ("ROBOT", "ACCEPT_MEDIUM", "SEARCH"))
        tools_row2.addWidget(self._btn_rect)
        tools_row2.addWidget(self._btn_sam)
        lay.addLayout(tools_row2)

        self._btn_brush.setChecked(True)
        self._current_tool = "brush"

        # ── 撤销 / 重做 / 清空 ────────────────────────────────────
        lay.addWidget(_separator())
        act_row = QHBoxLayout()
        act_row.setSpacing(6)
        self._btn_undo = TransparentToolButton(
            _safe_fluent_icon("LEFT_ARROW", "RETURN", "CANCEL"), self)
        self._btn_undo.setToolTip("撤销 (Ctrl+Z)")
        self._btn_undo.clicked.connect(self.undo_requested.emit)
        self._btn_redo = TransparentToolButton(
            _safe_fluent_icon("RIGHT_ARROW", "SEND", "ACCEPT"), self)
        self._btn_redo.setToolTip("重做 (Ctrl+Y)")
        self._btn_redo.clicked.connect(self.redo_requested.emit)
        self._btn_apply_seg = PushButton("应用魔棒", self)
        self._btn_apply_seg.setEnabled(False)
        self._btn_apply_seg.clicked.connect(self.apply_seg_requested.emit)
        act_row.addWidget(self._btn_undo)
        act_row.addWidget(self._btn_redo)
        lay.addLayout(act_row)
        lay.addWidget(self._btn_apply_seg)

        self._btn_clear = PushButton(FIF.DELETE, "清空蒙版", self)
        self._btn_clear.clicked.connect(self.clear_requested.emit)
        lay.addWidget(self._btn_clear)

        # ── 笔刷大小 / 不透明度 ──────────────────────────────────
        lay.addWidget(_separator())
        lay.addWidget(CaptionLabel("笔刷大小", self))
        sz_row = QHBoxLayout()
        self._size_slider = Slider(Qt.Orientation.Horizontal, self)
        self._size_slider.setRange(1, 200)
        self._size_slider.setValue(30)
        self._size_lbl = CaptionLabel("30", self)
        self._size_lbl.setFixedWidth(30)
        self._size_slider.valueChanged.connect(
            lambda v: (self._size_lbl.setText(str(v)),
                       self.brush_size_changed.emit(v)))
        sz_row.addWidget(self._size_slider)
        sz_row.addWidget(self._size_lbl)
        lay.addLayout(sz_row)

        lay.addWidget(CaptionLabel("蒙版不透明度", self))
        op_row = QHBoxLayout()
        self._op_slider = Slider(Qt.Orientation.Horizontal, self)
        self._op_slider.setRange(10, 100)
        self._op_slider.setValue(55)
        self._op_lbl = CaptionLabel("55", self)
        self._op_lbl.setFixedWidth(30)
        self._op_slider.valueChanged.connect(
            lambda v: (self._op_lbl.setText(str(v)),
                       self.opacity_changed.emit(v)))
        op_row.addWidget(self._op_slider)
        op_row.addWidget(self._op_lbl)
        lay.addLayout(op_row)

        # ── 切换对比 ──────────────────────────────────────────────
        lay.addWidget(_separator())
        cp_row = QHBoxLayout()
        cp_row.addWidget(CaptionLabel("切换对比", self))
        cp_row.addStretch()
        self._cmp_switch = SwitchButton(self)
        self._cmp_switch.setEnabled(False)
        self._cmp_switch.checkedChanged.connect(self.compare_toggled.emit)
        cp_row.addWidget(self._cmp_switch)
        lay.addLayout(cp_row)

        lay.addStretch()

    def _select_tool(self, key: str):
        self._current_tool = key
        # 应用魔棒按钮仅在 sam 工具激活时可点
        self._btn_apply_seg.setEnabled(False)   # 真正启用要等到画布有点击
        self.tool_changed.emit(key)

    def current_tool(self) -> str:
        return self._current_tool

    def enable_compare(self, on: bool):
        self._cmp_switch.setEnabled(on)

    def set_apply_seg_enabled(self, on: bool):
        if self._current_tool == "sam":
            self._btn_apply_seg.setEnabled(on)


# ══════════════════════════════════════════════════════════════════════
#  MaskCanvas —— 核心画布
# ══════════════════════════════════════════════════════════════════════
class MaskCanvas(QGraphicsView):
    """图像 + 蒙版 + 工具状态机 + 前后对比滑块。"""

    mask_changed = pyqtSignal()
    sam_clicks_changed = pyqtSignal(int)     # 当前累积点击数
    image_loaded = pyqtSignal(int, int)      # w, h
    request_run_seg = pyqtSignal(list)       # [(x,y,label), ...] —— 转给页面

    UNDO_DEPTH = 20
    MASK_COLOR = QColor(0, 120, 212)         # 蒙版叠色 = ACCENT

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("MaskCanvas")
        self.setRenderHints(
            QPainter.RenderHint.Antialiasing
            | QPainter.RenderHint.SmoothPixmapTransform)
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setTransformationAnchor(
            QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(
            QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setStyleSheet(
            "MaskCanvas{background:rgba(0,0,0,80);"
            "border:1px solid rgba(128,128,128,40);border-radius:8px;}"
        )
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.viewport().setCursor(Qt.CursorShape.CrossCursor)
        self.setAcceptDrops(True)

        scene = QGraphicsScene(self)
        self.setScene(scene)

        # 图层
        self._bg_item = QGraphicsPixmapItem()
        self._bg_item.setZValue(0)
        scene.addItem(self._bg_item)

        self._after_item = QGraphicsPixmapItem()
        self._after_item.setZValue(1)
        self._after_item.setVisible(False)
        scene.addItem(self._after_item)

        self._mask_item = QGraphicsPixmapItem()
        self._mask_item.setZValue(2)
        scene.addItem(self._mask_item)

        self._preview_item = QGraphicsPathItem()
        self._preview_item.setZValue(3)
        pen = QPen(self.MASK_COLOR)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        self._preview_item.setPen(pen)
        scene.addItem(self._preview_item)

        # 矩形拖框预览
        self._rect_item = QGraphicsRectItem()
        self._rect_item.setZValue(3)
        rect_pen = QPen(QColor(255, 255, 255), 1, Qt.PenStyle.DashLine)
        self._rect_item.setPen(rect_pen)
        self._rect_item.setBrush(QBrush(QColor(0, 120, 212, 60)))
        self._rect_item.setVisible(False)
        scene.addItem(self._rect_item)

        # SAM 点击标记
        self._sam_click_items: list[QGraphicsPixmapItem] = []

        # 状态
        self._image_rgb: Optional[np.ndarray] = None     # H×W×3 uint8 RGB
        self._mask_qimg: Optional[QImage] = None         # Alpha8
        self._tool = "brush"
        self._brush_size = 30
        self._mask_alpha = 140
        self._undo_stack: deque = deque(maxlen=self.UNDO_DEPTH)
        self._redo_stack: deque = deque(maxlen=self.UNDO_DEPTH)

        self._panning = False
        self._space_held = False
        self._pan_anchor: Optional[QPoint] = None

        self._stroke_active = False
        self._stroke_path: Optional[QPainterPath] = None
        self._stroke_last: Optional[QPointF] = None
        self._stroke_lasso = False

        self._rect_start: Optional[QPointF] = None
        self._sam_clicks: list[tuple[int, int, int]] = []

        self._compare_mode = False
        self._compare_split = 0.5          # 0..1
        self._before_pix: Optional[QPixmap] = None
        self._after_pix: Optional[QPixmap] = None
        self._compare_dragging = False

        # 缩放
        self._zoom = 1.0
        self._zoom_min = 0.05
        self._zoom_max = 16.0

    # ── 公共 API ──────────────────────────────────────────────────────
    def load_image(self, path: str) -> bool:
        try:
            import cv2
        except ImportError:
            return False
        # 中文路径走 imdecode
        try:
            data = np.fromfile(path, dtype=np.uint8)
            bgr = cv2.imdecode(data, cv2.IMREAD_COLOR)
        except Exception:
            bgr = cv2.imread(path)
        if bgr is None:
            return False
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        self._image_rgb = np.ascontiguousarray(rgb)

        h, w = rgb.shape[:2]
        # QImage 不复制底层 buffer，必须保活 ndarray
        qimg = QImage(self._image_rgb.data, w, h,
                      self._image_rgb.strides[0],
                      QImage.Format.Format_RGB888)
        pix = QPixmap.fromImage(qimg.copy())   # 拷贝避免引用悬挂
        self._before_pix = pix
        self._bg_item.setPixmap(pix)

        # 重置蒙版
        self._mask_qimg = QImage(w, h, QImage.Format.Format_Alpha8)
        self._mask_qimg.fill(0)
        self._refresh_mask_overlay()

        self._after_item.setPixmap(QPixmap())
        self._after_item.setVisible(False)
        self._after_pix = None
        self._compare_mode = False
        self._clear_sam_marks()
        self._sam_clicks.clear()
        self.sam_clicks_changed.emit(0)
        self._undo_stack.clear()
        self._redo_stack.clear()

        self.scene().setSceneRect(QRectF(0, 0, w, h))
        self.fitInView(self._bg_item, Qt.AspectRatioMode.KeepAspectRatio)
        self._zoom = self.transform().m11()
        self.image_loaded.emit(w, h)
        return True

    def set_tool(self, tool: str):
        self._tool = tool
        self._clear_preview_path()
        self._rect_item.setVisible(False)
        if tool != "sam":
            self._clear_sam_marks()
            self._sam_clicks.clear()
            self.sam_clicks_changed.emit(0)
        cursor = {
            "brush":  Qt.CursorShape.CrossCursor,
            "eraser": Qt.CursorShape.CrossCursor,
            "rect":   Qt.CursorShape.CrossCursor,
            "sam":    Qt.CursorShape.PointingHandCursor,
        }.get(tool, Qt.CursorShape.ArrowCursor)
        self.viewport().setCursor(cursor)

    def set_brush_size(self, size: int):
        self._brush_size = max(1, int(size))

    def set_mask_opacity(self, percent: int):
        self._mask_alpha = max(0, min(255, int(percent * 255 / 100)))
        self._refresh_mask_overlay()

    def get_image_rgb(self) -> Optional[np.ndarray]:
        return self._image_rgb

    def get_mask_gray(self) -> Optional[np.ndarray]:
        if self._mask_qimg is None:
            return None
        w, h = self._mask_qimg.width(), self._mask_qimg.height()
        ptr = self._mask_qimg.bits()
        ptr.setsize(self._mask_qimg.sizeInBytes())
        arr = np.frombuffer(ptr, dtype=np.uint8).reshape(
            (h, self._mask_qimg.bytesPerLine()))
        arr = arr[:, :w].copy()
        return (arr > 0).astype(np.uint8) * 255

    def has_mask(self) -> bool:
        m = self.get_mask_gray()
        return m is not None and bool(m.any())

    def has_image(self) -> bool:
        return self._image_rgb is not None

    def clear_mask(self):
        if self._mask_qimg is None:
            return
        self._push_undo()
        self._mask_qimg.fill(0)
        self._refresh_mask_overlay()
        self.mask_changed.emit()

    def undo(self):
        if not self._undo_stack or self._mask_qimg is None:
            return
        self._redo_stack.append(self._mask_qimg.copy())
        self._mask_qimg = self._undo_stack.pop()
        self._refresh_mask_overlay()
        self.mask_changed.emit()

    def redo(self):
        if not self._redo_stack or self._mask_qimg is None:
            return
        self._undo_stack.append(self._mask_qimg.copy())
        self._mask_qimg = self._redo_stack.pop()
        self._refresh_mask_overlay()
        self.mask_changed.emit()

    def set_after_image(self, pix: QPixmap):
        if pix.isNull():
            return
        self._after_pix = pix
        self._after_item.setPixmap(pix)
        # 默认不直接进入对比模式，由页面切换控制

    def set_compare_mode(self, on: bool):
        self._compare_mode = bool(on) and self._after_pix is not None
        self._after_item.setVisible(self._compare_mode)
        self._mask_item.setVisible(not self._compare_mode)
        self._preview_item.setVisible(not self._compare_mode)
        self._rect_item.setVisible(False)
        for it in self._sam_click_items:
            it.setVisible(not self._compare_mode)
        if self._compare_mode:
            self._compare_split = 0.5
        self.viewport().update()

    def merge_segmentation_mask(self, mask: np.ndarray):
        """把 SAM 结果（uint8 H×W，>0=前景）合并进当前蒙版。"""
        if self._mask_qimg is None or mask is None:
            return
        w, h = self._mask_qimg.width(), self._mask_qimg.height()
        if mask.shape != (h, w):
            try:
                import cv2
                mask = cv2.resize(mask, (w, h),
                                  interpolation=cv2.INTER_NEAREST)
            except Exception:
                return

        self._push_undo()
        # 用 QImage 直接覆盖白色像素：将 mask>0 的位置加进 alpha8
        mask_bool = mask > 0
        ptr = self._mask_qimg.bits()
        ptr.setsize(self._mask_qimg.sizeInBytes())
        arr = np.frombuffer(ptr, dtype=np.uint8).reshape(
            (h, self._mask_qimg.bytesPerLine()))
        # bytesPerLine 可能 > w，按 width 切片
        view = arr[:, :w]
        np.maximum(view, mask_bool.astype(np.uint8) * 255, out=view)
        self._refresh_mask_overlay()
        self._clear_sam_marks()
        self._sam_clicks.clear()
        self.sam_clicks_changed.emit(0)
        self.mask_changed.emit()

    def reset(self):
        self._bg_item.setPixmap(QPixmap())
        self._after_item.setPixmap(QPixmap())
        self._mask_item.setPixmap(QPixmap())
        self._after_item.setVisible(False)
        self._image_rgb = None
        self._mask_qimg = None
        self._before_pix = None
        self._after_pix = None
        self._undo_stack.clear()
        self._redo_stack.clear()
        self._clear_sam_marks()
        self._sam_clicks.clear()
        self.sam_clicks_changed.emit(0)
        self._compare_mode = False
        self.scene().setSceneRect(QRectF())
        self.viewport().update()

    # ── 内部 ──────────────────────────────────────────────────────────
    def _push_undo(self):
        if self._mask_qimg is None:
            return
        self._undo_stack.append(self._mask_qimg.copy())
        self._redo_stack.clear()

    def _refresh_mask_overlay(self):
        """把 Alpha8 蒙版渲染成彩色 ARGB pixmap 给 _mask_item 显示。"""
        if self._mask_qimg is None:
            self._mask_item.setPixmap(QPixmap())
            return
        w, h = self._mask_qimg.width(), self._mask_qimg.height()
        argb = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
        argb.fill(Qt.GlobalColor.transparent)
        p = QPainter(argb)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        p.fillRect(0, 0, w, h, QColor(
            self.MASK_COLOR.red(),
            self.MASK_COLOR.green(),
            self.MASK_COLOR.blue(),
            self._mask_alpha))
        p.setCompositionMode(
            QPainter.CompositionMode.CompositionMode_DestinationIn)
        p.drawImage(0, 0, self._mask_qimg)
        p.end()
        self._mask_item.setPixmap(QPixmap.fromImage(argb))

    def _clear_preview_path(self):
        self._preview_item.setPath(QPainterPath())
        self._stroke_path = None
        self._stroke_active = False

    def _clear_sam_marks(self):
        for it in self._sam_click_items:
            self.scene().removeItem(it)
        self._sam_click_items.clear()

    def _add_sam_mark(self, scene_pt: QPointF, positive: bool):
        # 用一个圆形 QPixmap 作为标记
        diameter = max(10, int(min(self.width(), self.height()) / 40))
        pix = QPixmap(diameter, diameter)
        pix.fill(Qt.GlobalColor.transparent)
        pr = QPainter(pix)
        pr.setRenderHint(QPainter.RenderHint.Antialiasing)
        col = QColor("#0DB37E") if positive else QColor("#FC5C65")
        pr.setBrush(QBrush(col))
        pr.setPen(QPen(QColor(255, 255, 255, 220), 2))
        pr.drawEllipse(1, 1, diameter - 2, diameter - 2)
        pr.end()
        item = QGraphicsPixmapItem(pix)
        item.setZValue(4)
        item.setOffset(-diameter / 2, -diameter / 2)
        item.setPos(scene_pt)
        self.scene().addItem(item)
        self._sam_click_items.append(item)

    # ── 鼠标/键盘 事件 ────────────────────────────────────────────────
    def mousePressEvent(self, e: QMouseEvent):
        if self._image_rgb is None:
            return super().mousePressEvent(e)

        # 对比模式：左键拖动改变 split
        if self._compare_mode and e.button() == Qt.MouseButton.LeftButton:
            self._compare_dragging = True
            self._update_compare_split(e.position().toPoint())
            return

        # Space 持按 / 中键 → 平移
        if (self._space_held and e.button() == Qt.MouseButton.LeftButton) \
                or e.button() == Qt.MouseButton.MiddleButton:
            self._panning = True
            self._pan_anchor = e.position().toPoint()
            self.viewport().setCursor(Qt.CursorShape.ClosedHandCursor)
            return

        if e.button() != Qt.MouseButton.LeftButton \
                and e.button() != Qt.MouseButton.RightButton:
            return super().mousePressEvent(e)

        scene_pt = self.mapToScene(e.position().toPoint())

        if self._tool in ("brush", "eraser"):
            self._push_undo()
            self._stroke_active = True
            # eraser 工具复用为「套索填充」：拖动只描预览，松开闭合后填内部
            self._stroke_lasso = (self._tool == "eraser")
            self._stroke_path = QPainterPath()
            self._stroke_path.moveTo(scene_pt)
            self._stroke_last = scene_pt
            pen = self._preview_item.pen()
            pen.setWidthF(self._brush_size if not self._stroke_lasso else 2.0)
            pen.setColor(self.MASK_COLOR)
            self._preview_item.setPen(pen)
            self._preview_item.setPath(self._stroke_path)
            # 画笔模式：单点立即落到 mask；套索模式：等闭合再填
            if not self._stroke_lasso:
                self._commit_dot(scene_pt)
        elif self._tool == "rect":
            self._rect_start = scene_pt
            self._rect_item.setRect(QRectF(scene_pt, scene_pt))
            self._rect_item.setVisible(True)
        elif self._tool == "sam":
            # 左键 = 正点，右键或 Ctrl+左键 = 负点
            modifiers = e.modifiers()
            label = 1
            if e.button() == Qt.MouseButton.RightButton or \
                    (modifiers & Qt.KeyboardModifier.ControlModifier):
                label = 0
            x = int(max(0, min(self._image_rgb.shape[1] - 1, scene_pt.x())))
            y = int(max(0, min(self._image_rgb.shape[0] - 1, scene_pt.y())))
            self._sam_clicks.append((x, y, label))
            self._add_sam_mark(QPointF(x, y), label == 1)
            self.sam_clicks_changed.emit(len(self._sam_clicks))

    def mouseMoveEvent(self, e: QMouseEvent):
        if self._compare_dragging:
            self._update_compare_split(e.position().toPoint())
            return

        if self._panning and self._pan_anchor is not None:
            delta = e.position().toPoint() - self._pan_anchor
            self._pan_anchor = e.position().toPoint()
            hbar = self.horizontalScrollBar()
            vbar = self.verticalScrollBar()
            hbar.setValue(hbar.value() - delta.x())
            vbar.setValue(vbar.value() - delta.y())
            return

        if self._stroke_active and self._stroke_path is not None:
            scene_pt = self.mapToScene(e.position().toPoint())
            self._stroke_path.lineTo(scene_pt)
            self._preview_item.setPath(self._stroke_path)
            # 画笔模式实时落到 mask；套索模式只描预览，松开再 flood-fill
            if not self._stroke_lasso:
                self._commit_segment(self._stroke_last, scene_pt)
            self._stroke_last = scene_pt
            return

        if self._rect_start is not None and self._tool == "rect":
            scene_pt = self.mapToScene(e.position().toPoint())
            self._rect_item.setRect(
                QRectF(self._rect_start, scene_pt).normalized())
            return

        return super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e: QMouseEvent):
        if self._compare_dragging:
            self._compare_dragging = False
            return

        if self._panning:
            self._panning = False
            self.viewport().setCursor(
                Qt.CursorShape.OpenHandCursor if self._space_held
                else Qt.CursorShape.CrossCursor)
            return

        if self._stroke_active:
            self._stroke_active = False
            # 套索模式：闭合 path 并把内部 flood-fill 到 mask
            if self._stroke_lasso and self._stroke_path is not None:
                self._commit_lasso_fill(self._stroke_path)
            self._preview_item.setPath(QPainterPath())
            self._stroke_path = None
            self._stroke_last = None
            self._stroke_lasso = False
            self.mask_changed.emit()
            return

        if self._rect_start is not None and self._tool == "rect":
            scene_pt = self.mapToScene(e.position().toPoint())
            rect = QRectF(self._rect_start, scene_pt).normalized()
            self._rect_start = None
            self._rect_item.setVisible(False)
            if rect.width() >= 2 and rect.height() >= 2 \
                    and self._mask_qimg is not None:
                self._push_undo()
                p = QPainter(self._mask_qimg)
                p.setCompositionMode(
                    QPainter.CompositionMode.CompositionMode_Source)
                p.fillRect(rect.toRect(), QColor(255, 255, 255, 255))
                p.end()
                self._refresh_mask_overlay()
                self.mask_changed.emit()
            return

        return super().mouseReleaseEvent(e)

    def wheelEvent(self, e: QWheelEvent):
        # Ctrl + wheel = 缩放；wheel = 改笔刷大小（笔刷工具时）
        mods = e.modifiers()
        if mods & Qt.KeyboardModifier.ControlModifier:
            delta = e.angleDelta().y()
            factor = 1.25 if delta > 0 else (1 / 1.25)
            new_zoom = self._zoom * factor
            if not (self._zoom_min <= new_zoom <= self._zoom_max):
                return
            self._zoom = new_zoom
            self.scale(factor, factor)
            return
        if self._tool in ("brush", "eraser"):
            delta = e.angleDelta().y()
            step = 2 if abs(delta) > 0 else 0
            new_size = max(1, min(200, self._brush_size +
                                  (step if delta > 0 else -step)))
            if new_size != self._brush_size:
                self._brush_size = new_size
            return
        super().wheelEvent(e)

    def keyPressEvent(self, e: QKeyEvent):
        if e.key() == Qt.Key.Key_Space and not e.isAutoRepeat():
            self._space_held = True
            self.viewport().setCursor(Qt.CursorShape.OpenHandCursor)
            return
        return super().keyPressEvent(e)

    def keyReleaseEvent(self, e: QKeyEvent):
        if e.key() == Qt.Key.Key_Space and not e.isAutoRepeat():
            self._space_held = False
            self.viewport().setCursor(Qt.CursorShape.CrossCursor)
            return
        return super().keyReleaseEvent(e)

    # ── 笔迹落盘 ──────────────────────────────────────────────────────
    def _commit_dot(self, scene_pt: QPointF):
        if self._mask_qimg is None:
            return
        p = QPainter(self._mask_qimg)
        p.setCompositionMode(
            QPainter.CompositionMode.CompositionMode_Source)
        p.setBrush(QBrush(QColor(255, 255, 255, 255)))
        p.setPen(Qt.PenStyle.NoPen)
        r = self._brush_size / 2.0
        p.drawEllipse(scene_pt, r, r)
        p.end()
        self._refresh_mask_overlay()

    def _commit_segment(self, a: QPointF, b: QPointF):
        if self._mask_qimg is None or a is None:
            return
        p = QPainter(self._mask_qimg)
        pen = QPen(QColor(255, 255, 255, 255))
        pen.setWidthF(self._brush_size)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setCompositionMode(
            QPainter.CompositionMode.CompositionMode_Source)
        p.setPen(pen)
        p.drawLine(a, b)
        p.end()
        self._refresh_mask_overlay()

    def _commit_lasso_fill(self, path: QPainterPath):
        """套索模式松开时调用：闭合 path 并把内部区域 flood-fill 到 mask。

        点数 < 3 视为无效（防止误点单点造成空多边形）。
        """
        if self._mask_qimg is None or path is None:
            return
        if path.elementCount() < 3:
            return
        closed = QPainterPath(path)
        closed.closeSubpath()
        p = QPainter(self._mask_qimg)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setCompositionMode(
            QPainter.CompositionMode.CompositionMode_Source)
        p.setBrush(QBrush(QColor(255, 255, 255, 255)))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawPath(closed)
        p.end()
        self._refresh_mask_overlay()

    # ── 对比模式渲染 ─────────────────────────────────────────────────
    def _update_compare_split(self, view_pt: QPoint):
        if not self._compare_mode or self._after_pix is None:
            return
        scene_pt = self.mapToScene(view_pt)
        sr = self.sceneRect()
        if sr.width() <= 0:
            return
        self._compare_split = max(
            0.0, min(1.0, (scene_pt.x() - sr.x()) / sr.width()))
        self.viewport().update()

    def drawForeground(self, painter: QPainter, rect: QRectF):
        if not self._compare_mode or self._after_pix is None:
            return super().drawForeground(painter, rect)
        sr = self.sceneRect()
        if sr.isEmpty():
            return
        split_x = sr.x() + sr.width() * self._compare_split
        clip = QRectF(split_x, sr.y(), sr.width() *
                      (1 - self._compare_split), sr.height())
        painter.save()
        painter.setClipRect(clip)
        painter.drawPixmap(sr, self._after_pix, QRectF(self._after_pix.rect()))
        painter.restore()

        # 滑块手柄
        pen = QPen(QColor(255, 255, 255, 220), max(2.0, 2.0 / self._zoom))
        painter.setPen(pen)
        painter.drawLine(QPointF(split_x, sr.top()),
                         QPointF(split_x, sr.bottom()))
        handle_r = max(8.0, 10.0 / self._zoom)
        painter.setBrush(QBrush(QColor(0, 120, 212, 220)))
        painter.drawEllipse(
            QPointF(split_x, sr.center().y()), handle_r, handle_r)

    # ── 拖拽加载 ──────────────────────────────────────────────────────
    def dragEnterEvent(self, e: QDragEnterEvent):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e: QDropEvent):
        for u in e.mimeData().urls():
            path = u.toLocalFile()
            if path.lower().endswith(
                    ('.png', '.jpg', '.jpeg', '.webp', '.bmp')):
                self.load_image(path)
                # 通知页面（可选）
                parent = self.parent()
                while parent is not None and not isinstance(parent, IOPaintPage):
                    parent = parent.parent()
                if parent is not None:
                    parent._on_image_loaded_from_canvas(path)
                return

    # ── 通过页面提交魔棒点击 ─────────────────────────────────────────
    def get_sam_clicks(self) -> list:
        return list(self._sam_clicks)


# ══════════════════════════════════════════════════════════════════════
#  IOPaintParamPanel —— 右侧参数面板
# ══════════════════════════════════════════════════════════════════════
class IOPaintParamPanel(QWidget):
    def __init__(self, parent=None, device_options: Optional[dict] = None):
        super().__init__(parent)
        self.device_options = device_options or {"cpu": "cpu"}
        self.setSizePolicy(QSizePolicy.Policy.Preferred,
                           QSizePolicy.Policy.Expanding)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        scroll = SmoothScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("background:transparent;border:none;")
        _no_scrollbar(scroll)
        outer.addWidget(scroll)

        body = QWidget()
        body.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Preferred)
        scroll.setWidget(body)
        lay = QVBoxLayout(body)
        lay.setContentsMargins(0, 4, 8, 16)
        lay.setSpacing(12)

        # 模型
        lay.addWidget(_section_title("修复模型",
                                     _safe_fluent_icon(
                                         "DEVELOPER_TOOLS", "APPLICATION"),
                                     body))
        self.model_combo = ComboBox(body)
        self.model_combo.addItems(MODELS)
        default_model = get_field("iopaint.model", "lama")
        if default_model in MODELS:
            self.model_combo.setCurrentText(default_model)
        self.model_combo.currentTextChanged.connect(self._on_model_changed)
        lay.addWidget(self.model_combo)

        self._model_desc = CaptionLabel("", body)
        self._model_desc.setWordWrap(True)
        self._model_desc.setStyleSheet("color:rgba(128,128,128,200);")
        lay.addWidget(self._model_desc)
        lay.addWidget(_separator())

        # 设备
        lay.addWidget(_section_title(
            "推理设备", _safe_fluent_icon("SPEED_HIGH", "POWER_BUTTON"), body))
        self.device_combo = ComboBox(body)
        # device_options 形如 {"NVIDIA xxx": "cuda:0", "cpu": "cpu"}
        items = [f"{k} · {v}" for k, v in self.device_options.items()] \
            or ["cpu · cpu"]
        self.device_combo.addItems(items)
        default_dev = get_field("iopaint.device", "")
        for i, dev in enumerate(self.device_options.values()):
            if dev == default_dev:
                self.device_combo.setCurrentIndex(i)
                break
        self.device_combo.currentTextChanged.connect(
            lambda _t: set_field("iopaint.device", self._parse_device()))
        lay.addWidget(self.device_combo)
        lay.addWidget(_separator())

        # HD 策略
        lay.addWidget(_section_title("高分辨率策略",
                                     _safe_fluent_icon("ZOOM", "LAYOUT"),
                                     body))
        self.hd_combo = ComboBox(body)
        self.hd_combo.addItems(HD_STRATEGIES)
        default_hd = get_field("iopaint.hd_strategy", "CROP")
        if default_hd in HD_STRATEGIES:
            self.hd_combo.setCurrentText(default_hd)
        self.hd_combo.currentTextChanged.connect(
            lambda t: set_field("iopaint.hd_strategy", t))
        lay.addWidget(self.hd_combo)

        # HD 参数
        self._add_caption(lay, "CROP 触发尺寸 (px)", body)
        self.crop_trig_spin = QSpinBox(body)
        self.crop_trig_spin.setRange(256, 4096)
        self.crop_trig_spin.setSingleStep(64)
        self.crop_trig_spin.setValue(int(get_field(
            "iopaint.hd_strategy_crop_trigger_size", 800)))
        self.crop_trig_spin.valueChanged.connect(
            lambda v: set_field("iopaint.hd_strategy_crop_trigger_size", v))
        lay.addWidget(self.crop_trig_spin)

        self._add_caption(lay, "CROP 边距 (px)", body)
        self.crop_margin_spin = QSpinBox(body)
        self.crop_margin_spin.setRange(16, 512)
        self.crop_margin_spin.setSingleStep(16)
        self.crop_margin_spin.setValue(int(get_field(
            "iopaint.hd_strategy_crop_margin", 128)))
        self.crop_margin_spin.valueChanged.connect(
            lambda v: set_field("iopaint.hd_strategy_crop_margin", v))
        lay.addWidget(self.crop_margin_spin)

        self._add_caption(lay, "RESIZE 上限 (px)", body)
        self.resize_lim_spin = QSpinBox(body)
        self.resize_lim_spin.setRange(512, 4096)
        self.resize_lim_spin.setSingleStep(64)
        self.resize_lim_spin.setValue(int(get_field(
            "iopaint.hd_strategy_resize_limit", 1280)))
        self.resize_lim_spin.valueChanged.connect(
            lambda v: set_field("iopaint.hd_strategy_resize_limit", v))
        lay.addWidget(self.resize_lim_spin)

        # LDM steps（仅 ldm 时显示）
        self._ldm_caption = CaptionLabel("LDM 采样步数", body)
        self._ldm_caption.setStyleSheet("color:rgba(128,128,128,200);")
        lay.addWidget(self._ldm_caption)
        self.ldm_steps_spin = QSpinBox(body)
        self.ldm_steps_spin.setRange(5, 100)
        self.ldm_steps_spin.setValue(int(get_field("iopaint.ldm_steps", 20)))
        self.ldm_steps_spin.valueChanged.connect(
            lambda v: set_field("iopaint.ldm_steps", v))
        lay.addWidget(self.ldm_steps_spin)
        lay.addWidget(_separator())

        # SAM
        lay.addWidget(_section_title("魔棒 (SAM)",
                                     _safe_fluent_icon("ROBOT", "SEARCH"),
                                     body))
        self.sam_combo = ComboBox(body)
        self.sam_combo.addItems(SAM_MODELS)
        default_sam = get_field("iopaint.sam_model", "mobile_sam")
        if default_sam in SAM_MODELS:
            self.sam_combo.setCurrentText(default_sam)
        self.sam_combo.currentTextChanged.connect(
            lambda t: set_field("iopaint.sam_model", t))
        lay.addWidget(self.sam_combo)
        lay.addWidget(_separator())

        # 输出
        lay.addWidget(_section_title("输出", FIF.FOLDER, body))
        self._add_caption(lay, "输出目录", body)
        out_row = QHBoxLayout()
        self.out_dir_edit = LineEdit(body)
        self.out_dir_edit.setText(
            get_field("iopaint.out_dir", DEFAULT_OUT_DIR))
        self.out_dir_edit.textChanged.connect(
            lambda t: set_field("iopaint.out_dir", t))
        browse_btn = ToolButton(FIF.FOLDER, body)
        browse_btn.clicked.connect(self._browse_output)
        out_row.addWidget(self.out_dir_edit)
        out_row.addWidget(browse_btn)
        lay.addLayout(out_row)

        self._add_caption(lay, "输出格式", body)
        self.fmt_combo = ComboBox(body)
        self.fmt_combo.addItems(OUT_FORMATS)
        default_fmt = get_field("iopaint.out_format", "png")
        if default_fmt in OUT_FORMATS:
            self.fmt_combo.setCurrentText(default_fmt)
        self.fmt_combo.currentTextChanged.connect(
            lambda t: set_field("iopaint.out_format", t))
        lay.addWidget(self.fmt_combo)

        lay.addStretch()
        self._on_model_changed(self.model_combo.currentText())

    def _add_caption(self, lay, text, parent):
        lbl = CaptionLabel(text, parent)
        lbl.setStyleSheet("color:rgba(128,128,128,200);margin-top:2px;")
        lay.addWidget(lbl)

    def _on_model_changed(self, name: str):
        self._model_desc.setText(MODEL_DESC.get(name, ""))
        is_ldm = (name == "ldm")
        self._ldm_caption.setVisible(is_ldm)
        self.ldm_steps_spin.setVisible(is_ldm)
        set_field("iopaint.model", name)

    def _browse_output(self):
        d = QFileDialog.getExistingDirectory(
            self, "选择输出目录", self.out_dir_edit.text() or "./")
        if d:
            self.out_dir_edit.setText(d)

    def _parse_device(self) -> str:
        text = self.device_combo.currentText()
        # 提取最后一个 "·" 后的部分
        if "·" in text:
            return text.rsplit("·", 1)[-1].strip()
        return text.strip() or "cpu"

    def get_params(self) -> dict:
        return {
            "model":   self.model_combo.currentText(),
            "device":  self._parse_device(),
            "hd_strategy": self.hd_combo.currentText(),
            "hd_strategy_crop_trigger_size": self.crop_trig_spin.value(),
            "hd_strategy_crop_margin": self.crop_margin_spin.value(),
            "hd_strategy_resize_limit": self.resize_lim_spin.value(),
            "ldm_steps": self.ldm_steps_spin.value(),
            "sam_model": self.sam_combo.currentText(),
            "out_dir":   self.out_dir_edit.text().strip() or DEFAULT_OUT_DIR,
            "out_format": self.fmt_combo.currentText(),
            "lama_model_url": get_field("iopaint.lama_model_url", None),
        }


# ══════════════════════════════════════════════════════════════════════
#  IOPaintPage —— 顶层页面
# ══════════════════════════════════════════════════════════════════════
class IOPaintPage(QWidget):
    def __init__(self, parent=None, device_options: Optional[dict] = None):
        super().__init__(parent)
        self.setObjectName("IOPaintPage")
        self.device_options = device_options or {"cpu": "cpu"}

        self._current_path: Optional[str] = None
        self._last_output: Optional[str] = None
        self._worker = None
        self._seg_worker = None
        self._running = False
        self._seg_first_hint_shown = False

        patch_iopaint_page(self)
        self._build_ui()

        # 快捷键
        QShortcut(QKeySequence("Ctrl+Z"), self, self._canvas.undo)
        QShortcut(QKeySequence("Ctrl+Y"), self, self._canvas.redo)
        QShortcut(QKeySequence("Ctrl+Shift+Z"), self, self._canvas.redo)
        QShortcut(QKeySequence("B"), self,
                  lambda: self._on_tool_changed("brush", from_shortcut=True))
        QShortcut(QKeySequence("E"), self,
                  lambda: self._on_tool_changed("eraser", from_shortcut=True))
        QShortcut(QKeySequence("R"), self,
                  lambda: self._on_tool_changed("rect", from_shortcut=True))
        QShortcut(QKeySequence("W"), self,
                  lambda: self._on_tool_changed("sam", from_shortcut=True))

    # ── UI ────────────────────────────────────────────────────────────
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Topbar
        topbar = QWidget(self)
        tb = QHBoxLayout(topbar)
        tb.setContentsMargins(24, 14, 24, 14)
        tb.setSpacing(10)
        tb.addWidget(TitleLabel("图像修复", topbar))
        badge = QLabel("IOPaint (实验性存在未知bug)", topbar)
        badge.setStyleSheet(
            f"QLabel{{background:{ACCENT}22;color:{ACCENT};"
            f"border:1px solid {ACCENT}55;border-radius:10px;"
            f"padding:2px 10px;font-size:12px;font-weight:600;}}")
        tb.addWidget(badge)
        tb.addStretch()

        self._open_dir_btn = TransparentPushButton(
            FIF.FOLDER, "打开输出目录", topbar)
        self._open_dir_btn.clicked.connect(self._open_output_dir)
        tb.addWidget(self._open_dir_btn)

        self._browse_btn = TransparentPushButton(FIF.PHOTO, "选择图像", topbar)
        self._browse_btn.clicked.connect(self._browse_image)
        tb.addWidget(self._browse_btn)

        self._reset_btn = TransparentPushButton(FIF.SYNC, "重置画布", topbar)
        self._reset_btn.clicked.connect(self._reset)
        tb.addWidget(self._reset_btn)

        self._run_btn = PrimaryPushButton(FIF.PLAY, "开始修复", topbar)
        self._run_btn.clicked.connect(self._toggle_run)
        tb.addWidget(self._run_btn)

        root.addWidget(topbar)
        root.addWidget(_separator())

        # Body
        body = QWidget(self)
        body_lay = QHBoxLayout(body)
        body_lay.setContentsMargins(16, 12, 16, 16)
        body_lay.setSpacing(12)

        splitter = QSplitter(Qt.Orientation.Horizontal, body)
        splitter.setHandleWidth(8)
        splitter.setChildrenCollapsible(False)
        splitter.setStyleSheet("QSplitter::handle{background:transparent;}")
        body_lay.addWidget(splitter)

        # 左 = 工具
        self._toolpalette = ToolPalette(splitter)

        # 中 = 画布 + 状态卡片
        center = QWidget(splitter)
        cl = QVBoxLayout(center)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(10)

        canvas_card = ElevatedCardWidget(center)
        canvas_card.setSizePolicy(QSizePolicy.Policy.Expanding,
                                  QSizePolicy.Policy.Expanding)
        cc = QVBoxLayout(canvas_card)
        cc.setContentsMargins(12, 10, 12, 10)
        cc.setSpacing(8)

        head_row = QHBoxLayout()
        head_row.addWidget(_section_title(
            "画布", _safe_fluent_icon("PHOTO", "VIEW"), canvas_card))
        head_row.addStretch()
        self._info_lbl = CaptionLabel("尚未加载图像", canvas_card)
        self._info_lbl.setStyleSheet("color:rgba(128,128,128,180);")
        head_row.addWidget(self._info_lbl)
        cc.addLayout(head_row)

        self._canvas = MaskCanvas(canvas_card)
        cc.addWidget(self._canvas, 1)

        hint = CaptionLabel(
            "提示：拖入图片 / 滚轮+Ctrl 缩放 / 空格+拖拽 平移 / 滚轮 改笔刷大小 / "
            "魔棒：左键正点，右键或 Ctrl+左键 负点",
            canvas_card)
        hint.setStyleSheet("color:rgba(128,128,128,140);")
        hint.setWordWrap(True)
        cc.addWidget(hint)

        cl.addWidget(canvas_card, 1)

        # 进度 + 日志
        st_card = ElevatedCardWidget(center)
        st = QVBoxLayout(st_card)
        st.setContentsMargins(14, 10, 14, 10)
        st.setSpacing(8)

        prog_row = QHBoxLayout()
        prog_row.addWidget(StrongBodyLabel("进度", st_card))
        prog_row.addStretch()
        self._pct_lbl = CaptionLabel("0%", st_card)
        self._pct_lbl.setStyleSheet(f"color:{ACCENT};")
        prog_row.addWidget(self._pct_lbl)
        st.addLayout(prog_row)

        self._prog = ProgressBar(st_card)
        self._prog.setValue(0)
        st.addWidget(self._prog)

        self._cur_lbl = CaptionLabel("就绪", st_card)
        self._cur_lbl.setStyleSheet("color:rgba(128,128,128,180);")
        st.addWidget(self._cur_lbl)

        self._log = BodyLabel("等待开始…", st_card)
        self._log.setWordWrap(True)
        self._log.setStyleSheet(
            "background:rgba(0,0,0,40);border:1px solid rgba(128,128,128,30);"
            "border-radius:6px;padding:6px 8px;"
            "font-family:Consolas,monospace;font-size:11px;"
            "color:rgba(220,220,220,220);")
        self._log.setMinimumHeight(60)
        self._log.setMaximumHeight(120)
        self._log_lines: list[str] = []
        st.addWidget(self._log)

        cl.addWidget(st_card)

        # 右 = 参数
        right_wrap = QWidget(splitter)
        right_wrap.setSizePolicy(QSizePolicy.Policy.Preferred,
                                 QSizePolicy.Policy.Expanding)
        rlay = QVBoxLayout(right_wrap)
        rlay.setContentsMargins(8, 0, 0, 0)
        rlay.setSpacing(0)
        self._params = IOPaintParamPanel(right_wrap, self.device_options)
        rlay.addWidget(self._params)

        splitter.addWidget(self._toolpalette)
        splitter.addWidget(center)
        splitter.addWidget(right_wrap)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 4)
        splitter.setStretchFactor(2, 1)
        splitter.setSizes([200, 880, 320])

        root.addWidget(body, 1)

        # 连线
        self._toolpalette.tool_changed.connect(self._on_tool_changed)
        self._toolpalette.brush_size_changed.connect(
            self._canvas.set_brush_size)
        self._toolpalette.opacity_changed.connect(
            self._canvas.set_mask_opacity)
        self._toolpalette.undo_requested.connect(self._canvas.undo)
        self._toolpalette.redo_requested.connect(self._canvas.redo)
        self._toolpalette.clear_requested.connect(self._canvas.clear_mask)
        self._toolpalette.compare_toggled.connect(
            self._canvas.set_compare_mode)
        self._toolpalette.apply_seg_requested.connect(self._apply_seg)

        self._canvas.sam_clicks_changed.connect(
            self._toolpalette.set_apply_seg_enabled)
        self._canvas.image_loaded.connect(self._on_image_size)

        # 同步默认值
        self._canvas.set_brush_size(30)
        self._canvas.set_mask_opacity(55)

    # ── 槽 ────────────────────────────────────────────────────────────
    def _on_tool_changed(self, key: str, from_shortcut: bool = False):
        self._canvas.set_tool(key)
        if from_shortcut:
            # 同步按钮组高亮
            mapping = {
                "brush":  self._toolpalette._btn_brush,
                "eraser": self._toolpalette._btn_eraser,
                "rect":   self._toolpalette._btn_rect,
                "sam":    self._toolpalette._btn_sam,
            }
            btn = mapping.get(key)
            if btn is not None:
                btn.setChecked(True)
                self._toolpalette._current_tool = key

    def _on_image_size(self, w: int, h: int):
        self._info_lbl.setText(f"{w} × {h} px")

    def _apply_seg(self):
        clicks = self._canvas.get_sam_clicks()
        if not clicks:
            return
        self._run_seg(clicks)

    # ── 文件 ──────────────────────────────────────────────────────────
    def _browse_image(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择图像", "",
            "图像 (*.png *.jpg *.jpeg *.webp *.bmp);;所有文件 (*)")
        if not path:
            return
        if not self._canvas.load_image(path):
            InfoBar.error(title="加载失败", content=f"无法读取：{path}",
                          parent=self.window(),
                          position=InfoBarPosition.TOP_RIGHT, duration=4000)
            return
        self._current_path = path
        self._toolpalette.enable_compare(False)
        self._append_log(f"📷 已加载：{Path(path).name}")

    def _on_image_loaded_from_canvas(self, path: str):
        """画布通过拖拽加载图像时回调。"""
        self._current_path = path
        self._toolpalette.enable_compare(False)
        self._append_log(f"📷 已加载：{Path(path).name}")

    # ── 运行控制 ──────────────────────────────────────────────────────
    def _toggle_run(self):
        if self._running:
            self._abort()
        else:
            self._start()

    # _start / _abort / _run_seg 由 patch_iopaint_page 注入

    # ── 进度/日志回调（由 worker 调） ────────────────────────────────
    def _on_progress(self, pct: int, name: str):
        self._prog.setValue(max(0, min(100, int(pct))))
        self._pct_lbl.setText(f"{pct}%")
        self._cur_lbl.setText(name)

    def _on_image_done(self, inp: str, out: str, ms: float):
        self._last_output = out

    def _on_finished(self, count: int, elapsed: float):
        self._running = False
        self._run_btn.setText("开始修复")
        self._run_btn.setIcon(FIF.PLAY)
        self._params.setEnabled(True)
        self._toolpalette.setEnabled(True)
        self._prog.setValue(100)
        self._pct_lbl.setText("100%")
        self._cur_lbl.setText("完成")
        InfoBar.success(
            title="修复完成",
            content=f"耗时 {elapsed:.2f}s · 输出：{self._last_output}",
            parent=self.window(),
            position=InfoBarPosition.TOP_RIGHT, duration=5000)

    def _on_error(self, msg: str):
        self._running = False
        self._run_btn.setText("开始修复")
        self._run_btn.setIcon(FIF.PLAY)
        self._params.setEnabled(True)
        self._toolpalette.setEnabled(True)
        InfoBar.error(title="修复出错", content=msg,
                      parent=self.window(),
                      position=InfoBarPosition.TOP_RIGHT, duration=6000)

    def _append_log(self, line: str):
        self._log_lines.append(line)
        self._log_lines = self._log_lines[-8:]
        self._log.setText("<br>".join(self._log_lines))

    # ── 杂项 ──────────────────────────────────────────────────────────
    def _open_output_dir(self):
        d = self._params.out_dir_edit.text().strip() or DEFAULT_OUT_DIR
        os.makedirs(d, exist_ok=True)
        import subprocess
        try:
            subprocess.Popen(
                ["explorer" if sys.platform == "win32" else "open", d])
        except Exception as e:
            warning(f"打开目录失败：{e}")

    def _reset(self):
        self._canvas.reset()
        self._current_path = None
        self._last_output = None
        self._info_lbl.setText("尚未加载图像")
        self._toolpalette.enable_compare(False)
        self._prog.setValue(0)
        self._pct_lbl.setText("0%")
        self._cur_lbl.setText("就绪")
        self._log_lines = []
        self._log.setText("等待开始…")
