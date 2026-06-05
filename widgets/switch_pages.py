import sys
from PyQt6.QtWidgets import QStackedLayout, QVBoxLayout, QWidget
from PyQt6.QtCore import Qt
from qfluentwidgets import ElevatedCardWidget, BodyLabel
from workers.pip_worker import PipWorker


class NoInstallWidget(ElevatedCardWidget):
    def __init__(self, package_name: str = "demucs", parent=None):
        super().__init__(parent=parent)
        self.setObjectName("NoInstallWidget")
        self.setMinimumSize(400, 200)

        main_layout = QVBoxLayout(self)
        main_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        label = BodyLabel(f"未安装 {package_name}\n请先安装所需依赖", self)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        main_layout.addWidget(label)


class SwitchPage(QWidget):
    def __init__(self, page_name: str = None, parent=None):
        super().__init__(parent=parent)
        self.stacked_layout = QStackedLayout(self)
        self.stacked_layout.setContentsMargins(0, 0, 0, 0)
        if page_name == "demucs":
            self.setObjectName("audioSeparationInterface")
            self.handel_demucs()

    def handel_demucs(self):
        from widgets.audio_separation_widget import AudioSeparationWidget
        self._real_page_0 = NoInstallWidget("demucs")
        self._real_page_1 = AudioSeparationWidget(self)
        if not PipWorker.is_package_installed("demucs"):
            self.stacked_layout.addWidget(self._real_page_0)
        else:
            self.stacked_layout.addWidget(self._real_page_1)
