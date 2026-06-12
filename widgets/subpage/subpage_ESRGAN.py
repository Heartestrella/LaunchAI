from typing import Optional
from qfluentwidgets import HorizontalFlipView
from PyQt6.QtWidgets import QWidget
from PyQt6.QtGui import QPainter, QPainterPath, QPen, QColor, QPixmap, QCursor
from PyQt6.QtCore import Qt, QRect, QPoint
import sys
import os
from pathlib import Path
import re

from PyQt6.QtCore import (
    Qt, QThread, QObject, pyqtSignal, QPoint, QRect, QSize, QRectF
)
from PyQt6.QtGui import (
    QColor, QDragEnterEvent, QDropEvent, QPixmap,
    QPainter, QPen, QBrush, QCursor
)
from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QFileDialog, QLabel, QFrame, QSizePolicy, QToolTip
)

from qfluentwidgets import (
    setTheme, Theme, setThemeColor,
    FluentWindow, NavigationItemPosition,
    ElevatedCardWidget,
    TitleLabel, SubtitleLabel, BodyLabel, CaptionLabel, StrongBodyLabel,
    PrimaryPushButton, PushButton, TransparentPushButton, TransparentToolButton, ToolButton,
    ComboBox, Slider, SwitchButton, LineEdit,
    ProgressBar,
    SmoothScrollArea,
    InfoBar, InfoBarPosition,
    FluentIcon as FIF,
    IconWidget,
    HorizontalFlipView,
)
from workers.realesrgan_worker import patch_inference_page


# ══════════════════════════════════════════════════════════════════════
#  常量
# ══════════════════════════════════════════════════════════════════════

ACCENT = "#0078D4"
SUCCESS = "#0DB37E"
WARNING = "#F7B731"
DANGER = "#FC5C65"

MODEL_INFO = {
    "realesr-animevideov3-x4":  ("动漫视频帧 4× 超分",                   "4×",    "~10 MB"),
    "realesr-animevideov3-x3":  ("动漫视频帧 3× 超分",                   "3×",    "~10 MB"),
    "realesr-animevideov3-x2":  ("动漫视频帧 2× 超分",                   "2×",    "~10 MB"),
    "realesr-generalv3-x4":     ("通用 4× 超分（改进版，推荐用于照片）",  "4×",    "~15 MB"),
    "realesrgan-plus-x4":       ("照片通用 4× 超分",                     "4×",    "~20 MB"),
    "realesrgan-plus-anime-x4": ("动漫/插画 4× 超分",                    "4×",    "~15 MB"),
}

OUTPUT_FORMATS = ["PNG", "JPG", "WEBP"]


# ══════════════════════════════════════════════════════════════════════
#  工具函数
# ══════════════════════════════════════════════════════════════════════

def _separator():
    sep = QFrame()
    sep.setFrameShape(QFrame.Shape.HLine)
    sep.setStyleSheet("background: rgba(128,128,128,40); max-height:1px;")
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
    lbl = StrongBodyLabel(text, row)
    lay.addWidget(lbl)
    lay.addStretch()
    return row


def _badge(text: str, color: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setStyleSheet(f"""
        QLabel {{
            background: {color}22; color: {color};
            border: 1px solid {color}55;
            border-radius: 8px;
            padding: 1px 8px;
            font-size: 11px; font-weight: 600;
        }}
    """)
    return lbl


# ══════════════════════════════════════════════════════════════════════
#  模拟推理 Worker（无真实后端时回退用）
# ══════════════════════════════════════════════════════════════════════

class InferenceWorker(QObject):
    progress = pyqtSignal(int, str)
    finished = pyqtSignal(int, float)
    error = pyqtSignal(str)
    log_line = pyqtSignal(str)

    def __init__(self, files: list[str], params: dict):
        super().__init__()
        self._files = files
        self._params = params
        self._abort = False

    def abort(self):
        self._abort = True

    def run(self):
        import time
        t0 = time.time()
        total = len(self._files)
        for i, f in enumerate(self._files):
            if self._abort:
                self.log_line.emit("[中止] 用户中止推理")
                return
            name = Path(f).name
            self.log_line.emit(f"[{i+1}/{total}] 处理: {name}")
            for step in range(10):
                if self._abort:
                    return
                time.sleep(0.06)
                pct = int((i * 10 + step + 1) / (total * 10) * 100)
                self.progress.emit(pct, name)
        elapsed = time.time() - t0
        self.log_line.emit(f"[完成] 共处理 {total} 张，耗时 {elapsed:.1f}s")
        self.finished.emit(total, elapsed)


# ══════════════════════════════════════════════════════════════════════
#  MagnifyLabel  —  支持鼠标悬停放大镜的图片预览控件
#
#  功能：
#    - 鼠标移入时在图片上绘制一个放大镜圆形遮罩，显示当前位置的局部放大
#    - 提供 peer 属性，设置后两个控件互相同步放大镜位置（原图↔超分图同步）
#    - setPixmap() / clearPixmap() 控制图片
# ══════════════════════════════════════════════════════════════════════

# class MagnifyLabel(QWidget):
#     """带放大镜效果的图片预览 QWidget"""

#     # 放大镜圆圈半径（px，显示在控件上的尺寸）
#     LENS_R = 72
#     # 放大倍率（原始像素层面取多大的区域）
#     ZOOM = 3.0
#     # 圆圈边框宽度
#     BORDER_W = 2

#     def __init__(self, label_text: str = "", parent=None):
#         super().__init__(parent)
#         self._pixmap:     QPixmap | None = None   # 缩放后适应控件的 pixmap
#         self._src_pixmap: QPixmap | None = None   # 原始全尺寸 pixmap
#         self._label_text = label_text
#         self._mouse_pos:  QPoint | None = None    # 控件坐标
#         self._peer:       "MagnifyLabel | None" = None
#         self._zoom:       float = 3.0             # 当前放大倍数（可通过滚轮调节）

#         self.setMouseTracking(True)
#         self.setCursor(QCursor(Qt.CursorShape.CrossCursor))
#         self.setSizePolicy(QSizePolicy.Policy.Expanding,
#                            QSizePolicy.Policy.Expanding)
#         self.setMinimumHeight(160)
#         self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
#         self._apply_style(False)

#     # ── 公共 API ──────────────────────────────────────────────────────

#     def setPixmap(self, pixmap: QPixmap):
#         self._src_pixmap = pixmap
#         self._refresh_scaled()
#         self.update()

#     def clearPixmap(self):
#         self._src_pixmap = None
#         self._pixmap = None
#         self._mouse_pos = None
#         self.update()

#     @property
#     def peer(self) -> "MagnifyLabel | None":
#         return self._peer

#     @peer.setter
#     def peer(self, other: "MagnifyLabel"):
#         self._peer = other
#         other._peer = self     # 双向绑定

#     # ── 内部辅助 ──────────────────────────────────────────────────────

#     def _apply_style(self, has_image: bool):
#         self.setStyleSheet(
#             "MagnifyLabel{"
#             "background:rgba(128,128,128,12);"
#             "border:1px solid rgba(128,128,128,35);"
#             "border-radius:8px;}"
#         )

#     def _refresh_scaled(self):
#         """将 src_pixmap 缩放到适应控件当前尺寸（保持比例）。"""
#         if self._src_pixmap is None or self._src_pixmap.isNull():
#             self._pixmap = None
#             return
#         self._pixmap = self._src_pixmap.scaled(
#             self.width(), self.height(),
#             Qt.AspectRatioMode.KeepAspectRatio,
#             Qt.TransformationMode.SmoothTransformation
#         )

#     def _img_rect(self) -> QRect:
#         """图片在控件内居中后的实际绘制矩形。"""
#         if self._pixmap is None or self._pixmap.isNull():
#             return QRect()
#         x = (self.width() - self._pixmap.width()) // 2
#         y = (self.height() - self._pixmap.height()) // 2
#         return QRect(x, y, self._pixmap.width(), self._pixmap.height())

#     def _widget_to_src(self, pos: QPoint) -> QPoint | None:
#         """将控件坐标转换为原始图像坐标（用于取放大区域）。"""
#         if self._src_pixmap is None or self._pixmap is None:
#             return None
#         ir = self._img_rect()
#         if not ir.contains(pos):
#             return None
#         # 控件内图片坐标 → 原始图像坐标
#         rx = (pos.x() - ir.x()) / ir.width()
#         ry = (pos.y() - ir.y()) / ir.height()
#         sx = int(rx * self._src_pixmap.width())
#         sy = int(ry * self._src_pixmap.height())
#         return QPoint(sx, sy)

#     # ── 事件 ──────────────────────────────────────────────────────────

#     def resizeEvent(self, e):
#         super().resizeEvent(e)
#         self._refresh_scaled()
#         self.update()

#     def mouseMoveEvent(self, e):
#         pos = e.position().toPoint()
#         ir = self._img_rect()
#         if ir.contains(pos):
#             self._mouse_pos = pos
#             # 计算归一化比例并同步到 peer
#             if self._peer is not None and self._peer._pixmap is not None:
#                 peer_ir = self._peer._img_rect()
#                 if peer_ir.width() > 0 and peer_ir.height() > 0:
#                     rx = (pos.x() - ir.x()) / max(1, ir.width())
#                     ry = (pos.y() - ir.y()) / max(1, ir.height())
#                     px = int(peer_ir.x() + rx * peer_ir.width())
#                     py = int(peer_ir.y() + ry * peer_ir.height())
#                     self._peer._mouse_pos = QPoint(px, py)
#                     self._peer.update()
#         else:
#             self._mouse_pos = None
#             if self._peer is not None:
#                 self._peer._mouse_pos = None
#                 self._peer.update()
#         self.update()

#     def leaveEvent(self, e):
#         self._mouse_pos = None
#         if self._peer is not None:
#             self._peer._mouse_pos = None
#             self._peer.update()
#         self.update()

#     def wheelEvent(self, e):
#         """处理滚轮事件，调节放大倍数。向上滚轮增加倍数，向下滚轮减少倍数。"""
#         delta = e.angleDelta().y()
#         if delta > 0:  # 向上滚
#             self._zoom = min(10.0, self._zoom + 0.5)
#         else:  # 向下滚
#             self._zoom = max(2.0, self._zoom - 0.5)
#         self.update()
#         # 同步到 peer
#         if self._peer is not None:
#             self._peer._zoom = self._zoom
#             self._peer.update()

#     # ── 绘制 ──────────────────────────────────────────────────────────

#     def paintEvent(self, _):
#         p = QPainter(self)
#         p.setRenderHint(QPainter.RenderHint.Antialiasing)
#         p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

#         w, h = self.width(), self.height()

#         # 背景
#         p.fillRect(0, 0, w, h, QColor(0, 0, 0, 0))

#         if self._pixmap is None or self._pixmap.isNull():
#             # 空状态：居中占位图标 + 文字
#             p.setPen(QColor(128, 128, 128, 100))
#             p.setFont(self.font())
#             p.drawText(
#                 QRectF(0, 0, w, h),
#                 Qt.AlignmentFlag.AlignCenter,
#                 f"🖼  {self._label_text}"
#             )
#             p.end()
#             return

#         # 绘制主图
#         ir = self._img_rect()
#         p.drawPixmap(ir, self._pixmap)

#         # 放大镜
#         if self._mouse_pos is not None and ir.contains(self._mouse_pos):
#             self._draw_lens(p, self._mouse_pos, ir)

#         p.end()

#     def _draw_lens(self, p: QPainter, center: QPoint, img_rect: QRect):
#         """在 center 位置绘制放大镜圆圈。"""
#         if self._src_pixmap is None:
#             return

#         r = self.LENS_R
#         cx, cy = center.x(), center.y()

#         # 源图中对应的区域（_zoom 倍缩小的取样范围）
#         src_w = int(self._pixmap.width() / self._zoom)
#         src_h = int(self._pixmap.height() / self._zoom)

#         # 目标圆的直径对应 src_pixmap 多少像素
#         # lens显示区域直径 = 2*r，对应 src 中 2*r/_zoom 个 pixmap 像素
#         # 但我们直接在 src_pixmap 上取区域
#         scale_x = self._src_pixmap.width() / self._pixmap.width()
#         scale_y = self._src_pixmap.height() / self._pixmap.height()

#         half_src_w = int(r * scale_x / self._zoom)
#         half_src_h = int(r * scale_y / self._zoom)

#         # 鼠标在 pixmap 上的坐标
#         px = int((cx - img_rect.x()) * scale_x)
#         py = int((cy - img_rect.y()) * scale_y)

#         src_x = max(
#             0, min(px - half_src_w, self._src_pixmap.width() - 2 * half_src_w))
#         src_y = max(
#             0, min(py - half_src_h, self._src_pixmap.height() - 2 * half_src_h))
#         src_rect = QRect(src_x, src_y,
#                          min(2 * half_src_w, self._src_pixmap.width()),
#                          min(2 * half_src_h, self._src_pixmap.height()))

#         # 裁剪到圆形区域再绘制
#         p.save()

#         # 圆形剪裁路径
#         from PyQt6.QtGui import QPainterPath
#         clip = QPainterPath()
#         clip.addEllipse(QRectF(cx - r, cy - r, 2 * r, 2 * r))
#         p.setClipPath(clip)

#         # 将 src_rect 缩放后绘制到圆圈区域
#         dst_rect = QRect(cx - r, cy - r, 2 * r, 2 * r)
#         p.drawPixmap(dst_rect, self._src_pixmap, src_rect)

#         p.restore()

#         # 圆圈边框
#         pen = QPen(QColor(ACCENT), self.BORDER_W)
#         p.setPen(pen)
#         p.setBrush(Qt.BrushStyle.NoBrush)
#         p.drawEllipse(QRectF(cx - r, cy - r, 2 * r, 2 * r))

#         # 十字准线（细线）
#         pen2 = QPen(QColor(255, 255, 255, 160), 1)
#         p.setPen(pen2)
#         p.drawLine(cx - r, cy, cx + r, cy)
#         p.drawLine(cx, cy - r, cx, cy + r)


class MagnifyFlipView(HorizontalFlipView):
    """带放大镜效果的 HorizontalFlipView"""

    LENS_R = 72  # 放大镜半径
    BORDER_W = 2  # 边框宽度

    def __init__(self, parent=None):
        super().__init__(parent)
        self._zoom = 3.0  # 放大倍数
        self._peer: Optional["MagnifyFlipView"] = None
        self._mouse_pos: Optional[QPoint] = None
        self._current_pixmap: Optional[QPixmap] = None  # 缓存当前图片

        self.setMouseTracking(True)
        self.setCursor(QCursor(Qt.CursorShape.CrossCursor))

        # 监听索引变化
        self.currentIndexChanged.connect(self._on_index_changed)

    def addImages(self, images: list[str]):
        """添加图片（支持文件路径列表）"""
        super().addImages(images)
        # 更新当前图片缓存
        self._update_current_pixmap()

    def setPixmap(self, pixmap: QPixmap):
        """设置单张图片（兼容原接口）"""
        # 清空现有图片
        while self.count() > 0:
            self.takeItem(0)

        # 转换为 QImage 并添加
        image = pixmap.toImage()
        self.addImage(image)

        # 缓存当前图片
        self._current_pixmap = pixmap
        self.update()

    def clearPixmap(self):
        """清空图片"""
        while self.count() > 0:
            self.takeItem(0)
        self._current_pixmap = None
        self._mouse_pos = None
        self.update()

    def _on_index_changed(self, index: int):
        """当前索引改变时更新当前图片"""
        self._update_current_pixmap()

        # 同步到 peer
        if self._peer:
            self._peer.setCurrentIndex(index)
            self._peer._update_current_pixmap()

        self.update()

    def _update_current_pixmap(self):
        """更新当前显示的图片缓存"""
        current_index = self.currentIndex()
        if current_index >= 0 and current_index < self.count():
            # 从 FlipView 获取当前图片
            image = self.image(current_index)
            if not image.isNull():
                self._current_pixmap = QPixmap.fromImage(image)
            else:
                self._current_pixmap = None
        else:
            self._current_pixmap = None

    @property
    def zoom(self) -> float:
        return self._zoom

    @zoom.setter
    def zoom(self, value: float):
        self._zoom = max(2.0, min(10.0, value))
        if self._peer:
            self._peer._zoom = self._zoom
            self._peer.update()
        self.update()

    @property
    def peer(self) -> Optional["MagnifyFlipView"]:
        return self._peer

    @peer.setter
    def peer(self, other: "MagnifyFlipView"):
        self._peer = other
        other._peer = self

    def wheelEvent(self, event):
        """滚轮调节放大倍数"""
        delta = event.angleDelta().y()
        if delta > 0:
            self.zoom += 0.5
        else:
            self.zoom -= 0.5
        event.accept()

    def mouseMoveEvent(self, event):
        """鼠标移动事件"""
        # 获取鼠标在 viewport 中的位置
        pos = event.position().toPoint()

        # 检查是否在图片区域内
        if self._is_point_in_image(pos):
            self._mouse_pos = pos
        else:
            self._mouse_pos = None

        # 同步到 peer
        if self._peer:
            self._peer._mouse_pos = self._mouse_pos
            self._peer.update()

        self.update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        """鼠标离开事件"""
        self._mouse_pos = None
        if self._peer:
            self._peer._mouse_pos = None
            self._peer.update()
        self.update()
        super().leaveEvent(event)

    def _is_point_in_image(self, pos: QPoint) -> bool:
        """检查点是否在当前图片区域内"""
        img_rect = self._get_image_rect()
        return img_rect is not None and img_rect.contains(pos)

    def paintEvent(self, event):
        """绘制放大镜"""
        # 先调用父类绘制图片
        super().paintEvent(event)

        # 获取当前显示的图片
        if self._mouse_pos is None or self._current_pixmap is None:
            return

        # 获取当前图片的显示区域
        img_rect = self._get_image_rect()
        if not img_rect or not img_rect.contains(self._mouse_pos):
            return

        # 绘制放大镜
        painter = QPainter(self.viewport())
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        self._draw_lens(painter, self._mouse_pos,
                        img_rect, self._current_pixmap)
        painter.end()

    def _get_image_rect(self) -> Optional[QRect]:
        """获取当前图片在视口中的显示区域"""
        if not self.viewport() or self._current_pixmap is None:
            return None

        # 获取视口大小
        viewport_rect = self.viewport().rect()

        # 获取 item 的实际大小（itemSize 可能不是最终显示大小）
        if self.currentIndex() < 0 or self.currentIndex() >= self.count():
            return None

        item = self.item(self.currentIndex())
        if not item:
            return None

        item_hint = item.sizeHint()

        # 计算居中位置
        x = (viewport_rect.width() - item_hint.width()) // 2
        y = (viewport_rect.height() - item_hint.height()) // 2

        return QRect(x, y, item_hint.width(), item_hint.height())

    def _draw_lens(self, painter: QPainter, center: QPoint,
                   img_rect: QRect, src_pixmap: QPixmap):
        """绘制放大镜"""
        r = self.LENS_R
        cx, cy = center.x(), center.y()

        # 计算缩放比例（显示图片到原始图片）
        # 注意：img_rect 是实际显示区域大小，src_pixmap 是原始图片大小
        scale_x = src_pixmap.width() / img_rect.width()
        scale_y = src_pixmap.height() / img_rect.height()

        # 源图取样区域大小（放大镜显示的区域对应原始图的区域要除以放大倍数）
        src_size_w = (2 * r) * scale_x / self._zoom
        src_size_h = (2 * r) * scale_y / self._zoom

        # 计算源图坐标
        src_center_x = (center.x() - img_rect.x()) * scale_x
        src_center_y = (center.y() - img_rect.y()) * scale_y

        # 构建源矩形
        src_x = max(0, int(src_center_x - src_size_w / 2))
        src_y = max(0, int(src_center_y - src_size_h / 2))
        src_w = min(int(src_size_w), src_pixmap.width() - src_x)
        src_h = min(int(src_size_h), src_pixmap.height() - src_y)

        if src_w <= 0 or src_h <= 0:
            return

        src_rect = QRect(src_x, src_y, src_w, src_h)

        # 绘制
        painter.save()

        # 圆形裁剪
        clip_path = QPainterPath()
        clip_path.addEllipse(cx - r, cy - r, 2 * r, 2 * r)
        painter.setClipPath(clip_path)

        # 绘制放大区域
        dst_rect = QRect(cx - r, cy - r, 2 * r, 2 * r)
        painter.drawPixmap(dst_rect, src_pixmap, src_rect)

        painter.restore()

        accent_color = QColor(0, 120, 215)
        pen = QPen(accent_color, self.BORDER_W)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(cx - r, cy - r, 2 * r, 2 * r)

        # 十字线
        pen2 = QPen(QColor(255, 255, 255, 160), 1)
        painter.setPen(pen2)
        painter.drawLine(cx - r, cy, cx + r, cy)
        painter.drawLine(cx, cy - r, cx, cy + r)
# ══════════════════════════════════════════════════════════════════════
#  文件列表项
# ══════════════════════════════════════════════════════════════════════


class FileListItem(QWidget):
    removed = pyqtSignal(str)

    _STATUS_COLORS = {
        "等待":   "#888888",
        "处理中": ACCENT,
        "完成":   SUCCESS,
        "失败":   DANGER,
    }

    def __init__(self, path: str, parent=None):
        super().__init__(parent)
        self.path = path
        self.setFixedHeight(42)
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Fixed)
        self._status = "等待"

        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 0, 8, 0)
        lay.setSpacing(8)

        ico = IconWidget(FIF.PHOTO, self)
        ico.setFixedSize(15, 15)
        lay.addWidget(ico)

        self._name_lbl = BodyLabel(Path(path).name, self)
        self._name_lbl.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        lay.addWidget(self._name_lbl, 1)

        size_kb = os.path.getsize(path) // 1024 if os.path.exists(path) else 0
        self._size_lbl = CaptionLabel(f"{size_kb} KB", self)
        self._size_lbl.setStyleSheet("color: rgba(128,128,128,180);")
        lay.addWidget(self._size_lbl)

        self._status_lbl = _badge("等待", "#888888")
        lay.addWidget(self._status_lbl)

        del_btn = TransparentToolButton(FIF.CLOSE, self)
        del_btn.setFixedSize(22, 22)
        del_btn.clicked.connect(lambda: self.removed.emit(self.path))
        lay.addWidget(del_btn)

    def set_status(self, s: str):
        self._status = s
        color = self._STATUS_COLORS.get(s, "#888888")
        self._status_lbl.setText(s)
        self._status_lbl.setStyleSheet(f"""
            QLabel {{
                background: {color}22; color: {color};
                border: 1px solid {color}55;
                border-radius: 8px;
                padding: 1px 8px;
                font-size: 11px; font-weight: 600;
            }}
        """)


# ══════════════════════════════════════════════════════════════════════
#  拖拽上传区  —  最多接受 1 个文件，拖入后锁定
# ══════════════════════════════════════════════════════════════════════

class DropZone(QWidget):
    files_dropped = pyqtSignal(list)
    clicked_open = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Preferred)
        self._locked = False   # 已有文件时锁定，不再接受拖拽

        lay = QVBoxLayout(self)
        lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.setContentsMargins(16, 20, 16, 20)
        lay.setSpacing(6)

        self._ico = IconWidget(FIF.PHOTO, self)
        self._ico.setFixedSize(32, 32)
        lay.addWidget(self._ico, 0, Qt.AlignmentFlag.AlignCenter)

        self._title = StrongBodyLabel("拖拽图片到此处，或点击选择", self)
        self._title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self._title)

        self._sub = CaptionLabel("支持 PNG · JPG · WEBP · BMP（仅限一张）", self)
        self._sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._sub.setStyleSheet("color: rgba(128,128,128,180);")
        lay.addWidget(self._sub)

        self._set_style(False)

    def lock(self):
        """锁定：已有文件，不再接受拖拽，只能点击替换。"""
        self._locked = True
        self.setAcceptDrops(False)

    def unlock(self):
        self._locked = False
        self.setAcceptDrops(True)

    def _set_style(self, hover: bool):
        bc = ACCENT if hover else "rgba(128,128,128,80)"
        bg = "rgba(0,120,212,8)" if hover else "rgba(128,128,128,6)"
        self.setStyleSheet(
            f"DropZone{{border:1.5px dashed {bc};border-radius:10px;background:{bg};}}")

    def dragEnterEvent(self, e: QDragEnterEvent):
        if self._locked:
            e.ignore()
            return
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
            self._set_style(True)

    def dragLeaveEvent(self, e):
        self._set_style(False)

    def dropEvent(self, e: QDropEvent):
        self._set_style(False)
        if self._locked:
            return
        paths = [u.toLocalFile() for u in e.mimeData().urls()
                 if u.toLocalFile().lower().endswith(
                     ('.png', '.jpg', '.jpeg', '.webp', '.bmp'))]
        if paths:
            # 接受所有拖入的文件
            self.files_dropped.emit(paths)

    def mousePressEvent(self, e):
        self.clicked_open.emit()


# ══════════════════════════════════════════════════════════════════════
#  右侧参数面板
# ══════════════════════════════════════════════════════════════════════

class ParamPanel(QWidget):
    def __init__(self, parent=None, device_options: dict = {}):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Preferred,
                           QSizePolicy.Policy.Expanding)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        scroll = SmoothScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("background:transparent;border:none;")
        outer.addWidget(scroll)

        container = QWidget()
        container.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        scroll.setWidget(container)

        lay = QVBoxLayout(container)
        lay.setContentsMargins(0, 4, 4, 16)
        lay.setSpacing(14)

        # ── 模型选择 ──────────────────────────────────────────────────
        lay.addWidget(_section_title("模型选择", FIF.DEVELOPER_TOOLS, container))

        self.model_combo = ComboBox(container)
        self.model_combo.addItems(list(MODEL_INFO.keys()))
        self.model_combo.currentTextChanged.connect(self._on_model_changed)
        self.model_combo.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        lay.addWidget(self.model_combo)

        self._model_info_lbl = CaptionLabel("", container)
        self._model_info_lbl.setWordWrap(True)
        self._model_info_lbl.setStyleSheet("color:rgba(128,128,128,200);")
        lay.addWidget(self._model_info_lbl)

        badge_row = QHBoxLayout()
        self._scale_badge = _badge("4×", ACCENT)
        self._size_badge = _badge("64.0 MB", "#888888")
        badge_row.addWidget(self._scale_badge)
        badge_row.addWidget(self._size_badge)
        badge_row.addStretch()
        lay.addLayout(badge_row)

        lay.addWidget(_separator())

        # ── 推理设备 ──────────────────────────────────────────────────
        lay.addWidget(_section_title("推理设备", FIF.SPEED_HIGH, container))
        self.device_combo = ComboBox(container)
        devices = []
        for name, idx in device_options.items():
            if "cpu" not in name.lower():
                devices.append(f"{name} · {idx}")
        self.device_combo.addItems(devices)
        self.device_combo.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        lay.addWidget(self.device_combo)
        lay.addWidget(_separator())

        # ── 推理参数 ──────────────────────────────────────────────────
        lay.addWidget(_section_title("推理参数", FIF.SETTING, container))

        self._add_caption(lay, "放大倍数", container)
        self.scale_combo = ComboBox(container)
        self.scale_combo.addItems(["4×", "3×", "2×", "1×"])
        self.scale_combo.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        lay.addWidget(self.scale_combo)

        # 现在 scale_combo 已创建，可以安全调用 _on_model_changed
        self._on_model_changed(self.model_combo.currentText())

        self.tile_slider, self._tile_lbl = self._add_slider(
            lay, "图块大小 (Tile Size)", 0, 1024, 512, 64, container)
        self.tile_slider.valueChanged.connect(
            lambda v: self._tile_lbl.setText("自动" if v == 0 else str(v)))
        self._tile_lbl.setText("512")

        self.tpad_slider, self._tpad_lbl = self._add_slider(
            lay, "图块重叠 (Tile Pad)", 0, 64, 10, 1, container)

        self.ppad_slider, self._ppad_lbl = self._add_slider(
            lay, "边缘填充 (Pre Pad)", 0, 32, 0, 1, container)

        self.fp16_switch = self._add_toggle(
            lay, "半精度推理 (fp16)", True, container)
        self.face_switch = self._add_toggle(
            lay, "人脸增强 (GFPGAN)", False, container)
        self.face_switch.checkedChanged.connect(self._on_face_toggle)

        self._face_str_widget = QWidget(container)
        self._face_str_widget.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        fsw_lay = QVBoxLayout(self._face_str_widget)
        fsw_lay.setContentsMargins(0, 0, 0, 0)
        fsw_lay.setSpacing(4)
        fsw_lay.addWidget(CaptionLabel("人脸增强强度", self._face_str_widget))
        self.face_slider, self._face_str_lbl = self._make_slider_row(
            0, 100, 50, 1, self._face_str_widget)
        self.face_slider.valueChanged.connect(
            lambda v: self._face_str_lbl.setText(f"{v/100:.2f}"))
        self._face_str_lbl.setText("0.50")
        fs_row = QHBoxLayout()
        fs_row.addWidget(self.face_slider)
        fs_row.addWidget(self._face_str_lbl)
        fsw_lay.addLayout(fs_row)
        self._face_str_widget.hide()
        lay.addWidget(self._face_str_widget)

        lay.addWidget(_separator())

        # ── 输出设置 ──────────────────────────────────────────────────
        lay.addWidget(_section_title("输出设置", FIF.FOLDER, container))

        self._add_caption(lay, "输出目录", container)
        out_row = QHBoxLayout()
        self.out_dir_edit = LineEdit(container)
        self.out_dir_edit.setText("./results")
        self.out_dir_edit.setPlaceholderText("选择输出文件夹…")
        self.out_dir_edit.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        browse_btn = ToolButton(FIF.FOLDER, container)
        browse_btn.clicked.connect(self._browse_output)
        out_row.addWidget(self.out_dir_edit)
        out_row.addWidget(browse_btn)
        lay.addLayout(out_row)

        self._add_caption(lay, "输出格式", container)
        self.fmt_combo = ComboBox(container)
        self.fmt_combo.addItems(OUTPUT_FORMATS)
        self.fmt_combo.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        lay.addWidget(self.fmt_combo)

        self.suffix_switch = self._add_toggle(
            lay, "保留原始文件名后缀", True, container)
        lay.addStretch()

    # ── 辅助 ──────────────────────────────────────────────────────────

    def _add_caption(self, lay, text, parent):
        lbl = CaptionLabel(text, parent)
        lbl.setStyleSheet("color:rgba(128,128,128,200);margin-top:2px;")
        lay.addWidget(lbl)

    def _make_slider_row(self, lo, hi, val, step, parent):
        slider = Slider(Qt.Orientation.Horizontal, parent)
        slider.setRange(lo, hi)
        slider.setSingleStep(step)
        slider.setValue(val)
        slider.setSizePolicy(QSizePolicy.Policy.Expanding,
                             QSizePolicy.Policy.Fixed)
        val_lbl = CaptionLabel(str(val), parent)
        val_lbl.setFixedWidth(34)
        val_lbl.setAlignment(Qt.AlignmentFlag.AlignRight |
                             Qt.AlignmentFlag.AlignVCenter)
        slider.valueChanged.connect(lambda v: val_lbl.setText(str(v)))
        return slider, val_lbl

    def _add_slider(self, lay, label, lo, hi, val, step, parent):
        self._add_caption(lay, label, parent)
        slider, val_lbl = self._make_slider_row(lo, hi, val, step, parent)
        row = QHBoxLayout()
        row.addWidget(slider)
        row.addWidget(val_lbl)
        lay.addLayout(row)
        return slider, val_lbl

    def _add_toggle(self, lay, label, default, parent):
        row = QHBoxLayout()
        lbl = CaptionLabel(label, parent)
        lbl.setSizePolicy(QSizePolicy.Policy.Expanding,
                          QSizePolicy.Policy.Preferred)
        sw = SwitchButton(parent)
        sw.setChecked(default)
        row.addWidget(lbl)
        row.addStretch()
        row.addWidget(sw)
        lay.addLayout(row)
        return sw

    def _on_model_changed(self, name: str):
        info = MODEL_INFO.get(name)
        if info:
            desc, scale, size = info
            self._model_info_lbl.setText(desc)
            self._scale_badge.setText(scale)
            self._size_badge.setText(size)
            # 根据模型倍数更新 scale_combo 的可选项
            max_scale = int(scale.rstrip('×'))
        else:
            self._model_info_lbl.setText("从模型目录加载的权重文件")
            found = re.findall(r'x(\d+)', name, re.I)
            max_scale = int(found[0]) if found else 4
            self._scale_badge.setText(f"{max_scale}×")
            self._size_badge.setText("—")

        # 动态更新 scale_combo 选项（根据模型的最大倍数）
        # 生成倍数列表：从 max_scale 降序到 1
        scale_options = [f"{i}×" for i in range(max_scale, 0, -1)]
        self.scale_combo.clear()
        self.scale_combo.addItems(scale_options)
        # 默认选择第一个（最大倍数）
        self.scale_combo.setCurrentIndex(0)

    def _on_face_toggle(self, checked: bool):
        self._face_str_widget.setVisible(checked)

    def _browse_output(self):
        d = QFileDialog.getExistingDirectory(self, "选择输出目录", "./")
        if d:
            self.out_dir_edit.setText(d)

    def get_params(self) -> dict:
        # 解析 device_combo 中的 gpu_id
        dev_text = self.device_combo.currentText()
        gm = re.search(r'·\s*(\d+)', dev_text)
        gpu_id = gm.group(1) if gm else "auto"

        # 解析 scale
        scale_text = self.scale_combo.currentText()
        sm = re.search(r'(\d+)', scale_text)
        scale = int(sm.group(1)) if sm else 4

        fmt = self.fmt_combo.currentText().lower()

        return {
            "model":       self.model_combo.currentText(),
            "device":      dev_text,
            "gpu_id":      gpu_id,           # worker 直接使用
            "scale":       scale,
            "tile":        self.tile_slider.value(),
            "tile_pad":    self.tpad_slider.value(),
            "pre_pad":     self.ppad_slider.value(),
            "fp16":        self.fp16_switch.isChecked(),
            "face_enh":    self.face_switch.isChecked(),
            "face_str":    self.face_slider.value() / 100.0,
            "out_dir":     self.out_dir_edit.text().strip() or "./results",
            "out_fmt":     fmt,
            "fmt":         fmt,
            "keep_suffix": self.suffix_switch.isChecked(),
            "threads":     "1:2:2",
            "tta":         False,
        }


# ══════════════════════════════════════════════════════════════════════
#  推理主页面
# ══════════════════════════════════════════════════════════════════════

class InferencePage(QWidget):
    def __init__(self, parent=None, device_options: dict = {}):
        super().__init__(parent)
        self.device_options = device_options
        self.setObjectName("InferencePage")
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Expanding)

        self._files: list[str] = []
        self._file_items: dict[str, FileListItem] = {}
        self._worker: InferenceWorker | None = None
        self._thread: QThread | None = None
        self._running = False

        self._build_ui()

        try:
            patch_inference_page(self)
        except Exception:
            pass

    # ── UI 构建 ───────────────────────────────────────────────────────

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Topbar
        topbar = QWidget(self)
        topbar.setSizePolicy(QSizePolicy.Policy.Expanding,
                             QSizePolicy.Policy.Fixed)
        tb_lay = QHBoxLayout(topbar)
        tb_lay.setContentsMargins(24, 14, 24, 14)
        tb_lay.setSpacing(10)

        title = TitleLabel("图像超分推理", topbar)
        tb_lay.addWidget(title)

        badge = QLabel("推理模式", topbar)
        badge.setStyleSheet(f"""
            QLabel {{
                background:{ACCENT}22;color:{ACCENT};
                border:1px solid {ACCENT}55;border-radius:10px;
                padding:2px 10px;font-size:12px;font-weight:600;
            }}
        """)
        tb_lay.addWidget(badge, 0, Qt.AlignmentFlag.AlignVCenter)
        tb_lay.addStretch()

        self._open_dir_btn = TransparentPushButton(
            FIF.FOLDER, "打开输出目录", topbar)
        self._open_dir_btn.clicked.connect(self._open_output_dir)
        tb_lay.addWidget(self._open_dir_btn)

        self._reset_btn = TransparentPushButton(FIF.SYNC, "重置", topbar)
        self._reset_btn.clicked.connect(self._reset)
        tb_lay.addWidget(self._reset_btn)

        self._run_btn = PrimaryPushButton(FIF.PLAY, "开始推理", topbar)
        self._run_btn.clicked.connect(self._toggle_run)
        tb_lay.addWidget(self._run_btn)

        root.addWidget(topbar)
        root.addWidget(_separator())

        # 滚动主体
        main_scroll = SmoothScrollArea(self)
        main_scroll.setWidgetResizable(True)
        main_scroll.setFrameShape(QFrame.Shape.NoFrame)
        main_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        main_scroll.setStyleSheet("background:transparent;border:none;")
        root.addWidget(main_scroll, 1)

        body_widget = QWidget()
        body_widget.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        main_scroll.setWidget(body_widget)

        body_lay = QHBoxLayout(body_widget)
        body_lay.setContentsMargins(24, 16, 24, 24)
        body_lay.setSpacing(14)
        body_lay.setAlignment(Qt.AlignmentFlag.AlignTop)

        left_lay = QVBoxLayout()
        left_lay.setSpacing(12)
        left_lay.setAlignment(Qt.AlignmentFlag.AlignTop)

        # ── 输入区 ────────────────────────────────────────────────────
        input_card = ElevatedCardWidget(body_widget)
        input_card.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        ic_lay = QVBoxLayout(input_card)
        ic_lay.setContentsMargins(18, 14, 18, 14)
        ic_lay.setSpacing(10)
        ic_lay.addWidget(_section_title("输入图像", FIF.PHOTO, input_card))

        self._drop_zone = DropZone(input_card)
        self._drop_zone.clicked_open.connect(self._browse_files)
        self._drop_zone.files_dropped.connect(self._add_files)
        ic_lay.addWidget(self._drop_zone)

        self._file_scroll = SmoothScrollArea(input_card)
        self._file_scroll.setWidgetResizable(True)
        self._file_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._file_scroll.setMaximumHeight(180)
        self._file_scroll.setStyleSheet("background:transparent;border:none;")
        self._file_scroll.hide()

        self._file_container = QWidget()
        self._file_container.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._file_list_lay = QVBoxLayout(self._file_container)
        self._file_list_lay.setContentsMargins(0, 0, 0, 0)
        self._file_list_lay.setSpacing(3)
        self._file_scroll.setWidget(self._file_container)
        ic_lay.addWidget(self._file_scroll)

        fbtn_row = QHBoxLayout()
        self._add_btn = PushButton(FIF.ADD, "添加文件", input_card)
        self._add_btn.clicked.connect(self._browse_files)
        self._clear_btn = PushButton(FIF.DELETE, "清空列表", input_card)
        self._clear_btn.clicked.connect(self._clear_files)
        self._clear_btn.setEnabled(False)
        self._file_count_lbl = CaptionLabel("已选 0 个文件", input_card)
        self._file_count_lbl.setStyleSheet("color:rgba(128,128,128,180);")
        fbtn_row.addWidget(self._add_btn)
        fbtn_row.addWidget(self._clear_btn)
        fbtn_row.addStretch()
        fbtn_row.addWidget(self._file_count_lbl)
        ic_lay.addLayout(fbtn_row)
        left_lay.addWidget(input_card)

        # ── 预览对比（MagnifyLabel 互相绑定） ─────────────────────────
        prev_card = ElevatedCardWidget(body_widget)
        prev_card.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        pv_lay = QVBoxLayout(prev_card)
        pv_lay.setContentsMargins(18, 14, 18, 14)
        pv_lay.setSpacing(10)

        prev_hdr = _section_title("预览对比", FIF.VIEW, prev_card)
        self._prev_hint = CaptionLabel("鼠标悬停图片可放大查看细节，两侧同步", prev_card)
        self._prev_hint.setStyleSheet(
            "color:rgba(128,128,128,160);font-size:11px;")
        pv_hdr_lay = QHBoxLayout()
        pv_hdr_lay.setContentsMargins(0, 0, 0, 0)
        pv_hdr_lay.addWidget(prev_hdr)
        pv_hdr_lay.addStretch()
        pv_hdr_lay.addWidget(self._prev_hint)
        pv_lay.addLayout(pv_hdr_lay)

        prev_imgs = QHBoxLayout()
        prev_imgs.setSpacing(10)

        # 左侧标签列
        left_col = QVBoxLayout()
        left_col.setSpacing(4)
        orig_label = CaptionLabel("原图", prev_card)
        orig_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        orig_label.setStyleSheet("color:rgba(128,128,128,180);")
        # self._prev_orig = MagnifyLabel("原图", prev_card)
        self._prev_orig = MagnifyFlipView(self)
        left_col.addWidget(self._prev_orig)
        left_col.addWidget(orig_label)
        prev_imgs.addLayout(left_col)

        # 右侧标签列
        right_col = QVBoxLayout()
        right_col.setSpacing(4)
        out_label = CaptionLabel("超分结果", prev_card)
        out_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        out_label.setStyleSheet("color:rgba(128,128,128,180);")
        # self._prev_out = MagnifyLabel("超分结果", prev_card)
        self._prev_out = MagnifyFlipView(self)
        right_col.addWidget(self._prev_out)
        right_col.addWidget(out_label)
        prev_imgs.addLayout(right_col)

        # 绑定放大镜同步
        self._prev_orig.peer = self._prev_out

        pv_lay.addLayout(prev_imgs)
        left_lay.addWidget(prev_card)

        # ── 进度 & 统计 ───────────────────────────────────────────────
        stat_card = ElevatedCardWidget(body_widget)
        stat_card.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        st_lay = QVBoxLayout(stat_card)
        st_lay.setContentsMargins(18, 14, 18, 14)
        st_lay.setSpacing(10)
        st_lay.addWidget(_section_title(
            "推理进度 & 统计", FIF.SPEED_HIGH, stat_card))

        sg = QGridLayout()
        sg.setSpacing(8)
        sg.setColumnStretch(0, 1)
        sg.setColumnStretch(1, 1)
        self._stat_widgets: dict[str, StrongBodyLabel] = {}
        for i, (key, label, init) in enumerate([
            ("processed", "已处理",  "0 / 0"),
            ("avg_time",  "平均耗时", "—"),
            ("vram",      "显存占用", "—"),
            ("device",    "推理设备", "GPU"),
        ]):
            cell = QWidget(stat_card)
            cell.setSizePolicy(QSizePolicy.Policy.Expanding,
                               QSizePolicy.Policy.Preferred)
            cell.setStyleSheet(
                "QWidget{background:rgba(128,128,128,10);border-radius:8px;}")
            cl = QVBoxLayout(cell)
            cl.setContentsMargins(10, 8, 10, 8)
            cl.setSpacing(2)
            k = CaptionLabel(label, cell)
            k.setStyleSheet("color:rgba(128,128,128,180);")
            v = StrongBodyLabel(init, cell)
            cl.addWidget(k)
            cl.addWidget(v)
            sg.addWidget(cell, i // 2, i % 2)
            self._stat_widgets[key] = v
        st_lay.addLayout(sg)

        prog_box = QWidget(stat_card)
        prog_box.setSizePolicy(QSizePolicy.Policy.Expanding,
                               QSizePolicy.Policy.Preferred)
        prog_box.setStyleSheet(
            "QWidget{background:rgba(128,128,128,8);border-radius:8px;}")
        pb_lay = QVBoxLayout(prog_box)
        pb_lay.setContentsMargins(12, 10, 12, 10)
        pb_lay.setSpacing(5)

        ph = QHBoxLayout()
        ph.addWidget(StrongBodyLabel("处理进度", prog_box))
        ph.addStretch()
        self._prog_pct_lbl = CaptionLabel("0%", prog_box)
        self._prog_pct_lbl.setStyleSheet(f"color:{ACCENT};")
        ph.addWidget(self._prog_pct_lbl)
        pb_lay.addLayout(ph)

        self._progress_bar = ProgressBar(prog_box)
        self._progress_bar.setValue(0)
        pb_lay.addWidget(self._progress_bar)

        pm = QHBoxLayout()
        self._cur_file_lbl = CaptionLabel("就绪", prog_box)
        self._cur_file_lbl.setStyleSheet("color:rgba(128,128,128,180);")
        self._eta_lbl = CaptionLabel("—", prog_box)
        self._eta_lbl.setStyleSheet("color:rgba(128,128,128,180);")
        pm.addWidget(self._cur_file_lbl)
        pm.addStretch()
        pm.addWidget(self._eta_lbl)
        pb_lay.addLayout(pm)
        st_lay.addWidget(prog_box)

        log_cap = CaptionLabel("运行日志", stat_card)
        log_cap.setStyleSheet("color:rgba(128,128,128,180);margin-top:2px;")
        st_lay.addWidget(log_cap)
        self._log_area = QLabel("等待开始…", stat_card)
        self._log_area.setWordWrap(True)
        self._log_area.setAlignment(
            Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self._log_area.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._log_area.setStyleSheet("""
            QLabel{background:rgba(0,0,0,25);border-radius:6px;padding:8px;
                   font-family:Consolas,monospace;font-size:11px;
                   color:rgba(200,200,200,200);}
        """)
        st_lay.addWidget(self._log_area)

        left_lay.addWidget(stat_card)
        body_lay.addLayout(left_lay, 3)

        self._param_panel = ParamPanel(body_widget, self.device_options)
        body_lay.addWidget(self._param_panel, 1)

    # ── 文件操作 ───────────────────────────────────────────────────────

    def _browse_files(self):
        """点击选择：支持多文件选择。"""
        paths, _ = QFileDialog.getOpenFileNames(
            self, "选择图像文件", "",
            "图像文件 (*.png *.jpg *.jpeg *.webp *.bmp);;所有文件 (*)")
        if paths:
            self._add_files(paths)  # 支持多文件

    def _add_files(self, paths: list[str]):
        for p in paths:
            if p in self._files:
                continue
            self._files.append(p)
            item = FileListItem(p, self._file_container)
            item.removed.connect(self._remove_file)
            self._file_list_lay.addWidget(item)
            self._file_items[p] = item

            # 遍历所有文件，加载原图预览
            self._load_orig_preview(p)

        self._refresh_file_ui()

        # 有文件后锁定拖拽区
        if self._files:
            self._drop_zone.lock()

    def _remove_file(self, path: str):
        if path in self._file_items:
            self._file_items[path].setParent(None)
            del self._file_items[path]
        if path in self._files:
            self._files.remove(path)
        self._refresh_file_ui()

        # 清空时解锁拖拽、清空预览
        if not self._files:
            self._drop_zone.unlock()
            self._prev_orig.clearPixmap()
            self._prev_out.clearPixmap()

    def _clear_files(self):
        for item in self._file_items.values():
            item.setParent(None)
        self._file_items.clear()
        self._files.clear()
        self._drop_zone.unlock()
        self._prev_orig.clearPixmap()
        self._prev_out.clearPixmap()
        self._refresh_file_ui()

    def _refresh_file_ui(self):
        n = len(self._files)
        self._file_count_lbl.setText(f"已选 {n} 个文件")
        self._clear_btn.setEnabled(n > 0)
        self._file_scroll.setVisible(n > 0)
        self._drop_zone.setVisible(n == 0)

    def _load_orig_preview(self, path: str):
        """加载原图到左侧预览（支持多文件遍历加载，自动去重）。"""
        try:
            pix = QPixmap(path)
            if not pix.isNull():
                self._prev_orig.addImages([path])
        except Exception:
            pass

    # ── 推理控制 ───────────────────────────────────────────────────────

    def _toggle_run(self):
        if self._running:
            self._abort_run()
        else:
            self._start_run()

    def _start_run(self):
        if not self._files:
            InfoBar.warning(title="未选择文件", content="请先添加要处理的图像文件",
                            parent=self.window(), position=InfoBarPosition.TOP_RIGHT, duration=3000)
            return
        self._running = True
        self._run_btn.setText("停止推理")
        self._run_btn.setIcon(FIF.PAUSE)
        self._param_panel.setEnabled(False)

        params = self._param_panel.get_params()
        self._stat_widgets["processed"].setText(f"0 / {len(self._files)}")
        self._stat_widgets["device"].setText(
            "GPU" if params.get("gpu_id", "auto") != "-1" else "CPU")
        for item in self._file_items.values():
            item.set_status("等待")

        self._worker = InferenceWorker(self._files, params)
        self._thread = QThread(self)
        self._worker.moveToThread(self._thread)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_finished)
        self._worker.error.connect(self._on_error)
        self._worker.log_line.connect(self._append_log)
        self._thread.started.connect(self._worker.run)
        self._thread.start()
        self._append_log("推理开始…")

    def _abort_run(self):
        if self._worker:
            self._worker.abort()
        self._running = False
        self._run_btn.setText("开始推理")
        self._run_btn.setIcon(FIF.PLAY)
        self._param_panel.setEnabled(True)

    def _on_progress(self, pct: int, filename: str):
        self._progress_bar.setValue(pct)
        self._prog_pct_lbl.setText(f"{pct}%")
        self._cur_file_lbl.setText(filename)
        done = int(pct / 100 * len(self._files))
        self._stat_widgets["processed"].setText(f"{done} / {len(self._files)}")
        for i, path in enumerate(self._files):
            if i < done:
                self._file_items[path].set_status("完成")
            elif i == done:
                self._file_items[path].set_status("处理中")

    def _on_finished(self, count: int, elapsed: float):
        self._running = False
        self._run_btn.setText("开始推理")
        self._run_btn.setIcon(FIF.PLAY)
        self._param_panel.setEnabled(True)
        self._progress_bar.setValue(100)
        self._prog_pct_lbl.setText("100%")
        self._cur_file_lbl.setText("全部完成")
        self._eta_lbl.setText(f"总耗时 {elapsed:.1f}s")
        self._stat_widgets["avg_time"].setText(
            f"{elapsed/count:.1f}s" if count else "—")
        for item in self._file_items.values():
            item.set_status("完成")
        InfoBar.success(title="推理完成",
                        content=f"共处理 {count} 张图像，耗时 {elapsed:.1f} 秒",
                        parent=self.window(), position=InfoBarPosition.TOP_RIGHT, duration=4000)
        if self._thread:
            self._thread.quit()

    def _on_error(self, msg: str):
        self._abort_run()
        InfoBar.error(title="推理出错", content=msg,
                      parent=self.window(), position=InfoBarPosition.TOP_RIGHT, duration=5000)

    def _append_log(self, line: str):
        try:
            print(line)
        except Exception:
            pass
        old = self._log_area.text()
        lines = old.split("\n") if old != "等待开始…" else []
        lines.append(line)
        self._log_area.setText("\n".join(lines[-6:]))

    def update_preview(self, orig_path: str, out_path: str):
        """Worker 完成单张后调用：更新左右预览图。"""
        try:
            if orig_path and os.path.exists(orig_path):
                pix = QPixmap(orig_path)
                if not pix.isNull():
                    self._prev_orig.addImages([pix])
            if out_path and os.path.exists(out_path):
                pix2 = QPixmap(out_path)
                if not pix2.isNull():
                    self._prev_out.addImages([pix2])
        except Exception:
            pass

    def _open_output_dir(self):
        d = self._param_panel.out_dir_edit.text()
        if os.path.exists(d):
            import subprocess
            subprocess.Popen(
                ["explorer" if sys.platform == "win32" else "open", d])

    def _reset(self):
        self._clear_files()
        self._progress_bar.setValue(0)
        self._prog_pct_lbl.setText("0%")
        self._cur_file_lbl.setText("就绪")
        self._eta_lbl.setText("—")
        self._log_area.setText("等待开始…")
        for key, v in self._stat_widgets.items():
            v.setText("—" if key != "processed" else "0 / 0")
