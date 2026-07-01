"""widgets/subpage/subpage_health_page.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
「自检 / 运行测试」页面。

对每个已安装工具做端到端真实运行(逻辑在 server/health_check.py,子进程执行),
逐行展示流式日志并把每个工具的通过 / 失败 / 未安装状态回填到对应行。

卡片 / 布局风格镜像 subpage_setting_page.py(ExpandGroupSettingCard + 自定义行 +
彩色 CaptionLabel 状态 + apply_app_font)。
"""
import os
import sys

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QFrame
from qfluentwidgets import (
    FluentIcon as FIF, IconWidget, TitleLabel, BodyLabel, CaptionLabel,
    PrimaryPushButton, PushButton, SingleDirectionScrollArea,
    ExpandGroupSettingCard, InfoBar,
)

from server.tool_runners import TOOLS
from workers.health_worker import HealthCheckWorker, InstallProbeWorker
from widgets.subpage.subpage_setting_page import apply_app_font
from widgets.log_text_edit import LogTextEdit
from logger import info

# 每个工具的导航图标(FIF 版本命名不一,统一兜底)
_TOOL_ICON = {
    "demucs":     FIF.DEVELOPER_TOOLS,
    "whisper":    FIF.MICROPHONE,
    "realesrgan": FIF.PHOTO,
    "rvc":        FIF.ALBUM,
    "gptsovits":  getattr(FIF, "MEGAPHONE", FIF.MICROPHONE),
    "audiocraft": getattr(FIF, "MUSIC_FOLDER", FIF.MUSIC),
    "yolo":       FIF.FILTER,
    "iopaint":    getattr(FIF, "BRUSH", FIF.EDIT),
}

_GREEN, _RED, _GREY, _BLUE, _AMBER = (
    "#4CAF50", "#F44336", "#9E9E9E", "#4FC3F7", "#FFB300")

# FIF 图标名在不同 qfluentwidgets 版本里不一致,统一兜底
_IC_PLAY   = getattr(FIF, "PLAY", None) or getattr(FIF, "SEND", FIF.RIGHT_ARROW)
_IC_STOP   = getattr(FIF, "CANCEL", None) or getattr(FIF, "CLOSE", FIF.CANCEL)
_IC_SYNC   = getattr(FIF, "SYNC", None) or getattr(FIF, "UPDATE", FIF.SYNC)
_IC_CARD   = (getattr(FIF, "CERTIFICATE", None)
              or getattr(FIF, "HEART", None) or FIF.DEVELOPER_TOOLS)
_IC_ROW    = getattr(FIF, "APPLICATION", None) or FIF.DEVELOPER_TOOLS


class _ToolCard(ExpandGroupSettingCard):
    """一张卡容纳所有工具行。每行:图标 + 名称 + 状态 + 「测试」按钮。"""

    def __init__(self, on_test, parent=None):
        icon = _IC_CARD
        super().__init__(icon, "工具运行测试",
                         "用内置极小样本真跑一遍,产出文件才算通过;"
                         "变声 / 语音合成需自备权重,仅做启动检查。", parent)
        self.viewLayout.setContentsMargins(0, 0, 0, 0)
        self.viewLayout.setSpacing(0)
        self._on_test = on_test
        self.rows: dict[str, dict] = {}
        for tool, meta in TOOLS.items():
            self.addGroupWidget(self._build_row(tool, meta["display"]))

    def _build_row(self, tool: str, display: str) -> QWidget:
        item = QWidget(self)
        lay = QHBoxLayout(item)
        lay.setContentsMargins(20, 12, 20, 12)
        lay.setSpacing(10)

        icw = IconWidget(_TOOL_ICON.get(tool, _IC_ROW), self)
        icw.setFixedSize(20, 20)
        lay.addWidget(icw)

        name_lbl = BodyLabel(display, self)
        name_lbl.setMinimumWidth(180)
        lay.addWidget(name_lbl)

        status_lbl = CaptionLabel("待测", self)
        status_lbl.setStyleSheet(f"color:{_GREY};")
        lay.addWidget(status_lbl, 1)

        btn = PrimaryPushButton("测试", self, _IC_PLAY)
        btn.setFixedWidth(96)
        btn.clicked.connect(lambda _=False, t=tool: self._on_test(t))
        lay.addWidget(btn)

        self.rows[tool] = {"status": status_lbl, "btn": btn}
        return item

    def set_status(self, tool: str, text: str, color: str):
        row = self.rows.get(tool)
        if row:
            row["status"].setText(text)
            row["status"].setStyleSheet(f"color:{color};")

    def set_buttons_enabled(self, enabled: bool):
        for row in self.rows.values():
            row["btn"].setEnabled(enabled)


class HealthCheckPage(QWidget):
    def __init__(self, cuda_drivers: dict | None = None, parent=None):
        super().__init__(parent)
        self.setObjectName("healthCheckInterface")
        self._cuda_drivers = cuda_drivers or {}
        self._worker = None
        self._probe = None
        # 已探测到的「已安装」工具集,决定按钮启用状态。首次探测前假定全装(乐观),
        # probe 完成后收敛到真实值,后续测试跑完 _set_running 也据此重启用。
        self._installed: set[str] = set(TOOLS.keys())

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = SingleDirectionScrollArea(self, orient=Qt.Orientation.Vertical)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("QScrollArea{background:transparent;border:none}")
        scroll.viewport().setStyleSheet("background:transparent")

        content = QWidget(scroll)
        content.setStyleSheet("background:transparent")
        layout = QVBoxLayout(content)
        layout.setSpacing(12)
        layout.setContentsMargins(30, 30, 30, 30)

        layout.addWidget(TitleLabel("自检 / 运行测试", content))
        layout.addWidget(BodyLabel(
            "检测已安装的 AI 工具当前能否真正跑通一遍,快速定位「装了却跑不起来」的依赖问题。",
            content))

        bar = QHBoxLayout()
        bar.setSpacing(10)
        self.test_all_btn = PrimaryPushButton("测试全部已安装", content, _IC_PLAY)
        self.test_all_btn.clicked.connect(self._on_test_all)
        bar.addWidget(self.test_all_btn)
        self.stop_btn = PushButton("停止", content, _IC_STOP)
        self.stop_btn.clicked.connect(self._on_stop)
        self.stop_btn.setEnabled(False)
        bar.addWidget(self.stop_btn)
        self.refresh_btn = PushButton("刷新安装状态", content, _IC_SYNC)
        self.refresh_btn.clicked.connect(self._probe_installs)
        bar.addWidget(self.refresh_btn)
        bar.addStretch()
        layout.addLayout(bar)

        self.card = _ToolCard(self._on_test_one, content)
        layout.addWidget(self.card)

        self.log = LogTextEdit(content)
        self.log.setMinimumHeight(220)
        layout.addWidget(self.log)

        layout.addStretch()
        scroll.setWidget(content)
        apply_app_font(content)

        self._stack_content = content
        outer.addWidget(scroll)

        self._probe_installs()

    # ── 设备选择 ────────────────────────────────────────────────────────────
    def _device(self) -> str | None:
        for v in self._cuda_drivers.values():
            if isinstance(v, str) and v.startswith("cuda"):
                return v
        return "cpu"

    # ── 安装状态探测 ────────────────────────────────────────────────────────
    def _probe_installs(self):
        if self._probe and self._probe.isRunning():
            return
        self.refresh_btn.setEnabled(False)
        self._probe = InstallProbeWorker(self)
        self._probe.done.connect(self._on_probe_done)
        self._probe.start()

    def _on_probe_done(self, rows: list):
        self._installed = {r.get("tool") for r in rows if r.get("installed")}
        for r in rows:
            tool = r.get("tool")
            installed = bool(r.get("installed"))
            row = self.card.rows.get(tool)
            if installed:
                self.card.set_status(tool, "已安装 · 待测", _GREY)
            else:
                self.card.set_status(tool, "未安装", _GREY)
            if row:
                # 对称启停:已装的必须启用(修复「刷新后按钮不可用」),没装的禁用。
                row["btn"].setEnabled(installed)
        self.refresh_btn.setEnabled(True)

    # ── 触发测试 ────────────────────────────────────────────────────────────
    def _on_test_one(self, tool: str):
        self._start([tool])

    def _on_test_all(self):
        self._start(list(TOOLS.keys()))

    def _start(self, tools: list[str]):
        if self._worker and self._worker.isRunning():
            InfoBar.warning("正在测试", "请等待当前测试结束或点击停止", parent=self)
            return
        info(f"开始自检: {tools}")
        self.log.clear()
        self._set_running(True)
        for t in tools:
            self.card.set_status(t, "测试中…", _BLUE)

        self._worker = HealthCheckWorker(tools, device=self._device(), parent=self)
        self._worker.output.connect(self.log.append_colored)
        self._worker.tool_started.connect(
            lambda t: self.card.set_status(t, "测试中…", _BLUE))
        self._worker.tool_result.connect(self._on_tool_result)
        self._worker.error.connect(self._on_error)
        self._worker.finished.connect(self._on_finished)
        self._worker.start()

    def _on_tool_result(self, tool: str, ok: bool, detail: str):
        row = self.card.rows.get(tool)
        mode = "run" if tool in ("demucs", "whisper", "realesrgan", "yolo",
                                 "audiocraft", "iopaint") else "launch"
        if "未安装" in detail:
            self.card.set_status(tool, "未安装", _GREY)
            if row:
                row["btn"].setEnabled(False)
        elif ok and mode == "launch":
            self.card.set_status(tool, "启动正常 · 需模型", _AMBER)
        elif ok:
            self.card.set_status(tool, "通过", _GREEN)
        else:
            self.card.set_status(tool, "失败", _RED)

    def _on_error(self, msg: str):
        self.log.append_colored(f'<span style="color:{_RED};">{msg}</span>')

    def _on_finished(self, summary: str):
        self._set_running(False)
        InfoBar.success("自检完成", summary, parent=self, duration=4000)

    def _on_stop(self):
        if self._worker and self._worker.isRunning():
            self._worker.cancel()
            self.stop_btn.setEnabled(False)

    def _set_running(self, running: bool):
        self.test_all_btn.setEnabled(not running)
        self.refresh_btn.setEnabled(not running)
        self.stop_btn.setEnabled(running)
        if running:
            self.card.set_buttons_enabled(False)
        else:
            # 跑完只重启用「已安装」工具的按钮,未安装的保持禁用
            for tool, row in self.card.rows.items():
                row["btn"].setEnabled(tool in self._installed)
