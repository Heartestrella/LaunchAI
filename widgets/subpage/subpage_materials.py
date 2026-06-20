"""
widgets/subpage/subpage_materials.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
素材库子页面 —— B 站 / 网易云音乐 搜索 → 预览 → 保存。

UX
==
两级视图通过 QStackedWidget 切换：

  page 0  搜索视图   头部 + 免责横幅 + 搜索行 + 结果卡片列表
  page 1  预览视图   返回按钮 + 元信息 + (AudioWaveformWidget 或 VideoWidget) + 保存按钮

- **NetEase 命中** → 预览页用 ``AudioWaveformWidget`` 加载 mp3 显示波形 + 播放控制
- **Bilibili 命中** → 预览页用 ``qfluentwidgets.multimedia.VideoWidget``
  加载 ``download_bilibili_preview`` muxed 出来的低画质 mp4

预览文件下载到 ``outputs/node/materials/.preview/`` 子目录 用户点
「保存到素材库」时 ``shutil.move`` 到正式目录。返回 / 切走预览页时主动
``stop()`` 媒体播放器以释放文件句柄 避免 Windows 文件锁。
"""

from __future__ import annotations

import os
import shutil
from typing import Optional

from PyQt6.QtCore import Qt, pyqtSignal, QThread, QTimer, QUrl
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QStackedWidget, QDialog,
    QTextBrowser, QSizePolicy,
)
from qfluentwidgets import (
    BodyLabel, CaptionLabel, TitleLabel, StrongBodyLabel,
    PushButton, PrimaryPushButton, ComboBox, LineEdit, ToolButton,
    ProgressBar, CardWidget, SmoothScrollArea, IconWidget,
    InfoBar, InfoBarPosition, IndeterminateProgressBar,
    FluentIcon as FIF,
)
from qfluentwidgets.multimedia import VideoWidget

from widgets.audio_waveform_widget import AudioWaveformWidget
from utils import paths as _paths
from utils.configer import get_field, set_field
from logger import info, error


# ── 文案 ──────────────────────────────────────────────────────────────

_BANNER_TEXT = (
    "⚠️ 本功能仅供个人学习/研究使用，所有音视频内容版权归相应平台权利人所有；"
    "使用本功能的全部法律风险由您本人承担。"
)

_DISCLAIMER_HTML = """
<h3>LaunchAI 素材获取功能 —— 免责声明</h3>
<ol>
  <li><b>使用前提</b>：本功能仅供个人学习、研究、备份及非商业用途。继续使用即表示您已阅读并接受本声明全部条款。</li>

  <li><b>版权责任</b>：网易云音乐、哔哩哔哩平台上的全部音视频内容著作权归相应权利人所有。下载、保存、传播这些内容须遵守
      <i>《中华人民共和国著作权法》</i>、<i>《信息网络传播权保护条例》</i>以及相应平台
      <i>《用户协议》</i>、<i>《版权声明》</i>。<b>任何因您使用本功能下载内容而产生的版权纠纷，由您本人承担全部法律责任。</b></li>

  <li><b>API 来源声明</b>：本功能下载所用的接口解析逻辑（网易云 <code>weapi</code> 协议、哔哩哔哩 <code>playurl/search</code> 协议等）
      <b>并非由 LaunchAI 项目作者原创设计或维护</b>，而是参考、复用以下开源社区项目长期公开的协议解读与实现：
      <ul>
        <li><i>Binaryify/NeteaseCloudMusicApi</i></li>
        <li><i>jixunmoe/netease-cloud-music-api</i></li>
        <li><i>greats3an/pyncm</i> 及其各下游 fork</li>
        <li><i>SocialSisterYi/bilibili-API-collect</i></li>
      </ul>
      LaunchAI 仅在本项目内嵌了这些协议的最小实现以便单机运行。<b>API 的可用性、稳定性、返回内容的合法性、
      平台封锁策略调整等均由对应平台与上述上游开源项目控制，LaunchAI 项目作者不对 API 本身的行为、
      返回结果及由此产生的任何后果承担责任</b>，包括但不限于：拿到的 URL 失效、解析到的曲目版权状态、平台风控反爬封禁等。</li>

  <li><b>平台条款</b>：本功能通过模拟客户端请求公开 Web 接口获取数据，可能违反平台《Robots 协议》或《用户协议》中关于自动化抓取的条款。
      请您自行控制使用频率，避免对平台服务造成负担。</li>

  <li><b>内容来源</b>：本工具不存储、不分发、不缓存任何音视频内容；所有数据均直接从平台公开 CDN 拉取至您本地设备。
      下载文件与平台原始文件等价，本工具不做任何修改、加密或解 DRM 处理。</li>

  <li><b>使用限制</b>：请勿用于商业牟利、二次分发、上传至公开网络、训练 AI 模型或任何侵犯著作权人合法权益的行为。
      建议下载内容用毕后及时删除；如需长期保存请通过平台正规途径购买授权。</li>

  <li><b>免除责任</b>：LaunchAI 项目作者及贡献者仅提供本工具的技术封装，
      <b>不对您使用本工具的任何行为及后果负责</b>，包括但不限于版权追责、账号封禁、网络安全风险、数据丢失。</li>

  <li><b>未成年人</b>：未满 18 周岁用户请在监护人陪同下使用，监护人须同意本声明全部条款。</li>

  <li><b>声明修改</b>：作者保留在不另行通知情况下修改本声明的权利，修改后的声明将随软件更新发布。</li>
</ol>
<p style="color:#888;font-size:12px;">点击下方「我已阅读并同意」即视为您已完整理解并接受全部 9 条条款。</p>
"""


# ══════════════════════════════════════════════════════════════════════
#  免责声明对话框
# ══════════════════════════════════════════════════════════════════════

class DisclaimerDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("素材获取功能 — 免责声明")
        self.setModal(True)
        self.resize(680, 520)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 16)
        layout.setSpacing(12)

        title = StrongBodyLabel("请仔细阅读以下条款", self)
        layout.addWidget(title)

        self._browser = QTextBrowser(self)
        self._browser.setOpenExternalLinks(False)
        self._browser.setHtml(_DISCLAIMER_HTML)
        layout.addWidget(self._browser, 1)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(12)
        btn_row.addStretch()
        self.cancel_btn = PushButton("我不同意", self)
        self.cancel_btn.clicked.connect(self.reject)
        self.yes_btn = PrimaryPushButton("我已阅读并同意", self)
        self.yes_btn.clicked.connect(self.accept)
        btn_row.addWidget(self.cancel_btn)
        btn_row.addWidget(self.yes_btn)
        layout.addLayout(btn_row)


# ══════════════════════════════════════════════════════════════════════
#  后台线程
# ══════════════════════════════════════════════════════════════════════

class MaterialsSearchWorker(QThread):
    results = pyqtSignal(list)
    error   = pyqtSignal(str)

    def __init__(self, source: str, keyword: str, parent=None):
        super().__init__(parent)
        self.source = source
        self.keyword = keyword

    def run(self):
        try:
            from utils.material_fetcher import (
                search_netease, search_bilibili,
            )
            if self.source == "netease":
                rs = search_netease(self.keyword, limit=20,
                                    drop_instrumental=True)
            else:
                rs = search_bilibili(self.keyword, limit=20)
            self.results.emit(rs)
        except Exception as e:
            self.error.emit(f"搜索失败: {e}")


class MaterialsPreviewWorker(QThread):
    """统一的预览资源下载器
    netease  → 公开 CDN mp3
    bilibili → DASH video + audio ffmpeg copy-mux 后的 mp4 (画质由 quality 控制)
    """
    progress = pyqtSignal(int, str)
    finished = pyqtSignal(str)
    error    = pyqtSignal(str)

    def __init__(self, source: str, item: dict, cache_dir: str,
                 quality: int = 16, parent=None):
        super().__init__(parent)
        self.source = source
        self.item = item
        self.cache_dir = cache_dir
        self.quality = int(quality)
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            from utils.material_fetcher import (
                download_netease, download_bilibili_preview, CancelledError,
            )

            def _prog(p, t): self.progress.emit(p, t)
            def _cancel():   return self._cancelled

            if self.source == "netease":
                title = f"{self.item.get('name','')} - {self.item.get('artists','')}".strip(" -")
                path = download_netease(
                    self.item.get("id"), self.cache_dir, title=title,
                    progress_cb=_prog, cancel_cb=_cancel,
                )
            else:
                title = f"{self.item.get('title','')} - {self.item.get('author','')}".strip(" -")
                path = download_bilibili_preview(
                    self.item.get("bvid"), self.cache_dir, title=title,
                    quality=self.quality,
                    progress_cb=_prog, cancel_cb=_cancel,
                )
            self.finished.emit(path)
        except CancelledError:
            self.error.emit("已取消")
        except Exception as e:
            self.error.emit(f"预览下载失败: {e}")


# ══════════════════════════════════════════════════════════════════════
#  搜索结果卡片
# ══════════════════════════════════════════════════════════════════════

class _ResultCard(CardWidget):
    """单条搜索结果 整卡可点 触发跳转到预览页"""

    clicked_preview = pyqtSignal(str, dict)   # (source, item)

    def __init__(self, source: str, item: dict, parent=None):
        super().__init__(parent)
        self.setBorderRadius(10)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._source = source
        self._item = item

        root = QHBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 12)
        root.setSpacing(12)

        icon = IconWidget(
            FIF.MUSIC if source == "netease" else FIF.VIDEO, self)
        icon.setFixedSize(28, 28)
        root.addWidget(icon)

        info_box = QVBoxLayout()
        info_box.setSpacing(2)
        if source == "netease":
            title_txt = f"{item.get('name','(无标题)')}"
            sub_txt = f"{item.get('artists') or '未知歌手'} · 《{item.get('album') or '-'}》"
            dur_ms = int(item.get("duration_ms") or 0)
            extra_txt = f"{dur_ms//60000}:{(dur_ms%60000)//1000:02d}" if dur_ms else "—"
        else:
            title_txt = item.get("title", "(无标题)")
            sub_txt = f"UP: {item.get('author') or '未知'} · 播放 {item.get('play','0')}"
            extra_txt = item.get("duration") or "—"

        title_lbl = StrongBodyLabel(title_txt, self)
        sub_lbl = CaptionLabel(sub_txt, self)
        info_box.addWidget(title_lbl)
        info_box.addWidget(sub_lbl)
        root.addLayout(info_box, 1)

        extra_lbl = CaptionLabel(extra_txt, self)
        root.addWidget(extra_lbl)
        root.addSpacing(8)

        # 装饰性的"预览"提示按钮 实际点击走整卡 mouseRelease
        self.go_btn = PushButton("预览 →", self)
        self.go_btn.setFixedWidth(96)
        self.go_btn.clicked.connect(self._fire)
        root.addWidget(self.go_btn)

    def _fire(self):
        self.clicked_preview.emit(self._source, self._item)

    def mouseReleaseEvent(self, ev):
        super().mouseReleaseEvent(ev)
        if ev.button() == Qt.MouseButton.LeftButton:
            self._fire()


# ══════════════════════════════════════════════════════════════════════
#  预览视图
# ══════════════════════════════════════════════════════════════════════

class _PreviewView(QWidget):
    """预览页:返回 + 元信息 + 加载状态 + 播放器 + 保存按钮

    生命周期由 MaterialsWidget 全权管理 切走时必须调 ``release()``
    停止播放并释放 VideoWidget 持有的文件句柄 否则 Windows 上 shutil.move
    会被锁。
    """

    back_requested = pyqtSignal()

    def __init__(self, save_dir: str, parent=None):
        super().__init__(parent)
        self._save_dir = save_dir
        self._source: str = ""
        self._item: dict = {}
        self._cache_path: Optional[str] = None
        self._saved_path: Optional[str] = None
        self._worker: Optional[MaterialsPreviewWorker] = None
        self._cur_quality: int = 16   # 默认 360P 体积最小适合预览
        self._cache_dir: str = save_dir   # start() 时被覆盖为 .preview/

        self._setup_ui()

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(32, 24, 32, 24)
        root.setSpacing(16)

        # 顶部 返回 + 标题
        top = QHBoxLayout()
        top.setSpacing(10)
        self.back_btn = ToolButton(FIF.RETURN, self)
        self.back_btn.setToolTip("返回搜索结果")
        self.back_btn.setFixedSize(36, 36)
        self.back_btn.clicked.connect(self.back_requested.emit)
        top.addWidget(self.back_btn)

        self.title_lbl = TitleLabel("预览", self)
        f = QFont(); f.setPointSize(18); f.setBold(True)
        self.title_lbl.setFont(f)
        top.addWidget(self.title_lbl, 1)

        # 画质选择 (B 站时显示 默认 360P 切换触发重新下载)
        from utils.material_fetcher import BILI_QUALITIES
        self._bili_qualities = BILI_QUALITIES
        self.quality_lbl = CaptionLabel("画质", self)
        self.quality_combo = ComboBox(self)
        for _q, name in BILI_QUALITIES:
            self.quality_combo.addItem(name)
        self.quality_combo.setCurrentText("360P")
        self.quality_combo.setFixedWidth(110)
        self.quality_combo.currentIndexChanged.connect(self._on_quality_changed)
        self.quality_lbl.setVisible(False)
        self.quality_combo.setVisible(False)
        top.addWidget(self.quality_lbl)
        top.addWidget(self.quality_combo)

        self.save_btn = PrimaryPushButton("保存到素材库", self, FIF.SAVE)
        self.save_btn.setFixedWidth(160)
        self.save_btn.setEnabled(False)
        self.save_btn.clicked.connect(self._on_save)
        top.addWidget(self.save_btn)

        root.addLayout(top)

        # 元信息行
        self.meta_lbl = CaptionLabel("", self)
        self.meta_lbl.setWordWrap(True)
        root.addWidget(self.meta_lbl)

        # 加载区 ─ 显示下载进度 / 错误
        self.loading_card = CardWidget(self)
        self.loading_card.setBorderRadius(10)
        ll = QVBoxLayout(self.loading_card)
        ll.setContentsMargins(20, 16, 20, 16)
        ll.setSpacing(10)
        self.loading_status = BodyLabel("准备中…", self.loading_card)
        ll.addWidget(self.loading_status)
        self.loading_bar = ProgressBar(self.loading_card)
        self.loading_bar.setValue(0)
        ll.addWidget(self.loading_bar)
        self.loading_indet = IndeterminateProgressBar(self.loading_card)
        self.loading_indet.setVisible(False)
        ll.addWidget(self.loading_indet)
        root.addWidget(self.loading_card)

        # 播放器栈: 0 = audio  1 = video
        self.player_stack = QStackedWidget(self)
        self.player_stack.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.player_stack.setMinimumHeight(360)

        self.audio_player = AudioWaveformWidget()
        self.audio_player.set_embedded_mode(True)
        self.audio_player.disable_mic_recording()
        self.player_stack.addWidget(self.audio_player)

        self.video_player = VideoWidget(self)
        self.player_stack.addWidget(self.video_player)

        root.addWidget(self.player_stack, 1)
        self.player_stack.setVisible(False)

    # ── 状态机 ────────────────────────────────────────────────────────

    def start(self, source: str, item: dict, cache_dir: str):
        """进入预览页时调 触发预览下载并实时更新 UI"""
        self.release()
        self._source = source
        self._item = item
        self._cache_dir = cache_dir
        self._cache_path = None
        self._saved_path = None

        # 标题 + 元信息 + 画质选择可见性
        if source == "netease":
            self.title_lbl.setText(item.get("name", "预览"))
            dur_ms = int(item.get("duration_ms") or 0)
            dur_txt = f"{dur_ms//60000}:{(dur_ms%60000)//1000:02d}" if dur_ms else "—"
            self.meta_lbl.setText(
                f"{item.get('artists') or '未知歌手'}  ·  "
                f"《{item.get('album') or '-'}》  ·  {dur_txt}  ·  网易云音乐"
            )
            self.player_stack.setCurrentWidget(self.audio_player)
            self.quality_lbl.setVisible(False)
            self.quality_combo.setVisible(False)
        else:
            self.title_lbl.setText(item.get("title", "预览"))
            self.meta_lbl.setText(
                f"UP: {item.get('author') or '未知'}  ·  "
                f"播放 {item.get('play','0')}  ·  时长 {item.get('duration','—')}  "
                f"·  哔哩哔哩"
            )
            self.player_stack.setCurrentWidget(self.video_player)
            self.quality_lbl.setVisible(True)
            self.quality_combo.setVisible(True)

        self.player_stack.setVisible(False)
        self.loading_card.setVisible(True)
        if source == "bilibili":
            qname = self.quality_combo.currentText()
            self.loading_status.setText(
                f"正在准备 {qname} 预览资源…\n"
                "B 站会下载对应画质 DASH 视频流并用 ffmpeg 合成 mp4 "
                "画质越高耗时和流量越大；80P+ 通常需要登录账号。"
            )
        else:
            self.loading_status.setText("正在解析直链并下载…")
        self.loading_bar.setValue(0)
        self.loading_bar.setVisible(True)
        self.loading_indet.setVisible(False)
        self.save_btn.setEnabled(False)
        self.save_btn.setText("保存到素材库")

        self._worker = MaterialsPreviewWorker(
            source, item, cache_dir, quality=self._cur_quality, parent=self)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_finished)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _on_quality_changed(self, idx: int):
        if not self._bili_qualities or idx < 0 or idx >= len(self._bili_qualities):
            return
        new_q, _name = self._bili_qualities[idx]
        if new_q == self._cur_quality:
            return
        self._cur_quality = new_q
        if self._source == "bilibili" and self._item:
            # 拿原 item + 新画质重新拉 release() 会把旧 worker 杀掉
            self.start(self._source, self._item, self._cache_dir)

    def _on_progress(self, percent: int, text: str):
        self.loading_bar.setValue(max(0, min(100, percent)))
        self.loading_status.setText(text)
        # 接近合成阶段切到 indeterminate 反映 ffmpeg 阻塞
        if percent >= 90 and percent < 100 and self._source == "bilibili":
            self.loading_bar.setVisible(False)
            self.loading_indet.setVisible(True)

    def _on_finished(self, path: str):
        self._cache_path = path
        self.loading_card.setVisible(False)
        self.player_stack.setVisible(True)
        self.save_btn.setEnabled(True)

        if self._source == "netease":
            self.audio_player.load_file(path)
        else:
            url = QUrl.fromLocalFile(os.path.abspath(path))
            self.video_player.setVideo(url)
            self.video_player.play()
        info(f"[materials] 预览就绪 → {path}")

    def _on_error(self, msg: str):
        self.loading_bar.setVisible(False)
        self.loading_indet.setVisible(False)
        self.loading_status.setText(f"预览失败: {msg}")
        InfoBar.error(
            "预览失败", msg,
            position=InfoBarPosition.TOP_RIGHT, duration=5000,
            parent=self.window(),
        )
        error(f"[materials] preview {msg}")

    def _on_save(self):
        if not self._cache_path or not os.path.isfile(self._cache_path):
            InfoBar.warning(
                "无可保存的文件", "预览资源不存在",
                position=InfoBarPosition.TOP_RIGHT, duration=3000,
                parent=self.window(),
            )
            return

        # video 文件被 QMediaPlayer 持有 必须先停才能移动
        self._release_players()
        os.makedirs(self._save_dir, exist_ok=True)
        target = os.path.join(self._save_dir, os.path.basename(self._cache_path))
        # 同名追加 _N
        if os.path.abspath(target) != os.path.abspath(self._cache_path):
            base, ext = os.path.splitext(target)
            i = 1
            while os.path.exists(target):
                target = f"{base}_{i}{ext}"
                i += 1
            try:
                shutil.move(self._cache_path, target)
            except Exception as e:
                InfoBar.error(
                    "保存失败", str(e),
                    position=InfoBarPosition.TOP_RIGHT, duration=5000,
                    parent=self.window(),
                )
                return
            self._cache_path = target
        self._saved_path = target
        self.save_btn.setText("已保存")
        self.save_btn.setEnabled(False)
        InfoBar.success(
            "已保存到素材库", os.path.basename(target),
            position=InfoBarPosition.TOP_RIGHT, duration=3500,
            parent=self.window(),
        )
        info(f"[materials] 保存 → {target}")

        # 保存完文件路径变了 重新加载到播放器 让用户继续预览
        try:
            if self._source == "netease":
                self.audio_player.load_file(target)
            else:
                self.video_player.setVideo(QUrl.fromLocalFile(os.path.abspath(target)))
        except Exception:
            pass

    def _release_players(self):
        """停止两个播放器 释放文件句柄"""
        try:
            self.audio_player._stop()
        except Exception:
            pass
        try:
            self.video_player.stop()
            self.video_player.player.setSource(QUrl())
        except Exception:
            pass

    def release(self):
        """从预览页切走时调 取消下载 + 释放播放器 + 清理未保存的缓存"""
        if self._worker and self._worker.isRunning():
            self._worker.cancel()
            self._worker.wait(1500)
        self._worker = None

        self._release_players()

        # 未保存的缓存文件直接删 不污染 .preview/
        if (self._cache_path and not self._saved_path
                and os.path.isfile(self._cache_path)):
            try:
                os.remove(self._cache_path)
            except OSError:
                pass
        self._cache_path = None
        self._saved_path = None


# ══════════════════════════════════════════════════════════════════════
#  主页面
# ══════════════════════════════════════════════════════════════════════

class MaterialsWidget(QWidget):
    """素材库子页面 顶层是 QStackedWidget(搜索 / 预览)"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("materialsInterface")
        self._search_worker: MaterialsSearchWorker | None = None
        self._save_dir = _paths.output_dir("node", "materials")
        self._preview_cache_dir = os.path.join(self._save_dir, ".preview")
        os.makedirs(self._preview_cache_dir, exist_ok=True)

        self._setup_ui()
        self._refresh_acceptance_state()
        QTimer.singleShot(0, self._maybe_show_disclaimer)

    # ── UI ────────────────────────────────────────────────────────────

    def _setup_ui(self):
        self._stack = QStackedWidget(self)

        self._search_view = self._build_search_view()
        self._preview_view = _PreviewView(self._save_dir, self)
        self._preview_view.back_requested.connect(self._exit_preview)

        self._stack.addWidget(self._search_view)
        self._stack.addWidget(self._preview_view)
        self._stack.setCurrentWidget(self._search_view)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self._stack)

    def _build_search_view(self) -> QWidget:
        view = QWidget(self)

        scroll = SmoothScrollArea(view)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("""
SmoothScrollArea { background: transparent; border: none; }
QWidget#materialsContainer { background: transparent; }
""")
        container = QWidget()
        container.setObjectName("materialsContainer")
        scroll.setWidget(container)

        root = QVBoxLayout(container)
        root.setContentsMargins(32, 28, 32, 32)
        root.setSpacing(20)

        # 头部
        header = QHBoxLayout()
        icon = IconWidget(FIF.DOWNLOAD, view)
        icon.setFixedSize(32, 32)
        title = TitleLabel("素材库", view)
        f = QFont(); f.setPointSize(20); f.setBold(True)
        title.setFont(f)
        header.addWidget(icon)
        header.addWidget(title)
        header.addStretch()
        root.addLayout(header)

        desc = BodyLabel(
            "搜索网易云音乐 / 哔哩哔哩公开素材 可在线预览后再保存到本地 保存的文件可直接喂给 Demucs / Whisper / RVC 等节点。",
            view)
        desc.setWordWrap(True)
        root.addWidget(desc)

        # 免责横幅
        self._banner = CardWidget(view)
        self._banner.setBorderRadius(8)
        self._banner.setStyleSheet(
            "CardWidget { background: rgba(255, 152, 0, 0.10); "
            "border: 1px solid rgba(255, 152, 0, 0.45); }"
        )
        bn = QHBoxLayout(self._banner)
        bn.setContentsMargins(16, 10, 16, 10)
        bn.setSpacing(10)
        self._banner_lbl = BodyLabel(_BANNER_TEXT, self._banner)
        self._banner_lbl.setWordWrap(True)
        bn.addWidget(self._banner_lbl, 1)
        self._view_disclaimer_btn = PushButton("查看完整声明", self._banner)
        self._view_disclaimer_btn.clicked.connect(self._open_disclaimer_dialog)
        bn.addWidget(self._view_disclaimer_btn)
        root.addWidget(self._banner)

        # 搜索行
        search_card = CardWidget(view)
        search_card.setBorderRadius(10)
        sl = QHBoxLayout(search_card)
        sl.setContentsMargins(16, 12, 16, 12)
        sl.setSpacing(10)

        self._source_combo = ComboBox(view)
        self._source_combo.addItems(["网易云音乐", "哔哩哔哩"])
        self._source_combo.setFixedWidth(140)
        sl.addWidget(self._source_combo)

        self._kw_edit = LineEdit(view)
        self._kw_edit.setPlaceholderText("输入歌名 / 视频关键词 (回车 = 搜索)")
        self._kw_edit.returnPressed.connect(self._on_search_clicked)
        sl.addWidget(self._kw_edit, 1)

        self._search_btn = PrimaryPushButton("搜索", view, FIF.SEARCH)
        self._search_btn.setFixedWidth(120)
        self._search_btn.clicked.connect(self._on_search_clicked)
        sl.addWidget(self._search_btn)

        root.addWidget(search_card)

        # 状态
        self._status_lbl = CaptionLabel("", view)
        self._status_lbl.setVisible(False)
        root.addWidget(self._status_lbl)

        # 结果区
        self._results_card = CardWidget(view)
        self._results_card.setBorderRadius(10)
        results_layout = QVBoxLayout(self._results_card)
        results_layout.setContentsMargins(16, 12, 16, 16)
        results_layout.setSpacing(8)
        results_layout.addWidget(StrongBodyLabel("搜索结果 (点击进入预览)", view))
        self._results_container = QVBoxLayout()
        self._results_container.setSpacing(8)
        results_layout.addLayout(self._results_container)
        results_layout.addStretch()
        self._results_card.setVisible(False)
        root.addWidget(self._results_card)

        root.addStretch()

        v = QVBoxLayout(view)
        v.setContentsMargins(0, 0, 0, 0)
        v.addWidget(scroll)
        return view

    # ── 免责声明 ─────────────────────────────────────────────────────

    def _maybe_show_disclaimer(self):
        if bool(get_field("materials.disclaimer_accepted", False)):
            return
        self._open_disclaimer_dialog(force_first_time=True)

    def _open_disclaimer_dialog(self, force_first_time: bool = False):
        dlg = DisclaimerDialog(self.window())
        if dlg.exec() == QDialog.DialogCode.Accepted:
            set_field("materials.disclaimer_accepted", True)
            info("[materials] 用户同意了素材功能免责声明")
            self._refresh_acceptance_state()
        else:
            if force_first_time:
                InfoBar.warning(
                    "已拒绝免责声明",
                    "搜索 / 预览 / 下载已禁用 可随时点击「查看完整声明」重新确认",
                    position=InfoBarPosition.TOP, duration=5000,
                    parent=self.window(),
                )
            self._refresh_acceptance_state()

    def _refresh_acceptance_state(self):
        accepted = bool(get_field("materials.disclaimer_accepted", False))
        self._search_btn.setEnabled(accepted)
        self._kw_edit.setEnabled(accepted)
        self._source_combo.setEnabled(accepted)
        if accepted:
            self._banner_lbl.setText(_BANNER_TEXT)
        else:
            self._banner_lbl.setText(
                "⛔ 您尚未接受免责声明 搜索 / 预览 / 下载已禁用 "
                "请点击右侧「查看完整声明」查看并同意条款"
            )

    # ── 搜索 ──────────────────────────────────────────────────────────

    def _selected_source(self) -> str:
        return "netease" if self._source_combo.currentIndex() == 0 else "bilibili"

    def _on_search_clicked(self):
        if not bool(get_field("materials.disclaimer_accepted", False)):
            self._open_disclaimer_dialog()
            return
        kw = self._kw_edit.text().strip()
        if not kw:
            InfoBar.warning(
                "请输入关键词", "搜索框是空的",
                position=InfoBarPosition.TOP, duration=2500,
                parent=self.window(),
            )
            return

        if self._search_worker and self._search_worker.isRunning():
            self._search_worker.requestInterruption()
            self._search_worker.wait(500)

        self._clear_results()
        self._status_lbl.setText(f"搜索 {self._source_combo.currentText()}: {kw} …")
        self._status_lbl.setVisible(True)
        self._search_btn.setEnabled(False)
        self._search_btn.setText("搜索中…")

        self._search_worker = MaterialsSearchWorker(
            self._selected_source(), kw, self)
        self._search_worker.results.connect(self._on_results)
        self._search_worker.error.connect(self._on_search_error)
        self._search_worker.finished.connect(self._on_search_done)
        self._search_worker.start()

    def _on_search_done(self):
        self._search_btn.setEnabled(True)
        self._search_btn.setText("搜索")

    def _on_search_error(self, msg: str):
        self._status_lbl.setText(msg)
        InfoBar.error(
            "搜索失败", msg,
            position=InfoBarPosition.TOP_RIGHT, duration=5000,
            parent=self.window(),
        )
        error(f"[materials] {msg}")

    def _on_results(self, items: list):
        if not items:
            self._status_lbl.setText("无结果")
            self._results_card.setVisible(False)
            return

        self._status_lbl.setText(f"找到 {len(items)} 条结果")
        source = self._selected_source()
        for it in items:
            card = _ResultCard(source, it, self)
            card.clicked_preview.connect(self._enter_preview)
            self._results_container.addWidget(card)
        self._results_card.setVisible(True)

    def _clear_results(self):
        while self._results_container.count():
            it = self._results_container.takeAt(0)
            w = it.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        self._results_card.setVisible(False)

    # ── 预览页 切换 ───────────────────────────────────────────────────

    def _enter_preview(self, source: str, item: dict):
        if not bool(get_field("materials.disclaimer_accepted", False)):
            self._open_disclaimer_dialog()
            return
        self._preview_view.start(source, item, self._preview_cache_dir)
        self._stack.setCurrentWidget(self._preview_view)

    def _exit_preview(self):
        self._preview_view.release()
        self._stack.setCurrentWidget(self._search_view)
