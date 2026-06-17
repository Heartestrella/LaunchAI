# LaunchAI · Singularity

[简体中文](./README.md) · **English**

> **AI ALL IN ONE** — a unified desktop launcher for open-source AI tools built with PyQt6 + Fluent Design

Wraps popular open-source AI projects behind a single desktop UI with managed installs parameter panels and run views

- Repository: <https://github.com/Heartestrella/LaunchAI>
- Version: **0.0.0 Build 1** (developer preview)
- Platform: Windows 10 / 11

> ⚠️ Personal open-source project for study and personal use only **Do not use commercially** Always free — get it from GitHub @Heartestrella

---

## Features

- Fluent Design UI via [QFluentWidgets](https://qfluentwidgets.com/) with dark / light theme switching
- Home page showing CPU / RAM / disk / GPU / CUDA driver info
- Auto dependency install: pip index + Aliyun PyTorch wheel mirror + dulwich Git clone + mirror auto-probe
- Node editor (preview): ComfyUI-style visual workflow `Shift+A` opens node picker
- PyInstaller-ready — all resource access goes through `utils.atool.resource_path`

---

## Supported Tools

| Category | Tool | Purpose | Upstream |
| -------- | ---- | ------- | -------- |
| Audio | **Demucs** | Stem separation | [facebookresearch/demucs](https://github.com/facebookresearch/demucs) |
| Audio | **Whisper** | Speech recognition / subtitles | [openai/whisper](https://github.com/openai/whisper) |
| Audio | **RVC** | AI voice conversion | [Applio](https://github.com/IAHispano/Applio) / [rvc-inferpy](https://pypi.org/project/rvc-inferpy/) |
| Image | **Real-ESRGAN** | Image super-resolution with bundled ncnn | [xinntao/Real-ESRGAN](https://github.com/xinntao/Real-ESRGAN) |
| Image | **YOLO** | Object detection | [ultralytics](https://github.com/ultralytics/ultralytics) |

---

## Requirements

- Windows 10 / 11
- Python 3.10 (bundled `qt_venv/` recommended)
- NVIDIA + CUDA (optional — some tools fall back to CPU)
- 10 GB+ disk space

---

## Running

```bash
qt_venv\Scripts\python.exe app.py
```

> ⚠️ Must use the interpreter inside `qt_venv` — Workers call CLIs via `sys.executable` so an external Python won't find dependencies

First time opening a tool page without dependencies triggers an install wizard — click install and it deploys automatically

---

## Project Structure

```
app.py                 Entry point main window + navigation
logger.py              Shared logger
configs/config.json    System snapshot + runtime config
widgets/subpage/       Feature subpages + install/feature switch container
workers/               QThread workers with uniform signals
node/                  Node editor (preview)
utils/                 Resource paths / config reader
resource/              Fonts FFmpeg NCNN binaries QSS
```

---


## Acknowledgements

All bundled AI models and binaries belong to their original authors — LaunchAI is just a unified frontend

[demucs](https://github.com/facebookresearch/demucs) · [whisper](https://github.com/openai/whisper) · [RVC-Project](https://github.com/RVC-Project/Retrieval-based-Voice-Conversion-WebUI) · [Applio](https://github.com/IAHispano/Applio) · [Real-ESRGAN](https://github.com/xinntao/Real-ESRGAN) · [ultralytics](https://github.com/ultralytics/ultralytics) · [QFluentWidgets](https://github.com/zhiyiYo/PyQt-Fluent-Widgets) · [FFmpeg](https://ffmpeg.org/)

---

## License

No open-source license chosen yet — do not use commercially For collaboration or redistribution contact [@Heartestrella](https://github.com/Heartestrella)
