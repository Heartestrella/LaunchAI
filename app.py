# coding:utf-8
# isort: off
# fmt: off


import threading
import time
import os
import sys
import traceback
import sys
import os

sys.path.insert(0, os.getcwd())
project_root = os.path.dirname(os.path.abspath(__file__))
if project_root not in sys.path:
    sys.path.insert(0, project_root)
from utils.atool import resource_path
from utils import paths as _paths
_paths.ensure_defaults()
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



from PyQt6.QtGui import QFontDatabase, QFont
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QWidget, QHBoxLayout, QVBoxLayout
from qfluentwidgets import (NavigationItemPosition, setTheme, Theme, FluentWindow,
                            SubtitleLabel, setFont,
                            SettingCardGroup, SettingCard, FluentIcon as FIF, isDarkTheme,
                            InfoBar)
from widgets.subpage.subpage_demucs import AudioSeparationWidget
from workers.demucs_worker import DemucsWorker
from widgets.home_page import HomePage
from widgets.subpage.subpage_setting_page import SettingsWidget
from widgets.subpage.subpage_switch_pages import SwitchPage, LazySwitchPage
from widgets.subpage.subpage_materials import MaterialsWidget
from widgets.subpage.subpage_model_hub import ModelHubWidget
from widgets.subpage.subpage_llm_chat import LLMChatPage
from widgets.subpage.subpage_health_page import HealthCheckPage
from node.node_editor import NodeEditorPage
from workers.pip_worker import PipWorker
from utils.configer import get_field
from logger import info, warning, debug, error

CUDA_DRIVERS = PipWorker.get_torch_devices()
info(f"获取到设备列表: {CUDA_DRIVERS}")
LAZY_STARTUP = bool(get_field("app.lazy_startup", False))
info(f"懒启动: {'开' if LAZY_STARTUP else '关'}")

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
        t_super = time.perf_counter()
        super().__init__()
        info(f"[init] super().__init__: {(time.perf_counter()-t_super)*1000:.0f} ms")

        self.resize(1316, 726)
        self.setMinimumSize(860, 600)

        _t = time.perf_counter()
        def mark(name):
            nonlocal _t
            now = time.perf_counter()
            info(f"[init] {name}: {(now-_t)*1000:.0f} ms")
            _t = now

        # 懒启动开关:audio / image 分组下八个工具页用 LazySwitchPage,
        # 启动只挂占位,首次点开才构造真页。home / llm_chat / 设置 / 素材库 /
        # 模型市场 / 节点编辑器 永远饿加载。
        _PageCls = LazySwitchPage if LAZY_STARTUP else SwitchPage

        self.homeInterface       = HomePage(self);                                          mark("HomePage")
        self.llmChatInterface    = LLMChatPage(self);                                       mark("LLMChatPage")
        self.settingInterface    = SettingsWidget(self);                                    mark("SettingsWidget")
        self.demucsinterface     = _PageCls("demucs",  CUDA_DRIVERS, self);                 mark("SwitchPage demucs")
        self.ESRGANinterface     = _PageCls("ESRGAN",  CUDA_DRIVERS, self);                 mark("SwitchPage ESRGAN")
        self.whisperInterface    = _PageCls("whisper", CUDA_DRIVERS, self);                 mark("SwitchPage whisper")
        self.rvcInterface        = _PageCls("rvc",     CUDA_DRIVERS, self);                 mark("SwitchPage rvc")
        self.gptsovitsInterface  = _PageCls("gptsovits", CUDA_DRIVERS, self);               mark("SwitchPage gptsovits")
        self.audiocraftInterface = _PageCls("audiocraft", CUDA_DRIVERS, self);              mark("SwitchPage audiocraft")
        self.materialsInterface  = MaterialsWidget(self);                                   mark("MaterialsWidget")
        self.modelHubInterface   = ModelHubWidget(self);                                    mark("ModelHubWidget")
        self.yoloInterface       = _PageCls("yolo",    CUDA_DRIVERS, self);                 mark("SwitchPage yolo")
        self.iopaintInterface    = _PageCls("iopaint", CUDA_DRIVERS, self);                 mark("SwitchPage iopaint")
        self.nodeEditorInterface = NodeEditorPage(cuda_drivers=CUDA_DRIVERS, parent=self);  mark("NodeEditorPage")
        self.healthInterface     = HealthCheckPage(CUDA_DRIVERS, self);                      mark("HealthCheckPage")

        self.worker = None
        self.initNavigation();  mark("initNavigation")
        self.initWindow();      mark("initWindow")


    def navigate_to(self, page_name: str):
        """导航到指定页面"""
        page_map = {
            "home": self.homeInterface,
            "setting": self.settingInterface,
            "demucs": self.demucsinterface,
            "whisper": self.whisperInterface,
            "rvc": self.rvcInterface,
            "gptsovits": self.gptsovitsInterface,
            "audiocraft": self.audiocraftInterface,
            "materials": self.materialsInterface,
            "model_hub": self.modelHubInterface,
            "node_editor": self.nodeEditorInterface,
            "llm_chat": self.llmChatInterface,
            "yolo": self.yoloInterface,
            "iopaint": self.iopaintInterface,
            "health": self.healthInterface,
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
        self.addSubInterface(self.nodeEditorInterface, FIF.EDIT, '节点编辑器')
        _llm_icon = getattr(FIF, "CHAT", None) \
            or getattr(FIF, "MESSAGE", None) \
            or FIF.EDIT
        self.addSubInterface(self.llmChatInterface, _llm_icon, '模型对话')
        audio_parent = Widget('音频', self)
        audio_parent.setObjectName("audioParent")
        self.addSubInterface(audio_parent, FIF.MUSIC, '音频')

        self.addSubInterface(
            self.demucsinterface,
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

        self.addSubInterface(
            self.rvcInterface,
            FIF.ALBUM,
            'AI 变声 - RVC',
            parent=audio_parent
        )

        # FIF 在不同 qfluentwidgets 版本里图标命名不一致，挑一个存在的
        _gptsovits_icon = getattr(FIF, "MEGAPHONE", None) \
            or getattr(FIF, "HEADPHONE", None) \
            or getattr(FIF, "ROBOT", FIF.MICROPHONE)
        self.addSubInterface(
            self.gptsovitsInterface,
            _gptsovits_icon,
            '语音合成 - GPT-SoVITS',
            parent=audio_parent
        )

        _audiocraft_icon = getattr(FIF, "MUSIC_FOLDER", None) \
            or getattr(FIF, "MUSIC", FIF.ALBUM)
        self.addSubInterface(
            self.audiocraftInterface,
            _audiocraft_icon,
            '音乐生成 - Audiocraft',
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

        self.addSubInterface(
            self.yoloInterface,
            FIF.FILTER,
            '目标检测 - YOLO',
            parent=image_parent
        )

        # FIF 在不同 qfluentwidgets 版本里图标命名不一致，挑一个存在的
        _iopaint_icon = getattr(FIF, "BRUSH", None) \
            or getattr(FIF, "PALETTE", None) \
            or getattr(FIF, "PHOTO", FIF.EDIT)
        self.addSubInterface(
            self.iopaintInterface,
            _iopaint_icon,
            '图像修复 - IOPaint',
            parent=image_parent
        )

        self.addSubInterface(
            self.materialsInterface,
            FIF.DOWNLOAD,
            '素材库',
        )

        _modelhub_icon = getattr(FIF, "CLOUD_DOWNLOAD", None) \
            or getattr(FIF, "LIBRARY", None) \
            or FIF.DOWNLOAD
        self.addSubInterface(
            self.modelHubInterface,
            _modelhub_icon,
            '模型市场',
        )

        _health_icon = getattr(FIF, "CERTIFICATE", None) \
            or getattr(FIF, "HEART", None) \
            or FIF.DEVELOPER_TOOLS
        self.addSubInterface(self.healthInterface, _health_icon,
                             '自检 / 运行测试', NavigationItemPosition.BOTTOM)

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

        self.demucsinterface._real_page_1.set_running(True)
        self.worker = DemucsWorker(params)
        self.worker.progress.connect(
            self.demucsinterface._real_page_1.set_progress)
        self.worker.finished.connect(lambda output_dir: self.on_separation_finished(output_dir, params))
        self.worker.error.connect(self.on_separation_error)
        self.worker.start()

    def on_separation_finished(self, output_dir, params):
        self.demucsinterface._real_page_1.set_progress(100, "完成！")
        self.demucsinterface._real_page_1.reset_progress()
        self.demucsinterface._real_page_1.set_running(False)

        self.demucsinterface._real_page_1.add_history_task(
            params['input'],
            output_dir
        )

        InfoBar.success("完成", f"分离完成，文件保存在 {output_dir}", parent=self)

    def on_separation_error(self, error_msg):
        self.demucsinterface._real_page_1.reset_progress()
        self.demucsinterface._real_page_1.set_running(False)
        InfoBar.error("错误", error_msg, parent=self)




def main():
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    setTheme(Theme.DARK)
    font_id = QFontDatabase.addApplicationFont(
        resource_path(os.path.join("resource", "JetBrainsMapleMono-BoldItalic.ttf")))
    if font_id != -1:
        font_family = QFontDatabase.applicationFontFamilies(font_id)[0]
        font = QFont(font_family)
        font.setPointSize(10)
        # 不强制 Bold/Italic，让控件按需设置
        font.setWeight(QFont.Weight.Normal)
        font.setItalic(False)
        app.setFont(font)
    else:
        warning("字体加载失败，使用系统默认字体")
    window = Window()
    window.show()

    # 可选:按配置自启动 HTTP API 服务(api_server.autostart 且已设 Key)。
    # 在 QApplication 之后启动,server 里实例化 Qt worker 才安全。
    try:
        if bool(get_field("api_server.autostart", False)) \
                and (get_field("api_server.api_key", "") or "").strip():
            from server.api_server import ApiServerManager
            ApiServerManager.instance().start(
                get_field("api_server.host", "127.0.0.1") or "127.0.0.1",
                int(get_field("api_server.port", 8765) or 8765))
    except Exception as e:
        warning(f"[api] 自启动失败: {e}")

    app.exec()


if __name__ == '__main__':
    main()
