"""
workers/_gptsovits_runner.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~
GPTSoVITSInferWorker 的子进程入口：在 GPT-SoVITS 仓库的工作目录下，
导入 inference_webui 模块完成 TTS 合成并把结果写到磁盘。

GPT-SoVITS 仓库由 PipWorker.install_from_git("GPT-SoVITS") 克隆到：
    <project_root>/_git_projects/GPT-SoVITS_main/
"""

import argparse
import os
import sys
import traceback

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_GIT_PROJECTS = os.path.normpath(
    os.path.join(_THIS_DIR, "..", "_git_projects"))


def _log(msg: str):
    sys.stdout.write(f"[runner] {msg}\n")
    sys.stdout.flush()


def _err(msg: str):
    sys.stdout.write(f"[runner][ERROR] {msg}\n")
    sys.stdout.flush()


def _ensure_nltk_resources():
    """确保英文 g2p 用到的 NLTK 资源齐全。
    现在的 nltk 在 pos_tag 里找的是 `averaged_perceptron_tagger_eng`,
    旧的 `averaged_perceptron_tagger` 即使装了也不顶用。已存在则秒过。
    """
    try:
        import nltk
    except Exception as e:
        _err(f"导入 nltk 失败: {e}")
        return
    needed = [
        ("taggers/averaged_perceptron_tagger_eng",
         "averaged_perceptron_tagger_eng"),
        ("corpora/cmudict", "cmudict"),
    ]
    for path, pkg in needed:
        try:
            nltk.data.find(path)
        except LookupError:
            _log(f"下载 NLTK 资源: {pkg}")
            try:
                nltk.download(pkg, quiet=True)
            except Exception as e:
                _err(f"下载 {pkg} 失败: {e}")


def _find_gptsovits_root() -> str:
    """
    在 _git_projects/ 下寻找 GPT-SoVITS 仓库根目录。
    校验子目录 GPT_SoVITS/ 必须存在（仓库标志）。
    """
    if not os.path.isdir(_GIT_PROJECTS):
        return ""
    for entry in os.listdir(_GIT_PROJECTS):
        full = os.path.join(_GIT_PROJECTS, entry)
        if not os.path.isdir(full):
            continue
        if not entry.lower().startswith("gpt-sovits"):
            continue
        if os.path.isdir(os.path.join(full, "GPT_SoVITS")):
            return full
    return ""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--gpt-model", required=True, help="GPT 权重 .ckpt 路径")
    parser.add_argument("--sovits-model", required=True, help="SoVITS 权重 .pth 路径")
    parser.add_argument("--ref-audio", required=True, help="参考音频 wav 路径(3~10s)")
    parser.add_argument("--ref-text", required=True, help="参考音频对应文本")
    parser.add_argument("--ref-language", default="中文",
                        help="参考文本语种 中文/英文/日文/中英混/日英混/多语种混")
    parser.add_argument("--target-text", required=True, help="待合成目标文本")
    parser.add_argument("--target-language", default="中文",
                        help="目标文本语种")
    parser.add_argument("--output", required=True, help="输出 wav 路径")
    parser.add_argument("--how-to-cut", default="不切",
                        help="切分策略：不切/凑四句一切/凑50字一切/按中文句号切/按英文句号切/按标点符号切")
    parser.add_argument("--top-k", type=int, default=15)
    parser.add_argument("--top-p", type=float, default=1.0)
    parser.add_argument("--temperature", type=float, default=1.0)
    parser.add_argument("--speed", type=float, default=1.0)
    parser.add_argument("--device", default="cuda:0")
    args = parser.parse_args()

    root = _find_gptsovits_root()
    if not root:
        _err("未找到 GPT-SoVITS 仓库，请先在「未安装」页完成安装")
        sys.exit(2)
    _log(f"GPT-SoVITS 根目录: {root}")

    # 配置工作目录与 import 路径
    os.chdir(root)
    if root not in sys.path:
        sys.path.insert(0, root)
    gptsovits_dir = os.path.join(root, "GPT_SoVITS")
    if gptsovits_dir not in sys.path:
        sys.path.insert(0, gptsovits_dir)

    # 校验文件
    for label, path in (("GPT 模型", args.gpt_model),
                        ("SoVITS 模型", args.sovits_model),
                        ("参考音频", args.ref_audio)):
        if not os.path.exists(path):
            _err(f"{label}文件不存在: {path}")
            sys.exit(3)

    # 设备：GPT-SoVITS 通过 CUDA_VISIBLE_DEVICES 选 GPU
    if args.device and args.device.lower().startswith("cuda:"):
        try:
            gpu_idx = args.device.split(":", 1)[1]
            os.environ["CUDA_VISIBLE_DEVICES"] = gpu_idx
            _log(f"使用 GPU: cuda:{gpu_idx}")
        except Exception:
            pass
    elif args.device and args.device.lower() == "cpu":
        os.environ["CUDA_VISIBLE_DEVICES"] = "-1"
        _log("使用 CPU 推理（速度较慢）")

    _log("加载 GPT-SoVITS 推理模块...")
    _ensure_nltk_resources()
    try:
        try:
            from GPT_SoVITS import inference_webui as iw
        except ImportError:
            import inference_webui as iw  # type: ignore
    except Exception as e:
        _err(f"导入 inference_webui 失败: {e}")
        _err("请检查 GPT-SoVITS 仓库完整性与依赖是否装好")
        _err(traceback.format_exc())
        sys.exit(4)

    _log("加载 GPT 模型...")
    try:
        iw.change_gpt_weights(gpt_path=args.gpt_model)
    except Exception as e:
        _err(f"加载 GPT 模型失败: {e}")
        _err(traceback.format_exc())
        sys.exit(5)

    _log("加载 SoVITS 模型...")
    try:
        iw.change_sovits_weights(sovits_path=args.sovits_model)
    except Exception as e:
        _err(f"加载 SoVITS 模型失败: {e}")
        _err(traceback.format_exc())
        sys.exit(6)

    _log("开始合成语音...")
    try:
        import numpy as np
        import soundfile as sf

        chunks = []
        sr = None
        # get_tts_wav 是一个 generator，逐块 yield (sample_rate, audio_ndarray)
        gen = iw.get_tts_wav(
            ref_wav_path=args.ref_audio,
            prompt_text=args.ref_text,
            prompt_language=args.ref_language,
            text=args.target_text,
            text_language=args.target_language,
            how_to_cut=args.how_to_cut,
            top_k=args.top_k,
            top_p=args.top_p,
            temperature=args.temperature,
            speed=args.speed,
        )
        for sr_, audio in gen:
            sr = sr_
            chunks.append(audio)
            _log(f"已生成 {len(chunks)} 块 (sr={sr})")

        if not chunks or sr is None:
            _err("合成结果为空")
            sys.exit(7)

        full = np.concatenate(chunks)
        out_dir = os.path.dirname(os.path.abspath(args.output))
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
        sf.write(args.output, full, sr)
        _log(f"输出已保存: {args.output}")
    except Exception as e:
        _err(f"合成失败: {e}")
        _err(traceback.format_exc())
        sys.exit(8)


if __name__ == "__main__":
    main()
