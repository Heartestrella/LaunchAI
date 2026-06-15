# LaunchAI · 奇点

**简体中文** · [English](./en.md)

> **AI ALL IN ONE** —— 一个基于 PyQt6 + Fluent Design 的 AI 工具一站式启动器

将主流开源 AI 工具（音频分离、语音识别、图像超分、目标检测 …）整合到同一个桌面应用中，提供统一的安装管理、参数面板和运行界面，免去逐个搭建环境、记忆命令行参数的麻烦。

- 项目地址：<https://github.com/Heartestrella/LaunchAI>
- 当前版本：**0.0.0 Build 1**（开发预览，接口可能随时变动）
- 平台：Windows（已在 Windows 11 + RTX 30 系列验证）

> ⚠️ 本项目为个人开源项目，仅供学习与个人使用，**请勿用于任何盈利性用途**。本启动器永远免费，请认准 GitHub @Heartestrella 官方仓库。

---

## 功能特性

- **统一启动器界面**：基于 [QFluentWidgets](https://qfluentwidgets.com/) 的 Fluent Design 风格，深色 / 浅色主题一键切换。
- **首页 / 系统信息**：CPU、内存、磁盘、GPU、CUDA 驱动、关键依赖版本一览。
- **自动安装与依赖管理**：每个 AI 工具进入时若未检测到依赖会自动进入安装页，支持
  - Python 包安装（pip + 自定义索引源）
  - 阿里云 PyTorch wheel 直链下载（带本地 `_cache/` 复用）
  - GitHub 仓库克隆（[dulwich](https://www.dulwich.io/) 实现，无需系统 git）+ Git 加速镜像自动探测/排序
  - 针对特定项目的修复脚本（如 `basicsr` 与新版 torchvision 的兼容补丁）
- **节点编辑器（预览版）**：类似 ComfyUI 的可视化连线工作流，按 `Shift+A` 调出节点选择器。当前仍在开发中。
- **打包友好**：所有资源访问走 `utils.atool.resource_path`，兼容 PyInstaller `sys._MEIPASS`。

---

## 已支持的 AI 工具

| 分类     | 工具            | 用途         | 来源 |
| -------- | --------------- | ------------ | ---- |
| 音频     | **Demucs**      | 音轨分离（人声 / 鼓 / 贝斯 / 其他） | <https://github.com/facebookresearch/demucs> |
| 音频     | **OpenAI Whisper** | 语音识别 / 字幕生成 | <https://github.com/openai/whisper> |
| 音频     | **RVC** (AI 变声 / 翻唱) | 基于第三方封装 [`rvc-inferpy`](https://pypi.org/project/rvc-inferpy/)（Applio 生态）调用 [RVC-Project](https://github.com/RVC-Project/Retrieval-based-Voice-Conversion-WebUI) 推理 | <https://github.com/IAHispano/Applio> |
| 图像     | **Real-ESRGAN** | 图像 / 动漫超分辨率（内置 `realesrgan-ncnn-vulkan`，无需安装即可使用） | <https://github.com/xinntao/Real-ESRGAN> |
| 图像     | **YOLO (Ultralytics)** | 目标检测（图像，视频开发中） | <https://github.com/ultralytics/ultralytics> |

> 添加新工具的方式：编写一个 `QThread` Worker、一个功能子页面，在 `widgets/subpage/subpage_switch_pages.py` 中增加 `handel_xxx` 分支，并在 `app.py` 中注册导航即可。

---

## 环境要求

- **操作系统**：Windows 10 / 11（路径处理与捆绑二进制目前面向 Windows）
- **Python**：3.10（推荐使用仓库内置的 `qt_venv/`）
- **GPU**：NVIDIA 显卡 + CUDA（可选，无 GPU 时部分工具会回退到 CPU 模式）
- **磁盘**：建议预留 10 GB+ 用于模型与依赖

仓库自带 `qt_venv/` 虚拟环境与若干二进制依赖：

```
resource/ffmepg/bin/                  ← FFmpeg（启动时自动加入 PATH）
resource/realesrgan-ncnn-vulkan/      ← Real-ESRGAN NCNN 推理可执行文件
resource/JetBrainsMapleMono-*.ttf     ← UI 字体
qt_venv/                              ← 内置 Python 3.10 虚拟环境
```

---

## 运行

```bash
qt_venv\Scripts\python.exe app.py
```

> ⚠️ 必须使用 `qt_venv` 内的解释器：所有 Worker 通过 `sys.executable -m <module>` 调用对应 CLI，启动器外的 Python 解释器无法找到依赖。

首次进入某个 AI 工具页时，若依赖未安装，会弹出安装引导页，点击安装即可在内置 Python 环境中自动完成依赖部署。

---

## 项目结构

```
LaunchAI/
├── app.py                    # 入口：主窗口 + 导航 + 全局异常钩子
├── logger.py                 # 带 ANSI 颜色的统一日志（from logger import info,...）
├── configs/config.json       # 系统快照 + 运行时配置（含 git 加速镜像列表）
├── widgets/
│   ├── home_page.py          # 首页（横幅、快捷文件夹、公告）
│   └── subpage/              # 各功能子页面
│       ├── subpage_switch_pages.py   # 安装页 ↔ 功能页 的二选一容器
│       ├── subpage_demucs.py         # Demucs UI
│       ├── subpage_ESRGAN.py         # Real-ESRGAN UI
│       ├── subpage_whisper.py        # Whisper UI
│       ├── subpage_yolov.py          # YOLO UI
│       ├── subpage_info_page.py      # 系统信息页
│       └── subpage_setting_page.py   # 设置页
├── workers/                  # 全部继承 QThread，对外 emit 同构信号
│   ├── pip_worker.py         #   包管理 + git 克隆 + 镜像加速核心
│   ├── demucs_worker.py
│   ├── whisper_worker.py
│   ├── realesrgan_worker.py
│   └── yolo_worker.py
├── node/                     # 节点编辑器（预览版）
│   ├── node_editor.py        #   编辑器主窗口 + Shift+A 节点选择器
│   ├── node_canvas.py        #   QGraphicsScene 画布
│   ├── node_registry.py      #   NodeDef 全局注册表（新增节点入口）
│   └── node_graph.py
├── utils/
│   ├── atool.py              # resource_path —— 兼容 PyInstaller
│   ├── configer.py           # configs/config.json 的单例读写
│   └── cpu_score.py
├── resource/                 # 字体、FFmpeg、NCNN 可执行文件、QSS
├── _git_projects/            # 通过 git 克隆安装的项目（已 gitignore）
├── _cache/                   # PyTorch wheel 等本地缓存（已 gitignore）
├── torch_cache/              # torch 下载缓存（已 gitignore）
└── results/                  # 各工具输出目录（已 gitignore）
```

---

## 配置

`configs/config.json` 由 `utils/configer.py` 的单例读写，支持点号路径（如 `static_info.gpu_name`）。常用字段：

| 字段 | 说明 |
| ---- | ---- |
| `static_info.*`             | 启动时采集的硬件、操作系统、关键 pip 包版本快照 |
| `git_mirror_hosts`          | GitHub 加速镜像列表，启动时会自动连通性测试并按延迟排序 |

读写方式：

```python
from utils.configer import get_field, set_field
mirrors = get_field("git_mirror_hosts", [])
set_field("static_info.cpu_single_score", 1234)
```

---

## 开发说明

### Worker 信号约定

所有 Worker 均为 `QThread` 子类，对外暴露统一的信号形态，方便 UI 复用：

```python
progress = pyqtSignal(int, str)   # 百分比, 状态文字
output   = pyqtSignal(str)        # 实时日志（HTML 格式）
finished = pyqtSignal(str)        # 完成时返回输出目录 / 路径
error    = pyqtSignal(str)        # 错误信息
```

`subpage_switch_pages.py` 中的 `LogTextEdit` 在检测到日志包含 `下载进度` 时会原地覆盖当前行，因此进度类消息请保留这一关键字。

### 安装页 ↔ 功能页机制

每个 AI 工具子页都是一个 `SwitchPage`，内部用 `QStackedLayout` 维护两页：

- `_real_page_0`：未安装时显示的 `NoInstallWidget`
- `_real_page_1`：真实的功能 UI

启动时通过 `PipWorker.is_package_installed("demucs")` 之类的检测自动切换。安装完成后 `NoInstallWidget` 会 emit `finish` 信号，触发切换到功能页。

### 资源路径

任何对资源文件（字体、二进制、QSS、模型）的访问都必须经过：

```python
from utils.atool import resource_path
path = resource_path(os.path.join("resource", "realesrgan-ncnn-vulkan"))
```

直接使用 `os.getcwd()` 或相对路径会在 PyInstaller 打包后失效。

### 日志

```python
from logger import info, warning, debug, error
info("启动完成")
```

不要在 Worker 中使用 `print`：UI 端的日志窗口订阅的是 `output_signal`，`print` 内容只会出现在终端。

---

## 路线图

- [x] Demucs / Whisper / Real-ESRGAN / YOLO 图像
- [ ] YOLO 视频流支持
- [ ] 节点编辑器实际执行能力
- [ ] Linux / macOS 适配
- [ ] PyInstaller 一键打包脚本

---

## 致谢

本项目集成的所有 AI 模型与可执行文件版权归原作者所有，启动器仅作为统一前端：

- [facebookresearch/demucs](https://github.com/facebookresearch/demucs)
- [openai/whisper](https://github.com/openai/whisper)
- [RVC-Project/Retrieval-based-Voice-Conversion-WebUI](https://github.com/RVC-Project/Retrieval-based-Voice-Conversion-WebUI) · [IAHispano/Applio](https://github.com/IAHispano/Applio) · 通过 [`rvc-inferpy`](https://pypi.org/project/rvc-inferpy/) 调用
- [xinntao/Real-ESRGAN](https://github.com/xinntao/Real-ESRGAN) · [Real-ESRGAN-ncnn-vulkan](https://github.com/xinntao/Real-ESRGAN-ncnn-vulkan)
- [ultralytics/ultralytics](https://github.com/ultralytics/ultralytics)
- [zhiyiYo/PyQt-Fluent-Widgets](https://github.com/zhiyiYo/PyQt-Fluent-Widgets)
- [FFmpeg](https://ffmpeg.org/)

---

## 许可

仓库尚未指定开源许可证（个人项目，请勿用于商业用途）。如需后续协作或二次分发，请先与作者 [@Heartestrella](https://github.com/Heartestrella) 联系。
