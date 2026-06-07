import sys
from PyQt6.QtWidgets import QStackedLayout, QVBoxLayout, QWidget
from PyQt6.QtCore import Qt
from qfluentwidgets import InfoBar, BodyLabel, IconWidget, TitleLabel, PrimaryPushButton, FluentIcon as FIF
from workers.pip_worker import PipWorker
import re
from PyQt6.QtCore import QUrl, QTimer, pyqtSignal
from PyQt6.QtGui import QTextCursor, QDesktopServices
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QSplitter
from qfluentwidgets import (
    BodyLabel, TitleLabel, PrimaryPushButton, PushButton,
    IconWidget, FluentIcon as FIF, TextEdit
)


class LogTextEdit(TextEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptRichText(True)
        self.setReadOnly(True)

    def append_colored(self, html_text):
        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self.setTextCursor(cursor)

        # 检测并转换 URL 为超链接
        html_text = self._convert_urls_to_links(html_text)

        if "下载进度" in html_text:
            # 进度消息：覆盖当前行
            cursor.movePosition(
                QTextCursor.MoveOperation.StartOfLine, QTextCursor.MoveMode.KeepAnchor)
            cursor.removeSelectedText()
            cursor.insertHtml(html_text)
        else:
            # 普通消息：插入 HTML 并换行
            cursor.insertHtml(html_text + '<br>')

        self.ensureCursorVisible()

    def _convert_urls_to_links(self, text):
        """将文本中的 URL 转换为可点击的超链接"""
        # 匹配 http:// 或 https:// 开头的 URL
        url_pattern = r'(https?://[^\s<>"\'{}|\\^`\[\]]+)'

        def replace_url(match):
            url = match.group(1)
            # 截断过长的 URL 显示
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


class NoInstallWidget(QWidget):
    finish = pyqtSignal(bool, str)

    def __init__(self, package_name: str = "demucs", parent=None):
        super().__init__(parent=parent)
        self.package_name = package_name
        self.is_installing = False
        self.setObjectName("NoInstallWidget")
        self.setMinimumSize(800, 400)  # 增加最小宽度以容纳左右布局

        # 主布局
        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(20, 20, 20, 20)
        main_layout.setSpacing(20)

        # ========== 左侧：安装信息区域 ==========
        self.left_widget = QWidget()
        self.left_widget.setFixedWidth(300)
        left_layout = QVBoxLayout(self.left_widget)
        left_layout.setContentsMargins(20, 20, 20, 20)
        left_layout.setSpacing(20)
        left_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        left_layout.addStretch()
        # 图标
        self.icon_widget = IconWidget(FIF.MUSIC_FOLDER, self)
        self.icon_widget.setFixedSize(64, 64)
        left_layout.addWidget(
            self.icon_widget, alignment=Qt.AlignmentFlag.AlignCenter)

        # 标题
        self.title_label = TitleLabel(f"未安装 {package_name}", self)
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title_label.setWordWrap(True)
        left_layout.addWidget(self.title_label)

        # 描述
        description = ""
        button_text = "立即安装"
        if package_name == "demucs":
            description = "音频分离功能需要 demucs 模型支持\n请安装依赖后使用"
        elif package_name == "pytorch":
            description = "AIGC工具需要安装Pytorch 请前往设置安装后重启"
            button_text = "前往设置"
        self.desc_label = BodyLabel(
            description,
            self
        )
        self.desc_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.desc_label.setWordWrap(True)
        left_layout.addWidget(self.desc_label)

        # 安装按钮
        self.install_btn = PrimaryPushButton(button_text, self)
        self.install_btn.setFixedSize(160, 40)
        if package_name != "pytorch":
            self.install_btn.clicked.connect(self._start_install)
        else:
            self.install_btn.clicked.connect(self._open_settings)
        left_layout.addWidget(
            self.install_btn, alignment=Qt.AlignmentFlag.AlignCenter)

        # 手动安装提示
        self.manual_label = BodyLabel(f"pip install {package_name}", self)
        self.manual_label.setStyleSheet("color: #888888; font-size: 11px;")
        self.manual_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        left_layout.addWidget(self.manual_label)

        # 取消安装按钮（初始隐藏）
        self.cancel_btn = PushButton("取消安装", self)
        self.cancel_btn.setFixedSize(120, 35)
        self.cancel_btn.setVisible(False)
        self.cancel_btn.clicked.connect(self._cancel_install)
        left_layout.addWidget(
            self.cancel_btn, alignment=Qt.AlignmentFlag.AlignCenter)

        left_layout.addStretch()

        # ========== 右侧：日志输出区域 ==========
        self.right_widget = QWidget()
        self.right_widget.setVisible(False)  # 初始隐藏
        right_layout = QVBoxLayout(self.right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(10)

        # 日志标题栏
        log_header = QHBoxLayout()
        log_title = TitleLabel("安装日志", self)
        log_title.setStyleSheet("font-size: 16px;")
        log_header.addWidget(log_title)
        log_header.addStretch()

        # 清空日志按钮
        self.clear_log_btn = PushButton(FIF.DELETE, "清空", self)
        # self.clear_log_btn.setFixedSize(70, 28)
        self.clear_log_btn.clicked.connect(self._clear_log)
        log_header.addWidget(self.clear_log_btn)

        right_layout.addLayout(log_header)

        # 日志文本框
        self.log_text = LogTextEdit(self)
        self.log_text.setMinimumHeight(300)
        self.log_text.setPlaceholderText("安装日志将显示在这里...")
        self.log_text.setStyleSheet("""
            LogTextEdit {
                background-color: #1e1e1e;
                border: 1px solid #3c3c3c;
                border-radius: 8px;
                font-family: Consolas, monospace;
                font-size: 12px;
            }
        """)
        right_layout.addWidget(self.log_text)

        # 进度提示
        self.progress_label = BodyLabel("准备就绪", self)
        self.progress_label.setStyleSheet("color: #888888;")
        right_layout.addWidget(self.progress_label)

        # 将左右部件添加到主布局
        main_layout.addWidget(self.left_widget)
        main_layout.addWidget(self.right_widget, stretch=1)  # 右侧可拉伸

        # 存储安装进程引用
        self.install_process = None

    def _open_settings(self):
        main_window = self.window()
        main_window.navigate_to("setting")

    def _start_install(self):
        """开始安装"""
        if self.is_installing:
            return

        self.is_installing = True

        # 切换 UI 状态
        self.install_btn.setVisible(False)
        self.manual_label.setVisible(False)
        self.cancel_btn.setVisible(True)

        # 显示右侧日志区域
        self.right_widget.setVisible(True)

        # 调整左侧宽度为 250
        self.left_widget.setFixedWidth(280)

        self._append_log(f"🚀 开始安装 {self.package_name}...", "#4CAF50")
        self._append_log(f"📦 执行命令: pip install {self.package_name}", "#2196F3")

        self._install()

    def _install(self):
        if self.package_name == "demucs":
            self.pip = PipWorker(["demucs"], from_git=(
                True, "https://github.com/adefossez/demucs"))
            self.pip.output_signal.connect(self._append_log)
            self.pip.finished_signal.connect(self.on_install_finished)
            self.pip.start()

    def on_install_finished(self, success, message):
        """安装完成"""

        if success:
            InfoBar.success("安装成功", message,
                            parent=self.window(), duration=-1)
            self._install_finished()
        else:
            InfoBar.error("安装失败", message, parent=self.window(), duration=-1)

    def _simulate_step(self):
        """模拟安装步骤"""
        steps = [
            "正在下载依赖包...",
            "解析包依赖关系...",
            "下载 torch (可能需要几分钟)...",
            "下载 demucs 核心库...",
            "安装完成，正在验证..."
        ]

        if self.step_index < len(steps):
            self._append_log(f"✓ {steps[self.step_index]}", "#FFA500")
            self.progress_label.setText(
                f"进度: {int((self.step_index + 1) / len(steps) * 100)}%")
            self.step_index += 1
        else:
            self.step_timer.stop()
            self._install_finished()

    def _append_log(self, message: str, color: str = "#FFFFFF", format_html: bool = True):
        """添加日志"""
        if format_html:
            html = f'<span style="color:{color};">{message}</span>'
            self.log_text.append_colored(html)
        else:
            cursor = self.log_text.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.End)
            self.log_text.setTextCursor(cursor)
            self.log_text.append_colored(message)

        scrollbar = self.log_text.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def _clear_log(self):
        """清空日志"""
        self.log_text.clear()
        self._append_log("日志已清空", "#888888")

    def _cancel_install(self):
        """取消安装"""
        if self.install_process and self.install_process.state() != 0:
            self.install_process.terminate()
        self._append_log("⚠️ 用户取消了安装", "#FF9800")
        self._reset_ui()

    def _install_finished(self):
        """安装完成"""
        self._append_log("✅ 安装成功！5s后刷新界面...", "#4CAF50")
        self.progress_label.setText("✅ 安装完成")

        # 延迟后刷新界面
        QTimer.singleShot(5000, self._refresh_after_install)

    def _refresh_after_install(self):
        self.finish.emit(True, "安装完成")

        self._reset_ui()

        self.desc_label.setText("✅ 安装成功！\n请重新打开此页面使用音频分离功能")
        self.desc_label.setStyleSheet("color: #4CAF50;")

    def _reset_ui(self):
        """重置 UI 状态"""
        self.is_installing = False
        self.install_btn.setVisible(True)
        self.manual_label.setVisible(True)
        self.cancel_btn.setVisible(False)
        self.left_widget.setFixedWidth(300)


class SwitchPage(QWidget):
    def __init__(self, page_name: str = None, parent=None):
        super().__init__(parent=parent)
        self.page_name = page_name
        self.stacked_layout = QStackedLayout(self)
        self.stacked_layout.setContentsMargins(0, 0, 0, 0)
        self.stacked_layout.setSpacing(0)
        self.handel_pytorch()

    def handel_pytorch(self):
        if not PipWorker.is_package_installed("torch"):
            self._real_page_0 = NoInstallWidget("pytorch")
            self.setObjectName("SwitchPage")
            self.stacked_layout.addWidget(self._real_page_0)
        else:
            if self.page_name == "demucs":
                self.setObjectName("audioSeparationInterface")
                self.handel_demucs()

    def handel_demucs(self):
        from widgets.audio_separation_widget import AudioSeparationWidget
        self._real_page_0 = NoInstallWidget("demucs")
        self._real_page_1 = AudioSeparationWidget(self)
        self._real_page_1.separationRequested.connect(
            self.window().start_separation)
        self.stacked_layout.addWidget(self._real_page_0)
        self.stacked_layout.addWidget(self._real_page_1)
        self._real_page_0.finish.connect(lambda: self.switch_page(1))
        if not PipWorker.is_package_installed("demucs"):
            self.switch_page(0)
        else:
            self.switch_page(1)

    def switch_page(self, index: int = 0):
        self.stacked_layout.setCurrentIndex(index)
