"""
workers/_rvc_realtime_runner.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
RVCRealtimeWorker 的子进程入口。

Applio 3.6.x 之后实时类从 `tabs.realtime.realtime` 迁移到了
`rvc.realtime.callbacks.AudioCallbacks`（内部组合 VoiceChanger + Audio）。
本 runner 自动发现 `_git_projects/Applio_*` 仓库根目录，加入 sys.path 后
直接用 AudioCallbacks + audio_manager.start() 驱动常驻实时变声循环；
父进程通过 SIGTERM / process.terminate() 触发优雅停机。

调试时可以单独跑：
    qt_venv/Scripts/python.exe workers/_rvc_realtime_runner.py --list-modules
"""

import argparse
import os
import re
import signal
import sys
import time


_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_GIT_PROJECTS = os.path.normpath(os.path.join(_THIS_DIR, "..", "_git_projects"))


def _find_applio_root() -> str:
    """
    在 _git_projects/ 下寻找 Applio 仓库根目录。
    优先匹配 `Applio_<version>`，没有则回退到 `Applio_main` / 任何带 Applio 前缀的目录。
    版本号按自然排序取最大值。
    """
    if not os.path.isdir(_GIT_PROJECTS):
        return ""

    candidates = []
    for entry in os.listdir(_GIT_PROJECTS):
        full = os.path.join(_GIT_PROJECTS, entry)
        if not os.path.isdir(full):
            continue
        if not entry.lower().startswith("applio"):
            continue
        # 必须能找到 rvc/realtime 或 tabs/realtime
        if (os.path.isdir(os.path.join(full, "rvc", "realtime"))
                or os.path.isdir(os.path.join(full, "tabs", "realtime"))):
            candidates.append((entry, full))

    if not candidates:
        return ""

    def _ver_key(name: str):
        m = re.search(r"(\d+(?:\.\d+)*)", name)
        if not m:
            return (0,)
        return tuple(int(p) for p in m.group(1).split("."))

    candidates.sort(key=lambda x: _ver_key(x[0]), reverse=True)
    return candidates[0][1]


APPLIO_ROOT = _find_applio_root()


def _setup_applio_path():
    if not APPLIO_ROOT or not os.path.isdir(APPLIO_ROOT):
        print(f"[realtime][ERROR] 未在 {_GIT_PROJECTS} 找到 Applio 仓库",
              flush=True)
        print("[realtime][ERROR] 请先在 RVC 页面完成 Applio 安装", flush=True)
        sys.exit(2)
    print(f"[realtime] 使用 Applio 仓库: {APPLIO_ROOT}", flush=True)
    # Applio 内部模块大量依赖 os.getcwd()，必须 chdir
    os.chdir(APPLIO_ROOT)
    if APPLIO_ROOT not in sys.path:
        sys.path.insert(0, APPLIO_ROOT)


def _import_audio_callbacks():
    """
    优先使用 Applio 3.6.x 的 rvc.realtime.callbacks.AudioCallbacks。
    旧版本兜底：tabs.realtime.realtime 里的实时类。
    返回 (kind, target):
        kind="new"    -> target 是 AudioCallbacks 类
        kind="legacy" -> target 是旧版实时类
    """
    import importlib

    # 新版（3.6.x+）
    try:
        mod = importlib.import_module("rvc.realtime.callbacks")
        if hasattr(mod, "AudioCallbacks"):
            print("[realtime] 命中 rvc.realtime.callbacks.AudioCallbacks",
                  flush=True)
            return ("new", mod.AudioCallbacks)
    except Exception as e:
        print(f"[realtime] rvc.realtime.callbacks 不可用: {e}", flush=True)

    # 旧版兜底
    try:
        mod = importlib.import_module("tabs.realtime.realtime")
    except Exception as e:
        print(f"[realtime][ERROR] 无法 import 任何实时模块: {e}", flush=True)
        sys.exit(3)

    for name in ("Realtime", "RealtimeConverter", "RealtimeVoiceConversion",
                 "RealtimeRVC", "VoiceConverterRealtime", "VC"):
        obj = getattr(mod, name, None)
        if isinstance(obj, type):
            print(f"[realtime] 命中旧版实时类: {name}", flush=True)
            return ("legacy", obj)

    public_classes = [a for a in dir(mod)
                      if not a.startswith("_")
                      and isinstance(getattr(mod, a, None), type)]
    print("[realtime][ERROR] 未找到任何已知的实时入口。", flush=True)
    print(f"[realtime][ERROR] tabs.realtime.realtime 公开类: {public_classes}",
          flush=True)
    sys.exit(4)


def _list_modules():
    _setup_applio_path()
    import importlib
    for mod_name in ("rvc.realtime.callbacks", "tabs.realtime.realtime"):
        print(f"[realtime] 尝试加载 {mod_name} ...", flush=True)
        try:
            mod = importlib.import_module(mod_name)
            print(f"[realtime] {mod_name} 公开符号:")
            for name in sorted(dir(mod)):
                if name.startswith("_"):
                    continue
                obj = getattr(mod, name, None)
                print(f"    {name:30s} {type(obj).__name__}")
        except Exception as e:
            print(f"[realtime] {mod_name} 加载失败: {e}", flush=True)


def _ensure_prerequisites():
    """
    在实例化 AudioCallbacks 之前，确保 f0 预测器（rmvpe.pt / fcpe.pt）和
    contentvec embedder 等推理期必需文件已下载。

    复用 Applio 自带的 `rvc.lib.tools.prerequisites_download` —— 它会自动
    跳过已存在文件，所以重复调用是安全的。pretraineds（训练用）和 ffmpeg
    都不需要，跳过。
    """
    try:
        from rvc.lib.tools.prerequisites_download import (
            prequisites_download_pipeline,
        )
    except Exception as e:
        print(f"[realtime][WARN] 无法导入 prerequisites_download: {e}",
              flush=True)
        return

    # 关键路径预检查，避免无谓的网络请求日志
    needed = []
    for rel in ("rvc/models/predictors/rmvpe.pt",
                "rvc/models/predictors/fcpe.pt",
                "rvc/models/embedders/contentvec/pytorch_model.bin"):
        if not os.path.isfile(rel):
            needed.append(rel)
    if not needed:
        return

    print("[realtime] 缺少推理期必需文件，正在下载（首次启动需较长时间）:",
          flush=True)
    for n in needed:
        print(f"[realtime]   - {n}", flush=True)

    try:
        prequisites_download_pipeline(
            pretraineds_hifigan=False,   # 训练用，实时不需要
            models=True,                 # rmvpe.pt + fcpe.pt + contentvec
            exe=False,                   # 我们走 resource/ffmepg
        )
        print("[realtime] 推理期模型下载完成", flush=True)
    except Exception as e:
        print(f"[realtime][ERROR] 推理期模型下载失败: {e}", flush=True)
        import traceback
        traceback.print_exc()
        raise


def _run_new(AudioCallbacks, args):
    """
    驱动 Applio 3.6.x 的 AudioCallbacks。

    Applio 内部使用固定 48k 采样率：block_frame = read_chunk_size * 128。
    我们用 worker 传入的 block_time(秒) 反推 read_chunk_size。
    """
    AUDIO_SAMPLE_RATE = 48000
    block_frame = max(1, int(args.block_time * AUDIO_SAMPLE_RATE))
    read_chunk_size = max(1, block_frame // 128)
    print(f"[realtime] block_time={args.block_time}s → "
          f"block_frame={block_frame}, read_chunk_size={read_chunk_size}",
          flush=True)

    init_kwargs = dict(
        pass_through=False,
        read_chunk_size=read_chunk_size,
        cross_fade_overlap_size=args.crossfade_time,
        extra_convert_size=args.extra_time,
        model_path=args.model_path,
        index_path=args.index_path or "",
        f0_method=args.f0_method,
        embedder_model=args.embedder_model,
        embedder_model_custom=None,
        silent_threshold=-90,
        f0_up_key=args.pitch,
        index_rate=args.index_rate,
        protect=args.protect,
        volume_envelope=1.0,
        f0_autotune=False,
        f0_autotune_strength=1.0,
        proposed_pitch=False,
        proposed_pitch_threshold=155.0,
        input_audio_gain=args.input_gain,
        output_audio_gain=args.output_gain,
        monitor_audio_gain=1.0,
        monitor=False,
        vad_enabled=False,
        vad_sensitivity=3,
        vad_frame_ms=30,
        sid=0,
        clean_audio=False,
        clean_strength=0.5,
        post_process=False,
        record_audio=False,
        record_audio_path=None,
        export_format="WAV",
    )

    print("[realtime] 实例化 AudioCallbacks ...", flush=True)
    _ensure_prerequisites()
    try:
        callbacks = AudioCallbacks(**init_kwargs)
    except TypeError:
        # 不同子版本可能微调签名，过滤一遍
        import inspect
        sig = inspect.signature(AudioCallbacks.__init__)
        accepted = {n for n in sig.parameters}
        filtered = {k: v for k, v in init_kwargs.items() if k in accepted}
        print(f"[realtime] 构造器签名: {sorted(accepted - {'self'})}",
              flush=True)
        callbacks = AudioCallbacks(**filtered)
    except Exception as e:
        print(f"[realtime][ERROR] AudioCallbacks 实例化失败: {e}", flush=True)
        import traceback
        traceback.print_exc()
        sys.exit(5)

    audio_manager = callbacks.audio

    print(f"[realtime] 启动音频流 (in={args.input_device}, "
          f"out={args.output_device}) ...", flush=True)
    try:
        audio_manager.start(
            input_device_id=args.input_device,
            output_device_id=args.output_device,
            output_monitor_id=None,
            exclusive_mode=False,
            asio_input_channel=-1,
            asio_output_channel=-1,
            asio_output_monitor_channel=-1,
            read_chunk_size=read_chunk_size,
        )
    except Exception as e:
        print(f"[realtime][ERROR] 音频流启动失败: {e}", flush=True)
        import traceback
        traceback.print_exc()
        sys.exit(6)

    _stopped = {"flag": False}

    def _on_term(*_):
        if _stopped["flag"]:
            return
        _stopped["flag"] = True
        print("[realtime] 收到 SIGTERM，正在停止实时变声 ...", flush=True)
        try:
            audio_manager.stop()
        except Exception as e:
            print(f"[realtime] stop 调用出错: {e}", flush=True)

    signal.signal(signal.SIGTERM, _on_term)
    try:
        signal.signal(signal.SIGINT, _on_term)
    except (ValueError, AttributeError):
        pass

    print("[realtime] 实时变声已启动，按 Ctrl+C 或父进程 terminate 退出",
          flush=True)
    last_log = 0.0
    try:
        while not _stopped["flag"]:
            time.sleep(0.2)
            now = time.time()
            if now - last_log >= 2.0:
                lat = getattr(audio_manager, "latency", None)
                vol = getattr(audio_manager, "volume", None)
                if lat is not None or vol is not None:
                    print(f"[realtime] latency={lat} volume={vol}",
                          flush=True)
                last_log = now
    except KeyboardInterrupt:
        _on_term()
    except Exception as e:
        print(f"[realtime][ERROR] 运行时异常: {e}", flush=True)
        import traceback
        traceback.print_exc()
        sys.exit(7)

    print("[realtime] 已退出", flush=True)


def _run_legacy(cls, args):
    """旧版 Applio：在 tabs.realtime.realtime 中直接实例化。"""
    init_kwargs = dict(
        pth_path=args.model_path,
        index_path=args.index_path,
        f0_method=args.f0_method,
        f0_up_key=args.pitch,
        pitch=args.pitch,
        block_time=args.block_time,
        crossfade_time=args.crossfade_time,
        extra_time=args.extra_time,
        index_rate=args.index_rate,
        protect=args.protect,
        rms_mix_rate=args.rms_mix_rate,
        input_device=args.input_device,
        output_device=args.output_device,
        gpu=args.gpu,
    )

    print(f"[realtime] 实例化 {cls.__name__} ...", flush=True)
    try:
        instance = cls(**init_kwargs)
    except TypeError:
        import inspect
        sig = inspect.signature(cls.__init__)
        accepted = {n for n in sig.parameters}
        filtered = {k: v for k, v in init_kwargs.items() if k in accepted}
        instance = cls(**filtered)

    start_fn = None
    for name in ("start", "start_voice_changer", "run", "loop"):
        fn = getattr(instance, name, None)
        if callable(fn):
            start_fn = fn
            print(f"[realtime] 启动方法: {name}", flush=True)
            break
    stop_fn = None
    for name in ("stop", "stop_voice_changer", "shutdown", "close"):
        fn = getattr(instance, name, None)
        if callable(fn):
            stop_fn = fn
            break

    if start_fn is None:
        print("[realtime][ERROR] 未找到启动方法", flush=True)
        sys.exit(6)

    _stopped = {"flag": False}

    def _on_term(*_):
        if _stopped["flag"]:
            return
        _stopped["flag"] = True
        if stop_fn:
            try:
                stop_fn()
            except Exception as e:
                print(f"[realtime] stop 调用出错: {e}", flush=True)

    signal.signal(signal.SIGTERM, _on_term)
    try:
        signal.signal(signal.SIGINT, _on_term)
    except (ValueError, AttributeError):
        pass

    print("[realtime] 实时变声已启动，按 Ctrl+C 或父进程 terminate 退出",
          flush=True)
    try:
        ret = start_fn()
        if ret is None or not _stopped["flag"]:
            while not _stopped["flag"]:
                time.sleep(0.2)
    except KeyboardInterrupt:
        _on_term()
    except Exception as e:
        print(f"[realtime][ERROR] 运行时异常: {e}", flush=True)
        sys.exit(7)

    print("[realtime] 已退出", flush=True)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--model-path",     required=False, default="")
    p.add_argument("--index-path",     default="")
    p.add_argument("--input-device",   type=int, default=-1)
    p.add_argument("--output-device",  type=int, default=-1)
    p.add_argument("--pitch",          type=int,   default=0)
    p.add_argument("--f0-method",      default="rmvpe")
    p.add_argument("--block-time",     type=float, default=0.25)
    p.add_argument("--crossfade-time", type=float, default=0.05)
    p.add_argument("--extra-time",     type=float, default=2.5)
    p.add_argument("--index-rate",     type=float, default=0.75)
    p.add_argument("--protect",        type=float, default=0.33)
    p.add_argument("--rms-mix-rate",   type=float, default=0.25)
    p.add_argument("--input-gain",     type=float, default=1.0,
                   help="输入电平倍数 (1.0=100%%)")
    p.add_argument("--output-gain",    type=float, default=1.5,
                   help="输出电平倍数 (1.5=150%%，对应 Applio UI 默认)")
    p.add_argument("--gpu",            type=int,   default=0,
                   help="GPU 序号；通过 CUDA_VISIBLE_DEVICES 选择对应卡")
    p.add_argument("--embedder-model", default="contentvec",
                   help="嵌入模型 (3.6.x 新增必填)")
    p.add_argument("--list-modules", action="store_true",
                   help="只打印 Applio 实时模块的公开符号后退出")
    args = p.parse_args()

    if args.list_modules:
        _list_modules()
        return

    if not args.model_path or not os.path.isfile(args.model_path):
        print(f"[realtime][ERROR] 无效模型路径: {args.model_path}", flush=True)
        sys.exit(1)

    # 通过环境变量限定可见 GPU（Applio Config() 单例靠 torch 自动选第 0 张）
    if args.gpu >= 0:
        os.environ["CUDA_VISIBLE_DEVICES"] = str(args.gpu)
    else:
        os.environ["CUDA_VISIBLE_DEVICES"] = ""

    _setup_applio_path()
    kind, target = _import_audio_callbacks()

    if kind == "new":
        _run_new(target, args)
    else:
        _run_legacy(target, args)


if __name__ == "__main__":
    main()
