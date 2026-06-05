# coding:utf-8

import threading
import subprocess
import os
import sys
import traceback
import sys
import os

project_root = os.path.dirname(os.path.abspath(__file__))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

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
        print("\n用户中断 (Ctrl+C)")
        sys.exit(0)

    print("\n" + "=" * 60)
    print("未捕获的异常:")
    print("=" * 60)
    print(f"类型: {exc_type.__name__}")
    print(f"信息: {exc_value}")
    print("-" * 60)
    traceback.print_exception(exc_type, exc_value, exc_tb)
    print("=" * 60)


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
from workers.atool import resource_path
from widgets.audio_separation_widget import AudioSeparationWidget
# from workers.demucs_worker import DemucsWorker
from widgets.info_page import SystemInfoPage
from widgets.home_page import HomePage
from widgets.setting_page import SettingsWidget
from widgets.switch_pages import SwitchPage


class Widget(QWidget):
    def __init__(self, text: str, parent=None):
        super().__init__(parent=parent)
        self.label = SubtitleLabel(text, self)
        self.hBoxLayout = QHBoxLayout(self)

        setFont(self.label, 24)
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.hBoxLayout.addWidget(self.label, 1, Qt.AlignmentFlag.AlignCenter)
        self.setObjectName(text.replace(' ', '-'))

class Window(FluentWindow):
    def __init__(self):
        super().__init__()
        self.resize(1075, 726)
        self.setMinimumSize(860, 600)
        self.homeInterface = HomePage(self)
        self.systemInfoInterface = SystemInfoPage(self)
        self.settingInterface = SettingsWidget(self)
        self.audioSeparationInterface = SwitchPage("demucs",self)
        # self.worker = None
        # self.audioSeparationInterface.separationRequested.connect(
        #     self.start_separation)
        self.initNavigation()
        self.initWindow()

    # def start_separation(self, params):
    #     # 如果已有 worker 在运行，先取消
    #     if self.worker and self.worker.isRunning():
    #         self.worker.cancel()
    #         self.worker.wait()

    #     self.audioSeparationInterface.set_running(True)
    #     self.worker = DemucsWorker(params)
    #     self.worker.progress.connect(
    #         self.audioSeparationInterface.set_progress)
    #     self.worker.finished.connect(self.on_separation_finished)
    #     self.worker.error.connect(self.on_separation_error)
    #     self.worker.start()

    # def on_separation_finished(self, output_dir):
    #     self.audioSeparationInterface.set_progress(100, "完成！")
    #     self.audioSeparationInterface.reset_progress()
    #     self.audioSeparationInterface.set_running(False)
    #     # 添加历史记录（需要保存最后一次的参数）
    #     if hasattr(self, 'last_params'):
    #         self.audioSeparationInterface.add_history_task(
    #             self.last_params['input'],
    #             self.last_params['output']
    #         )
    #     InfoBar.success("完成", f"分离完成，文件保存在 {output_dir}", parent=self)

    # def on_separation_error(self, error_msg):
    #     self.audioSeparationInterface.reset_progress()
    #     self.audioSeparationInterface.set_running(False)
    #     InfoBar.error("错误", error_msg, parent=self)

    def Switch_color_tone(self):
        if isDarkTheme():
            setTheme(Theme.LIGHT)
        else:
            setTheme(Theme.DARK)

    def initNavigation(self):
        self.addSubInterface(self.homeInterface, FIF.HOME, '主页')
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
        print(f"窗口分辨率: {self.width()} × {self.height()}")

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
