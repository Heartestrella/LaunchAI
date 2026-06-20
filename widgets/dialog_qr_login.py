"""
widgets/dialog_qr_login.py
~~~~~~~~~~~~~~~~~~~~~~~~~~
统一的 QR 登录对话框 —— 网易云 / 哔哩哔哩 共用一份 UI。

UX 流程
=======
1. 弹窗后立即调对应平台的 ``qr_create / generate`` 取 (key, url)
2. 用 ``qrcode`` 库本地渲染 url 到 256x256 像素的二维码 → QPixmap
3. 启 ``QRPollWorker`` 后台线程每 2 秒调一次轮询 API
4. 收到 "已扫码 / 已确认" 状态时实时更新文案
5. 登录成功 → 把 cookie_string 写到 ``materials.<platform>_cookie`` 配置 → accept()
6. 过期 → 自动重新生成 + 刷新 QR
7. 用户点取消 / 关闭 → stop worker → reject()

依赖
====
- ``qrcode`` (pip 已装) 本地渲染二维码 不依赖外部图片服务
- ``Pillow`` 已在 qt_venv (iopaint 用到) qrcode 用它来 make_image
"""

from __future__ import annotations

import io
from typing import Optional

from PyQt6.QtCore import Qt, pyqtSignal, QThread
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QWidget,
)
from qfluentwidgets import (
    BodyLabel, CaptionLabel, StrongBodyLabel,
    PushButton, PrimaryPushButton, IconWidget,
    InfoBar, InfoBarPosition,
    FluentIcon as FIF,
)

from utils.configer import get_field, set_field
from logger import info, error


# ── 状态码常量 ────────────────────────────────────────────────────────

_NETEASE_STATES = {
    800: ("expired",  "二维码已过期，正在重新生成…"),
    801: ("waiting",  "请用网易云音乐 App 扫描二维码"),
    802: ("scanned",  "已扫码，请在手机端确认登录"),
    803: ("success",  "登录成功，正在保存凭据…"),
}
_BILI_STATES = {
    86101: ("waiting",  "请用 哔哩哔哩 App 扫描二维码"),
    86090: ("scanned",  "已扫码，请在手机端确认登录"),
    86038: ("expired",  "二维码已过期，正在重新生成…"),
    0:     ("success",  "登录成功，正在保存凭据…"),
}

_NETEASE_SUCCESS = 803
_BILI_SUCCESS = 0
_NETEASE_EXPIRED = 800
_BILI_EXPIRED = 86038


# ══════════════════════════════════════════════════════════════════════
#  后台轮询 worker
# ══════════════════════════════════════════════════════════════════════

class QRPollWorker(QThread):
    """2 秒一次轮询 QR 登录状态 收到成功 / 不可恢复错误后自然退出"""

    state = pyqtSignal(int, str)   # (raw_code, cookie_string)
    error = pyqtSignal(str)

    def __init__(self, platform: str, key: str, parent=None):
        super().__init__(parent)
        self.platform = platform
        self.key = key
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        ua = (get_field("materials.user_agent", "") or "").strip() or None
        proxy = (get_field("materials.proxy", "") or "").strip() or None
        try:
            while not self._stop:
                try:
                    if self.platform == "netease":
                        from utils._netease_weapi import qr_poll
                        code, cookie = qr_poll(self.key, user_agent=ua, proxy=proxy)
                    else:
                        from utils.material_fetcher import bili_qr_poll
                        code, cookie = bili_qr_poll(self.key)
                    self.state.emit(code, cookie)

                    if self.platform == "netease" and code == _NETEASE_SUCCESS:
                        return
                    if self.platform == "bilibili" and code == _BILI_SUCCESS:
                        return
                    if self.platform == "netease" and code == _NETEASE_EXPIRED:
                        return
                    if self.platform == "bilibili" and code == _BILI_EXPIRED:
                        return
                except Exception as e:
                    self.error.emit(str(e))
                    # 网络一过性错 继续重试 直到用户取消
                # 等 2 秒 中途响应 stop
                for _ in range(20):
                    if self._stop:
                        return
                    self.msleep(100)
        finally:
            pass


# ══════════════════════════════════════════════════════════════════════
#  对话框
# ══════════════════════════════════════════════════════════════════════

class QRLoginDialog(QDialog):
    """登录对话框 平台传 ``"netease"`` 或 ``"bilibili"``

    成功 (``QDialog.DialogCode.Accepted``) 时已经把 cookie 写到 config
    调用方再读 ``materials.<p>_cookie`` 即可
    """

    PLATFORMS = {
        "netease":  ("网易云音乐", "music.163.com"),
        "bilibili": ("哔哩哔哩",  "bilibili.com"),
    }

    def __init__(self, platform: str, parent=None):
        super().__init__(parent)
        if platform not in self.PLATFORMS:
            raise ValueError(f"unknown platform: {platform}")
        self.platform = platform
        self._worker: Optional[QRPollWorker] = None
        self._unikey: str = ""

        self.setModal(True)
        self.setWindowTitle(f"{self.PLATFORMS[platform][0]} 登录")
        self.resize(380, 540)

        self._setup_ui()
        # 弹窗显示后立刻生成首张 QR
        self._generate_qr()

    # ── UI ────────────────────────────────────────────────────────────

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(14)

        head = QHBoxLayout()
        icon = IconWidget(
            FIF.MUSIC if self.platform == "netease" else FIF.VIDEO, self)
        icon.setFixedSize(28, 28)
        head.addWidget(icon)
        head.addWidget(StrongBodyLabel(
            f"{self.PLATFORMS[self.platform][0]} 扫码登录", self))
        head.addStretch()
        root.addLayout(head)

        root.addWidget(CaptionLabel(
            f"凭据将仅保存在本机 ``configs/config.json`` 的 "
            f"``materials.{self.platform}_cookie`` 字段。", self
        ))

        # QR 大方块
        self.qr_label = QLabel(self)
        self.qr_label.setFixedSize(280, 280)
        self.qr_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.qr_label.setStyleSheet(
            "QLabel { background: white; border: 1px solid rgba(0,0,0,0.15); "
            "border-radius: 8px; }"
        )
        self.qr_label.setText("生成二维码中…")
        qr_row = QHBoxLayout()
        qr_row.addStretch()
        qr_row.addWidget(self.qr_label)
        qr_row.addStretch()
        root.addLayout(qr_row)

        self.status_lbl = BodyLabel("准备中…", self)
        self.status_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_lbl.setWordWrap(True)
        root.addWidget(self.status_lbl)

        # 按钮
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)
        btn_row.addStretch()
        self.refresh_btn = PushButton("刷新二维码", self, FIF.SYNC)
        self.refresh_btn.clicked.connect(self._generate_qr)
        self.cancel_btn = PushButton("取消", self)
        self.cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(self.refresh_btn)
        btn_row.addWidget(self.cancel_btn)
        root.addLayout(btn_row)

    # ── QR 生成 + 渲染 ──────────────────────────────────────────────

    def _generate_qr(self):
        """同步生成新 QR (网络极轻 < 1s 不必单独开线程)"""
        self._stop_worker()
        self.qr_label.setText("生成二维码中…")
        self.status_lbl.setText("正在向平台请求登录 key…")
        self.refresh_btn.setEnabled(False)

        ua = (get_field("materials.user_agent", "") or "").strip() or None
        proxy = (get_field("materials.proxy", "") or "").strip() or None
        try:
            if self.platform == "netease":
                from utils._netease_weapi import qr_create_unikey
                key, url = qr_create_unikey(user_agent=ua, proxy=proxy)
            else:
                from utils.material_fetcher import bili_qr_generate
                key, url = bili_qr_generate()
        except Exception as e:
            error(f"[qr-login {self.platform}] 生成失败 {e}")
            self.status_lbl.setText(f"生成二维码失败: {e}")
            self.refresh_btn.setEnabled(True)
            return

        self._unikey = key
        self._render_qr_pixmap(url)
        self.status_lbl.setText(_pretty_state(self.platform, "waiting"))
        self.refresh_btn.setEnabled(True)
        info(f"[qr-login {self.platform}] 新 QR key={key[:8]}…")

        # 启动轮询
        self._worker = QRPollWorker(self.platform, key, self)
        self._worker.state.connect(self._on_state)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _render_qr_pixmap(self, url: str):
        try:
            import qrcode
            qr = qrcode.QRCode(
                version=None,
                error_correction=qrcode.constants.ERROR_CORRECT_M,
                box_size=8, border=2,
            )
            qr.add_data(url)
            qr.make(fit=True)
            img = qr.make_image(fill_color="black", back_color="white")
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            pix = QPixmap()
            pix.loadFromData(buf.getvalue(), "PNG")
            pix = pix.scaled(
                self.qr_label.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self.qr_label.setPixmap(pix)
        except Exception as e:
            error(f"[qr-login] 渲染失败 {e}")
            self.qr_label.setText(f"二维码渲染失败: {e}")

    # ── 状态机 ────────────────────────────────────────────────────────

    def _on_state(self, code: int, cookie: str):
        states = (_NETEASE_STATES if self.platform == "netease"
                  else _BILI_STATES)
        meta = states.get(code)
        if meta is None:
            self.status_lbl.setText(f"未知状态 code={code}")
            return
        kind, msg = meta
        self.status_lbl.setText(msg)

        if kind == "expired":
            # 自动刷新一次
            self._generate_qr()
            return

        if kind == "success":
            # 落 cookie 并 accept
            if not cookie:
                self.status_lbl.setText("登录成功但未拿到 cookie 字段 异常 请重试")
                return
            set_field(f"materials.{self.platform}_cookie", cookie)
            info(f"[qr-login {self.platform}] 登录成功 已保存 cookie")
            InfoBar.success(
                f"{self.PLATFORMS[self.platform][0]} 登录成功",
                "已保存登录凭据",
                position=InfoBarPosition.TOP_RIGHT, duration=3000,
                parent=self.window(),
            )
            self.accept()

    def _on_error(self, msg: str):
        # 轮询途中网络瞬时错 不打断 只更新状态文字
        self.status_lbl.setText(f"轮询出错(将重试): {msg}")

    # ── 生命周期 ─────────────────────────────────────────────────────

    def _stop_worker(self):
        if self._worker and self._worker.isRunning():
            self._worker.stop()
            self._worker.wait(2000)
        self._worker = None

    def closeEvent(self, ev):
        self._stop_worker()
        super().closeEvent(ev)

    def reject(self):
        self._stop_worker()
        super().reject()


def _pretty_state(platform: str, kind: str) -> str:
    table = _NETEASE_STATES if platform == "netease" else _BILI_STATES
    for k, (k_name, msg) in table.items():
        if k_name == kind:
            return msg
    return "…"
