# LaunchAI · 奇点

**简体中文** · [English](./en.md)

> **AI ALL IN ONE** —— 基于 PyQt6 + Fluent Design 的 AI 工具一站式启动器

把主流开源 AI 工具整合到同一个桌面应用 提供统一的安装管理 参数面板和运行界面

- 项目地址：<https://github.com/Heartestrella/LaunchAI>
- 当前版本：**0.0.0 Build 1**（开发预览）
- 平台：Windows 10 / 11

> ⚠️ 个人开源项目 仅供学习与个人使用 **请勿用于任何盈利性用途** 请认准 GitHub @Heartestrella

---

## 功能

- 基于 [QFluentWidgets](https://qfluentwidgets.com/) 的 Fluent 风格界面 深色 / 浅色主题一键切换
- 首页展示 CPU / 内存 / 磁盘 / GPU / CUDA 驱动等系统信息
- 自动依赖安装：pip 源 + 阿里云 PyTorch wheel 直链 + dulwich Git 克隆 + 镜像探测排序
- 节点编辑器（预览）：ComfyUI 风格的可视化连线工作流 `Shift+A` 调出节点选择器
- PyInstaller 打包兼容 所有资源访问走 `utils.atool.resource_path`

---

## 已支持工具

| 分类 | 工具 | 用途 | 来源 |
| ---- | ---- | ---- | ---- |
| 音频 | **Demucs** | 音轨分离 | [facebookresearch/demucs](https://github.com/facebookresearch/demucs) |
| 音频 | **Whisper** | 语音识别 / 字幕生成 | [openai/whisper](https://github.com/openai/whisper) |
| 音频 | **RVC** | AI 变声 / 翻唱 | [Applio](https://github.com/IAHispano/Applio) / [rvc-inferpy](https://pypi.org/project/rvc-inferpy/) |
| 图像 | **Real-ESRGAN** | 图像超分辨率 内置 ncnn 推理 | [xinntao/Real-ESRGAN](https://github.com/xinntao/Real-ESRGAN) |
| 图像 | **YOLO** | 目标检测 | [ultralytics](https://github.com/ultralytics/ultralytics) |

---

## 环境要求

- Windows 10 / 11
- Python 3.10（推荐使用仓库内置 `qt_venv/`）
- NVIDIA + CUDA（可选 无 GPU 时部分工具回退 CPU）
- 磁盘 10 GB+

---

## 运行

```bash
qt_venv\Scripts\python.exe app.py
```

> ⚠️ 必须使用 `qt_venv` 内的解释器 Worker 通过 `sys.executable` 调用 CLI 外部解释器找不到依赖

首次进入工具页时若依赖未安装 会弹出安装引导 点击即可自动部署

---

## 项目结构

```
app.py                 入口 主窗口 + 导航
logger.py              统一日志
configs/config.json    系统快照 + 运行时配置
widgets/subpage/       各功能子页面 + 安装/功能切换容器
workers/               QThread Worker 统一信号
node/                  节点编辑器（预览）
utils/                 资源路径 / 配置读写
resource/              字体 FFmpeg NCNN QSS
```

---

## 致谢

集成的 AI 模型与可执行文件版权归原作者所有 本项目仅作为统一前端

[demucs](https://github.com/facebookresearch/demucs) · [whisper](https://github.com/openai/whisper) · [RVC-Project](https://github.com/RVC-Project/Retrieval-based-Voice-Conversion-WebUI) · [Applio](https://github.com/IAHispano/Applio) · [Real-ESRGAN](https://github.com/xinntao/Real-ESRGAN) · [ultralytics](https://github.com/ultralytics/ultralytics) · [QFluentWidgets](https://github.com/zhiyiYo/PyQt-Fluent-Widgets) · [FFmpeg](https://ffmpeg.org/)

---

## 许可

尚未指定开源许可证 请勿用于商业用途 如需协作或二次分发请联系 [@Heartestrella](https://github.com/Heartestrella)
