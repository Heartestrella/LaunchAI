import sys
import os
from pathlib import Path

from PyQt6.QtCore import Qt, QThread, QObject, pyqtSignal
from PyQt6.QtGui import QColor, QDragEnterEvent, QDropEvent
from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QFileDialog, QLabel, QFrame, QSizePolicy,
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
)


# ══════════════════════════════════════════════════════════════════════
#  常量
# ══════════════════════════════════════════════════════════════════════

ACCENT = "#0078D4"
SUCCESS = "#0DB37E"
WARNING = "#F7B731"
DANGER = "#FC5C65"

MODEL_INFO = {
    "RealESRGAN_x4plus":          ("通用 4× 超分，适合真实照片与复杂纹理", "4×", "64.0 MB"),
    "RealESRGAN_x4plus_anime_6B": ("动漫/插画专用，6-block 轻量版",        "4×", "17.0 MB"),
    "RealESRGAN_x2plus":          ("2× 放大，更小噪声，适合高清素材",      "2×", "64.0 MB"),
    "RealESRGANv3_x4":            ("v3 改进版，视频帧超分推荐",             "4×", "67.0 MB"),
    "自定义模型…":                 ("从指定路径加载 .pth 权重文件",          "—",  "—"),
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
#  后台推理线程（模拟，替换 run() 接入真实 RealESRGANer）
# ══════════════════════════════════════════════════════════════════════

class InferenceWorker(QObject):
    progress = pyqtSignal(int, str)    # (0-100, current_filename)
    finished = pyqtSignal(int, float)  # (count, elapsed_sec)
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
        """
        替换此处为真实推理逻辑，示例：
            from realesrgan import RealESRGANer
            upsampler = RealESRGANer(...)
            for i, f in enumerate(self._files):
                output, _ = upsampler.enhance(cv2.imread(f), outscale=4)
                cv2.imwrite(out_path, output)
                self.progress.emit(int((i+1)/len(self._files)*100), Path(f).name)
        """
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
#  文件列表项  —  高度固定 42px，宽度随父级伸缩
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
        # 高度固定，宽度 Expanding（跟随父级）
        self.setFixedHeight(42)
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Fixed)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 0, 8, 0)
        lay.setSpacing(8)

        ico = IconWidget(FIF.PHOTO, self)
        ico.setFixedSize(15, 15)
        lay.addWidget(ico)

        self._name_lbl = BodyLabel(Path(path).name, self)
        # 不 setFixedWidth，让文字随空间自然截断
        self._name_lbl.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        lay.addWidget(self._name_lbl, 1)

        size_kb = os.path.getsize(path) // 1024 if os.path.exists(path) else 0
        self._size_lbl = CaptionLabel(f"{size_kb} KB", self)
        self._size_lbl.setStyleSheet("color: rgba(128,128,128,180);")
        self._size_lbl.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        lay.addWidget(self._size_lbl)

        self._status_lbl = _badge("等待", "#888888")
        lay.addWidget(self._status_lbl)

        del_btn = TransparentToolButton(FIF.CLOSE, self)
        del_btn.setFixedSize(22, 22)
        del_btn.clicked.connect(lambda: self.removed.emit(self.path))
        lay.addWidget(del_btn)

    def set_status(self, s: str):
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
#  拖拽上传区  —  无最小/最大尺寸限制，完全由父级撑开
# ══════════════════════════════════════════════════════════════════════

class DropZone(QWidget):
    files_dropped = pyqtSignal(list)
    clicked_open = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        # 只设 Expanding 策略，不写死任何最小高度
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Preferred)

        lay = QVBoxLayout(self)
        lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.setContentsMargins(16, 20, 16, 20)
        lay.setSpacing(6)

        ico = IconWidget(FIF.PHOTO, self)
        ico.setFixedSize(32, 32)
        lay.addWidget(ico, 0, Qt.AlignmentFlag.AlignCenter)

        title = StrongBodyLabel("拖拽图片到此处，或点击选择", self)
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(title)

        sub = CaptionLabel("支持 PNG · JPG · WEBP · BMP，可批量导入", self)
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sub.setStyleSheet("color: rgba(128,128,128,180);")
        lay.addWidget(sub)

        self._set_style(False)

    def _set_style(self, hover: bool):
        bc = ACCENT if hover else "rgba(128,128,128,80)"
        bg = "rgba(0,120,212,8)" if hover else "rgba(128,128,128,6)"
        self.setStyleSheet(
            f"DropZone{{border:1.5px dashed {bc};border-radius:10px;background:{bg};}}")

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
#  右侧参数面板  —  宽度用 stretch factor 控制，不 setFixedWidth
# ══════════════════════════════════════════════════════════════════════

class ParamPanel(QWidget):
    """纯 QWidget 容器（无固定宽度），内部用 SmoothScrollArea 处理溢出。"""

    def __init__(self, parent=None, device_options: dict = {}):
        super().__init__(parent)
        # Preferred 宽，高 Expanding；宽度由外层 layout 的 stretch 决定
        self.setSizePolicy(QSizePolicy.Policy.Preferred,
                           QSizePolicy.Policy.Expanding)

        # 外层只放一个滚动区
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

        self._on_model_changed(self.model_combo.currentText())
        lay.addWidget(_separator())

        # ── 推理设备 ──────────────────────────────────────────────────
        lay.addWidget(_section_title("推理设备", FIF.SPEED_HIGH, container))
        self.device_combo = ComboBox(container)
        devices = []
        for drivername, driverindex in device_options.items():
            devices.append(f"{drivername} · {driverindex}")
        self.device_combo.addItems(devices)
        self.device_combo.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        lay.addWidget(self.device_combo)
        lay.addWidget(_separator())

        # ── 推理参数 ──────────────────────────────────────────────────
        lay.addWidget(_section_title("推理参数", FIF.SETTING, container))

        self._add_caption(lay, "放大倍数", container)
        self.scale_combo = ComboBox(container)
        self.scale_combo.addItems(["4× (默认)", "2×", "1× (去噪/修复)"])
        self.scale_combo.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        lay.addWidget(self.scale_combo)

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

        # 人脸增强强度（隐藏）
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

    # ── 辅助构建方法 ──────────────────────────────────────────────────

    def _add_caption(self, lay, text: str, parent):
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
        val_lbl.setFixedWidth(34)   # 仅值标签固定宽度，其余自适应
        val_lbl.setAlignment(Qt.AlignmentFlag.AlignRight |
                             Qt.AlignmentFlag.AlignVCenter)
        slider.valueChanged.connect(lambda v: val_lbl.setText(str(v)))
        return slider, val_lbl

    def _add_slider(self, lay, label: str, lo, hi, val, step, parent):
        self._add_caption(lay, label, parent)
        slider, val_lbl = self._make_slider_row(lo, hi, val, step, parent)
        row = QHBoxLayout()
        row.addWidget(slider)
        row.addWidget(val_lbl)
        lay.addLayout(row)
        return slider, val_lbl

    def _add_toggle(self, lay, label: str, default: bool, parent) -> SwitchButton:
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
        desc, scale, size = MODEL_INFO.get(name, ("", "—", "—"))
        self._model_info_lbl.setText(desc)
        self._scale_badge.setText(scale)
        self._size_badge.setText(size)

    def _on_face_toggle(self, checked: bool):
        self._face_str_widget.setVisible(checked)

    def _browse_output(self):
        d = QFileDialog.getExistingDirectory(self, "选择输出目录", "./")
        if d:
            self.out_dir_edit.setText(d)

    def get_params(self) -> dict:
        return {
            "model":       self.model_combo.currentText(),
            "device":      self.device_combo.currentText(),
            "scale":       self.scale_combo.currentText(),
            "tile":        self.tile_slider.value(),
            "tile_pad":    self.tpad_slider.value(),
            "pre_pad":     self.ppad_slider.value(),
            "fp16":        self.fp16_switch.isChecked(),
            "face_enh":    self.face_switch.isChecked(),
            "face_str":    self.face_slider.value() / 100.0,
            "out_dir":     self.out_dir_edit.text(),
            "out_fmt":     self.fmt_combo.currentText(),
            "keep_suffix": self.suffix_switch.isChecked(),
        }


# ══════════════════════════════════════════════════════════════════════
#  推理主页面  —  完全自适应，不设任何固定/最小尺寸
# ══════════════════════════════════════════════════════════════════════

class InferencePage(QWidget):
    """
    可作为独立窗口，也可嵌入任意父级（FluentWindow / QStackedWidget 等）。
    大小完全由父级控制，内部使用 SmoothScrollArea 处理内容溢出。
    """

    def __init__(self, parent=None, device_options: dict = {}):
        super().__init__(parent)
        self.device_options = device_options
        self.setObjectName("InferencePage")
        # 允许向任意方向伸缩，不设最小尺寸
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Expanding)

        self._files: list[str] = []
        self._file_items: dict[str, FileListItem] = {}
        self._worker: InferenceWorker | None = None
        self._thread: QThread | None = None
        self._running = False

        self._build_ui()

    # ── UI 构建 ───────────────────────────────────────────────────────

    def _build_ui(self):
        # 最外层：垂直布局，上方 topbar 固定，下方滚动区 Expanding
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ─ Topbar（不随滚动移动） ─────────────────────────────────────
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
        # 不 setFixedWidth，让按钮自然适配文字宽度
        self._run_btn.clicked.connect(self._toggle_run)
        tb_lay.addWidget(self._run_btn)

        root.addWidget(topbar)

        # 分隔线
        root.addWidget(_separator())

        # ─ 可滚动主体 ─────────────────────────────────────────────────
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

        # 主体：左内容 + 右参数（stretch 比 3:1）
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

        # 文件列表滚动区（最大高度 180，无最小高度）
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

        # 文件操作行
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

        # ── 预览对比 ──────────────────────────────────────────────────
        prev_card = ElevatedCardWidget(body_widget)
        prev_card.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        pv_lay = QVBoxLayout(prev_card)
        pv_lay.setContentsMargins(18, 14, 18, 14)
        pv_lay.setSpacing(10)
        pv_lay.addWidget(_section_title("预览对比", FIF.VIEW, prev_card))

        prev_imgs = QHBoxLayout()
        prev_imgs.setSpacing(10)
        for side, size_hint in (("原图", "256 × 256"), ("超分结果", "1024 × 1024  (4×)")):
            box = QFrame(prev_card)
            box.setSizePolicy(QSizePolicy.Policy.Expanding,
                              QSizePolicy.Policy.Preferred)
            box.setStyleSheet("""
                QFrame{background:rgba(128,128,128,12);
                       border:1px solid rgba(128,128,128,35);border-radius:8px;}
            """)
            bl = QVBoxLayout(box)
            bl.setContentsMargins(8, 20, 8, 20)
            bl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            bl.setSpacing(5)
            ico = IconWidget(FIF.PHOTO, box)
            ico.setFixedSize(28, 28)
            bl.addWidget(ico, 0, Qt.AlignmentFlag.AlignCenter)
            lbl = StrongBodyLabel(side, box)
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            bl.addWidget(lbl)
            sub = CaptionLabel(size_hint, box)
            sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
            sub.setStyleSheet("color:rgba(128,128,128,180);")
            bl.addWidget(sub)
            prev_imgs.addWidget(box)
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

        # 四宫格
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

        # 进度框
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

        # 日志
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
        body_lay.addLayout(left_lay, 3)   # 左:右 = 3:1

        # ── 右侧参数面板 ─────────────────────────────────────────────
        self._param_panel = ParamPanel(body_widget, self.device_options)
        body_lay.addWidget(self._param_panel, 1)

    # ── 文件操作 ───────────────────────────────────────────────────────

    def _browse_files(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "选择图像文件", "",
            "图像文件 (*.png *.jpg *.jpeg *.webp *.bmp);;所有文件 (*)")
        if paths:
            self._add_files(paths)

    def _add_files(self, paths: list[str]):
        for p in paths:
            if p in self._files:
                continue
            self._files.append(p)
            item = FileListItem(p, self._file_container)
            item.removed.connect(self._remove_file)
            self._file_list_lay.addWidget(item)
            self._file_items[p] = item
        self._refresh_file_ui()

    def _remove_file(self, path: str):
        if path in self._file_items:
            self._file_items[path].setParent(None)
            del self._file_items[path]
        if path in self._files:
            self._files.remove(path)
        self._refresh_file_ui()

    def _clear_files(self):
        for item in self._file_items.values():
            item.setParent(None)
        self._file_items.clear()
        self._files.clear()
        self._refresh_file_ui()

    def _refresh_file_ui(self):
        n = len(self._files)
        self._file_count_lbl.setText(f"已选 {n} 个文件")
        self._clear_btn.setEnabled(n > 0)
        self._file_scroll.setVisible(n > 0)
        self._drop_zone.setVisible(n == 0)

    # ── 推理控制 ───────────────────────────────────────────────────────

    def _toggle_run(self):
        if self._running:
            self._abort_run()
        else:
            self._start_run()

    def _start_run(self):
        if not self._files:
            InfoBar.warning(title="未选择文件", content="请先添加要处理的图像文件",
                            parent=self, position=InfoBarPosition.TOP_RIGHT, duration=3000)
            return
        self._running = True
        self._run_btn.setText("停止推理")
        self._run_btn.setIcon(FIF.PAUSE)
        self._param_panel.setEnabled(False)

        params = self._param_panel.get_params()
        self._stat_widgets["processed"].setText(f"0 / {len(self._files)}")
        self._stat_widgets["device"].setText(
            "GPU" if "GPU" in params["device"] else "CPU")
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
                        parent=self, position=InfoBarPosition.TOP_RIGHT, duration=4000)
        if self._thread:
            self._thread.quit()

    def _on_error(self, msg: str):
        self._abort_run()
        InfoBar.error(title="推理出错", content=msg,
                      parent=self, position=InfoBarPosition.TOP_RIGHT, duration=5000)

    def _append_log(self, line: str):
        old = self._log_area.text()
        lines = old.split("\n") if old != "等待开始…" else []
        lines.append(line)
        self._log_area.setText("\n".join(lines[-6:]))

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
