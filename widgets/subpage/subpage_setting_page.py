import sys
from logger import info, warning, debug, error
from PyQt6.QtGui import QDesktopServices, QTextCursor
from PyQt6.QtCore import QUrl
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout
from qfluentwidgets import SettingCard, FluentIcon as FIF, ElevatedCardWidget, TextEdit, ComboBox, PushButton, ToolTipFilter, IconWidget, MessageBox, InfoBar
from workers.pip_worker import PipWorker
import subprocess
import re
CUDA_MAP = {"CPU": "cpu", "CUDA11.8": "118", "CUDA12.4": "124",
            "CUDA12.6": "126", "CUDA13.0": "130", "CUDA13.2": "132"}
MIRROR_MAP = {"阿里云镜像源": "https://mirrors.aliyun.com/pytorch-wheels",
              "清华大学镜像源": "https://pypi.tuna.tsinghua.edu.cn/simple",
              "官方Pytorch源": "https://download.pytorch.org/whl"}


class HelpIcon(IconWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setIcon(FIF.HELP)
        self.setFixedSize(16, 16)
        self.setToolTip("点击查看详细教程")

    def mousePressEvent(self, event):
        QDesktopServices.openUrl(
            QUrl("https://blog.csdn.net/taotao_guiwang/article/details/156749455"))
        super().mousePressEvent(event)


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


class InstallPyTorchCard(ElevatedCardWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.reinstall = False
        # self.setMaximumHeight(180)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 16, 22, 16)
        layout.setSpacing(20)

        python_version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
        self.python_card = SettingCard(
            FIF.CODE,
            "Python 版本",
            python_version,
            parent=self
        )
        layout.addWidget(self.python_card)

        torch_version = self._get_pip_packages().get("torch", "未安装")
        Button_Text = "安装"
        if torch_version != "未安装":
            Button_Text = "更改版本"
            self.reinstall = True
        self.torch_card = SettingCard(
            FIF.CLOUD,
            "PyTorch 版本",
            torch_version,
            parent=self
        )

        btn_row = QHBoxLayout()

        self.comboBox = ComboBox()
        ver_list = ["CPU", "CUDA11.8", "CUDA12.4",
                    "CUDA12.6",  "CUDA13.0", "CUDA13.2"]
        self.comboBox.addItems(ver_list)
        self.comboBox.setPlaceholderText("选择需要的版本")

        self.combo_help_icon = HelpIcon(self)
        self.combo_help_icon.setFixedSize(16, 16)
        self.combo_help_icon.setToolTip(
            "我应该选择什么版本?\n10-30系列可用万金油CUDA11.8 其中10系必须使用11.8\n40系列推荐CUDA12.4+\n50系必须使用CUDA12.4+\n详细参考文章:https://blog.csdn.net/taotao_guiwang/article/details/156749455 \n (点击图标跳转至文章)")
        self.combo_help_icon.installEventFilter(
            ToolTipFilter(self.combo_help_icon))
        self.combo_help_icon.mousePressEvent = lambda event: QDesktopServices.openUrl(
            QUrl("https://blog.csdn.net/taotao_guiwang/article/details/156749455"))

        self.mirror_comboBox = ComboBox()
        self.mirror_list = ["阿里云镜像源",  "官方Pytorch源",]
        self.mirror_comboBox.addItems(self.mirror_list)

        self.install_btn = PushButton(Button_Text)
        self.install_btn.clicked.connect(self.on_install_clicked)
        btn_row.addWidget(self.comboBox)
        btn_row.addWidget(self.combo_help_icon)
        btn_row.addWidget(self.mirror_comboBox)
        btn_row.addWidget(self.install_btn)

        self.terminal_widget = QWidget(self)
        self.terminal_widget.setVisible(False)
        terminal_layout = QVBoxLayout(self.terminal_widget)
        terminal_layout.setContentsMargins(0, 10, 0, 0)
        self.terminal_text = LogTextEdit()
        self.terminal_text.setAcceptRichText(True)
        self.terminal_text.setReadOnly(True)
        self.terminal_text.setMaximumHeight(200)
        self.terminal_text.setStyleSheet("""
            TextEdit {
                background-color: #1e1e1e;
                color: #d4d4d4;
                font-family: Consolas, monospace;
                font-size: 11px;
                border-radius: 4px;
            }
        """)
        terminal_layout.addWidget(self.terminal_text)

        layout.addWidget(self.torch_card)
        layout.addLayout(btn_row)
        layout.addWidget(self.terminal_widget)

    @staticmethod
    def _get_pip_packages():
        """获取 Python 包信息"""
        targets = {
            "torch", "torchvision", "torchaudio"
        }
        result = {}
        try:
            output = subprocess.check_output(
                [sys.executable, "-m", "pip", "list", "--format=freeze"],
                timeout=10, stderr=subprocess.DEVNULL
            ).decode("utf-8", errors="ignore")
            for line in output.splitlines():
                if "==" in line:
                    name, ver = line.split("==", 1)
                    if name.lower() in targets:
                        result[name.lower()] = ver
        except Exception:
            pass
        return result

    def on_install_clicked(self,):
        selected_text = self.comboBox.currentText()
        selected_mirror = self.mirror_comboBox.currentText()

        cuda_ver = CUDA_MAP.get(selected_text, "cpu")

        msg_box = MessageBox(
            "确认安装",
            f"CUDA 版本: {cuda_ver}\n镜像源: {selected_mirror}\n\n是否继续安装？",
            self.window()
        )
        msg_box.yesButton.setText("继续")
        msg_box.cancelButton.setText("取消")

        if msg_box.exec():
            self.start_install(cuda_ver, selected_mirror)

    def start_install(self, cuda_ver: int, mirror: str):
        """开始安装 PyTorch"""
        info(f"开始安装 - CUDA: {cuda_ver}, 镜像源: {mirror}")
        base_mirror_url = MIRROR_MAP.get(mirror, None)
        mirror_url = f"{base_mirror_url}/cu{cuda_ver}"
        packages = ["torch", "torchvision", "torchaudio"]
        if int(cuda_ver) == 132:
            packages.remove("torchaudio")
        worker = PipWorker(packages, mirror_url,
                           is_torch=cuda_ver, force=self.reinstall)
        worker.output_signal.connect(self.terminal_text.append_colored)
        worker.finished_signal.connect(self.on_install_finished)

        worker.start()
        # 显示终端并改变按钮
        self.terminal_widget.setVisible(True)
        self.install_btn.setText("取消")
        self.install_btn.disconnect()
        self.install_btn.clicked.connect(self.cancel_install)
        self.terminal_widget.setVisible(True)

    def cancel_install(self):
        """取消安装"""
        if hasattr(self, 'worker') and self.worker.isRunning():
            self.worker.terminate()
            self.worker.wait()
            self.worker = None

        if self.install_btn:
            self.install_btn.setText("安装")
            try:
                self.install_btn.clicked.disconnect()
            except:
                pass
            self.install_btn.clicked.connect(self.on_install_clicked)
            self.install_btn.setEnabled(True)

        self.on_install_finished(False, "用户取消安装")

    def on_install_finished(self, success, message):
        """安装完成"""

        self.install_btn.setText("安装")
        self.install_btn.disconnect()
        self.install_btn.clicked.connect(self.on_install_clicked)
        self.install_btn.setEnabled(True)

        if success:
            InfoBar.success("安装成功", message,
                            parent=self.window(), duration=-1)
        else:
            InfoBar.error("安装失败", message, parent=self.window(), duration=-1)

    def append_html(self, html_text):
        """追加 HTML 格式的日志"""
        cursor = self.terminal_text.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        self.terminal_text.setTextCursor(cursor)
        self.terminal_text.insertHtml(html_text)
        self.terminal_text.ensureCursorVisible()


class SettingsWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self.setObjectName("settingsInterface")
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(30, 30, 30, 30)

        self.installer = InstallPyTorchCard(self)
        layout.addWidget(self.installer)
        layout.addStretch()
