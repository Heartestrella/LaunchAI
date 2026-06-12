"""
yolo_inference_page.py
~~~~~~~~~~~~~~~~~~~~~~
YOLO 目标检测推理页面
"""
from __future__ import annotations
from qfluentwidgets import MessageBoxBase, SubtitleLabel, TextEdit
import os
import sys
import re
import time
import random
import colorsys
from pathlib import Path
from typing import Optional
from PyQt6.QtCore import QUrl
from PyQt6.QtGui import QTextCursor, QDesktopServices
from qfluentwidgets import TextEdit
from PyQt6.QtCore import (
    Qt, QPoint, QPointF, QRect, QRectF, QSize,
    QThread, QObject, pyqtSignal, QTimer
)
from PyQt6.QtGui import (
    QPixmap, QImage, QPainter, QPen, QBrush, QColor, QFont,
    QDragEnterEvent, QDropEvent, QCursor, QPainterPath, QFontMetrics
)
from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QFileDialog, QLabel, QFrame, QSizePolicy, QSplitter,
    QLayout, QLayoutItem
)

from qfluentwidgets import (
    setTheme, Theme,
    ElevatedCardWidget, CardWidget,
    TitleLabel, BodyLabel, CaptionLabel, StrongBodyLabel,
    PrimaryPushButton, PushButton, TransparentPushButton, TransparentToolButton,
    ToolButton, ComboBox, Slider, SwitchButton, LineEdit, SearchLineEdit,
    ProgressBar, SmoothScrollArea,
    InfoBar, InfoBarPosition,
    FluentIcon as FIF, IconWidget,
    PillPushButton,
)
from workers.yolo_worker import patch_yolo_page

# ══════════════════════════════════════════════════════════════════════
#  常量
# ══════════════════════════════════════════════════════════════════════
ACCENT = "#0078D4"
SUCCESS = "#0DB37E"
WARNING = "#F7B731"
DANGER = "#FC5C65"

YOLO_MODELS = {
    "yolov8n":  ("YOLOv8 Nano · 最快",    "6.2M",  "640"),
    "yolov8s":  ("YOLOv8 Small · 平衡",   "21.5M", "640"),
    "yolov8m":  ("YOLOv8 Medium · 推荐",  "49.7M", "640"),
    "yolov8l":  ("YOLOv8 Large · 高精度", "83.7M", "640"),
    "yolov8x":  ("YOLOv8 X-Large · 极致", "130M",  "640"),
    "yolov11n": ("YOLOv11 Nano",          "5.4M",  "640"),
    "yolov11s": ("YOLOv11 Small",         "19.0M", "640"),
    "yolov11m": ("YOLOv11 Medium",        "43.9M", "640"),
    "yolov11l": ("YOLOv11 Large",         "57.0M", "640"),
    "yolov11x": ("YOLOv11 X-Large",       "112M",  "640"),
}

COCO_CLASSES = [
    "person", "bicycle", "car", "motorcycle", "airplane", "bus", "train", "truck", "boat",
    "traffic light", "fire hydrant", "stop sign", "parking meter", "bench", "bird", "cat",
    "dog", "horse", "sheep", "cow", "elephant", "bear", "zebra", "giraffe", "backpack",
    "umbrella", "handbag", "tie", "suitcase", "frisbee", "skis", "snowboard", "sports ball",
    "kite", "baseball bat", "baseball glove", "skateboard", "surfboard", "tennis racket",
    "bottle", "wine glass", "cup", "fork", "knife", "spoon", "bowl", "banana", "apple",
    "sandwich", "orange", "broccoli", "carrot", "hot dog", "pizza", "donut", "cake", "chair",
    "couch", "potted plant", "bed", "dining table", "toilet", "tv", "laptop", "mouse", "remote",
    "keyboard", "cell phone", "microwave", "oven", "toaster", "sink", "refrigerator", "book",
    "clock", "vase", "scissors", "teddy bear", "hair drier", "toothbrush",
]

IMG_SIZES = ["320", "416", "512", "640", "800", "1024", "1280"]
OUTPUT_FORMATS = ["不保存", "图片+TXT(YOLO)", "图片+JSON(COCO)", "仅图片"]


class LogTextEdit(TextEdit):
    """支持彩色文本和超链接的日志控件"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptRichText(True)
        self.setReadOnly(True)

    def append_colored(self, html_text: str):
        """添加彩色 HTML 文本到日志"""
        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self.setTextCursor(cursor)

        # 检测并转换 URL 为超链接
        html_text = self._convert_urls_to_links(html_text)

        # 插入 HTML 并换行
        cursor.insertHtml(html_text + '<br>')
        self.ensureCursorVisible()

    def _convert_urls_to_links(self, text: str) -> str:
        """将文本中的 URL 转换为可点击的超链接"""
        url_pattern = r'(https?://[^\s<>"\'{}|\\^`\[\]]+)'

        def replace_url(match):
            url = match.group(1)
            display_url = url if len(
                url) <= 80 else url[:40] + '...' + url[-30:]
            return f'<a href="{url}" style="color:#4FC3F7; text-decoration:underline;">{display_url}</a>'

        return re.sub(url_pattern, replace_url, text)

    def mousePressEvent(self, event):
        """处理超链接点击"""
        cursor = self.cursorForPosition(event.pos())
        if cursor.charFormat().isAnchor():
            anchor = cursor.charFormat().anchorHref()
            if anchor:
                QDesktopServices.openUrl(QUrl(anchor))
                return
        super().mousePressEvent(event)


# ══════════════════════════════════════════════════════════════════════
#  FlowLayout
# ══════════════════════════════════════════════════════════════════════
class FlowLayout(QLayout):
    """按宽度自动换行的流式布局。"""

    def __init__(self, parent=None, margin: int = 0,
                 h_spacing: int = 6, v_spacing: int = 6):
        super().__init__(parent)
        if parent is not None:
            self.setContentsMargins(margin, margin, margin, margin)
        self._h_space = h_spacing
        self._v_space = v_spacing
        self._items: list[QLayoutItem] = []

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, index):
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index):
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self._do_layout(QRect(0, 0, width, 0), True)

    def setGeometry(self, rect: QRect):
        super().setGeometry(rect)
        self._do_layout(rect, False)

    def sizeHint(self) -> QSize:
        return self.minimumSize()

    def minimumSize(self) -> QSize:
        size = QSize()
        for it in self._items:
            size = size.expandedTo(it.minimumSize())
        m = self.contentsMargins()
        return size + QSize(m.left() + m.right(), m.top() + m.bottom())

    def _do_layout(self, rect: QRect, test_only: bool) -> int:
        m = self.contentsMargins()
        eff = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        x = eff.x()
        y = eff.y()
        line_h = 0
        right = eff.right() + 1

        for item in self._items:
            w = item.widget()
            if w is not None and not w.isVisible():
                continue
            hint = item.sizeHint()
            next_x = x + hint.width() + self._h_space
            if next_x - self._h_space > right and line_h > 0:
                x = eff.x()
                y = y + line_h + self._v_space
                next_x = x + hint.width() + self._h_space
                line_h = 0
            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x = next_x
            line_h = max(line_h, hint.height())

        return (y + line_h) - rect.y() + m.bottom()


# ══════════════════════════════════════════════════════════════════════
#  工具函数
# ══════════════════════════════════════════════════════════════════════
def _separator():
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
    """彻底隐藏 SmoothScrollArea 的滚动条（保留滚轮 / 触摸滚动）。"""
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    # qfluentwidgets 的 SmoothScrollArea 用自绘的 SmoothScrollBar，
    # 仅靠 policy 在某些版本不生效，把 widget 尺寸压成 0 兜底
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


def _class_color(class_id: int) -> QColor:
    h = (class_id * 0.61803398875) % 1.0
    r, g, b = colorsys.hsv_to_rgb(h, 0.65, 0.95)
    return QColor(int(r * 255), int(g * 255), int(b * 255))


# ══════════════════════════════════════════════════════════════════════
#  Worker
# ══════════════════════════════════════════════════════════════════════
class YoloWorker(QObject):
    progress = pyqtSignal(int, str)
    image_done = pyqtSignal(str, list, float)
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
        t0 = time.time()
        total = len(self._files)
        for i, f in enumerate(self._files):
            if self._abort:
                self.log_line.emit("[中止] 用户中止推理")
                return
            name = Path(f).name
            self.log_line.emit(f"[{i+1}/{total}] {name}")
            time.sleep(0.15)

            n = random.randint(2, 8)
            dets = []
            for _ in range(n):
                cid = random.randint(0, len(COCO_CLASSES) - 1)
                conf = round(random.uniform(
                    self._params.get("conf", 0.25), 0.99), 3)
                x1 = random.randint(20, 400)
                y1 = random.randint(20, 300)
                x2 = x1 + random.randint(40, 200)
                y2 = y1 + random.randint(40, 200)
                dets.append({
                    "class_id":   cid,
                    "class_name": COCO_CLASSES[cid],
                    "conf":       conf,
                    "bbox":       [x1, y1, x2, y2],
                })
            self.image_done.emit(f, dets, random.uniform(15, 60))
            self.progress.emit(int((i + 1) / total * 100), name)

        elapsed = time.time() - t0
        self.log_line.emit(f"[完成] 共 {total} 张，耗时 {elapsed:.1f}s")
        self.finished.emit(total, elapsed)


# ══════════════════════════════════════════════════════════════════════
#  预览面板
# ══════════════════════════════════════════════════════════════════════
class DetectionPreview(QWidget):
    BOX_W = 2
    LABEL_PAD = 4
    LABEL_FONT = QFont("Segoe UI", 9, QFont.Weight.Bold)

    def __init__(self, parent=None):
        super().__init__(parent)
        # 最小尺寸，过小窗口下也保留可视区域
        self.setMinimumSize(QSize(420, 360))
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Expanding)
        self._pix:        Optional[QPixmap] = None
        self._detections: list = []
        self._show_boxes = True
        self._show_labels = True
        self.setStyleSheet(
            "DetectionPreview{background:rgba(0,0,0,40);"
            "border:1px solid rgba(128,128,128,40);border-radius:8px;}"
        )
        self._highlight_idx: int = -1

    def set_highlight(self, idx: int):
        if self._highlight_idx == idx:
            return
        self._highlight_idx = idx
        self.update()

    def set_image(self, path: str):
        self._pix = QPixmap(path) if path and os.path.exists(path) else None
        self._detections = []
        self.update()

    def set_detections(self, dets: list):
        self._detections = dets or []
        self.update()

    def set_show_boxes(self, on: bool):
        self._show_boxes = on
        self.update()

    def set_show_labels(self, on: bool):
        self._show_labels = on
        self.update()

    def clear(self):
        self._pix = None
        self._detections = []
        self._highlight_idx = -1
        self.update()

    def _img_rect(self) -> QRectF:
        if not self._pix or self._pix.isNull():
            return QRectF()
        scaled = self._pix.size().scaled(
            self.size(), Qt.AspectRatioMode.KeepAspectRatio)
        x = (self.width() - scaled.width()) / 2
        y = (self.height() - scaled.height()) / 2
        return QRectF(x, y, scaled.width(), scaled.height())

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        if not self._pix or self._pix.isNull():
            p.setPen(QColor(128, 128, 128, 140))
            p.setFont(QFont("Segoe UI", 11))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                       "🖼  暂无预览\n选择文件后将在此显示检测结果")
            return

        ir = self._img_rect()
        p.drawPixmap(ir, self._pix, QRectF(self._pix.rect()))

        if not self._show_boxes or not self._detections:
            return

        sx = ir.width() / self._pix.width()
        sy = ir.height() / self._pix.height()
        p.setFont(self.LABEL_FONT)
        fm = QFontMetrics(self.LABEL_FONT)

        for i, d in enumerate(self._detections):
            # 高亮态：只画被悬浮的那一个，其它全跳过
            if self._highlight_idx >= 0 and i != self._highlight_idx:
                continue

            x1, y1, x2, y2 = d["bbox"]
            color = _class_color(d["class_id"])
            rect = QRectF(
                ir.x() + x1 * sx, ir.y() + y1 * sy,
                (x2 - x1) * sx, (y2 - y1) * sy)

            is_hl = (i == self._highlight_idx)

            if is_hl:
                # 半透明填充
                fill = QColor(color)
                fill.setAlpha(70)
                p.fillRect(rect, fill)
                # 白色外描边
                p.setPen(QPen(QColor(255, 255, 255), self.BOX_W + 2))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRect(rect)
                # 类别色内描边
                p.setPen(QPen(color, self.BOX_W))
                p.drawRect(rect)
            else:
                p.setPen(QPen(color, self.BOX_W))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRect(rect)

            if not self._show_labels:
                continue

            text = f"{d['class_name']} {d['conf']:.2f}"
            tw = fm.horizontalAdvance(text) + self.LABEL_PAD * 2
            th = fm.height() + 2
            tag = QRectF(rect.x(), max(ir.y(), rect.y() - th), tw, th)
            p.setBrush(QBrush(color))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawRect(tag)
            p.setPen(QColor(255, 255, 255))
            p.drawText(tag, Qt.AlignmentFlag.AlignCenter, text)

            text = f"{d['class_name']} {d['conf']:.2f}"
            tw = fm.horizontalAdvance(text) + self.LABEL_PAD * 2
            th = fm.height() + 2
            tag = QRectF(rect.x(), max(ir.y(), rect.y() - th), tw, th)
            p.setBrush(QBrush(color))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawRect(tag)
            p.setPen(QColor(255, 255, 255))
            p.drawText(tag, Qt.AlignmentFlag.AlignCenter, text)


# ══════════════════════════════════════════════════════════════════════
#  检测结果行
# ══════════════════════════════════════════════════════════════════════
class DetectionRow(QFrame):
    """检测结果行：色条 + 类名 + 置信度 + 百分比；hover 高亮预览框。"""

    hovered = pyqtSignal(int)   # 原始 detection 索引；-1 = 离开

    def __init__(self, det: dict, idx: int, parent=None):
        super().__init__(parent)
        self._idx = idx
        self.setFixedHeight(34)
        self.setObjectName("detRow")
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet("""
            #detRow {
                background: transparent;
                border-radius: 4px;
            }
            #detRow:hover {
                background: rgba(255,255,255,0.07);
            }
        """)

        color = _class_color(det["class_id"])

        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 4, 10, 4)
        lay.setSpacing(8)

        bar = QFrame(self)
        bar.setFixedSize(4, 22)
        bar.setStyleSheet(f"background:{color.name()};border-radius:2px;")
        lay.addWidget(bar)

        name = BodyLabel(det["class_name"], self)
        lay.addWidget(name, 1)

        conf = det["conf"]
        track = QFrame(self)
        track.setFixedSize(70, 6)
        track.setStyleSheet(
            "background:rgba(128,128,128,40);border-radius:3px;")
        fill = QFrame(track)
        fill.setGeometry(0, 0, int(70 * conf), 6)
        fill.setStyleSheet(f"background:{color.name()};border-radius:3px;")
        lay.addWidget(track)

        val = CaptionLabel(f"{conf*100:.0f}%", self)
        val.setFixedWidth(40)
        val.setAlignment(Qt.AlignmentFlag.AlignRight |
                         Qt.AlignmentFlag.AlignVCenter)
        val.setStyleSheet(f"color:{color.name()};font-weight:600;")
        lay.addWidget(val)

    def enterEvent(self, e):
        self.hovered.emit(self._idx)
        super().enterEvent(e)

    def leaveEvent(self, e):
        self.hovered.emit(-1)
        super().leaveEvent(e)

# ══════════════════════════════════════════════════════════════════════
#  类别过滤面板（恢复之前版式 + FlowLayout 自适应）
# ══════════════════════════════════════════════════════════════════════
# 顶部 import 加一行（如果还没有的话）


class ClassFilterPanel(QWidget):
    selection_changed = pyqtSignal(set)
    classes_changed = pyqtSignal(list)

    def __init__(self, classes: list[str], parent=None):
        super().__init__(parent)
        self._classes:  list[str] = list(classes)
        self._chips:    dict[str, PillPushButton] = {}
        self._selected: set[int] = set()

        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Preferred)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(6)

        # ① 搜索框
        self._search = SearchLineEdit(self)
        self._search.setPlaceholderText("筛选类别…")
        self._search.textChanged.connect(self._on_search)
        outer.addWidget(self._search)

        # ② 操作行：信息 / 编辑 / 全选 / 清空
        op_row = QHBoxLayout()
        op_row.setSpacing(6)
        self._info_lbl = CaptionLabel("", self)
        self._info_lbl.setStyleSheet("color:rgba(128,128,128,180);")
        op_row.addWidget(self._info_lbl)
        op_row.addStretch()

        edit_btn = TransparentPushButton(FIF.EDIT, "编辑", self)
        edit_btn.clicked.connect(self._open_edit_dialog)
        all_btn = TransparentPushButton("全选", self)
        all_btn.clicked.connect(self._select_all)
        none_btn = TransparentPushButton("清空", self)
        none_btn.clicked.connect(self._select_none)
        op_row.addWidget(edit_btn)
        op_row.addWidget(all_btn)
        op_row.addWidget(none_btn)
        outer.addLayout(op_row)

        # ③ chip 区（FlowLayout 直接挂在外层，按内容自然撑开）
        self._chip_box = QWidget(self)
        self._chip_box.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._flow = FlowLayout(self._chip_box, margin=0,
                                h_spacing=6, v_spacing=6)
        outer.addWidget(self._chip_box)

        self._rebuild_chips()

    # ── 公共 API ──────────────────────────────────────────────────────
    def set_classes(self, classes: list[str]):
        """替换全部类别（如从 ultralytics model.names 加载）。"""
        self._classes = list(classes)
        self._selected.clear()
        self._rebuild_chips()
        self._on_search(self._search.text())
        self.classes_changed.emit(self._classes)

    def get_classes(self) -> list[str]:
        return list(self._classes)

    def get_selected_ids(self) -> Optional[list[int]]:
        return None if not self._selected else sorted(self._selected)

    # ── 内部 ──────────────────────────────────────────────────────────
    def _rebuild_chips(self):
        # 清旧
        for chip in list(self._chips.values()):
            chip.setParent(None)
            chip.deleteLater()
        self._chips.clear()
        # 建新
        for i, cls in enumerate(self._classes):
            btn = PillPushButton(cls, self._chip_box)
            btn.setCheckable(True)
            btn.setFixedHeight(26)
            btn.toggled.connect(lambda on, idx=i: self._on_chip(idx, on))
            self._chips[cls] = btn
            self._flow.addWidget(btn)
        # 选中集合做边界裁剪
        self._selected = {i for i in self._selected if i < len(self._classes)}
        self._update_info()
        self._chip_box.adjustSize()

    def _on_chip(self, idx: int, checked: bool):
        if checked:
            self._selected.add(idx)
        else:
            self._selected.discard(idx)
        self._update_info()
        self.selection_changed.emit(self._selected.copy())

    def _on_search(self, text: str):
        text = text.strip().lower()
        for cls, chip in self._chips.items():
            chip.setVisible(not text or text in cls.lower())
        self._flow.invalidate()
        self._chip_box.adjustSize()

    def _select_all(self):
        for chip in self._chips.values():
            if chip.isVisible():
                chip.setChecked(True)

    def _select_none(self):
        for chip in self._chips.values():
            chip.setChecked(False)

    def _update_info(self):
        n = len(self._selected)
        total = len(self._classes)
        if n == 0:
            self._info_lbl.setText(f"默认检测全部 {total} 类")
        else:
            self._info_lbl.setText(f"已选 {n} / {total} 类")

    def _open_edit_dialog(self):
        """弹出编辑对话框，支持手动输入/粘贴类别列表。"""
        class _EditDialog(MessageBoxBase):
            def __init__(self, current: list[str], parent=None):
                super().__init__(parent)
                self.titleLabel = SubtitleLabel("编辑类别", self)
                self.editor = TextEdit(self)
                self.editor.setPlaceholderText(
                    "每行一个类别，或用英文逗号 / 中文逗号分隔。\n"
                    "示例：\nperson\nbicycle, car, truck\ndog"
                )
                self.editor.setPlainText("\n".join(current))
                self.editor.setMinimumSize(440, 340)
                self.viewLayout.addWidget(self.titleLabel)
                self.viewLayout.addWidget(self.editor)
                self.yesButton.setText("应用")
                self.cancelButton.setText("取消")

        dlg = _EditDialog(self._classes, self.window())
        if dlg.exec():
            raw = dlg.editor.toPlainText().strip()
            parts = re.split(r'[\n,，;；]+', raw)
            seen = set()
            uniq: list[str] = []
            for c in (p.strip() for p in parts):
                if c and c not in seen:
                    seen.add(c)
                    uniq.append(c)
            if uniq:
                self.set_classes(uniq)


# ══════════════════════════════════════════════════════════════════════
#  文件项 / 拖拽区
# ══════════════════════════════════════════════════════════════════════
class FileListItem(QWidget):
    removed = pyqtSignal(str)
    clicked = pyqtSignal(str)

    _STATUS_COLORS = {
        "等待":   "#888888",
        "处理中": ACCENT,
        "完成":   SUCCESS,
        "失败":   DANGER,
    }

    def __init__(self, path: str, parent=None):
        super().__init__(parent)
        self.path = path
        self.setFixedHeight(38)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 0, 8, 0)
        lay.setSpacing(8)

        ico = IconWidget(FIF.PHOTO, self)
        ico.setFixedSize(15, 15)
        lay.addWidget(ico)

        self._name_lbl = BodyLabel(Path(path).name, self)
        lay.addWidget(self._name_lbl, 1)

        self._det_count_lbl = CaptionLabel("—", self)
        self._det_count_lbl.setStyleSheet("color:rgba(128,128,128,180);")
        lay.addWidget(self._det_count_lbl)

        self._status_lbl = _badge("等待", "#888888")
        lay.addWidget(self._status_lbl)

        del_btn = TransparentToolButton(FIF.CLOSE, self)
        del_btn.setFixedSize(22, 22)
        del_btn.clicked.connect(lambda: self.removed.emit(self.path))
        lay.addWidget(del_btn)

    def mousePressEvent(self, e):
        self.clicked.emit(self.path)
        super().mousePressEvent(e)

    def set_status(self, s: str):
        color = self._STATUS_COLORS.get(s, "#888888")
        self._status_lbl.setText(s)
        self._status_lbl.setStyleSheet(
            f"QLabel{{background:{color}22;color:{color};"
            f"border:1px solid {color}55;border-radius:8px;"
            f"padding:1px 8px;font-size:11px;font-weight:600;}}"
        )

    def set_det_count(self, n: int):
        self._det_count_lbl.setText(f"{n} 个目标")


class DropZone(QWidget):
    files_dropped = pyqtSignal(list)
    clicked_open = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(110)
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Preferred)

        lay = QVBoxLayout(self)
        lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.setContentsMargins(16, 18, 16, 18)
        lay.setSpacing(6)

        ico = IconWidget(FIF.PHOTO, self)
        ico.setFixedSize(32, 32)
        lay.addWidget(ico, 0, Qt.AlignmentFlag.AlignCenter)

        t = StrongBodyLabel("拖拽图片到此处，或点击选择", self)
        t.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(t)

        sub = CaptionLabel("支持 PNG · JPG · WEBP · BMP（可批量）", self)
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sub.setStyleSheet("color:rgba(128,128,128,180);")
        lay.addWidget(sub)

        self._set_style(False)

    def _set_style(self, hover: bool):
        bc = ACCENT if hover else "rgba(128,128,128,80)"
        bg = "rgba(0,120,212,8)" if hover else "rgba(128,128,128,6)"
        self.setStyleSheet(
            f"DropZone{{border:1.5px dashed {bc};border-radius:10px;background:{bg};}}"
        )

    def dragEnterEvent(self, e: QDragEnterEvent):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
            self._set_style(True)

    def dragLeaveEvent(self, e):
        self._set_style(False)

    def dropEvent(self, e: QDropEvent):
        self._set_style(False)
        paths = [u.toLocalFile() for u in e.mimeData().urls()
                 if u.toLocalFile().lower().endswith(
                     ('.png', '.jpg', '.jpeg', '.webp', '.bmp'))]
        if paths:
            self.files_dropped.emit(paths)

    def mousePressEvent(self, e):
        self.clicked_open.emit()


# ══════════════════════════════════════════════════════════════════════
#  右侧参数面板
# ══════════════════════════════════════════════════════════════════════
class YoloParamPanel(QWidget):
    def __init__(self, parent=None, device_options: dict = None):
        super().__init__(parent)
        device_options = device_options or {}
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
        lay.setContentsMargins(0, 4, 4, 16)
        lay.setSpacing(14)

        # 模型
        lay.addWidget(_section_title("模型", FIF.DEVELOPER_TOOLS, body))
        self.model_combo = ComboBox(body)
        self.model_combo.addItems(list(YOLO_MODELS.keys()))
        self.model_combo.currentTextChanged.connect(self._on_model_changed)
        lay.addWidget(self.model_combo)

        self._model_desc = CaptionLabel("", body)
        self._model_desc.setWordWrap(True)
        self._model_desc.setStyleSheet("color:rgba(128,128,128,200);")
        lay.addWidget(self._model_desc)

        badge_row = QHBoxLayout()
        self._param_badge = _badge("—", ACCENT)
        self._size_badge = _badge("—", "#888888")
        badge_row.addWidget(self._param_badge)
        badge_row.addWidget(self._size_badge)
        badge_row.addStretch()
        lay.addLayout(badge_row)
        lay.addWidget(_separator())

        # 设备
        lay.addWidget(_section_title("推理设备", FIF.SPEED_HIGH, body))
        self.device_combo = ComboBox(body)
        items = [f"{k} · {v}" for k, v in device_options.items()] \
            or ["GPU · 0", "CPU · -1"]
        self.device_combo.addItems(items)
        lay.addWidget(self.device_combo)
        lay.addWidget(_separator())

        # 检测参数
        lay.addWidget(_section_title("检测参数", FIF.SETTING, body))

        self._add_caption(lay, "输入尺寸 (imgsz)", body)
        self.imgsz_combo = ComboBox(body)
        self.imgsz_combo.addItems(IMG_SIZES)
        self.imgsz_combo.setCurrentText("640")
        lay.addWidget(self.imgsz_combo)

        self.conf_slider, self._conf_lbl = self._add_slider(
            lay, "置信度阈值 (conf)", 1, 99, 25, 1, body)
        self.conf_slider.valueChanged.connect(
            lambda v: self._conf_lbl.setText(f"{v/100:.2f}"))
        self._conf_lbl.setText("0.25")

        self.iou_slider, self._iou_lbl = self._add_slider(
            lay, "IoU 阈值 (NMS)", 1, 99, 45, 1, body)
        self.iou_slider.valueChanged.connect(
            lambda v: self._iou_lbl.setText(f"{v/100:.2f}"))
        self._iou_lbl.setText("0.45")

        self.max_det_slider, self._max_det_lbl = self._add_slider(
            lay, "最大检测数 (max_det)", 10, 1000, 300, 10, body)

        self.fp16_switch = self._add_toggle(lay, "半精度推理 (fp16)", True, body)
        self.tta_switch = self._add_toggle(lay, "TTA 测试时增强", False, body)
        self.agnostic_switch = self._add_toggle(
            lay, "类别无关 NMS (agnostic)", False, body)
        lay.addWidget(_separator())

        # 类别过滤
        lay.addWidget(_section_title("类别过滤", FIF.FILTER, body))
        self.class_filter = ClassFilterPanel(COCO_CLASSES, body)
        lay.addWidget(self.class_filter)
        lay.addWidget(_separator())

        # 可视化
        lay.addWidget(_section_title("可视化", FIF.VIEW, body))
        self.box_switch = self._add_toggle(lay, "绘制检测框", True, body)
        self.label_switch = self._add_toggle(lay, "显示类别标签", True, body)
        self._add_caption(lay, "线宽", body)
        self.line_w_slider, self._lw_lbl = self._make_slider_row(
            1, 8, 2, 1, body)
        lw_row = QHBoxLayout()
        lw_row.addWidget(self.line_w_slider)
        lw_row.addWidget(self._lw_lbl)
        lay.addLayout(lw_row)
        lay.addWidget(_separator())

        # 输出
        lay.addWidget(_section_title("输出", FIF.FOLDER, body))
        self._add_caption(lay, "输出目录", body)
        out_row = QHBoxLayout()
        self.out_dir_edit = LineEdit(body)
        self.out_dir_edit.setText("./runs/detect")
        browse_btn = ToolButton(FIF.FOLDER, body)
        browse_btn.clicked.connect(self._browse_output)
        out_row.addWidget(self.out_dir_edit)
        out_row.addWidget(browse_btn)
        lay.addLayout(out_row)

        self._add_caption(lay, "保存方式", body)
        self.save_combo = ComboBox(body)
        self.save_combo.addItems(OUTPUT_FORMATS)
        self.save_combo.setCurrentIndex(1)
        lay.addWidget(self.save_combo)

        lay.addStretch()
        self._on_model_changed(self.model_combo.currentText())

    # ── 辅助 ──────────────────────────────────────────────────────────
    def _add_caption(self, lay, text, parent):
        lbl = CaptionLabel(text, parent)
        lbl.setStyleSheet("color:rgba(128,128,128,200);margin-top:2px;")
        lay.addWidget(lbl)

    def _make_slider_row(self, lo, hi, val, step, parent):
        s = Slider(Qt.Orientation.Horizontal, parent)
        s.setRange(lo, hi)
        s.setSingleStep(step)
        s.setValue(val)
        s.setSizePolicy(QSizePolicy.Policy.Expanding,
                        QSizePolicy.Policy.Fixed)
        v = CaptionLabel(str(val), parent)
        v.setFixedWidth(40)
        v.setAlignment(Qt.AlignmentFlag.AlignRight |
                       Qt.AlignmentFlag.AlignVCenter)
        s.valueChanged.connect(lambda x: v.setText(str(x)))
        return s, v

    def _add_slider(self, lay, label, lo, hi, val, step, parent):
        self._add_caption(lay, label, parent)
        s, v = self._make_slider_row(lo, hi, val, step, parent)
        row = QHBoxLayout()
        row.addWidget(s)
        row.addWidget(v)
        lay.addLayout(row)
        return s, v

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
        info = YOLO_MODELS.get(name)
        if info:
            desc, params, default_imgsz = info
            self._model_desc.setText(desc)
            self._param_badge.setText(params)
            self._size_badge.setText(f"imgsz {default_imgsz}")

    def _browse_output(self):
        d = QFileDialog.getExistingDirectory(self, "选择输出目录", "./")
        if d:
            self.out_dir_edit.setText(d)

    def get_params(self) -> dict:
        dev_text = self.device_combo.currentText()
        gm = re.search(r'·\s*(-?\d+)', dev_text)
        device = gm.group(1) if gm else "0"
        return {
            "model":      self.model_combo.currentText(),
            "device":     device,
            "imgsz":      int(self.imgsz_combo.currentText()),
            "conf":       self.conf_slider.value() / 100.0,
            "iou":        self.iou_slider.value() / 100.0,
            "max_det":    self.max_det_slider.value(),
            "fp16":       self.fp16_switch.isChecked(),
            "tta":        self.tta_switch.isChecked(),
            "agnostic":   self.agnostic_switch.isChecked(),
            "classes":    self.class_filter.get_selected_ids(),
            "draw_boxes": self.box_switch.isChecked(),
            "draw_label": self.label_switch.isChecked(),
            "line_w":     self.line_w_slider.value(),
            "out_dir":    self.out_dir_edit.text().strip() or "./runs/detect",
            "save_mode":  self.save_combo.currentText(),
        }


# ══════════════════════════════════════════════════════════════════════
#  主页面
# ══════════════════════════════════════════════════════════════════════
class YoloInferencePage(QWidget):
    def __init__(self, parent=None, device_options: dict = None):
        super().__init__(parent)
        self.setObjectName("YoloInferencePage")
        self.device_options = device_options or {"GPU 0": 0, "CPU": -1}

        self._files: list[str] = []
        self._file_items: dict[str, FileListItem] = {}
        self._results: dict[str, list] = {}
        self._infer_times: list[float] = []
        self._current_path: Optional[str] = None
        self._worker:  Optional[YoloWorker] = None
        self._thread:  Optional[QThread] = None
        self._running = False
        patch_yolo_page(self)
        self._build_ui()

    # ── UI ────────────────────────────────────────────────────────────

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Topbar
        topbar = QWidget(self)
        tb_lay = QHBoxLayout(topbar)
        tb_lay.setContentsMargins(24, 14, 24, 14)
        tb_lay.setSpacing(10)
        tb_lay.addWidget(TitleLabel("目标检测", topbar))
        badge = QLabel("YOLO", topbar)
        badge.setStyleSheet(
            f"QLabel{{background:{ACCENT}22;color:{ACCENT};"
            f"border:1px solid {ACCENT}55;border-radius:10px;"
            f"padding:2px 10px;font-size:12px;font-weight:600;}}"
        )
        tb_lay.addWidget(badge)
        tb_lay.addStretch()

        self._open_dir_btn = TransparentPushButton(
            FIF.FOLDER, "打开输出目录", topbar)
        self._open_dir_btn.clicked.connect(self._open_output_dir)
        tb_lay.addWidget(self._open_dir_btn)

        self._reset_btn = TransparentPushButton(FIF.SYNC, "重置", topbar)
        self._reset_btn.clicked.connect(self._reset)
        tb_lay.addWidget(self._reset_btn)

        self._run_btn = PrimaryPushButton(FIF.PLAY, "开始检测", topbar)
        self._run_btn.clicked.connect(self._toggle_run)
        tb_lay.addWidget(self._run_btn)

        root.addWidget(topbar)
        root.addWidget(_separator())

        # 整页滚动
        page_scroll = SmoothScrollArea(self)
        page_scroll.setWidgetResizable(True)
        page_scroll.setFrameShape(QFrame.Shape.NoFrame)
        page_scroll.setStyleSheet("background:transparent;border:none;")
        _no_scrollbar(page_scroll)
        root.addWidget(page_scroll, 1)

        page = QWidget()
        page.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Expanding)
        page_scroll.setWidget(page)

        body_lay = QHBoxLayout(page)
        body_lay.setContentsMargins(24, 16, 24, 24)
        body_lay.setSpacing(0)

        splitter = QSplitter(Qt.Orientation.Horizontal, page)
        splitter.setHandleWidth(8)
        splitter.setChildrenCollapsible(False)
        splitter.setStyleSheet("QSplitter::handle{background:transparent;}")
        body_lay.addWidget(splitter)

        # ── 左侧 ─────────────────────────────────────────────────────
        left_wrap = QWidget(splitter)
        left_wrap.setSizePolicy(QSizePolicy.Policy.Expanding,
                                QSizePolicy.Policy.Expanding)
        left = QVBoxLayout(left_wrap)
        left.setContentsMargins(0, 0, 8, 0)
        left.setSpacing(12)

        # 输入区
        in_card = ElevatedCardWidget(left_wrap)
        ic = QVBoxLayout(in_card)
        ic.setContentsMargins(18, 14, 18, 14)
        ic.setSpacing(10)
        ic.addWidget(_section_title("输入图像", FIF.PHOTO, in_card))

        self._drop_zone = DropZone(in_card)
        self._drop_zone.clicked_open.connect(self._browse_files)
        self._drop_zone.files_dropped.connect(self._add_files)
        ic.addWidget(self._drop_zone)

        self._file_scroll = SmoothScrollArea(in_card)
        self._file_scroll.setWidgetResizable(True)
        self._file_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._file_scroll.setStyleSheet("background:transparent;border:none;")
        self._file_scroll.setMinimumHeight(60)
        self._file_scroll.setMaximumHeight(180)
        _no_scrollbar(self._file_scroll)
        self._file_scroll.hide()
        self._file_box = QWidget()
        self._file_lay = QVBoxLayout(self._file_box)
        self._file_lay.setContentsMargins(0, 0, 0, 0)
        self._file_lay.setSpacing(3)
        self._file_lay.addStretch()
        self._file_scroll.setWidget(self._file_box)
        ic.addWidget(self._file_scroll)

        ftb = QHBoxLayout()
        self._add_btn = PushButton(FIF.ADD, "添加文件", in_card)
        self._add_btn.clicked.connect(self._browse_files)
        self._clear_btn = PushButton(FIF.DELETE, "清空", in_card)
        self._clear_btn.clicked.connect(self._clear_files)
        self._clear_btn.setEnabled(False)
        self._count_lbl = CaptionLabel("已选 0 个文件", in_card)
        self._count_lbl.setStyleSheet("color:rgba(128,128,128,180);")
        ftb.addWidget(self._add_btn)
        ftb.addWidget(self._clear_btn)
        ftb.addStretch()
        ftb.addWidget(self._count_lbl)
        ic.addLayout(ftb)
        left.addWidget(in_card)

        # 预览 + 检测列表
        prev_card = ElevatedCardWidget(left_wrap)
        prev_card.setSizePolicy(QSizePolicy.Policy.Expanding,
                                QSizePolicy.Policy.Expanding)
        pv = QVBoxLayout(prev_card)
        pv.setContentsMargins(18, 14, 18, 14)
        pv.setSpacing(10)

        hdr_row = QHBoxLayout()
        hdr_row.addWidget(_section_title("预览", FIF.VIEW, prev_card))
        hdr_row.addStretch()
        self._toggle_box_btn = PillPushButton("显示框", prev_card)
        self._toggle_box_btn.setCheckable(True)
        self._toggle_box_btn.setChecked(True)
        self._toggle_box_btn.toggled.connect(
            lambda on: self._preview.set_show_boxes(on))
        self._toggle_label_btn = PillPushButton("显示标签", prev_card)
        self._toggle_label_btn.setCheckable(True)
        self._toggle_label_btn.setChecked(True)
        self._toggle_label_btn.toggled.connect(
            lambda on: self._preview.set_show_labels(on))
        hdr_row.addWidget(self._toggle_box_btn)
        hdr_row.addWidget(self._toggle_label_btn)
        pv.addLayout(hdr_row)

        prev_split = QSplitter(Qt.Orientation.Horizontal, prev_card)
        prev_split.setHandleWidth(8)
        prev_split.setChildrenCollapsible(False)
        prev_split.setStyleSheet("QSplitter::handle{background:transparent;}")

        self._preview = DetectionPreview(prev_split)
        prev_split.addWidget(self._preview)

        det_box = QWidget(prev_split)
        det_box_lay = QVBoxLayout(det_box)
        det_box_lay.setContentsMargins(0, 0, 0, 0)
        det_box_lay.setSpacing(4)
        det_box_lay.addWidget(CaptionLabel("检测结果", det_box))

        self._det_scroll = SmoothScrollArea(det_box)
        self._det_scroll.setWidgetResizable(True)
        self._det_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._det_scroll.setStyleSheet("background:transparent;border:none;")
        _no_scrollbar(self._det_scroll)
        self._det_inner = QWidget()
        self._det_lay = QVBoxLayout(self._det_inner)
        self._det_lay.setContentsMargins(0, 0, 0, 0)
        self._det_lay.setSpacing(3)
        self._det_lay.addStretch()
        self._det_scroll.setWidget(self._det_inner)
        det_box_lay.addWidget(self._det_scroll, 1)

        self._det_empty = CaptionLabel("尚未检测", det_box)
        self._det_empty.setStyleSheet("color:rgba(128,128,128,160);")
        self._det_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        det_box_lay.addWidget(self._det_empty)

        prev_split.addWidget(det_box)
        prev_split.setSizes([700, 260])
        pv.addWidget(prev_split, 1)
        left.addWidget(prev_card, 1)

        # 进度 + 统计 + 日志
        st_card = ElevatedCardWidget(left_wrap)
        st = QVBoxLayout(st_card)
        st.setContentsMargins(18, 14, 18, 14)
        st.setSpacing(10)
        st.addWidget(_section_title("进度 & 统计", FIF.SPEED_HIGH, st_card))

        sg = QGridLayout()
        sg.setSpacing(8)
        for c in range(4):
            sg.setColumnStretch(c, 1)
        self._stats: dict[str, StrongBodyLabel] = {}
        for i, (k, label, init) in enumerate([
            ("processed", "已处理",   "0 / 0"),
            ("total_det", "总检测框", "0"),
            ("avg_conf",  "平均置信", "—"),
            ("fps",       "推理 FPS", "—"),
        ]):
            cell = QWidget(st_card)
            cell.setStyleSheet(
                "QWidget{background:rgba(128,128,128,10);border-radius:8px;}")
            cl = QVBoxLayout(cell)
            cl.setContentsMargins(10, 8, 10, 8)
            cl.setSpacing(2)
            kk = CaptionLabel(label, cell)
            kk.setStyleSheet("color:rgba(128,128,128,180);")
            vv = StrongBodyLabel(init, cell)
            cl.addWidget(kk)
            cl.addWidget(vv)
            sg.addWidget(cell, 0, i)
            self._stats[k] = vv
        st.addLayout(sg)

        prog = QWidget(st_card)
        prog.setStyleSheet(
            "QWidget{background:rgba(128,128,128,8);border-radius:8px;}")
        pl = QVBoxLayout(prog)
        pl.setContentsMargins(12, 10, 12, 10)
        pl.setSpacing(5)
        ph = QHBoxLayout()
        ph.addWidget(StrongBodyLabel("处理进度", prog))
        ph.addStretch()
        self._pct_lbl = CaptionLabel("0%", prog)
        self._pct_lbl.setStyleSheet(f"color:{ACCENT};")
        ph.addWidget(self._pct_lbl)
        pl.addLayout(ph)
        self._prog = ProgressBar(prog)
        self._prog.setValue(0)
        pl.addWidget(self._prog)
        pm = QHBoxLayout()
        self._cur_lbl = CaptionLabel("就绪", prog)
        self._cur_lbl.setStyleSheet("color:rgba(128,128,128,180);")
        self._eta_lbl = CaptionLabel("—", prog)
        self._eta_lbl.setStyleSheet("color:rgba(128,128,128,180);")
        pm.addWidget(self._cur_lbl)
        pm.addStretch()
        pm.addWidget(self._eta_lbl)
        pl.addLayout(pm)
        st.addWidget(prog)

        st.addWidget(CaptionLabel("运行日志", st_card))
        self._log = LogTextEdit(st_card)
        self._log.setPlaceholderText("等待开始…")
        self._log.setMinimumHeight(120)
        self._log.setMaximumHeight(240)
        self._log.setStyleSheet("""
            LogTextEdit {
                background:rgba(0,0,0,40);
                border:1px solid rgba(128,128,128,30);
                border-radius:6px;
                padding:6px 8px;
                font-family:Consolas,'Cascadia Mono',monospace;
                font-size:11px;
                color:rgba(220,220,220,220);
            }
        """)
        st.addWidget(self._log)
        left.addWidget(st_card)

        # ── 右侧 ─────────────────────────────────────────────────────
        right_wrap = QWidget(splitter)
        right_wrap.setSizePolicy(QSizePolicy.Policy.Preferred,
                                 QSizePolicy.Policy.Expanding)
        rlay = QVBoxLayout(right_wrap)
        rlay.setContentsMargins(8, 0, 0, 0)
        rlay.setSpacing(0)
        self._params = YoloParamPanel(right_wrap, self.device_options)
        rlay.addWidget(self._params)

        splitter.addWidget(left_wrap)
        splitter.addWidget(right_wrap)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([900, 340])

    # ── 文件 ──────────────────────────────────────────────────────────
    def _browse_files(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "选择图像", "",
            "图像 (*.png *.jpg *.jpeg *.webp *.bmp);;所有文件 (*)")
        if paths:
            self._add_files(paths)

    def _add_files(self, paths: list[str]):
        for p in paths:
            if p in self._files:
                continue
            self._files.append(p)
            item = FileListItem(p, self._file_box)
            item.removed.connect(self._remove_file)
            item.clicked.connect(self._show_path)
            self._file_lay.insertWidget(self._file_lay.count() - 1, item)
            self._file_items[p] = item
        self._refresh_file_ui()
        if self._files and not self._current_path:
            self._show_path(self._files[0])

    def _remove_file(self, path: str):
        if path in self._file_items:
            self._file_items[path].setParent(None)
            del self._file_items[path]
        if path in self._files:
            self._files.remove(path)
        self._results.pop(path, None)
        if self._current_path == path:
            self._current_path = None
            self._preview.clear()
            self._refresh_det_list([])
        self._refresh_file_ui()

    def _clear_files(self):
        for it in self._file_items.values():
            it.setParent(None)
        self._file_items.clear()
        self._files.clear()
        self._results.clear()
        self._current_path = None
        self._preview.clear()
        self._refresh_det_list([])
        self._refresh_file_ui()

    def _refresh_file_ui(self):
        n = len(self._files)
        self._count_lbl.setText(f"已选 {n} 个文件")
        self._clear_btn.setEnabled(n > 0)
        self._file_scroll.setVisible(n > 0)
        self._drop_zone.setVisible(n == 0)

    def _show_path(self, path: str):
        self._current_path = path
        self._preview.set_image(path)
        dets = self._results.get(path, [])
        self._preview.set_detections(dets)
        self._refresh_det_list(dets)

    def _refresh_det_list(self, dets: list):
        for i in reversed(range(self._det_lay.count())):
            it = self._det_lay.itemAt(i)
            w = it.widget()
            if w:
                w.setParent(None)

        self._preview.set_highlight(-1)   # 清掉旧高亮

        if not dets:
            self._det_empty.setVisible(True)
            self._det_lay.addStretch()
            return
        self._det_empty.setVisible(False)

        # 排序仅影响 UI，但要保留原始 index 用于高亮
        indexed = list(enumerate(dets))
        indexed.sort(key=lambda x: -x[1]["conf"])
        for orig_idx, d in indexed:
            row = DetectionRow(d, orig_idx, self._det_inner)
            row.hovered.connect(self._preview.set_highlight)
            self._det_lay.addWidget(row)
        self._det_lay.addStretch()

    # ── 推理控制 ───────────────────────────────────────────────────────
    def _toggle_run(self):
        if self._running:
            self._abort()
        else:
            self._start()

    def _start(self):
        if not self._files:
            InfoBar.warning(title="未选择文件", content="请先添加图像",
                            parent=self.window(),
                            position=InfoBarPosition.TOP_RIGHT, duration=3000)
            return
        self._running = True
        self._run_btn.setText("停止检测")
        self._run_btn.setIcon(FIF.PAUSE)
        self._params.setEnabled(False)
        self._results.clear()
        self._infer_times.clear()

        self._stats["processed"].setText(f"0 / {len(self._files)}")
        self._stats["total_det"].setText("0")
        self._stats["avg_conf"].setText("—")
        self._stats["fps"].setText("—")
        for it in self._file_items.values():
            it.set_status("等待")

        params = self._params.get_params()
        self._worker = YoloWorker(self._files, params)
        self._thread = QThread(self)
        self._worker.moveToThread(self._thread)
        self._worker.progress.connect(self._on_progress)
        self._worker.image_done.connect(self._on_image_done)
        self._worker.finished.connect(self._on_finished)
        self._worker.error.connect(self._on_error)
        self._worker.log_line.connect(self._append_log)
        self._thread.started.connect(self._worker.run)
        self._thread.start()
        self._append_log(f"开始检测，模型 {params['model']}，imgsz {params['imgsz']}")

    def _abort(self):
        if self._worker:
            self._worker.abort()
        self._running = False
        self._run_btn.setText("开始检测")
        self._run_btn.setIcon(FIF.PLAY)
        self._params.setEnabled(True)

    def _on_progress(self, pct: int, name: str):
        self._prog.setValue(pct)
        self._pct_lbl.setText(f"{pct}%")
        self._cur_lbl.setText(name)
        done = int(pct / 100 * len(self._files))
        self._stats["processed"].setText(f"{done} / {len(self._files)}")
        for i, p in enumerate(self._files):
            if i < done:
                self._file_items[p].set_status("完成")
            elif i == done:
                self._file_items[p].set_status("处理中")

    def _on_image_done(self, path: str, dets: list, infer_ms: float):
        self._results[path] = dets
        self._infer_times.append(infer_ms)
        if path in self._file_items:
            self._file_items[path].set_det_count(len(dets))
        if not self._current_path or self._current_path == path:
            self._current_path = path
            self._preview.set_image(path)
            self._preview.set_detections(dets)
            self._refresh_det_list(dets)

        total = sum(len(v) for v in self._results.values())
        confs = [d["conf"] for v in self._results.values() for d in v]
        avg_conf = sum(confs) / len(confs) if confs else 0
        avg_ms = sum(self._infer_times) / len(self._infer_times)
        self._stats["total_det"].setText(str(total))
        self._stats["avg_conf"].setText(
            f"{avg_conf*100:.1f}%" if confs else "—")
        self._stats["fps"].setText(
            f"{1000/avg_ms:.1f}" if avg_ms > 0 else "—")

    def _on_finished(self, count: int, elapsed: float):
        self._running = False
        self._run_btn.setText("开始检测")
        self._run_btn.setIcon(FIF.PLAY)
        self._params.setEnabled(True)
        self._prog.setValue(100)
        self._pct_lbl.setText("100%")
        self._cur_lbl.setText("全部完成")
        self._eta_lbl.setText(f"总耗时 {elapsed:.1f}s")
        for it in self._file_items.values():
            it.set_status("完成")
        InfoBar.success(
            title="检测完成",
            content=f"{count} 张图像 · 耗时 {elapsed:.1f}s",
            parent=self.window(),
            position=InfoBarPosition.TOP_RIGHT, duration=4000
        )
        if self._thread:
            self._thread.quit()

    def _on_error(self, msg: str):
        self._abort()
        InfoBar.error(title="检测出错", content=msg,
                      parent=self.window(),
                      position=InfoBarPosition.TOP_RIGHT, duration=5000)

    def _append_log(self, line: str):
        self._log.append_colored(line)
        # old = self._log.text()
        # lines = old.split("\n") if old != "等待开始…" else []
        # lines.append(line)
        # self._log.setText("\n".join(lines[-6:]))

    def _open_output_dir(self):
        d = self._params.out_dir_edit.text()
        os.makedirs(d, exist_ok=True)
        import subprocess
        subprocess.Popen(
            ["explorer" if sys.platform == "win32" else "open", d])

    def _reset(self):
        self._clear_files()
        self._prog.setValue(0)
        self._pct_lbl.setText("0%")
        self._cur_lbl.setText("就绪")
        self._eta_lbl.setText("—")
        self._log.setText("等待开始…")
        self._stats["processed"].setText("0 / 0")
        self._stats["total_det"].setText("0")
        self._stats["avg_conf"].setText("—")
        self._stats["fps"].setText("—")


# ══════════════════════════════════════════════════════════════════════
#  独立运行
# ══════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    app = QApplication(sys.argv)
    setTheme(Theme.DARK)
    w = QWidget()
    w.setWindowTitle("YOLO Detection · Demo")
    w.resize(1500, 950)
    lay = QVBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.addWidget(YoloInferencePage(w, {"GPU 0": 0, "CPU": -1}))
    w.show()
    sys.exit(app.exec())
