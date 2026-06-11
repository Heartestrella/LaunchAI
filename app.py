# coding:utf-8

import threading
import subprocess
import os
import sys
import traceback
import sys
import os
from logger import info, warning, debug, error
from utils.atool import resource_path

project_root = os.path.dirname(os.path.abspath(__file__))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

current_path = os.environ.get("PATH", "")
ffmpeg_path = resource_path(os.path.join("resource", "ffmepg", "bin"))
if ffmpeg_path not in current_path:
    os.environ["PATH"] = ffmpeg_path + os.pathsep + current_path
# BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# if getattr(sys, 'frozen', False):
#     BASE_DIR = os.path.dirname(sys.executable)

# os.environ["PATH"] = os.path.join(
#     BASE_DIR, "resource", "ffmepg", "bin") + os.pathsep + os.environ["PATH"]
# result = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True)
# print(result.stdout[:100])


def global_exception_hook(exc_type, exc_value, exc_tb):
    """捕获所有未处理的异常并打印到控制台"""
    if exc_type == KeyboardInterrupt:
        warning("用户中断 (Ctrl+C)")
        sys.exit(0)

    error("\n" + "=" * 60)
    error("未捕获的异常:")
    error("=" * 60)
    error(f"类型: {exc_type.__name__}")
    error(f"信息: {exc_value}")
    error("-" * 60)
    error("".join(traceback.format_exception(exc_type, exc_value, exc_tb)))
    error("=" * 60)


sys.excepthook = global_exception_hook

threading.excepthook = lambda args: global_exception_hook(
    args.exc_type, args.exc_value, args.exc_traceback
)


# isort: off
# fmt: off
from PyQt6.QtGui import QFontDatabase, QFont
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QWidget, QHBoxLayout, QVBoxLayout
from qfluentwidgets import (NavigationItemPosition, setTheme, Theme, FluentWindow,
                            SubtitleLabel, setFont,
                            SettingCardGroup, SettingCard, FluentIcon as FIF, isDarkTheme,
                            InfoBar)
from widgets.subpage.subpage_demucs import AudioSeparationWidget
from workers.demucs_worker import DemucsWorker
from widgets.subpage.subpage_info_page import SystemInfoPage
from widgets.home_page import HomePage
from widgets.subpage.subpage_setting_page import SettingsWidget
from widgets.subpage.subpage_switch_pages import SwitchPage


class Widget(QWidget):
    def __init__(self, text: str, parent=None):
        super().__init__(parent=parent)
        self.label = SubtitleLabel(text, self)
        self.hBoxLayout = QHBoxLayout(self)

        setFont(self.label, 24)
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.hBoxLayout.addWidget(self.label, 1, Qt.AlignmentFlag.AlignCenter)
        # self.setObjectName(text.replace(' ', '-'))

class Window(FluentWindow):
    def __init__(self):
        super().__init__()
        self.resize(1075, 726)
        self.setMinimumSize(860, 600)
        self.homeInterface = HomePage(self)
        self.systemInfoInterface = SystemInfoPage(self)
        self.settingInterface = SettingsWidget(self)
        self.audioSeparationInterface = SwitchPage("demucs",self)
        self.ESRGANinterface = SwitchPage("ESRGAN",self)
        self.whisperInterface = SwitchPage("whisper",self)
        self.worker = None
        self.initNavigation()
        self.initWindow()


    def navigate_to(self, page_name: str):
        """导航到指定页面"""
        page_map = {
            "home": self.homeInterface,
            "setting": self.settingInterface,
            "system": self.systemInfoInterface,
            "demucs": self.audioSeparationInterface,
            "whisper": self.whisperInterface,
        }
        target = page_map.get(page_name)
        if target:
            self.switchTo(target)

    def Switch_color_tone(self):
        if isDarkTheme():
            setTheme(Theme.LIGHT)
        else:
            setTheme(Theme.DARK)

    def initNavigation(self):
        self.addSubInterface(self.homeInterface, FIF.HOME, '主页', )
        self.addSubInterface(self.systemInfoInterface, FIF.INFO, '系统信息')
        audio_parent = Widget('音频', self)
        audio_parent.setObjectName("audioParent")
        self.addSubInterface(audio_parent, FIF.MUSIC, '音频')

        self.addSubInterface(
            self.audioSeparationInterface,
            FIF.DEVELOPER_TOOLS,
            '音频分离 - Demucs',
            parent=audio_parent
        )
        self.addSubInterface(
            self.whisperInterface,
            FIF.MICROPHONE,
            '语音识别 - Whisper',
            parent=audio_parent
        )

        image_parent = Widget("图像",self)
        image_parent.setObjectName("imageParent")
        self.addSubInterface(image_parent, FIF.PHOTO, '图像')

        self.addSubInterface(
            self.ESRGANinterface,
            FIF.PHOTO,
            '图像超分 - Real-ESRGAN',
            parent=image_parent
        )


        self.addSubInterface(self.settingInterface, FIF.SETTING,
                             '设置', NavigationItemPosition.BOTTOM)

        self.navigationInterface.addItem(
            routeKey='bgmode',
            icon=FIF.BRIGHTNESS,
            text='灯泡',
            onClick=self.Switch_color_tone,
            position=NavigationItemPosition.BOTTOM,
        )

    def initWindow(self):
        self.setWindowTitle('LaunchAI')

        desktop = QApplication.primaryScreen().availableGeometry()
        w, h = desktop.width(), desktop.height()
        self.move(w//2 - self.width()//2, h//2 - self.height()//2)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        debug(f"窗口分辨率: {self.width()} × {self.height()}")


    # Worker Call
    def start_separation(self, params):

        self.current_task_params = params

        # 如果已有 worker 在运行，先取消
        if self.worker and self.worker.isRunning():
            self.worker.cancel()
            self.worker.wait()

        self.audioSeparationInterface._real_page_1.set_running(True)
        self.worker = DemucsWorker(params)
        self.worker.progress.connect(
            self.audioSeparationInterface._real_page_1.set_progress)
        self.worker.finished.connect(lambda output_dir: self.on_separation_finished(output_dir, params))
        self.worker.error.connect(self.on_separation_error)
        self.worker.start()

    def on_separation_finished(self, output_dir, params):
        self.audioSeparationInterface._real_page_1.set_progress(100, "完成！")
        self.audioSeparationInterface._real_page_1.reset_progress()
        self.audioSeparationInterface._real_page_1.set_running(False)

        self.audioSeparationInterface._real_page_1.add_history_task(
            params['input'],
            output_dir
        )

        InfoBar.success("完成", f"分离完成，文件保存在 {output_dir}", parent=self)

    def on_separation_error(self, error_msg):
        self.audioSeparationInterface._real_page_1.reset_progress()
        self.audioSeparationInterface._real_page_1.set_running(False)
        InfoBar.error("错误", error_msg, parent=self)




def main():
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    setTheme(Theme.DARK)
    font_id = QFontDatabase.addApplicationFont(
        resource_path(os.path.join("resource", "JetBrainsMapleMono-BoldItalic.ttf")))
    font_family = QFontDatabase.applicationFontFamilies(font_id)[0]

    font = QFont(font_family)
    font.setPointSize(10)
    app.setFont(font)
    window = Window()
    window.show()
    app.exec()


if __name__ == '__main__':
    main()
