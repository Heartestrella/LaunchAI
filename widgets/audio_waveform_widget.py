
import sys
import os
import math
import time
import numpy as np
from pathlib import Path

# os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import re as _icon_re

from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout,
    QSizePolicy, QFileDialog, QLabel
)
from PyQt6.QtCore import (
    Qt, QTimer, QThread, QObject, QRectF, QPointF, QByteArray, pyqtSignal
)
from PyQt6.QtGui import (
    QPainter, QColor, QPen, QBrush, QLinearGradient,
    QCursor, QMouseEvent, QIcon, QPixmap
)
from PyQt6.QtSvg import QSvgRenderer

from qfluentwidgets import (
    setTheme, Theme, setThemeColor, isDarkTheme,
    CardWidget, TitleLabel, CaptionLabel,
    PrimaryPushButton, PushButton, ToggleButton,
    TransparentToolButton, FluentIcon,
    InfoBar, InfoBarPosition
)

from utils.atool import resource_path


def _resource_svg_icon(rel_path: str, color: str = "#EAEAEA",
                       size: int = 20) -> QIcon:
    """从 resource/icons 下加载 SVG,把 fill 改成 `color` 再渲染成 QIcon。
    原始 SVG 里 fill=#444 在深色主题下几乎看不见,所以必须二次染色。
    任何读取 / 解析失败返回空 QIcon,不让 UI 链路崩。
    """
    try:
        with open(resource_path(rel_path), "r", encoding="utf-8") as f:
            svg = f.read()
    except OSError:
        return QIcon()
    svg = _icon_re.sub(r'fill="#[0-9A-Fa-f]{3,6}"', f'fill="{color}"', svg)
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    pix = QPixmap(size, size)
    pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix)
    renderer.render(painter)
    painter.end()
    return QIcon(pix)

try:
    import sounddevice as sd
    HAS_SD = True
except Exception:
    HAS_SD = False
    sd = None


# ─── colour helpers ───────────────────────────────────────────────────────────
def _c(hex_: str, a: int = 255) -> QColor:
    c = QColor(hex_)
    c.setAlpha(a)
    return c


PLAYED_DARK = _c("#0078D4")
PLAYED_LIGHT = _c("#0067B8")
UNPLAYED_DARK = _c("#AAAAAA", 140)
UNPLAYED_LIGHT = _c("#555555", 120)
HEAD_DARK = _c("#60CDFF")
HEAD_LIGHT = _c("#0078D4")
MIC_LIVE_COL = _c("#60CDFF")


def _resample(arr: np.ndarray, n: int) -> np.ndarray:
    if len(arr) == 0:
        return np.zeros(n)
    idx = np.linspace(0, len(arr) - 1, n)
    return np.interp(idx, np.arange(len(arr)), arr)


# ─── mic worker ──────────────────────────────────────────────────────────────
class MicWorker(QObject):
    chunk = pyqtSignal(np.ndarray)
    error = pyqtSignal(str)

    def __init__(self): super().__init__(); self._run = False

    def start(self):
        self._run = True
        if not HAS_SD:
            self.error.emit("sounddevice not installed")
            return
        try:
            def cb(indata, frames, t, st):
                if self._run:
                    self.chunk.emit(indata[:, 0].copy())
            self._s = sd.InputStream(samplerate=44100, channels=1,
                                     blocksize=1024, callback=cb, dtype='float32')
            self._s.start()
        except Exception as e:
            self.error.emit(str(e))

    def stop(self):
        self._run = False
        try:
            self._s.stop()
            self._s.close()
        except:
            pass


# ─── waveform canvas ─────────────────────────────────────────────────────────
class WaveformCanvas(QWidget):
    """
    Scrolling-playhead waveform display.

    The playhead is fixed at the horizontal centre.
    The waveform buffer scrolls left as playback advances.

    Optional constructor parameters
    --------------------------------
    bar_width : float | None
        Fixed pixel width per bar (None = auto).
    bar_color : QColor | str | None
        Colour of unplayed (right-side) bars. None = theme default.
    """

    # emits new 0-1 progress fraction when user drag-seeks
    seeked = pyqtSignal(float)
    seek_started = pyqtSignal()
    seek_finished = pyqtSignal()

    BAR_GAP = 0.55   # gap fraction of slot
    MIN_H_FRAC = 0.04
    CAP_RADIUS = 0.5
    HEAD_W = 2.0    # playhead line width px

    def __init__(self, parent=None, *, bar_width=None, bar_color=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Expanding)
        self.setMinimumHeight(80)
        self.setCursor(QCursor(Qt.CursorShape.SizeHorCursor))

        self._fixed_bw: float | None = float(
            bar_width) if bar_width is not None else None
        self._custom_unplayed: QColor | None = (
            QColor(bar_color) if isinstance(bar_color, str)
            else (QColor(bar_color) if bar_color is not None else None)
        )

        # ── source data ────────────────────────────────────────────────────
        # full per-sample amplitude overview of the loaded file
        self._overview:  np.ndarray = np.array([])   # raw, length varies
        self._progress:  float = 0.0            # 0-1 playback position
        self._mode:      str = 'idle'         # idle|file|mic

        # mic rolling buffer (RMS values appended each chunk)
        self._mic_buf: list[float] = []

        # idle animation
        self._phase: float = 0.0

        # drag state
        self._drag_start_x:   float | None = None
        self._drag_start_prog: float = 0.0

        # animation ticker
        t = QTimer(self)
        t.timeout.connect(self._tick)
        t.start(16)

    # ── helpers ────────────────────────────────────────────────────────────────
    def _bar_count(self) -> int:
        """How many bars fit in current widget width."""
        w = max(1, self.width())
        if self._fixed_bw is not None:
            slot = self._fixed_bw / (1.0 - self.BAR_GAP)
            # ensure odd so centre bar exists
            return max(8, int(w / slot) | 1)
        else:
            # aim for ~3px bar with BAR_GAP
            target_slot = 5.5
            return max(8, int(w / target_slot) | 1)

    def _bar_dims(self) -> tuple[float, float]:
        """Returns (bar_width_px, slot_width_px)."""
        w = max(1, self.width())
        n = self._bar_count()
        if self._fixed_bw is not None:
            bw = self._fixed_bw
            slot = bw / (1.0 - self.BAR_GAP)
        else:
            slot = w / n
            bw = slot * (1.0 - self.BAR_GAP)
        return bw, slot

    def _amps_for_display(self) -> np.ndarray:
        """
        Return amplitude array sized exactly to _bar_count(), centred on
        the current playhead position so scrolling happens naturally.
        """
        n = self._bar_count()
        half = n // 2          # bars to the left / right of the head

        if self._mode == 'idle':
            phase = self._phase
            t = np.linspace(0, 2 * math.pi, n)
            return (0.10 + 0.08 * np.sin(t + phase)
                         + 0.04 * np.sin(2 * t + phase * 1.3))

        if self._mode == 'file':
            src = self._overview
            if len(src) == 0:
                return np.full(n, 0.1)
            # map progress to index in src
            total = len(src)
            centre_idx = int(self._progress * (total - 1))
            lo = centre_idx - half
            hi = centre_idx + half + (n % 2)   # include centre bar
            # pad with zeros outside valid range
            result = np.zeros(n)
            src_start = max(0, lo)
            src_end = min(total, hi)
            dst_start = src_start - lo
            dst_end = dst_start + (src_end - src_start)
            result[dst_start:dst_end] = src[src_start:src_end]
            return result

        if self._mode == 'mic':
            src = np.array(
                self._mic_buf, dtype=float) if self._mic_buf else np.array([0.1])
            total = len(src)
            # playhead sits at the recording frontier (right edge of recorded)
            centre_idx = total - 1
            lo = centre_idx - half
            hi = centre_idx + half + (n % 2)
            result = np.zeros(n)
            src_start = max(0, lo)
            src_end = min(total, hi)
            dst_start = src_start - lo
            dst_end = dst_start + (src_end - src_start)
            result[dst_start:dst_end] = src[src_start:src_end]
            return result

        return np.full(n, 0.1)

    # ── public API ─────────────────────────────────────────────────────────────
    def load_overview(self, amps: np.ndarray):
        """Load a full-file amplitude overview (any length)."""
        mx = amps.max() if len(amps) else 1.0
        self._overview = np.abs(amps) / (mx + 1e-9)
        self._progress = 0.0
        self._mode = 'file'
        self.update()

    def set_progress(self, p: float):
        self._progress = max(0.0, min(1.0, p))
        self.update()

    def push_mic_chunk(self, samples: np.ndarray):
        rms = float(np.sqrt(np.mean(samples ** 2 + 1e-9)))
        self._mic_buf.append(min(1.0, rms * 6))
        self._mode = 'mic'
        self.update()

    def reset_mic(self):
        self._mic_buf.clear()
        self._mode = 'idle'
        self._progress = 0.0
        self.update()

    def set_idle(self):
        self._mode = 'idle'
        self._progress = 0.0
        self.update()

    # ── animation ──────────────────────────────────────────────────────────────
    def _tick(self):
        if self._mode == 'idle':
            self._phase += 0.032
            self.update()

    # ── resize: no stored per-bar data, recalculates on next paint ─────────────
    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.update()

    # ── drag to seek ───────────────────────────────────────────────────────────
    def mousePressEvent(self, e: QMouseEvent):
        if self._mode == 'file' and e.button() == Qt.MouseButton.LeftButton:
            self._drag_start_x = e.position().x()
            self._drag_start_prog = self._progress
            self.seek_started.emit()

    def mouseMoveEvent(self, e: QMouseEvent):
        if self._mode == 'file' and self._drag_start_x is not None:
            n = self._bar_count()
            _, slot = self._bar_dims()
            # total source samples mapped to widget width
            src_len = max(1, len(self._overview))
            # pixels per source sample
            px_per_sample = (self.width()) / src_len if src_len > 1 else 1.0
            # but we can also think in terms of: dragging one bar = 1/n of visible
            # Better: compute how many source samples one pixel represents
            # visible bars span ≈ n bars; each bar = 1 source sample (after overview resampling)
            # So 1 pixel drag ≈ slot pixels → 1 bar → 1/src_len fraction
            dx = e.position().x() - self._drag_start_x
            # drag left = forward, drag right = backward
            frac_per_px = 1.0 / (n * slot) if slot > 0 else 0.0
            new_prog = self._drag_start_prog - dx * frac_per_px
            new_prog = max(0.0, min(1.0, new_prog))
            self._progress = new_prog
            self.seeked.emit(new_prog)
            self.update()

    def mouseReleaseEvent(self, e: QMouseEvent):
        self._drag_start_x = None
        self.seek_finished.emit()

    # ── paint ──────────────────────────────────────────────────────────────────
    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        w, h = self.width(), self.height()
        dark = isDarkTheme()
        played_c = PLAYED_DARK if dark else PLAYED_LIGHT
        unplayed_c = self._custom_unplayed or (
            UNPLAYED_DARK if dark else UNPLAYED_LIGHT)
        head_c = HEAD_DARK if dark else HEAD_LIGHT

        p.fillRect(0, 0, w, h, Qt.GlobalColor.transparent)

        amps = self._amps_for_display()
        n = len(amps)
        bw, slot = self._bar_dims()
        r = bw * self.CAP_RADIUS
        mid = h / 2.0

        # total pixel span; centre it
        total_span = n * slot - (slot - bw)
        x0 = (w - total_span) / 2.0   # left edge of first bar
        head_x = w / 2.0              # playhead at widget centre
        half = n // 2

        for i in range(n):
            amp = max(self.MIN_H_FRAC, float(amps[i]))
            bh = amp * h * 0.86
            bx = x0 + i * slot
            by = mid - bh / 2.0

            # bars to the left of centre = played; right = unplayed
            if self._mode in ('file', 'mic'):
                color = played_c if i <= half else unplayed_c
            else:
                color = unplayed_c

            p.setBrush(QBrush(color))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawRoundedRect(QRectF(bx, by, bw, bh), r, r)

        # ── playhead ────────────────────────────────────────────────────────
        if self._mode in ('file', 'mic'):
            # vertical line
            pen = QPen(head_c, self.HEAD_W)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            p.drawLine(QPointF(head_x, 6), QPointF(head_x, h - 6))

            # knob circle
            knob_r = 5.0
            p.setBrush(QBrush(head_c))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(QRectF(head_x - knob_r, mid - knob_r,
                                 knob_r * 2, knob_r * 2))

        # ── mic live dot (recording frontier) ──────────────────────────────
        if self._mode == 'mic':
            dot_c = QColor(MIC_LIVE_COL)
            p.setBrush(QBrush(dot_c))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(QRectF(head_x - 4, mid - 4, 8, 8))

        # ── fade edges (subtle vignette so bars fade into card bg) ─────────
        for side in ('left', 'right'):
            grad = QLinearGradient()
            bg = QColor("#2b2b2b") if dark else QColor("#f9f9f9")
            transparent = QColor(bg)
            transparent.setAlpha(0)
            fade_w = min(40, w * 0.08)
            if side == 'left':
                grad.setStart(0, 0)
                grad.setFinalStop(fade_w, 0)
                grad.setColorAt(0, bg)
                grad.setColorAt(1, transparent)
            else:
                grad.setStart(w - fade_w, 0)
                grad.setFinalStop(w, 0)
                grad.setColorAt(0, transparent)
                grad.setColorAt(1, bg)
            p.fillRect(
                QRectF(0 if side == 'left' else w - fade_w, 0, fade_w, h),
                QBrush(grad)
            )

        p.end()


# ─── main window ─────────────────────────────────────────────────────────────
class AudioWaveformWidget(QWidget):

    def __init__(self):
        super().__init__()
        setTheme(Theme.DARK)
        setThemeColor("#0078D4")

        self._file_data:  np.ndarray | None = None
        self._file_sr:    int = 44100
        self._file_pos:   int = 0
        self._playing:    bool = False
        self._mic_on:     bool = False
        self._mic_worker: MicWorker | None = None
        self._mic_thread: QThread | None = None
        self._stream:     object | None = None
        self._CHUNK = 2048
        self._file_root_dir: str | None = None
        self._seek_dragging: bool = False
        self._paused_for_seek: bool = False

        self._build_ui()

        self._play_timer = QTimer(self)
        self._play_timer.timeout.connect(self._play_tick)

    # ── UI ────────────────────────────────────────────────────────────────────
    def _build_ui(self):
        self.setWindowTitle("AudioWaveformWidget — Fluent Design")
        self.setMinimumSize(600, 280)
        self.resize(900, 340)

        root = QVBoxLayout(self)
        root.setContentsMargins(28, 20, 28, 20)
        root.setSpacing(14)

        # header — 包装成独立 QWidget 便于 set_embedded_mode 整体隐藏
        self._header_widget = QWidget(self)
        hdr = QHBoxLayout(self._header_widget)
        hdr.setContentsMargins(0, 0, 0, 0)
        title = TitleLabel("Audio Waveform")
        title.setStyleSheet("font-size: 20px; font-weight: 600;")
        self._status = CaptionLabel("Idle")
        self._status.setStyleSheet(
            "color: rgba(255,255,255,0.45); font-size:12px;")
        theme_btn = TransparentToolButton(FluentIcon.CONSTRACT, self)
        theme_btn.setToolTip("Toggle theme")
        theme_btn.clicked.connect(self._toggle_theme)
        hdr.addWidget(title)
        hdr.addSpacing(12)
        hdr.addWidget(self._status, 0, Qt.AlignmentFlag.AlignVCenter)
        hdr.addStretch()
        hdr.addWidget(theme_btn)
        root.addWidget(self._header_widget)

        # waveform card
        wave_card = CardWidget(self)
        wave_card.setMinimumHeight(110)
        wl = QVBoxLayout(wave_card)
        wl.setContentsMargins(0, 10, 0, 10)

        time_row = QHBoxLayout()
        time_row.setContentsMargins(16, 0, 16, 0)
        self._cur_time = CaptionLabel("0:00")
        self._tot_time = CaptionLabel("0:00")
        for lbl in (self._cur_time, self._tot_time):
            lbl.setStyleSheet("color: rgba(255,255,255,0.45); font-size:11px;")
        time_row.addWidget(self._cur_time)
        time_row.addStretch()
        time_row.addWidget(self._tot_time)
        wl.addLayout(time_row)

        # canvas — bar_width=None → auto-fit; tweak here as needed
        self._canvas = WaveformCanvas(wave_card, bar_width=None)
        self._canvas.seeked.connect(self._on_seek)
        self._canvas.seek_started.connect(self._on_seek_press)
        self._canvas.seek_finished.connect(self._on_seek_release)
        wl.addWidget(self._canvas)

        root.addWidget(wave_card)

        # controls card
        ctrl_card = CardWidget(self)
        cl = QHBoxLayout(ctrl_card)
        cl.setContentsMargins(20, 12, 20, 12)
        cl.setSpacing(10)

        self._open_btn = PrimaryPushButton("Open File", self)
        self._open_btn.setIcon(FluentIcon.FOLDER)
        self._open_btn.setMinimumWidth(120)
        self._open_btn.clicked.connect(self._open_file)

        # 矢量图标:暂停 / 停止 走 resource/icons 下的本地 SVG(项目自带),
        # 播放暂时回落到 FluentIcon.PLAY —— resource/icons 里目前没有 play.svg,
        # 等用户补一个 play.svg 后再切。颜色按状态语义着色,深色主题下可见。
        self._icon_play = FluentIcon.PLAY.icon()
        self._icon_pause = _resource_svg_icon(
            "resource/icons/pause.svg", color="#EAEAEA", size=20)
        self._icon_stop = _resource_svg_icon(
            "resource/icons/stop.svg", color="#E55454", size=20)

        self._play_btn = PushButton(self._icon_play, "Play")
        self._play_btn.setMinimumWidth(90)
        self._play_btn.setEnabled(False)
        self._play_btn.clicked.connect(self._toggle_play)

        self._stop_btn = PushButton(self._icon_stop, "Stop")
        self._stop_btn.setMinimumWidth(90)
        self._stop_btn.setEnabled(False)
        self._stop_btn.clicked.connect(self._stop)

        self._sep_label = QLabel("│")
        self._sep_label.setStyleSheet(
            "color: rgba(255,255,255,0.15); font-size:22px;")

        self._mic_btn = ToggleButton("  Record", self)
        self._mic_btn.setIcon(FluentIcon.MICROPHONE)
        self._mic_btn.setMinimumWidth(110)
        self._mic_btn.clicked.connect(self._toggle_mic)

        self._file_label = CaptionLabel("No file loaded")
        self._file_label.setStyleSheet(
            "color:rgba(255,255,255,0.45); font-size:11px;")

        cl.addWidget(self._open_btn)
        cl.addWidget(self._play_btn)
        cl.addWidget(self._stop_btn)
        cl.addWidget(self._sep_label)
        cl.addWidget(self._mic_btn)
        cl.addSpacing(12)
        cl.addWidget(self._file_label)
        cl.addStretch()

        root.addWidget(ctrl_card)

    # ── helpers ───────────────────────────────────────────────────────────────
    def _build_overview(self, mono: np.ndarray, n_bars: int) -> np.ndarray:
        """Max-pool mono audio into n_bars amplitude values, normalised 0-1."""
        seg = max(1, len(mono) // n_bars)
        result = np.array([
            np.max(np.abs(mono[i * seg:(i + 1) * seg]))
            for i in range(n_bars)
        ], dtype=float)
        mx = result.max()
        if mx > 0:
            result /= mx
        return result

    def _overview_resolution(self) -> int:
        """How many samples to keep in the overview (= visual resolution)."""
        # 4× the visible bar count gives smooth scrolling with room to zoom
        return max(400, self._canvas._bar_count() * 4)

    def set_file_root_dir(self, folder: str):
        self._file_root_dir = folder

    def disable_mic_recording(self):
        """禁用录音功能"""
        self._mic_btn.setEnabled(False)

    # ── file I/O ──────────────────────────────────────────────────────────────
    def _open_file(self):
        start_dir = self._file_root_dir or ""
        path, _ = QFileDialog.getOpenFileName(
            self, "Open Audio File", start_dir,
            "Audio Files (*.wav *.flac *.mp3 *.ogg *.aiff *.m4a);;All Files (*)")
        if not path:
            return
        self.load_file(path)

    def load_file(self, path: str):
        """公开 API 程序化加载音频文件路径 不弹文件选择对话框

        供 SubpageMaterials 等外部页面在预览流程中复用此波形组件。
        失败通过 InfoBar 报错 不会抛异常出去。
        """
        if not path:
            return
        self._stop()
        try:
            import soundfile as sf
            data, sr = sf.read(path, dtype='float32', always_2d=True)
            mono = data.mean(axis=1)
            self._file_data = mono
            self._file_sr = sr
            self._file_pos = 0

            name = Path(path).name
            dur = len(mono) / sr
            self._file_label.setText(
                f"{name}  ·  {dur:.1f}s  ·  {sr//1000}kHz")
            self._tot_time.setText(f"{int(dur//60)}:{int(dur%60):02d}")
            self._play_btn.setEnabled(True)
            self._stop_btn.setEnabled(True)

            res = self._overview_resolution()
            overview = self._build_overview(mono, res)
            self._canvas.load_overview(overview)
            self._status.setText(f"Loaded  ·  {name}")
            InfoBar.success(title="File loaded", content=name, parent=self,
                            position=InfoBarPosition.TOP_RIGHT, duration=2500)
        except Exception as e:
            InfoBar.error(title="Load error", content=str(e), parent=self,
                          position=InfoBarPosition.TOP_RIGHT, duration=4000)

    def set_embedded_mode(self, embedded: bool):
        """嵌入模式 隐藏 Header / Open / Record 等冗余组件 留出空间给宿主页面

        - 隐藏: header_widget(标题 + 状态 + 主题按钮) / open_btn / mic_btn / 分隔线
        - 保留: 波形 / 播放 / 停止 / 当前文件名 caption
        - 同时把 contentsMargins 收小 紧贴宿主页面卡片边缘
        """
        for w in (self._header_widget, self._open_btn,
                  self._mic_btn, self._sep_label):
            w.setVisible(not embedded)
        outer = self.layout()
        if outer is not None:
            if embedded:
                outer.setContentsMargins(0, 0, 0, 0)
                outer.setSpacing(8)
            else:
                outer.setContentsMargins(28, 20, 28, 20)
                outer.setSpacing(14)

    # ── playback ──────────────────────────────────────────────────────────────
    def _toggle_play(self):
        if self._playing:
            self._pause()
        else:
            self._play()

    def _play(self):
        if self._file_data is None:
            return
        self._stop_mic()
        self._close_stream()
        self._playing = True
        self._play_btn.setIcon(self._icon_pause)
        self._play_btn.setText("Pause")
        self._status.setText("Playing")
        if HAS_SD:
            self._stream = sd.OutputStream(
                samplerate=self._file_sr, channels=1,
                callback=self._audio_cb, blocksize=self._CHUNK)
            self._stream.start()
        interval = max(16, int(1000 * self._CHUNK / self._file_sr))
        self._play_timer.start(interval)

    def _pause(self):
        self._playing = False
        self._play_timer.stop()
        if self._stream:
            try:
                self._stream.stop()
            except:
                pass
        self._play_btn.setIcon(self._icon_play)
        self._play_btn.setText("Play")
        self._status.setText("Paused")

    def _stop(self):
        self._pause()
        self._close_stream()
        self._file_pos = 0
        self._canvas.set_progress(0.0)
        self._cur_time.setText("0:00")
        if self._file_data is not None:
            res = self._overview_resolution()
            self._canvas.load_overview(
                self._build_overview(self._file_data, res))
        self._status.setText("Stopped")

    def _close_stream(self):
        if self._stream:
            try:
                self._stream.stop()
                self._stream.close()
            except:
                pass
            self._stream = None

    def _play_tick(self):
        if not self._playing or self._file_data is None:
            return
        prog = self._file_pos / len(self._file_data)
        self._canvas.set_progress(prog)
        cur = self._file_pos / self._file_sr
        self._cur_time.setText(f"{int(cur//60)}:{int(cur%60):02d}")
        if self._file_pos >= len(self._file_data):
            self._stop()

    def _audio_cb(self, outdata, frames, t, status):
        end = self._file_pos + frames
        if end >= len(self._file_data):
            rem = len(self._file_data) - self._file_pos
            outdata[:rem,   0] = self._file_data[self._file_pos:]
            outdata[rem:,   0] = 0.0
            self._file_pos = len(self._file_data)
            raise sd.CallbackStop()
        outdata[:, 0] = self._file_data[self._file_pos:end]
        self._file_pos = end

    def _on_seek(self, frac: float):
        if self._file_data is None:
            return
        self._file_pos = int(frac * len(self._file_data))
        self._canvas.set_progress(frac)
        cur = self._file_pos / self._file_sr
        self._cur_time.setText(f"{int(cur//60)}:{int(cur%60):02d}")
        if self._playing and not self._seek_dragging:
            self._close_stream()
            self._stream = sd.OutputStream(
                samplerate=self._file_sr, channels=1,
                callback=self._audio_cb, blocksize=self._CHUNK)
            self._stream.start()

    def _on_seek_press(self):
        self._seek_dragging = True
        if self._playing:
            self._paused_for_seek = True
            self._pause()

    def _on_seek_release(self):
        if not self._seek_dragging:
            return
        self._seek_dragging = False
        if self._paused_for_seek:
            self._paused_for_seek = False
            self._play()

    # ── mic ───────────────────────────────────────────────────────────────────
    def _toggle_mic(self):
        if self._mic_on:
            self._stop_mic()
        else:
            self._start_mic()

    def _start_mic(self):
        self._stop()
        self._canvas.reset_mic()
        self._mic_worker = MicWorker()
        self._mic_thread = QThread(self)
        self._mic_worker.moveToThread(self._mic_thread)
        self._mic_worker.chunk.connect(self._canvas.push_mic_chunk)
        self._mic_worker.error.connect(self._on_mic_err)
        self._mic_thread.started.connect(self._mic_worker.start)
        self._mic_thread.start()
        self._mic_on = True
        self._mic_btn.setText("  Stop")
        self._mic_btn.setChecked(True)
        self._status.setText("🔴  Recording…")

    def _stop_mic(self):
        if not self._mic_on:
            return
        if self._mic_worker:
            self._mic_worker.stop()
        if self._mic_thread:
            self._mic_thread.quit()
            self._mic_thread.wait(1000)
        self._mic_worker = self._mic_thread = None
        self._mic_on = False
        self._mic_btn.setText("  Record")
        self._mic_btn.setChecked(False)
        self._status.setText("Recording stopped")

    def _on_mic_err(self, msg: str):
        self._stop_mic()
        InfoBar.error(title="Mic error", content=msg, parent=self,
                      position=InfoBarPosition.TOP_RIGHT, duration=5000)

    # ── resize: rebuild overview to match new bar count ───────────────────────
    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._file_data is not None and self._canvas._mode == 'file':
            res = self._overview_resolution()
            overview = self._build_overview(self._file_data, res)
            # reload without resetting progress
            mx = overview.max() if len(overview) else 1.0
            self._canvas._overview = overview / (mx + 1e-9)
            self._canvas.update()

    # ── theme ─────────────────────────────────────────────────────────────────
    def _toggle_theme(self):
        dark = isDarkTheme()
        setTheme(Theme.LIGHT if dark else Theme.DARK)
        dim = "rgba(0,0,0,0.45)" if dark else "rgba(255,255,255,0.45)"
        for w in (self._status, self._cur_time, self._tot_time, self._file_label):
            fs = w.styleSheet().split("font-size:")
            sz = fs[1].strip() if len(fs) > 1 else "12px;"
            w.setStyleSheet(f"color:{dim}; font-size:{sz}")
        self._canvas.update()

    def closeEvent(self, e):
        self.cleanup()
        super().closeEvent(e)

    def cleanup(self):
        """主动释放音频设备 / 定时器 / 解码缓冲。

        嵌入宿主(例如聊天页里折叠媒体卡)用 setParent(None)+deleteLater
        移除本组件时,Qt 不会触发 closeEvent;同时 sounddevice.OutputStream
        的 callback 是 bound method `self._audio_cb`,持有 widget 的强引用,
        在流关闭前 widget 不会被 GC —— 也就会一直占着 `_file_data` 这块
        几十 MB 的解码缓冲。这里把这些资源都显式拆掉,调用应当幂等。
        """
        try:
            self._stop_mic()
        except Exception:
            pass
        try:
            self._close_stream()
        except Exception:
            pass
        try:
            self._play_timer.stop()
        except Exception:
            pass
        # WaveformCanvas 里 16ms 的 idle 动画定时器没保留引用,挨个找出来停掉
        try:
            for t in self._canvas.findChildren(QTimer):
                t.stop()
        except Exception:
            pass
        # 大块缓冲直接断引用,触发立即 GC
        self._file_data = None
        self._playing = False
        try:
            self._canvas._overview = np.array([])
            self._canvas._mic_buf = []
        except Exception:
            pass


# # ─── entry ────────────────────────────────────────────────────────────────────
def main():
    app = QApplication(sys.argv)
    app.setApplicationName("AudioWaveformWidget")
    win = AudioWaveformWidget()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
