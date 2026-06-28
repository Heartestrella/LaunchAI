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
- 自动依赖安装：pip 源 + 阿里云 PyTorch wheel 直链 + dulwich Git 克隆 + 镜像探测排序
- 节点编辑器：ComfyUI 风格的可视化连线工作流 快捷键类Blender
- 开放FastApi供其他主机调用 (研发中暂不可用)
---

## 已支持工具

| 分类 | 工具 | 用途 | 来源 |
| ---- | ---- | ---- | ---- |
| 音频 | **Demucs** | 音轨分离 | [facebookresearch/demucs](https://github.com/facebookresearch/demucs) |
| 音频 | **Whisper** | 语音识别 / 字幕生成 | [openai/whisper](https://github.com/openai/whisper) |
| 音频 | **RVC** | AI 变声 / 翻唱 | [Applio](https://github.com/IAHispano/Applio) / [rvc-inferpy](https://pypi.org/project/rvc-inferpy/) |
| 音频 | **GPT-SoVITS** | 语音合成 / 少样本声音克隆 | [RVC-Boss/GPT-SoVITS](https://github.com/RVC-Boss/GPT-SoVITS) |
| 音频 | **AudioCraft** | 音频生成 | [facebookresearch/audiocraft](https://github.com/facebookresearch/audiocraft) |
| 图像 | **Real-ESRGAN** | 图像超分辨率 内置 ncnn 推理 | [xinntao/Real-ESRGAN](https://github.com/xinntao/Real-ESRGAN) |
| 图像 | **YOLO** | 目标检测 当前支持图像 视频在做 | [ultralytics](https://github.com/ultralytics/ultralytics) |
| 图像 | **IOPaint** | 图像修复 / 对象擦除 / 水印去除 | [Sanster/IOPaint](https://github.com/Sanster/IOPaint) |

---



## 环境要求

- Windows 10 / 11 (目前仅限Windows)
- Python 3.11
- NVIDIA + CUDA（可选 无 GPU 时部分工具回退 CPU）
- 磁盘 10 GB+

---

## 发展

- 添加对SD的整合与管理
- 添加对LLM的支持
- 添加更多有用的工具
- 如果您对发展有建议 可以提交issue

---

## 运行

```bash
python app.py
```

> ⚠️ 当前尚未整理出requirements.txt
> 后续会在发行版中放出打包环境后的整合包


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

[demucs](https://github.com/facebookresearch/demucs) · [whisper](https://github.com/openai/whisper) · [RVC-Project](https://github.com/RVC-Project/Retrieval-based-Voice-Conversion-WebUI) · [Applio](https://github.com/IAHispano/Applio) · [AudioCraft](https://github.com/facebookresearch/audiocraft) ·   [GPT-SoVITS](https://github.com/RVC-Boss/GPT-SoVITS) · [Real-ESRGAN](https://github.com/xinntao/Real-ESRGAN) · [ultralytics](https://github.com/ultralytics/ultralytics) · [IOPaint](https://github.com/Sanster/IOPaint) · [QFluentWidgets](https://github.com/zhiyiYo/PyQt-Fluent-Widgets) · [FFmpeg](https://ffmpeg.org/) 

---

## 许可

使用GNU General Public License v3.0 许可证 严禁商用 [@Heartestrella](https://github.com/Heartestrella)
