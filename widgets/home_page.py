"""
绘世风格启动器主页面
依赖: pip install PyQt6 pyqt-fluent-widgets
运行: python launcher_home.py
"""
from utils.atool import resource_path
from pathlib import Path
import sys
import os
import subprocess
from PyQt6.QtCore import (Qt, QTimer, pyqtSignal, QSize)
from PyQt6.QtGui import (QColor, QFont, QPainter, QBrush,
                         QLinearGradient, QRadialGradient, QPixmap,)
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout,
    QFrame,  QSizePolicy, QGridLayout, QGraphicsOpacityEffect,
)
import math
from qfluentwidgets import (
    ElevatedCardWidget,
    TransparentToolButton,
    SubtitleLabel, BodyLabel, CaptionLabel, StrongBodyLabel, HorizontalFlipView,
    SmoothScrollArea,
    IconWidget, FluentIcon as FIF,
    InfoBar, InfoBarPosition,
    BodyLabel,
)


# ══════════════════════════════════════════════════════════════════════
#  配置数据
# ══════════════════════════════════════════════════════════════════════
APP_NAME = "LaunchAI"
APP_TITLE = "奇点 - 启动器"
APP_SLOGAN = "AI ALL IN ONE"
APP_VERSION = "0.0.0 Build 1"
APP_DESC_VER = "2026-6-1 12:00"
Launch_VER = "C000001 - First version... (2026-6-1 12:00)"


_BANNER_IMG_PATH = resource_path(os.path.join("resource", "home_bg"))
FOLDERS = [
    {"icon": FIF.FOLDER,       "name": "根目录",     "path": "."},
    {"icon": FIF.APPLICATION,  "name": "自定义节点",  "path": "custom_nodes"},
    {"icon": FIF.PHOTO,        "name": "输入图片",    "path": "input"},
    {"icon": FIF.SAVE_COPY,    "name": "输出图片",    "path": "output"},
]

NOTICES = [
    ("警告", "Github开源项目 请勿用于盈利性用途 归属个人所有"),
    ("声明", "本启动器免费提供，如您通过其他渠道付费获得本软件，请立即退款并投诉相应商家"),
    ("官方渠道", "本启动器唯一发布地点位于 Github @Estrella 请认准官方来源。"),
]

# 缓动函数：ease-out cubic


def _ease_out(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return 1.0 - (1.0 - t) ** 3

# ease-in-out sine


def _ease_inout(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return -(math.cos(math.pi * t) - 1) / 2


class BannerWidget(QWidget):
    TRANS_MS = 600
    TRANS_STEP = 16

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(120)
        self.setMaximumHeight(300)
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Preferred)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        # 布局
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # 使用 HorizontalFlipView - 移除 setFixedSize，让它自动铺满
        self.flip_view = HorizontalFlipView(self)
        self.flip_view.setSpacing(15)
        self.flip_view.setBorderRadius(15)
        self.flip_view.setAspectRatioMode(
            Qt.AspectRatioMode.KeepAspectRatioByExpanding)
        self.flip_view.clicked.connect(self._start_transition)
        layout.addWidget(self.flip_view)

        # 文字覆盖层 - 独立于 flip_view，始终在最顶层
        self.text_overlay = TextOverlayWidget(self, self)
        self.text_overlay.raise_()

        self._raw_pixmaps: list[QPixmap] = []
        self._load_images()

        self._blend = 0.0 if self._raw_pixmaps else 1.0
        self._img_scale = 1.0
        self._mode_img = bool(self._raw_pixmaps)
        self._transitioning = False
        self._trans_elapsed = 0

        self._phase = 0.0
        self._anim_timer = QTimer(self)
        self._anim_timer.timeout.connect(self._tick_anim)
        self._anim_timer.start(30)

        self._trans_timer = QTimer(self)
        self._trans_timer.setInterval(self.TRANS_STEP)
        self._trans_timer.timeout.connect(self._tick_transition)

    def _load_images(self):
        """加载多张图片"""
        if os.path.isdir(_BANNER_IMG_PATH):
            for f in sorted(os.listdir(_BANNER_IMG_PATH)):
                if f.lower().endswith(('.png', '.jpg', '.jpeg')):
                    px = QPixmap(os.path.join(_BANNER_IMG_PATH, f))
                    if not px.isNull():
                        self._raw_pixmaps.append(px)
                        self.flip_view.addImage(px)

        if not self._raw_pixmaps and os.path.isfile(_BANNER_IMG_PATH):
            px = QPixmap(_BANNER_IMG_PATH)
            if not px.isNull():
                self._raw_pixmaps.append(px)
                self.flip_view.addImage(px)

    def resizeEvent(self, event):
        """窗口大小变化时调整自身高度和文字覆盖层"""
        new_w = event.size().width()
        if new_w > 0 and self._raw_pixmaps:
            # 使用第一张图片的宽高比
            first_pix = self._raw_pixmaps[0]
            aspect = first_pix.width() / first_pix.height()
            target_h = int(new_w / aspect)
            target_h = max(self.minimumHeight(), min(
                self.maximumHeight(), target_h))
            if self.height() != target_h:
                self.setFixedHeight(target_h)

        super().resizeEvent(event)

        # 调整文字覆盖层大小和位置，使其覆盖整个 BannerWidget
        self.text_overlay.setGeometry(0, 0, self.width(), self.height())
        self.text_overlay.raise_()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._start_transition()
        super().mousePressEvent(e)

    def _start_transition(self):
        if not self._raw_pixmaps:
            return
        self._mode_img = not self._mode_img
        self._trans_elapsed = 0
        self._transitioning = True
        self._trans_timer.start()

    def _tick_anim(self):
        self._phase = (self._phase + 0.008) % 1.0
        self.update()

    def _tick_transition(self):
        self._trans_elapsed += self.TRANS_STEP
        t = self._trans_elapsed / self.TRANS_MS
        if t >= 1.0:
            t = 1.0
            self._transitioning = False
            self._trans_timer.stop()

        progress = t * t * (3.0 - 2.0 * t)

        if self._mode_img:
            self._blend = 1.0 - progress
            self._img_scale = 1.06 - 0.06 * progress
        else:
            self._blend = progress
            self._img_scale = 1.0 + 0.06 * progress

        # 控制 flip_view 透明度
        effect = QGraphicsOpacityEffect()
        effect.setOpacity(1.0 - self._blend)
        self.flip_view.setGraphicsEffect(effect if self._blend > 0 else None)

        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        w, h = self.width(), self.height()

        grad_alpha = int(255 * min(1.0, self._blend))

        # 只绘制动态渐变层（图片由 flip_view 显示）
        if grad_alpha > 0:
            p.setOpacity(grad_alpha / 255.0)

            bg = QLinearGradient(0, 0, w, h)
            bg.setColorAt(0.0, QColor(18, 14, 40))
            bg.setColorAt(0.35, QColor(30, 22, 70))
            bg.setColorAt(0.7, QColor(20, 38, 80))
            bg.setColorAt(1.0, QColor(10, 28, 55))
            p.fillRect(0, 0, w, h, QBrush(bg))

            # 光晕 1
            cx1 = int(w * (0.15 + 0.08 * math.sin(self._phase * 2 * math.pi)))
            cy1 = int(h * (0.4 + 0.1 * math.cos(self._phase * 2 * math.pi * 0.7)))
            rg1 = QRadialGradient(cx1, cy1, int(w * 0.35))
            rg1.setColorAt(0.0, QColor(200, 60, 140, 90))
            rg1.setColorAt(1.0, QColor(0, 0, 0, 0))
            p.fillRect(0, 0, w, h, QBrush(rg1))

            # 光晕 2
            cx2 = int(
                w * (0.65 + 0.06 * math.cos(self._phase * 2 * math.pi * 1.3)))
            cy2 = int(
                h * (0.3 + 0.12 * math.sin(self._phase * 2 * math.pi * 0.9)))
            rg2 = QRadialGradient(cx2, cy2, int(w * 0.4))
            rg2.setColorAt(0.0, QColor(80, 100, 220, 80))
            rg2.setColorAt(1.0, QColor(0, 0, 0, 0))
            p.fillRect(0, 0, w, h, QBrush(rg2))

            # 光晕 3
            cx3 = int(
                w * (0.85 + 0.04 * math.sin(self._phase * 2 * math.pi * 0.6)))
            cy3 = int(
                h * (0.7 + 0.08 * math.cos(self._phase * 2 * math.pi * 1.1)))
            rg3 = QRadialGradient(cx3, cy3, int(w * 0.28))
            rg3.setColorAt(0.0, QColor(40, 180, 160, 70))
            rg3.setColorAt(1.0, QColor(0, 0, 0, 0))
            p.fillRect(0, 0, w, h, QBrush(rg3))

            # 底部渐出
            fade2 = QLinearGradient(0, h * 0.6, 0, h)
            fade2.setColorAt(0.0, QColor(0, 0, 0, 0))
            fade2.setColorAt(1.0, QColor(32, 32, 32, 200))
            p.fillRect(0, 0, w, h, QBrush(fade2))

            p.setOpacity(1.0)

        p.end()

    # 文字单独绘制在覆盖层上
    def _update_text_overlay(self):
        """更新文字覆盖层的内容"""
        overlay = self.text_overlay
        overlay.update()

    def showEvent(self, event):
        super().showEvent(event)
        # 确保文字覆盖层在最顶层
        self.text_overlay.raise_()


class TextOverlayWidget(QWidget):
    def __init__(self, parent=None, banner_widget=None):
        super().__init__(parent)
        self.banner_widget = banner_widget
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def paintEvent(self, event):
        if not self.banner_widget:
            return

        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()

        # 文字（始终显示，不受透明度影响）
        p.setOpacity(1.0)

        p.setPen(QColor(255, 255, 255, 160))
        p.drawText(36, 54, APP_NAME)

        p.setPen(QColor(255, 255, 255, 245))
        p.drawText(36, 100, APP_TITLE)

        p.setPen(QColor(255, 255, 255, 190))
        p.drawText(38, 132, APP_SLOGAN)

        p.setPen(QColor(255, 255, 255, 80))
        p.drawText(w - 220, h - 12, "Created by 13ee.icu @ 2026")

        if self.banner_widget and hasattr(self.banner_widget, '_blend'):
            if self.banner_widget._blend < 0.5 and self.banner_widget._raw_pixmaps:
                hint_a = int(180 * (1.0 - self.banner_widget._blend * 2))
                p.setPen(QColor(255, 255, 255, hint_a))
                p.drawText(w - 130, 18, "点击切换动态模式 ✦")

        p.end()

# ══════════════════════════════════════════════════════════════════════
#  文件夹快捷卡
# ══════════════════════════════════════════════════════════════════════


class FolderCard(ElevatedCardWidget):
    clicked_open = pyqtSignal(str)

    def __init__(self, icon, name: str, path: str, parent=None):
        super().__init__(parent)
        self._path = path
        self.setFixedHeight(64)
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(16, 0, 12, 0)
        lay.setSpacing(12)

        ico = IconWidget(icon, self)
        ico.setFixedSize(20, 20)
        lay.addWidget(ico, 0, Qt.AlignmentFlag.AlignVCenter)

        col = QVBoxLayout()
        col.setSpacing(1)
        name_lbl = StrongBodyLabel(name, self)
        path_lbl = CaptionLabel(path, self)
        path_lbl.setStyleSheet("color: rgba(140,140,140,200);")
        col.addWidget(name_lbl)
        col.addWidget(path_lbl)
        lay.addLayout(col, 1)

        # 箭头
        arr = TransparentToolButton(FIF.CHEVRON_RIGHT, self)
        arr.setFixedSize(28, 28)
        arr.clicked.connect(self._open)
        lay.addWidget(arr, 0, Qt.AlignmentFlag.AlignVCenter)

    def _open(self):
        self.clicked_open.emit(self._path)

    def mousePressEvent(self, e):
        super().mousePressEvent(e)

    def mouseReleaseEvent(self, e):
        super().mouseReleaseEvent(e)
        if e.button() == Qt.MouseButton.LeftButton:
            self._open()


# ══════════════════════════════════════════════════════════════════════
#  公告条目
# ══════════════════════════════════════════════════════════════════════
class NoticeItem(QWidget):
    def __init__(self, title: str, content: str, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 4, 0, 12)
        lay.setSpacing(4)

        t = StrongBodyLabel(title, self)
        lay.addWidget(t)

        c = BodyLabel(content, self)
        c.setWordWrap(True)
        c.setStyleSheet("color: rgba(200,200,200,210); line-height: 1.6;")
        lay.addWidget(c)


# ══════════════════════════════════════════════════════════════════════
#  版本信息条
# ══════════════════════════════════════════════════════════════════════
class VersionBar(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(72)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 8, 0, 8)
        lay.setSpacing(3)

        def row(label, value):
            h = QHBoxLayout()
            h.setSpacing(8)
            lbl = CaptionLabel(label, self)
            lbl.setFixedWidth(90)
            lbl.setStyleSheet("color: rgba(140,140,140,200);")
            val = CaptionLabel(value, self)
            val.setStyleSheet("color: rgba(200,200,200,200);")
            h.addWidget(lbl)
            h.addWidget(val)
            h.addStretch()
            return h

        lay.addLayout(row("启动器版本：", APP_VERSION))
        lay.addLayout(row("描述文件版本：", APP_DESC_VER))
        lay.addLayout(row("LaunchAI 版本：", Launch_VER))


# ══════════════════════════════════════════════════════════════════════
#  主页面 Widget
# ════════════════════════════════════════════
class HomePage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("HomePage")

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── 外层滚动 ─────────────────────────────────────────────────
        scroll = SmoothScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet(
            "SmoothScrollArea{background:transparent;border:none;}")
        root.addWidget(scroll)

        body = QWidget()
        body.setObjectName("body")
        body.setStyleSheet("QWidget#body{background:transparent;}")
        body.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Expanding)
        scroll.setWidget(body)

        vlay = QVBoxLayout(body)
        vlay.setContentsMargins(0, 0, 0, 0)
        vlay.setSpacing(0)

        # ── 横幅 ─────────────────────────────────────────────────────
        self._banner = BannerWidget(body)
        vlay.addWidget(self._banner)

        # ── 内容区（左主 + 右侧公告） ─────────────────────────────────
        content_wrap = QWidget(body)
        content_lay = QHBoxLayout(content_wrap)
        content_lay.setContentsMargins(20, 20, 20, 20)
        content_lay.setSpacing(16)
        # content_wrap 要撑满横幅下方所有剩余空间
        content_wrap.setSizePolicy(QSizePolicy.Policy.Expanding,
                                   QSizePolicy.Policy.Expanding)
        vlay.addWidget(content_wrap, 1)   # stretch=1，吃掉全部剩余高度

        # ── 左：文件夹 ────────────────────────────────────────────────
        left = QVBoxLayout()
        left.setSpacing(12)

        folder_title = SubtitleLabel("文件夹", body)
        left.addWidget(folder_title)

        # 网格：2列
        grid = QGridLayout()
        grid.setSpacing(10)
        for i, item in enumerate(FOLDERS):
            card = FolderCard(item["icon"], item["name"], item["path"], body)
            card.clicked_open.connect(self._open_folder)
            row, col = divmod(i, 2)
            grid.addWidget(card, row, col)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        left.addLayout(grid)

        left.addStretch()

        # ── 版本信息条 ────────────────────────────────────────────────
        sep = QFrame(body)
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("background: rgba(128,128,128,40);")
        sep.setFixedHeight(1)
        left.addWidget(sep)
        left.addWidget(VersionBar(body))

        content_lay.addLayout(left, 3)

        # ── 右：公告 ──────────────────────────────────────────────────
        right_card = ElevatedCardWidget(body)
        right_card.setFixedWidth(260)
        right_lay = QVBoxLayout(right_card)
        right_lay.setContentsMargins(16, 14, 16, 14)
        right_lay.setSpacing(0)

        notice_title = SubtitleLabel("公告", right_card)
        right_lay.addWidget(notice_title)
        right_lay.addSpacing(10)

        notice_scroll = SmoothScrollArea(right_card)
        notice_scroll.setFrameShape(QFrame.Shape.NoFrame)
        notice_scroll.setWidgetResizable(True)
        notice_scroll.setStyleSheet(
            "SmoothScrollArea{background:transparent;border:none;}")
        notice_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        notice_body = QWidget()
        notice_body.setStyleSheet("background:transparent;")
        nb_lay = QVBoxLayout(notice_body)
        nb_lay.setContentsMargins(0, 0, 4, 0)
        nb_lay.setSpacing(0)

        for title, content in NOTICES:
            nb_lay.addWidget(NoticeItem(title, content, notice_body))
            sep2 = QFrame(notice_body)
            sep2.setFrameShape(QFrame.Shape.HLine)
            sep2.setStyleSheet("background: rgba(128,128,128,40);")
            sep2.setFixedHeight(1)
            nb_lay.addWidget(sep2)

        nb_lay.addStretch()
        notice_scroll.setWidget(notice_body)
        right_lay.addWidget(notice_scroll, 1)

        # 公告卡不设 AlignTop，跟左侧等高撑满
        right_card.setSizePolicy(QSizePolicy.Policy.Fixed,
                                 QSizePolicy.Policy.Expanding)
        content_lay.addWidget(right_card)

    def _open_folder(self, path: str):
        abs_path = os.path.abspath(path)
        if not os.path.exists(abs_path):
            InfoBar.warning(title="提示", content=f"路径不存在：{abs_path}",
                            orient=Qt.Orientation.Horizontal, isClosable=True,
                            position=InfoBarPosition.TOP_RIGHT,
                            duration=3000, parent=self)
            return
        if sys.platform == "win32":
            os.startfile(abs_path)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", abs_path])
        else:
            subprocess.Popen(["xdg-open", abs_path])
