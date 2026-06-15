# LaunchAI · Singularity

[简体中文](./README.md) · **English**

> **AI ALL IN ONE** — a unified desktop launcher for open-source AI tools, built with PyQt6 + Fluent Design.

LaunchAI bundles popular open-source AI projects (audio separation, speech recognition, image super-resolution, object detection, …) behind a single desktop UI with managed installs, parameter panels and run views — so you no longer need to set up each tool's environment or memorize its CLI flags.

- Repository: <https://github.com/Heartestrella/LaunchAI>
- Current version: **0.0.0 Build 1** (developer preview — APIs are not stable yet)
- Platform: Windows (verified on Windows 11 + RTX 30 series)

> ⚠️ This is a personal open-source project for study and personal use only. **Do not use it for any commercial purpose.** LaunchAI is and will always be free — please obtain it from the official GitHub repository @Heartestrella.

---

## Features

- **Unified launcher UI** powered by [QFluentWidgets](https://qfluentwidgets.com/), with one-click dark / light theme switching.
- **Home & System Info** pages summarising CPU, RAM, disk, GPU, CUDA driver and key Python package versions.
- **Automatic install & dependency management** — when a tool's page is opened and its backend is missing, an in-app installer takes over with:
  - pip installs through a configurable index
  - direct PyTorch wheel downloads from the Aliyun mirror (with local `_cache/` reuse)
  - GitHub clones via [dulwich](https://www.dulwich.io/) (no system `git` required) routed through Git acceleration mirrors that are auto-probed and sorted by latency
  - per-project fix-ups such as the `basicsr` / new-torchvision compatibility patch
- **Node editor (preview)** — ComfyUI-style visual workflow with a `Shift+A` node picker. Work in progress.
- **Packaging-friendly** — all resource access goes through `utils.atool.resource_path`, which is `sys._MEIPASS`-aware for PyInstaller bundles.

---

## Supported AI tools

| Category | Tool | Purpose | Upstream |
| -------- | ---- | ------- | -------- |
| Audio    | **Demucs**             | Stem separation (vocals / drums / bass / other) | <https://github.com/facebookresearch/demucs> |
| Audio    | **OpenAI Whisper**     | Speech recognition / subtitle generation | <https://github.com/openai/whisper> |
| Audio    | **RVC** (AI voice conversion / cover) | Inference through the third-party wrapper [`rvc-inferpy`](https://pypi.org/project/rvc-inferpy/) (Applio ecosystem) on top of [RVC-Project](https://github.com/RVC-Project/Retrieval-based-Voice-Conversion-WebUI) | <https://github.com/IAHispano/Applio> |
| Image    | **Real-ESRGAN**        | Image / anime super-resolution (ships with `realesrgan-ncnn-vulkan`, ready to use without installing) | <https://github.com/xinntao/Real-ESRGAN> |
| Image    | **YOLO (Ultralytics)** | Object detection (images supported; video WIP) | <https://github.com/ultralytics/ultralytics> |

> To add a new tool: write a `QThread` Worker, write a feature subpage, add a `handel_xxx` branch in `widgets/subpage/subpage_switch_pages.py`, and register the page in `app.py`.

---

## Requirements

- **OS**: Windows 10 / 11 (path handling and bundled binaries currently target Windows)
- **Python**: 3.10 (the bundled `qt_venv/` is recommended)
- **GPU**: NVIDIA + CUDA (optional — without a GPU some tools fall back to CPU)
- **Disk**: 10 GB+ recommended for models and dependencies

The repository ships with a pre-built virtual environment and several bundled binaries:

```
resource/ffmepg/bin/                  ← FFmpeg (prepended to PATH on startup)
resource/realesrgan-ncnn-vulkan/      ← Real-ESRGAN NCNN inference binary
resource/JetBrainsMapleMono-*.ttf     ← UI font
qt_venv/                              ← bundled Python 3.10 virtual environment
```

---

## Running

```bash
qt_venv\Scripts\python.exe app.py
```

> ⚠️ You **must** use the interpreter inside `qt_venv`: every Worker calls its CLI via `sys.executable -m <module>`, so a Python outside this venv will not find the dependencies.

The first time you open a tool whose backend is not installed, an install wizard appears. Click *Install* and the launcher will set up everything inside the bundled Python environment automatically.

---

## Project layout

```
LaunchAI/
├── app.py                    # Entry point: main window + navigation + global excepthook
├── logger.py                 # ANSI-coloured shared logger (from logger import info, ...)
├── configs/config.json       # System snapshot + runtime config (incl. git mirror list)
├── widgets/
│   ├── home_page.py          # Home (banner, quick folders, notices)
│   └── subpage/              # Per-feature subpages
│       ├── subpage_switch_pages.py   # Install-page ↔ feature-page container
│       ├── subpage_demucs.py         # Demucs UI
│       ├── subpage_ESRGAN.py         # Real-ESRGAN UI
│       ├── subpage_whisper.py        # Whisper UI
│       ├── subpage_yolov.py          # YOLO UI
│       ├── subpage_info_page.py      # System info page
│       └── subpage_setting_page.py   # Settings page
├── workers/                  # All QThread, all emit the same signal shape
│   ├── pip_worker.py         #   Package mgmt + git clone + mirror acceleration core
│   ├── demucs_worker.py
│   ├── whisper_worker.py
│   ├── realesrgan_worker.py
│   └── yolo_worker.py
├── node/                     # Node editor (preview)
│   ├── node_editor.py        #   Editor window + Shift+A node picker
│   ├── node_canvas.py        #   QGraphicsScene canvas
│   ├── node_registry.py      #   Global NodeDef registry (entry point for new nodes)
│   └── node_graph.py
├── utils/
│   ├── atool.py              # resource_path — PyInstaller-aware
│   ├── configer.py           # Singleton reader/writer for configs/config.json
│   └── cpu_score.py
├── resource/                 # Fonts, FFmpeg, NCNN binaries, QSS
├── _git_projects/            # Projects installed by cloning (gitignored)
├── _cache/                   # PyTorch wheels and similar caches (gitignored)
├── torch_cache/              # torch download cache (gitignored)
└── results/                  # Output directory for each tool (gitignored)
```

---

## Configuration

`configs/config.json` is read and written through the `utils/configer.py` singleton, which supports dotted paths (e.g. `static_info.gpu_name`). Common fields:

| Field | Description |
| ----- | ----------- |
| `static_info.*`             | Hardware / OS / key pip package snapshot collected at startup |
| `git_mirror_hosts`          | List of GitHub acceleration mirrors — probed on startup and sorted by latency |

Reading and writing:

```python
from utils.configer import get_field, set_field
mirrors = get_field("git_mirror_hosts", [])
set_field("static_info.cpu_single_score", 1234)
```

---

## Development notes

### Worker signal convention

All workers are `QThread` subclasses and expose a uniform signal shape so the UI side can be reused:

```python
progress = pyqtSignal(int, str)   # percent, status text
output   = pyqtSignal(str)        # live log (HTML)
finished = pyqtSignal(str)        # final output directory / path
error    = pyqtSignal(str)        # error message
```

`LogTextEdit` in `subpage_switch_pages.py` overwrites the current line whenever a log line contains the substring `下载进度` ("download progress"), so keep that token in progress messages.

### Install-page ↔ feature-page mechanism

Every AI tool subpage is a `SwitchPage` whose `QStackedLayout` holds exactly two children:

- `_real_page_0`: a `NoInstallWidget` shown when the backend is missing
- `_real_page_1`: the real feature UI

At startup the page checks `PipWorker.is_package_installed("demucs")` (or similar) and picks one. When installation finishes, `NoInstallWidget` emits `finish`, which flips the layout to the feature page.

### Resource paths

Any access to a bundled file (fonts, binaries, QSS, models) **must** go through:

```python
from utils.atool import resource_path
path = resource_path(os.path.join("resource", "realesrgan-ncnn-vulkan"))
```

Plain `os.getcwd()` or relative paths break once the app is bundled with PyInstaller.

### Logging

```python
from logger import info, warning, debug, error
info("startup complete")
```

Do not use `print` inside a Worker: the in-app log view subscribes to `output_signal`, so `print` output only ever lands in the terminal.

---

## Roadmap

- [x] Demucs / Whisper / Real-ESRGAN / YOLO image
- [ ] YOLO video stream support
- [ ] Make the node editor actually executable
- [ ] Linux / macOS support
- [ ] One-shot PyInstaller packaging script

---

## Acknowledgements

All bundled AI models and executables are the property of their original authors — LaunchAI is just a unified frontend:

- [facebookresearch/demucs](https://github.com/facebookresearch/demucs)
- [openai/whisper](https://github.com/openai/whisper)
- [RVC-Project/Retrieval-based-Voice-Conversion-WebUI](https://github.com/RVC-Project/Retrieval-based-Voice-Conversion-WebUI) · [IAHispano/Applio](https://github.com/IAHispano/Applio) · invoked through [`rvc-inferpy`](https://pypi.org/project/rvc-inferpy/)
- [xinntao/Real-ESRGAN](https://github.com/xinntao/Real-ESRGAN) · [Real-ESRGAN-ncnn-vulkan](https://github.com/xinntao/Real-ESRGAN-ncnn-vulkan)
- [ultralytics/ultralytics](https://github.com/ultralytics/ultralytics)
- [zhiyiYo/PyQt-Fluent-Widgets](https://github.com/zhiyiYo/PyQt-Fluent-Widgets)
- [FFmpeg](https://ffmpeg.org/)

---

## License

No open-source license has been chosen yet (personal project — please do not use commercially). For collaboration or redistribution, reach out to the author [@Heartestrella](https://github.com/Heartestrella) first.
