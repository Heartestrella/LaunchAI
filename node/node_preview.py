"""
node_preview.py
~~~~~~~~~~~~~~~
节点内嵌预览组件：根据文件类型自动选择音频波形 / 图片 / 视频 / 文本显示

设计原则：所有预览 widget 的子控件都必须能根据父 widget 当前几何自适应
（不再使用任何 scaledToWidth / setFixedSize 固定尺寸），保证它们总能严格
填满外框，不会从 NodeCanvas 上的预览区域里"漏"出来。

可交互预览（音频 / 视频）必须设置 ``INTERACTIVE = True``：
node_canvas 会读取该属性，跳过对预览 widget 的 WA_TransparentForMouseEvents
设置，以确保播放按钮 / 波形拖动等控件能正常接收鼠标事件。
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import numpy as np
from pathlib import Path

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTextEdit, QSizePolicy, QLabel,
)
from PyQt6.QtCore import Qt, QTimer, QUrl, pyqtSignal, QSize, QRectF
from PyQt6.QtGui import QColor, QPixmap, QResizeEvent, QImageReader, QPainter, QImage

from qfluentwidgets import (
    CaptionLabel, TransparentToolButton, FluentIcon,
)

try:
    from qfluentwidgets.multimedia import VideoWidget as FluentVideoWidget
    HAS_FLUENT_VIDEO = True
except Exception:
    HAS_FLUENT_VIDEO = False

try:
    import soundfile as sf
    HAS_SF = True
except Exception:
    HAS_SF = False

try:
    import sounddevice as sd
    HAS_SD = True
except Exception:
    HAS_SD = False
    sd = None

try:
    from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput, QVideoSink, QVideoFrame
    HAS_QT_MM = True
except Exception:
    HAS_QT_MM = False

AUDIO_EXT = {".wav", ".mp3", ".flac", ".ogg", ".aiff", ".m4a"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tiff"}
VIDEO_EXT = {".mp4", ".mov", ".mkv", ".avi", ".webm"}
TEXT_EXT = {
    ".txt", ".srt", ".json", ".csv", ".log", ".md",
    ".xml", ".yaml", ".yml", ".ini", ".conf", ".toml",
    ".py", ".js", ".ts", ".html", ".css", ".c", ".cpp", ".h",
}

PREVIEW_W = 190

_DARK_BG = "#2b2b2b"


def detect_file_type(path: str) -> str:
    ext = Path(path).suffix.lower()
    if ext in AUDIO_EXT:
        return "audio"
    if ext in IMAGE_EXT:
        return "image"
    if ext in VIDEO_EXT:
        return "video"
    if ext in TEXT_EXT:
        return "text"
    return "unknown"


def _is_binary_file(path: str, sample_size: int = 4096) -> bool:
    """采样判断文件是否为二进制：含 NUL 字节 或 非文本字符占比 > 30%"""
    try:
        with open(path, "rb") as f:
            chunk = f.read(sample_size)
        if not chunk:
            return False
        if b"\x00" in chunk:
            return True
        text_chars = bytes(range(32, 127)) + b"\n\r\t\b\f"
        nontext = sum(1 for b in chunk if b not in text_chars)
        return (nontext / len(chunk)) > 0.30
    except Exception:
        return True


def create_preview(path: str, parent: QWidget) -> "PreviewBase | None":
    if not path or not os.path.isfile(path):
        return None
    ftype = detect_file_type(path)
    if ftype == "audio":
        return NodeAudioPreview(path, parent)
    if ftype == "image":
        return NodeImagePreview(path, parent)
    if ftype == "video":
        return NodeVideoPreview(path, parent)
    if ftype == "text":
        return NodeTextPreview(path, parent)
    # 未知扩展名：先判断是否为二进制，避免把乱码塞进文本框
    if _is_binary_file(path):
        return NodeBinaryPreview(path, parent)
    return NodeTextPreview(path, parent)


# ══════════════════════════════════════════════════════════════════════
#  统一基类：保证外框样式 + Expanding 尺寸策略
# ══════════════════════════════════════════════════════════════════════
class PreviewBase(QWidget):
    # 可交互预览（含按钮、可点击控件）必须设为 True。
    # node_canvas.update_preview() 会读取此属性决定是否套上 WA_TransparentForMouseEvents。
    INTERACTIVE = False

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(f"background:{_DARK_BG}; border-radius:4px;")
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Expanding)
        self.setMinimumSize(20, 20)
        if not self.INTERACTIVE:
            self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def _make_children_transparent(self):
        """递归把所有子 widget 设为鼠标穿透。互动预览跳过。"""
        if self.INTERACTIVE:
            return
        for child in self.findChildren(QWidget):
            child.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def cleanup(self):
        pass


# ══════════════════════════════════════════════════════════════════════
#  音频预览
# ══════════════════════════════════════════════════════════════════════
class NodeAudioPreview(PreviewBase):
    INTERACTIVE = True

    def __init__(self, path: str, parent=None):
        super().__init__(parent)
        self._path = path
        self._file_data: np.ndarray | None = None
        self._file_sr: int = 44100
        self._file_pos: int = 0
        self._playing: bool = False
        self._stream = None
        self._CHUNK = 2048
        self._seek_dragging: bool = False
        self._paused_for_seek: bool = False
        self._tmp_wav: str | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(2)

        from widgets.audio_waveform_widget import WaveformCanvas
        self._canvas = WaveformCanvas(self, bar_width=2.0)
        self._canvas.setSizePolicy(QSizePolicy.Policy.Expanding,
                                   QSizePolicy.Policy.Expanding)
        self._canvas.setMinimumHeight(20)
        self._canvas.seek_started.connect(self._on_seek_press)
        self._canvas.seeked.connect(self._on_seek)
        self._canvas.seek_finished.connect(self._on_seek_release)
        root.addWidget(self._canvas, 1)

        ctrl = QHBoxLayout()
        ctrl.setContentsMargins(2, 0, 2, 0)
        ctrl.setSpacing(4)

        self._play_btn = TransparentToolButton(FluentIcon.PLAY, self)
        self._play_btn.setFixedSize(22, 22)
        self._play_btn.clicked.connect(self._toggle_play)
        ctrl.addWidget(self._play_btn)

        self._time_lbl = CaptionLabel("0:00 / 0:00", self)
        self._time_lbl.setStyleSheet("color:rgba(255,255,255,0.5); font-size:10px;")
        self._time_lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        ctrl.addWidget(self._time_lbl)

        ctrl.addStretch()

        self._meta_lbl = CaptionLabel("", self)
        self._meta_lbl.setStyleSheet("color:rgba(255,255,255,0.4); font-size:10px;")
        self._meta_lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        ctrl.addWidget(self._meta_lbl)

        root.addLayout(ctrl, 0)

        self._play_timer = QTimer(self)
        self._play_timer.timeout.connect(self._play_tick)

        self._load(path)

    def _load(self, path: str):
        if not HAS_SF:
            return
        # libsndfile 不支持 AAC/m4a (B站音频流) 老版本也不支持 mp3
        # sf.read 失败时用 ffmpeg 转 16-bit PCM wav 落到临时文件再读
        read_path = path
        try:
            sf.info(path)
        except Exception:
            decoded = self._decode_via_ffmpeg(path)
            if not decoded:
                return
            self._tmp_wav = decoded
            read_path = decoded
        try:
            data, sr = sf.read(read_path, dtype='float32', always_2d=True)
            mono = data.mean(axis=1)
            self._file_data = mono
            self._file_sr = sr
            self._file_pos = 0

            n = max(200, self._canvas._bar_count() * 4)
            seg = max(1, len(mono) // n)
            overview = np.array([
                np.max(np.abs(mono[i * seg:(i + 1) * seg]))
                for i in range(n)
            ], dtype=float)
            mx = overview.max()
            if mx > 0:
                overview /= mx
            self._canvas.load_overview(overview)

            dur = len(mono) / sr
            self._time_lbl.setText(f"0:00 / {int(dur // 60)}:{int(dur % 60):02d}")
            self._meta_lbl.setText(f"{sr // 1000} kHz")
        except Exception:
            pass

    def _toggle_play(self):
        if self._playing:
            self._pause()
        else:
            self._play()

    def _play(self):
        if self._file_data is None or not HAS_SD:
            return
        self._playing = True
        self._play_btn.setIcon(FluentIcon.PAUSE)
        try:
            self._stream = sd.OutputStream(
                samplerate=self._file_sr, channels=1,
                callback=self._audio_cb, blocksize=self._CHUNK)
            self._stream.start()
        except Exception:
            self._playing = False
            self._play_btn.setIcon(FluentIcon.PLAY)
            return
        interval = max(16, int(1000 * self._CHUNK / self._file_sr))
        self._play_timer.start(interval)

    def _pause(self):
        self._playing = False
        self._play_timer.stop()
        self._play_btn.setIcon(FluentIcon.PLAY)
        if self._stream:
            try:
                self._stream.stop()
            except Exception:
                pass

    def _play_tick(self):
        if not self._playing or self._file_data is None:
            return
        # 拖动期间不要回写进度，否则刚拖完又被旧 _file_pos 覆盖
        if self._seek_dragging:
            return
        total = len(self._file_data)
        prog = self._file_pos / total
        self._canvas.set_progress(prog)
        cur = self._file_pos / self._file_sr
        dur = total / self._file_sr
        self._time_lbl.setText(
            f"{int(cur // 60)}:{int(cur % 60):02d} / "
            f"{int(dur // 60)}:{int(dur % 60):02d}")
        if self._file_pos >= total:
            self._stop()

    # ── 波形拖动 seek 回调 ───────────────────────────────────────────
    def _on_seek_press(self):
        self._seek_dragging = True
        if self._playing:
            self._paused_for_seek = True
            self._pause()

    def _on_seek(self, frac: float):
        if self._file_data is None:
            return
        self._file_pos = int(frac * len(self._file_data))
        self._canvas.set_progress(frac)
        cur = self._file_pos / self._file_sr
        dur = len(self._file_data) / self._file_sr
        self._time_lbl.setText(
            f"{int(cur // 60)}:{int(cur % 60):02d} / "
            f"{int(dur // 60)}:{int(dur % 60):02d}")

    def _on_seek_release(self):
        if not self._seek_dragging:
            return
        self._seek_dragging = False
        if self._paused_for_seek:
            self._paused_for_seek = False
            self._play()

    def _stop(self):
        self._pause()
        self._close_stream()
        self._file_pos = 0
        self._canvas.set_progress(0.0)

    def _close_stream(self):
        if self._stream:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None

    def _audio_cb(self, outdata, frames, t, status):
        end = self._file_pos + frames
        if end >= len(self._file_data):
            rem = len(self._file_data) - self._file_pos
            outdata[:rem, 0] = self._file_data[self._file_pos:]
            outdata[rem:, 0] = 0.0
            self._file_pos = len(self._file_data)
            raise sd.CallbackStop()
        outdata[:, 0] = self._file_data[self._file_pos:end]
        self._file_pos = end

    def _decode_via_ffmpeg(self, src: str) -> str | None:
        try:
            from utils.atool import resource_path
            ffmpeg = os.path.join(resource_path(
                os.path.join("resource", "ffmepg", "bin")), "ffmpeg.exe")
            if not os.path.isfile(ffmpeg):
                ffmpeg = "ffmpeg"
        except Exception:
            ffmpeg = "ffmpeg"

        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        tmp.close()
        try:
            r = subprocess.run(
                [ffmpeg, "-y", "-i", src,
                 "-vn", "-ac", "1", "-ar", "44100",
                 "-c:a", "pcm_s16le", tmp.name],
                capture_output=True, timeout=60,
                creationflags=subprocess.CREATE_NO_WINDOW
                if os.name == "nt" else 0,
            )
            if r.returncode == 0 and os.path.getsize(tmp.name) > 0:
                return tmp.name
        except Exception:
            pass
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
        return None

    def cleanup(self):
        self._stop()
        self._close_stream()
        self._play_timer.stop()
        if self._tmp_wav and os.path.isfile(self._tmp_wav):
            try:
                os.unlink(self._tmp_wav)
            except OSError:
                pass
            self._tmp_wav = None


# ══════════════════════════════════════════════════════════════════════
#  自适应缩放的图片标签
# ══════════════════════════════════════════════════════════════════════
class _ScaledImageLabel(QLabel):
    def __init__(self, pixmap: QPixmap | None, parent=None):
        super().__init__(parent)
        self._src: QPixmap | None = pixmap if pixmap and not pixmap.isNull() else None
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Expanding)
        self.setMinimumSize(1, 1)
        self.setStyleSheet("background:transparent;")
        self._rescale()

    def set_pixmap(self, pixmap: QPixmap | None):
        self._src = pixmap if pixmap and not pixmap.isNull() else None
        self._rescale()

    def resizeEvent(self, e: QResizeEvent):
        self._rescale()
        super().resizeEvent(e)

    def _rescale(self):
        if self._src is None or self._src.isNull():
            self.clear()
            return
        sz = self.size()
        if sz.width() <= 1 or sz.height() <= 1:
            return
        scaled = self._src.scaled(
            sz,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.setPixmap(scaled)


def _load_pixmap_robust(path: str) -> QPixmap | None:
    """优先用 QPixmap 直接加载，失败回退到读字节再 loadFromData，
    可以绕开 QPixmap 在某些含中文路径下的加载失败。"""
    pm = QPixmap(path)
    if not pm.isNull():
        return pm
    try:
        reader = QImageReader(path)
        reader.setAutoTransform(True)
        img = reader.read()
        if not img.isNull():
            pm2 = QPixmap.fromImage(img)
            if not pm2.isNull():
                return pm2
    except Exception:
        pass
    try:
        with open(path, "rb") as f:
            data = f.read()
        pm3 = QPixmap()
        if pm3.loadFromData(data) and not pm3.isNull():
            return pm3
    except Exception:
        pass
    return None


# ══════════════════════════════════════════════════════════════════════
#  图片预览
# ══════════════════════════════════════════════════════════════════════
class NodeImagePreview(PreviewBase):

    def __init__(self, path: str, parent=None):
        super().__init__(parent)

        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(0)

        pm = _load_pixmap_robust(path)
        if pm is None:
            placeholder = QLabel("(无法加载图片)", self)
            placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
            placeholder.setStyleSheet(
                "color:rgba(255,255,255,0.45); font-size:11px; background:transparent;"
            )
            placeholder.setSizePolicy(QSizePolicy.Policy.Expanding,
                                      QSizePolicy.Policy.Expanding)
            root.addWidget(placeholder, 1)
        else:
            self._img = _ScaledImageLabel(pm, self)
            root.addWidget(self._img, 1)

        self._make_children_transparent()


# ══════════════════════════════════════════════════════════════════════
#  自绘视频画面 widget（旧方案，已注释保留）
#
#  说明：不能使用 QVideoWidget。QVideoWidget 在 Windows 上会创建一个
#  native（HWND）子窗口，永远叠在所有非 native Qt widget 之上，会把
#  导航栏、对话框、菜单等全部盖住。当时用 QVideoSink 收帧、QPainter
#  自绘，确保它和普通 QWidget 一样参与 Qt 的 z-order。
#  现在改用 qfluentwidgets.multimedia.VideoWidget（QGraphicsView 子类，
#  非 native），所以这套备用方案暂时不启用。
# ══════════════════════════════════════════════════════════════════════
# class _VideoSinkWidget(QWidget):
#     def __init__(self, parent=None):
#         super().__init__(parent)
#         self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
#         self.setSizePolicy(QSizePolicy.Policy.Expanding,
#                            QSizePolicy.Policy.Expanding)
#         self.setMinimumSize(1, 1)
#         self._image: QImage | None = None
#         self._sink = QVideoSink(self)
#         self._sink.videoFrameChanged.connect(self._on_frame)
#
#     def sink(self) -> "QVideoSink":
#         return self._sink
#
#     def _on_frame(self, frame: "QVideoFrame"):
#         if frame.isValid():
#             img = frame.toImage()
#             if not img.isNull():
#                 self._image = img
#                 self.update()
#
#     def paintEvent(self, _):
#         p = QPainter(self)
#         p.fillRect(self.rect(), QColor("black"))
#         if self._image is None or self._image.isNull():
#             return
#         w, h = self.width(), self.height()
#         iw, ih = self._image.width(), self._image.height()
#         if iw <= 0 or ih <= 0:
#             return
#         scale = min(w / iw, h / ih)
#         dw, dh = iw * scale, ih * scale
#         dx, dy = (w - dw) / 2, (h - dh) / 2
#         p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
#         p.drawImage(QRectF(dx, dy, dw, dh), self._image)
#         p.end()


# ══════════════════════════════════════════════════════════════════════
#  视频预览（基于 qfluentwidgets.multimedia.VideoWidget）
#
#  FluentVideoWidget 是 QGraphicsView 子类（非 native HWND），不会盖住
#  导航栏 / 对话框。它内置 QMediaPlayer，可通过 .player 访问；自带
#  播放控制条；我们只需要用一个外置按钮控制播放即可。
# ══════════════════════════════════════════════════════════════════════
class NodeVideoPreview(PreviewBase):
    INTERACTIVE = True

    def __init__(self, path: str, parent=None):
        super().__init__(parent)
        self._path = path
        self._tmp_path: str | None = None
        self._video: "FluentVideoWidget | None" = None
        self._player = None
        self._duration_ms: int = 0
        self._playing: bool = False

        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(2)

        # ── 旧的 QVideoSink + QPainter 自绘方案（保留注释，备用）────────
        # if HAS_QT_MM:
        #     self._video = _VideoSinkWidget(self)
        #     root.addWidget(self._video, 1)
        #     self._player = QMediaPlayer(self)
        #     self._audio_out = QAudioOutput(self)
        #     self._player.setAudioOutput(self._audio_out)
        #     self._player.setVideoSink(self._video.sink())
        #     self._player.durationChanged.connect(self._on_duration)
        #     self._player.positionChanged.connect(self._on_position)
        #     self._player.playbackStateChanged.connect(self._on_state)
        #     try:
        #         self._player.setSource(QUrl.fromLocalFile(os.path.abspath(path)))
        #     except Exception:
        #         pass

        # ── 新方案：qfluentwidgets VideoWidget ────────────────────────────
        if HAS_FLUENT_VIDEO:
            self._video = FluentVideoWidget(self)
            self._video.setSizePolicy(QSizePolicy.Policy.Expanding,
                                      QSizePolicy.Policy.Expanding)
            self._video.setMinimumSize(1, 1)
            root.addWidget(self._video, 1)

            try:
                self._video.setVideo(QUrl.fromLocalFile(os.path.abspath(path)))
            except Exception:
                pass

            # 旧外置按钮 / 时间显示用的 player 信号连接（已注释，参考用）
            # try:
            #     self._player = self._video.player
            #     self._player.durationChanged.connect(self._on_duration)
            #     self._player.positionChanged.connect(self._on_position)
            #     self._player.playbackStateChanged.connect(self._on_state)
            # except Exception:
            #     self._player = None
            try:
                self._player = self._video.player
            except Exception:
                self._player = None
        else:
            # 没装 QtMultimedia / fluent VideoWidget → 退化为抽帧缩略图
            thumb = self._extract_frame(path)
            pm = _load_pixmap_robust(thumb) if thumb else None
            if pm is not None:
                self._img = _ScaledImageLabel(pm, self)
                root.addWidget(self._img, 1)
            else:
                placeholder = QLabel("(无法生成视频缩略图)", self)
                placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
                placeholder.setStyleSheet(
                    "color:rgba(255,255,255,0.45); font-size:11px; background:transparent;"
                )
                placeholder.setSizePolicy(QSizePolicy.Policy.Expanding,
                                          QSizePolicy.Policy.Expanding)
                root.addWidget(placeholder, 1)

        # ── 旧的外置播放按钮 / 时间显示 / 文件名（已注释）────────────────
        # FluentVideoWidget 自带播放控制条，外置控件冗余，全部注释保留。
        #
        # ctrl = QHBoxLayout()
        # ctrl.setContentsMargins(2, 0, 2, 0)
        # ctrl.setSpacing(4)
        #
        # self._play_btn = TransparentToolButton(FluentIcon.PLAY, self)
        # self._play_btn.setFixedSize(22, 22)
        # self._play_btn.setEnabled(self._video is not None)
        # self._play_btn.clicked.connect(self._toggle_play)
        # ctrl.addWidget(self._play_btn)
        #
        # self._time_lbl = CaptionLabel("0:00 / 0:00", self)
        # self._time_lbl.setStyleSheet(
        #     "color:rgba(255,255,255,0.5); font-size:10px; background:transparent;"
        # )
        # self._time_lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        # ctrl.addWidget(self._time_lbl)
        # ctrl.addStretch()
        #
        # name_lbl = CaptionLabel(Path(path).name, self)
        # name_lbl.setStyleSheet(
        #     "color:rgba(255,255,255,0.45); font-size:9px; background:transparent;"
        # )
        # name_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        # name_lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        # ctrl.addWidget(name_lbl)
        #
        # root.addLayout(ctrl, 0)

    # ── 旧外置按钮的回调（已注释）─────────────────────────────────────
    # def _toggle_play(self):
    #     if self._video is None:
    #         return
    #     try:
    #         if self._playing:
    #             self._video.pause()
    #         else:
    #             # 播放到结尾后再点击：回到开头
    #             if (self._player is not None and self._duration_ms
    #                     and self._player.position() >= self._duration_ms - 50):
    #                 self._player.setPosition(0)
    #             self._video.play()
    #     except Exception:
    #         pass
    #
    # def _on_state(self, state):
    #     if not HAS_QT_MM:
    #         return
    #     self._playing = (state == QMediaPlayer.PlaybackState.PlayingState)
    #     self._play_btn.setIcon(
    #         FluentIcon.PAUSE if self._playing else FluentIcon.PLAY
    #     )
    #
    # def _on_duration(self, ms: int):
    #     self._duration_ms = max(0, ms)
    #     pos = self._player.position() if self._player is not None else 0
    #     self._update_time_label(pos)
    #
    # def _on_position(self, ms: int):
    #     self._update_time_label(ms)
    #
    # def _update_time_label(self, cur_ms: int):
    #     cs = max(0, cur_ms) // 1000
    #     ds = max(0, self._duration_ms) // 1000
    #     self._time_lbl.setText(
    #         f"{cs // 60}:{cs % 60:02d} / {ds // 60}:{ds % 60:02d}"
    #     )

    def _extract_frame(self, video_path: str) -> str | None:
        try:
            from utils.atool import resource_path
            ffmpeg = os.path.join(resource_path(
                os.path.join("resource", "ffmepg", "bin")), "ffmpeg.exe")
            if not os.path.isfile(ffmpeg):
                ffmpeg = "ffmpeg"
        except Exception:
            ffmpeg = "ffmpeg"

        tmp = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
        tmp.close()
        self._tmp_path = tmp.name
        try:
            subprocess.run(
                [ffmpeg, "-i", video_path, "-vframes", "1",
                 "-q:v", "2", "-y", self._tmp_path],
                capture_output=True, timeout=10,
                creationflags=subprocess.CREATE_NO_WINDOW
                if os.name == "nt" else 0,
            )
            return self._tmp_path
        except Exception:
            return None

    def cleanup(self):
        if self._video is not None:
            try:
                self._video.stop()
            except Exception:
                pass
        if self._player is not None:
            try:
                self._player.setSource(QUrl())
            except Exception:
                pass
        if self._tmp_path and os.path.isfile(self._tmp_path):
            try:
                os.unlink(self._tmp_path)
            except Exception:
                pass


# ══════════════════════════════════════════════════════════════════════
#  文本预览
# ══════════════════════════════════════════════════════════════════════
class NodeTextPreview(PreviewBase):

    def __init__(self, path: str, parent=None):
        super().__init__(parent)

        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(0)

        self._edit = QTextEdit(self)
        self._edit.setReadOnly(True)
        self._edit.setSizePolicy(QSizePolicy.Policy.Expanding,
                                 QSizePolicy.Policy.Expanding)
        self._edit.setStyleSheet(
            "QTextEdit {"
            f"  background:{_DARK_BG}; color:#e0e0e0;"
            "  border:none; font-size:10px; font-family:Consolas;"
            "}"
        )
        self._edit.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._edit.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                text = f.read(2048)
            self._edit.setPlainText(text)
        except Exception:
            self._edit.setPlainText("(无法读取)")

        root.addWidget(self._edit, 1)

        self._make_children_transparent()


# ══════════════════════════════════════════════════════════════════════
#  二进制文件占位预览
# ══════════════════════════════════════════════════════════════════════
class NodeBinaryPreview(PreviewBase):

    def __init__(self, path: str, parent=None):
        super().__init__(parent)

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(4)

        msg_lbl = QLabel("二进制文件无法预览", self)
        msg_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        msg_lbl.setStyleSheet(
            "color:rgba(255,255,255,0.6); font-size:11px; background:transparent;"
        )
        msg_lbl.setWordWrap(True)

        name_lbl = CaptionLabel(Path(path).name, self)
        name_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        name_lbl.setStyleSheet(
            "color:rgba(255,255,255,0.4); font-size:9px; background:transparent;"
        )
        name_lbl.setWordWrap(True)

        try:
            size = os.path.getsize(path)
        except Exception:
            size = 0
        size_lbl = CaptionLabel(_fmt_size(size), self)
        size_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        size_lbl.setStyleSheet(
            "color:rgba(255,255,255,0.35); font-size:9px; background:transparent;"
        )

        root.addStretch()
        root.addWidget(msg_lbl)
        root.addWidget(name_lbl)
        root.addWidget(size_lbl)
        root.addStretch()

        self._make_children_transparent()


def _fmt_size(n: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    f = float(n)
    for u in units:
        if f < 1024 or u == units[-1]:
            return f"{f:.1f} {u}" if u != "B" else f"{int(f)} {u}"
        f /= 1024
    return f"{n} B"
