"""
workers/_audiocraft_runner.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
AudiocraftWorker 的子进程入口。在 qt_venv 里 import audiocraft 跑 MusicGen /
AudioGen 推理，把状态以 `[runner] ...` / `[progress] N/M` 行打到 stdout，
让父进程的 AudiocraftWorker 解析并发到 UI。

实现成独立脚本是因为：
  - audiocraft 一次 import 会拖起 torch / xformers / encodec / transformers，
    放主进程会让 UI 卡好几秒；
  - 子进程死掉不会拖垮整个程序；
  - 取消只要 terminate 这个 PID。

CLI 见 build_argparser()。所有路径都用绝对路径，PowerShell / cmd 下都行。
"""

import argparse
import os
import sys
import time
import traceback
from pathlib import Path


def _say(line: str) -> None:
    """打印一行并 flush；父进程逐行读 stdout。"""
    sys.stdout.write(line + "\n")
    sys.stdout.flush()


def _parse_device(device: str) -> str:
    """audiocraft 接受 'cuda'/'cuda:N'/'cpu'/'mps' 字符串。"""
    if not device:
        return "cpu"
    device = device.strip()
    if device.lower() == "cpu":
        return "cpu"
    if device.lower().startswith("cuda"):
        return device
    if device.lower() == "mps":
        return "mps"
    return device


def _resolve_model_name(task: str, model: str) -> str:
    """允许传短名 (small/medium/large/melody) 或完整 hf id。"""
    if "/" in model:
        return model
    short = model.strip().lower()
    if task == "musicgen":
        return f"facebook/musicgen-{short}"
    if task == "audiogen":
        return f"facebook/audiogen-{short}"
    return model


def _make_progress_callback(total_steps_hint: int = 0):
    """audiocraft 的 set_custom_progress_callback 签名是 (generated, total)。"""
    state = {"last_emit": 0.0}

    def cb(generated_tokens: int, tokens_to_generate: int) -> None:
        # 太频繁会刷屏 + 父进程正则压力大，限到 ~10Hz
        now = time.monotonic()
        if now - state["last_emit"] < 0.1 and generated_tokens < tokens_to_generate:
            return
        state["last_emit"] = now
        total = tokens_to_generate or total_steps_hint or 0
        if total <= 0:
            return
        pct = min(100, int(generated_tokens * 100 / total))
        _say(f"[progress] {generated_tokens}/{total} ({pct}%)")

    return cb


def _load_melody(path: str):
    """读 melody 音频成 (wav_tensor, sample_rate)，给 generate_with_chroma 用。"""
    import torchaudio  # 延迟到运行时 import，省主进程开销

    wav, sr = torchaudio.load(path)
    return wav, sr


def _run_musicgen(args: argparse.Namespace) -> int:
    from audiocraft.models import MusicGen
    from audiocraft.data.audio import audio_write

    model_name = _resolve_model_name("musicgen", args.model)
    device = _parse_device(args.device)
    _say(f"[runner] 加载 MusicGen 模型: {model_name} (device={device})")
    model = MusicGen.get_pretrained(model_name, device=device)

    _say(
        f"[runner] 生成参数: duration={args.duration}s "
        f"top_k={args.top_k} top_p={args.top_p} "
        f"temperature={args.temperature} cfg_coef={args.cfg_coef}"
    )
    model.set_generation_params(
        duration=args.duration,
        top_k=args.top_k,
        top_p=args.top_p,
        temperature=args.temperature,
        cfg_coef=args.cfg_coef,
    )

    # 不是每个 audiocraft 版本都有这个 hook，没有就退回到不带进度
    set_cb = getattr(model, "set_custom_progress_callback", None)
    if callable(set_cb):
        set_cb(_make_progress_callback())

    prompts = args.prompts or [""]
    melody_path = args.melody or ""
    if melody_path and "melody" not in model_name.lower():
        _say("[runner] 提示音频已选择但当前模型不是 melody 变体，将忽略 melody")
        melody_path = ""

    _say(f"[runner] 共 {len(prompts)} 条提示词，开始生成…")
    if melody_path:
        melody_wav, melody_sr = _load_melody(melody_path)
        # 同一个 melody 给所有 prompt 用（按 batch 复制）
        melody_batch = melody_wav.unsqueeze(0).repeat(len(prompts), 1, 1)
        wavs = model.generate_with_chroma(
            descriptions=prompts,
            melody_wavs=melody_batch,
            melody_sample_rate=melody_sr,
            progress=True,
        )
    else:
        wavs = model.generate(prompts, progress=True)

    out_dir = args.output_dir
    os.makedirs(out_dir, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    sr = model.sample_rate

    saved = []
    for idx, wav in enumerate(wavs):
        stem = os.path.join(out_dir, f"musicgen_{ts}_{idx:02d}")
        audio_write(
            stem, wav.cpu(), sr,
            strategy="loudness",
            loudness_compressor=True,
            format=args.output_format,
        )
        path = f"{stem}.{args.output_format}"
        saved.append(path)
        _say(f"[runner] 已保存: {path}")

    _say(f"[runner] 完成，共 {len(saved)} 个文件")
    return 0


def _run_audiogen(args: argparse.Namespace) -> int:
    from audiocraft.models import AudioGen
    from audiocraft.data.audio import audio_write

    model_name = _resolve_model_name("audiogen", args.model)
    device = _parse_device(args.device)
    _say(f"[runner] 加载 AudioGen 模型: {model_name} (device={device})")
    model = AudioGen.get_pretrained(model_name, device=device)

    _say(
        f"[runner] 生成参数: duration={args.duration}s "
        f"top_k={args.top_k} top_p={args.top_p} "
        f"temperature={args.temperature} cfg_coef={args.cfg_coef}"
    )
    model.set_generation_params(
        duration=args.duration,
        top_k=args.top_k,
        top_p=args.top_p,
        temperature=args.temperature,
        cfg_coef=args.cfg_coef,
    )

    set_cb = getattr(model, "set_custom_progress_callback", None)
    if callable(set_cb):
        set_cb(_make_progress_callback())

    prompts = args.prompts or [""]
    _say(f"[runner] 共 {len(prompts)} 条提示词，开始生成…")
    wavs = model.generate(prompts, progress=True)

    out_dir = args.output_dir
    os.makedirs(out_dir, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    sr = model.sample_rate

    saved = []
    for idx, wav in enumerate(wavs):
        stem = os.path.join(out_dir, f"audiogen_{ts}_{idx:02d}")
        audio_write(
            stem, wav.cpu(), sr,
            strategy="loudness",
            loudness_compressor=True,
            format=args.output_format,
        )
        path = f"{stem}.{args.output_format}"
        saved.append(path)
        _say(f"[runner] 已保存: {path}")

    _say(f"[runner] 完成，共 {len(saved)} 个文件")
    return 0


def build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Audiocraft (MusicGen / AudioGen) runner")
    p.add_argument("--task", choices=("musicgen", "audiogen"), required=True)
    p.add_argument("--model", required=True,
                   help="短名 (small/medium/large/melody) 或完整 HF id")
    p.add_argument("--device", default="cuda")
    p.add_argument("--prompt", action="append", default=[],
                   help="可重复传，多条提示词")
    p.add_argument("--melody", default="",
                   help="MusicGen-melody 用的旋律音频路径；其它模型忽略")
    p.add_argument("--duration", type=float, default=8.0)
    p.add_argument("--top-k", dest="top_k", type=int, default=250)
    p.add_argument("--top-p", dest="top_p", type=float, default=0.0)
    p.add_argument("--temperature", type=float, default=1.0)
    p.add_argument("--cfg-coef", dest="cfg_coef", type=float, default=3.0)
    p.add_argument("--output-dir", dest="output_dir", required=True)
    p.add_argument("--output-format", dest="output_format",
                   default="wav", choices=("wav", "mp3"))
    return p


def main() -> int:
    parser = build_argparser()
    args = parser.parse_args()
    # argparse 把多次 --prompt 收进 args.prompt；为了少改下面代码再 alias 一下
    args.prompts = [p for p in (args.prompt or []) if p.strip()]
    if not args.prompts:
        _say("[runner][ERROR] 至少需要一条非空 --prompt")
        return 2

    try:
        if args.task == "musicgen":
            return _run_musicgen(args)
        elif args.task == "audiogen":
            return _run_audiogen(args)
        else:
            _say(f"[runner][ERROR] 未知 task: {args.task}")
            return 2
    except KeyboardInterrupt:
        _say("[runner] 收到 KeyboardInterrupt，已中断")
        return 130
    except Exception as e:
        _say(f"[runner][ERROR] {type(e).__name__}: {e}")
        for line in traceback.format_exc().splitlines():
            _say(f"[runner][ERROR] {line}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
