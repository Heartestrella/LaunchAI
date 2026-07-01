"""server/health_check.py
~~~~~~~~~~~~~~~~~~~~~~~~~~
「自检 / 运行测试」的**无 GUI** 逻辑库 + CLI 子进程入口。

回答的问题:某个已安装的工具**现在能不能真正跑通一遍**。做法是端到端真实运行 ——
用内置极小样本(1 秒正弦 wav / 64×64 png / 掩码)真跑一遍,产出文件才算通过。

复用 ``server.tool_runners.run_tool`` 作为统一执行入口(它已复用各 worker、Qt-free),
不另写推理逻辑。**刻意不 import workers.pip_worker** —— 那个模块 import 期会跑
``_test_git_mirrors()``(多秒 HEAD 探测),自检要快;安装检测在此内联。

两类工具的策略:
  - run    : 有内置样本 + 可自动获取模型的 6 个工具,真跑一遍。
  - launch : rvc / gptsovits 需用户自备权重,无法无条件端到端 → 只做启动级检查
             (仓库已部署 + runner ``--help`` 退出码 0),状态标「启动正常·需模型」。

作为库:``check_tool(tool, device, on_event) -> dict``、``is_installed(tool)``。
作为 CLI(GUI 侧 worker 以子进程方式调用,崩溃隔离):
  python -m server.health_check                 # 测所有已安装工具,人类可读表格
  python -m server.health_check --tool whisper  # 单个
  python -m server.health_check --list          # 只列安装状态,不运行
  python -m server.health_check --json          # 机器可读结果(总)
  python -m server.health_check --stream --json # 流式 @EVENT/@RESULT(供 worker 解析)
  python -m server.health_check --device cuda:0 # 覆盖设备
退出码:任一「已安装工具」测试失败 → 非零。
"""
from __future__ import annotations

import os
import sys
import json
import math
import wave
import zlib
import struct
import argparse
import subprocess

# 允许 `python -m server.health_check` 及被 worker 以 -m 方式拉起时都能定位到项目根
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_THIS_DIR)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

# CLI 单独调用时也得让 ffmpeg 上 PATH,不然 whisper.load_audio 直接 FileNotFoundError。
# GUI 侧继承的是 app.py 已改过的 PATH,不受影响,这里做的是兜底。目录名 "ffmepg" 是
# 项目里刻意的拼写(见 CLAUDE.md),别改。
_FFMPEG_BIN = os.path.join(_PROJECT_ROOT, "resource", "ffmepg", "bin")
if os.path.isdir(_FFMPEG_BIN) and _FFMPEG_BIN not in os.environ.get("PATH", ""):
    os.environ["PATH"] = _FFMPEG_BIN + os.pathsep + os.environ.get("PATH", "")

from server.tool_runners import TOOLS, run_tool, ToolError  # noqa: E402

# ── 工具 → 安装检测所需信息 ────────────────────────────────────────────────
# pip 包名(即 `pip show <name>`;对应 PipWorker.is_package_installed 用的字符串)
_PIP_NAME = {
    "demucs": "demucs",
    "whisper": "openai-whisper",
    "yolo": "ultralytics",
    "audiocraft": "audiocraft",
    "iopaint": "iopaint",
}
# 仓库式工具 → (config installed flag key, _git_projects 下的 <pkg>_<fork> 目录名)
# fork 值对齐 workers/pip_worker.py::fork_map,这里内联以避免 import 其慢启动副作用。
_REPO_TOOL = {
    "rvc": ("Applio", "Applio_3.6.2"),
    "gptsovits": ("GPT-SoVITS", "GPT-SoVITS_main"),
}
# 启动级检查用的 runner 脚本(位于 workers/ 下)
_LAUNCH_RUNNER = {
    "rvc": "_rvc_runner.py",
    "gptsovits": "_gptsovits_runner.py",
}
# run 模式的工具集合(其余走 launch)
_RUN_TOOLS = {"demucs", "whisper", "realesrgan", "yolo", "audiocraft", "iopaint"}

_SAMPLE_DIR = os.path.join(_PROJECT_ROOT, "resource", "selftest")


# ── 样本生成(无第三方依赖,stdlib 造 wav / png) ─────────────────────────────
def _write_wav(path: str, seconds: float = 1.0, sr: int = 16000,
               freq: float = 440.0) -> None:
    n = int(seconds * sr)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        frames = bytearray()
        for i in range(n):
            v = int(32767 * 0.3 * math.sin(2 * math.pi * freq * i / sr))
            frames += struct.pack("<h", v)
        w.writeframes(bytes(frames))


def _png_chunk(typ: bytes, data: bytes) -> bytes:
    body = typ + data
    return (struct.pack(">I", len(data)) + body
            + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF))


def _write_png(path: str, w: int, h: int, pixels: bytes) -> None:
    """pixels: 长度 w*h*3 的 RGB 字节。手写最小 PNG,避免依赖 PIL。"""
    raw = bytearray()
    for y in range(h):
        raw.append(0)  # 每行滤波类型 0
        raw += pixels[y * w * 3:(y + 1) * w * 3]
    out = (b"\x89PNG\r\n\x1a\n"
           + _png_chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
           + _png_chunk(b"IDAT", zlib.compress(bytes(raw)))
           + _png_chunk(b"IEND", b""))
    with open(path, "wb") as f:
        f.write(out)


def ensure_samples() -> dict:
    """在 resource/selftest/ 下按需生成极小样本,返回 {audio, image, mask} 绝对路径。"""
    os.makedirs(_SAMPLE_DIR, exist_ok=True)
    audio = os.path.join(_SAMPLE_DIR, "sample.wav")
    image = os.path.join(_SAMPLE_DIR, "sample.png")
    mask = os.path.join(_SAMPLE_DIR, "sample_mask.png")

    if not os.path.isfile(audio):
        _write_wav(audio)

    W = H = 64
    if not os.path.isfile(image):
        px = bytearray()
        for y in range(H):
            for x in range(W):
                px += bytes(((x * 4) % 256, (y * 4) % 256,
                             ((x + y) * 2) % 256))
        _write_png(image, W, H, bytes(px))
    if not os.path.isfile(mask):
        px = bytearray()
        for y in range(H):
            for x in range(W):
                inside = 20 <= x < 44 and 20 <= y < 44
                px += bytes((255, 255, 255) if inside else (0, 0, 0))
        _write_png(mask, W, H, bytes(px))

    return {"audio": audio, "image": image, "mask": mask}


# ── 安装检测(内联,不 import pip_worker) ──────────────────────────────────
def _pip_installed(pkg: str) -> bool:
    try:
        r = subprocess.run([sys.executable, "-m", "pip", "show", pkg],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=30)
        return r.returncode == 0
    except Exception:
        return False


def _realesrgan_exe() -> str:
    return os.path.join(_PROJECT_ROOT, "resource", "realesrgan-ncnn-vulkan",
                        "realesrgan-ncnn-vulkan.exe")


def _realesrgan_models_dir() -> str:
    return os.path.join(_PROJECT_ROOT, "resource", "realesrgan-ncnn-vulkan",
                        "models")


def _pick_realesrgan_model() -> str | None:
    """返回 models/ 下第一个 .param 的模型名(去后缀),没有返回 None。
    自检不挑挑剔的模型,能跑通就行。"""
    d = _realesrgan_models_dir()
    if not os.path.isdir(d):
        return None
    for name in sorted(os.listdir(d)):
        if name.lower().endswith(".param"):
            return name[:-len(".param")]
    return None


def _repo_installed(tool: str) -> bool:
    pkg, dirname = _REPO_TOOL[tool]
    try:
        from utils.configer import get_field
        if bool(get_field(f"installed.{pkg}", False)):
            return True
    except Exception:
        pass
    return os.path.isdir(os.path.join(_PROJECT_ROOT, "_git_projects", dirname))


def is_installed(tool: str) -> bool:
    if tool == "realesrgan":
        return os.path.isfile(_realesrgan_exe())
    if tool in _REPO_TOOL:
        return _repo_installed(tool)
    if tool in _PIP_NAME:
        return _pip_installed(_PIP_NAME[tool])
    return False


# ── run 模式:各工具最小参数(从 TOOLS 默认值出发再覆写) ────────────────────
def _resolve_device(tool: str, device: str | None) -> str:
    # whisper / iopaint 强制 cpu:模型极小,cpu 最稳,避免显存/驱动干扰自检
    if tool in ("whisper", "iopaint"):
        return "cpu"
    return device or "cpu"


def _build_params(tool: str, samples: dict, device: str | None) -> dict:
    p = dict(TOOLS[tool]["params"])
    dev = _resolve_device(tool, device)
    if tool == "demucs":
        # htdemucs 训练时的最大 segment 是 7.8s,TOOLS 默认 10 会触发
        # "Cannot use a Transformer model with a longer segment than it was trained for"
        # 设 7 秒最稳(样本本身只有 1 秒,长度用不满)
        p.update(input=samples["audio"], model="htdemucs", device=dev,
                 shifts=1, segment=7, format="wav")
    elif tool == "whisper":
        p.update(input=samples["audio"], model="tiny", device=dev,
                 output_format="txt")
    elif tool == "realesrgan":
        # bundle 里 models/ 的文件名和 worker MODELS 常量对不上(如 realesrgan-plus-x4
        # vs realesrgan-x4plus),硬编默认会 _wfopen failed。扫目录选第一个 .param。
        model = _pick_realesrgan_model()
        p.update(input=samples["image"], scale=2, fmt="png",
                 model=model or p.get("model", "realesrgan-x4plus"))
    elif tool == "yolo":
        p.update(files=[samples["image"]], weights="yolov8n.pt",
                 device="auto", imgsz=320)
    elif tool == "iopaint":
        p.update(image=samples["image"], mask=samples["mask"],
                 model="lama", device=dev)
    elif tool == "audiocraft":
        p.update(prompt="a short test tone", task="musicgen", model="small",
                 device=dev, duration=2.0, output_format="wav")
    return p


# ── 单工具自检 ──────────────────────────────────────────────────────────────
def check_tool(tool: str, device: str | None = None, on_event=None) -> dict:
    """返回 {tool, display, installed, mode, ok, elapsed, detail, outputs}。

    on_event(kind, payload) 与 run_tool 一致(kind="progress"/"log"),可为 None。
    """
    display = TOOLS.get(tool, {}).get("display", tool)
    mode = "run" if tool in _RUN_TOOLS else "launch"
    res = {"tool": tool, "display": display, "installed": is_installed(tool),
           "mode": mode, "ok": False, "elapsed": 0.0, "detail": "", "outputs": []}

    if not res["installed"]:
        res["detail"] = "未安装,跳过"
        return res

    if mode == "launch":
        return _check_launch(tool, res)

    # run 模式:真跑一遍
    import time
    t0 = time.time()
    try:
        samples = ensure_samples()
        params = _build_params(tool, samples, device)
        out = run_tool(tool, params, on_event)
        outs = [f for f in out.get("outputs", []) if f and os.path.exists(f)]
        res["elapsed"] = out.get("elapsed", round(time.time() - t0, 2))
        res["outputs"] = outs
        if outs:
            res["ok"] = True
            res["detail"] = f"通过 · 产出 {len(outs)} 个文件 · {res['elapsed']}s"
        else:
            res["detail"] = "运行结束但未产出文件"
    except ToolError as e:
        res["elapsed"] = round(time.time() - t0, 2)
        res["detail"] = f"失败(code {e.code}): {e}"
    except Exception as e:  # noqa: BLE001
        res["elapsed"] = round(time.time() - t0, 2)
        res["detail"] = f"异常: {type(e).__name__}: {e}"
    return res


def _check_launch(tool: str, res: dict) -> dict:
    """启动级检查:runner --help 退出码 0 即认为可启动(需用户自备权重故不真跑)。"""
    import time
    runner = os.path.join(_PROJECT_ROOT, "workers", _LAUNCH_RUNNER[tool])
    if not os.path.isfile(runner):
        res["detail"] = f"缺少 runner: {runner}"
        return res
    t0 = time.time()
    try:
        r = subprocess.run([sys.executable, runner, "--help"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=120)
        res["elapsed"] = round(time.time() - t0, 2)
        if r.returncode == 0:
            res["ok"] = True
            res["detail"] = "启动正常 · 需模型(端到端需自备权重)"
        else:
            res["detail"] = f"runner 启动失败(退出码 {r.returncode})"
    except Exception as e:  # noqa: BLE001
        res["elapsed"] = round(time.time() - t0, 2)
        res["detail"] = f"runner 异常: {type(e).__name__}: {e}"
    return res


# ── CLI ─────────────────────────────────────────────────────────────────────
def _emit(payload: dict) -> None:
    sys.stdout.write("@EVENT " + json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _human_row(r: dict) -> str:
    if not r["installed"]:
        icon = "[SKIP]"
    elif r["ok"]:
        icon = "[ OK ]"
    else:
        icon = "[FAIL]"
    return f"{icon} {r['display']:<22} [{r['mode']:<6}] {r['detail']}"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="LaunchAI 工具自检 / 运行测试")
    ap.add_argument("--tool", help="只测指定工具(不填=全部)")
    ap.add_argument("--device", default=None, help="设备,如 cuda:0 / cpu")
    ap.add_argument("--list", action="store_true", help="只列安装状态,不运行")
    ap.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    ap.add_argument("--stream", action="store_true",
                    help="流式发 @EVENT/@RESULT(供 GUI worker 解析)")
    args = ap.parse_args(argv)

    tools = [args.tool] if args.tool else list(TOOLS.keys())
    for t in tools:
        if t not in TOOLS:
            print(f"未知工具: {t}", file=sys.stderr)
            return 2

    if args.list:
        rows = [{"tool": t, "display": TOOLS[t]["display"],
                 "installed": is_installed(t),
                 "mode": "run" if t in _RUN_TOOLS else "launch"} for t in tools]
        if args.json:
            print(json.dumps(rows, ensure_ascii=False, indent=2))
        else:
            for r in rows:
                flag = "已安装" if r["installed"] else "未安装"
                print(f"[{flag}] {r['display']} ({r['mode']})")
        return 0

    on_event = None
    if args.stream:
        def on_event(kind, payload):
            _emit({"kind": kind, **payload})

    results = []
    failed = 0
    for t in tools:
        if args.stream:
            _emit({"kind": "tool_start", "tool": t,
                   "display": TOOLS[t]["display"]})
        r = check_tool(t, device=args.device, on_event=on_event)
        results.append(r)
        if args.stream:
            sys.stdout.write("@RESULT " + json.dumps(r, ensure_ascii=False) + "\n")
            sys.stdout.flush()
        elif not args.json:
            print(_human_row(r))
        # 已安装却没通过 → 计入失败(未安装的跳过不算)
        if r["installed"] and not r["ok"]:
            failed += 1

    if args.json and not args.stream:
        print(json.dumps(results, ensure_ascii=False, indent=2))

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
