# tests/llm_demo.py
# 独立运行 LLMChatPage 的薄壳，方便 UI 迭代时不用启动整个 LaunchAI。
# 主体代码在 widgets/subpage/subpage_llm_chat.py。
#
# 跑：qt_venv/Scripts/python.exe tests/llm_demo.py

import os
import sys

# 让脚本能直接跑：把项目根加进 sys.path
_HERE = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_HERE)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from PyQt6.QtWidgets import QApplication
from qfluentwidgets import setTheme, Theme, setThemeColor

from widgets.subpage.subpage_llm_chat import LLMChatPage, BRAND


def main():
    app = QApplication(sys.argv)
    setTheme(Theme.DARK)
    setThemeColor(BRAND)

    win = LLMChatPage()
    win.setWindowTitle("LaunchAI · 模型对话 (UI Demo)")
    win.resize(1080, 820)
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
