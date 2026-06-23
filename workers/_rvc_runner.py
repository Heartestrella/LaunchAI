"""
workers/_rvc_runner.py
~~~~~~~~~~~~~~~~~~~~~~
RVCInferWorker 的子进程入口：把 RVCInferWorker 的 CLI 参数翻译成 Applio
`core.py infer` 的 flag，并以 Applio 仓库为工作目录执行。

Applio 仓库由 PipWorker.install_from_git("Applio") 克隆到：
    <project_root>/_git_projects/Applio_main/
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path
import re

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_GIT_PROJECTS = os.path.normpath(
    os.path.join(_THIS_DIR, "..", "_git_projects"))


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

APPLIO_CORE = os.path.join(APPLIO_ROOT, "core.py")


def _parse_device(device: str) -> int:
    """Applio --gpu 接整数索引，cpu 用 -1。"""
    if not device or device.lower() == "cpu":
        return -1
    if device.lower().startswith("cuda:"):
        try:
            return int(device.split(":", 1)[1])
        except (ValueError, IndexError):
            return 0
    return 0


def _normalize_f0(method: str) -> str:
    """老配置里残留的 f0 算法名 → Applio 3.6+ 仍然合法的值。

    Applio 在 3.6 移除了 ``rmvpe+`` / ``pm`` / ``harvest`` 这几个旧选项，
    传过去会直接 ``exit=2 invalid choice``。``hybrid[...]`` 系列在 3.6.2
    的 CLI 里仍是合法 choice，但 ``rvc/infer/pipeline.py::get_f0`` 没有
    实现对应分支，运行时会抛 ``UnboundLocalError``，所以一并视为非法。
    这里统一映射到 rmvpe（质量损失最小）。完全不认识的值不再特判，交给
    Applio 自己抛错以暴露问题。
    """
    method = (method or "rmvpe").strip().lower()
    # 旧值/失效值 → 当前可用值的迁移表
    legacy = {
        "rmvpe+":  "rmvpe",
        "pm":      "rmvpe",
        "harvest": "rmvpe",
        "dio":     "rmvpe",   # 历史 alias 顺手挡掉
    }
    if method.startswith("hybrid["):
        return "rmvpe"
    return legacy.get(method, method)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--input",         required=True)
    p.add_argument("--output",        required=True, help="完整的输出文件路径")
    p.add_argument("--model-path",    required=True, help=".pth 模型路径")
    p.add_argument("--index-path",    default="",    help=".index 检索文件路径，可空")
    p.add_argument("--device",        default="cuda:0")
    p.add_argument("--f0-method",     default="rmvpe")
    p.add_argument("--transpose",     type=int,   default=0)
    p.add_argument("--index-rate",    type=float, default=0.75)
    p.add_argument("--filter-radius", type=int,   default=3,
                   help="保留兼容旧 CLI；Applio 3.6+ 已移除该项，运行时忽略")
    p.add_argument("--resample-sr",   type=int,   default=0,
                   help="保留兼容旧 CLI；Applio 3.6+ 已移除该项，运行时忽略")
    p.add_argument("--rms-mix-rate",  type=float, default=0.25,
                   help="映射为 Applio 的 --volume_envelope")
    p.add_argument("--protect",       type=float, default=0.33)
    p.add_argument("--split-infer",   action="store_true")
    args = p.parse_args()

    if not os.path.isdir(APPLIO_ROOT) or not os.path.isfile(APPLIO_CORE):
        print(f"[runner][ERROR] 未找到 Applio 仓库: {APPLIO_ROOT}", flush=True)
        print("[runner][ERROR] 请先在 RVC 页面完成 Applio 安装", flush=True)
        sys.exit(2)

    out_dir = Path(args.output).parent
    out_dir.mkdir(parents=True, exist_ok=True)

    gpu = _parse_device(args.device)
    f0_method = _normalize_f0(args.f0_method)
    export_format = Path(args.output).suffix.lstrip(".").upper() or "WAV"

    # Applio 3.6.x 的 `core.py infer` 不再接受 --filter_radius / --resample_sr / --gpu，
    # 并将 --rms_mix_rate 改名为 --volume_envelope。
    # GPU 选择通过子进程的 CUDA_VISIBLE_DEVICES 环境变量来约束。
    cmd = [
        sys.executable, "core.py", "infer",
        "--pth_path",        args.model_path,
        "--input_path",      args.input,
        "--output_path",     args.output,
        "--pitch",           str(args.transpose),
        "--f0_method",       f0_method,
        "--index_rate",      str(args.index_rate),
        "--volume_envelope", str(args.rms_mix_rate),
        "--protect",         str(args.protect),
        "--split_audio",     str(args.split_infer),
        "--clean_audio",     "False",
        "--export_format",   export_format,
        "--f0_autotune",     "False",
    ]
    if args.index_path and os.path.exists(args.index_path):
        cmd.extend(["--index_path", args.index_path])

    env = os.environ.copy()
    if gpu < 0:
        # CPU 模式：让子进程看不到任何 CUDA 设备
        env["CUDA_VISIBLE_DEVICES"] = ""
    else:
        env["CUDA_VISIBLE_DEVICES"] = str(gpu)

    print(f"[runner] APPLIO_ROOT = {APPLIO_ROOT}", flush=True)
    print(f"[runner] device      = {args.device} "
          f"(CUDA_VISIBLE_DEVICES={env['CUDA_VISIBLE_DEVICES']!r})", flush=True)
    print(f"[runner] f0_method   = {f0_method}", flush=True)
    print(f"[runner] export_fmt  = {export_format}", flush=True)
    if args.filter_radius != 3 or args.resample_sr != 0:
        print("[runner][WARN] --filter-radius / --resample-sr 在 Applio 3.6+ "
              "已被移除，本次调用将忽略它们", flush=True)
    print(f"[runner] cmd         = {' '.join(cmd)}", flush=True)
    print("[runner] starting Applio core.py infer ...", flush=True)

    # 直通 stdout/stderr 给父进程的 RVCInferWorker 解析
    proc = subprocess.Popen(
        cmd,
        cwd=APPLIO_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    for line in iter(proc.stdout.readline, ""):
        if not line:
            break
        sys.stdout.write(line)
        sys.stdout.flush()
    proc.wait()

    if proc.returncode != 0:
        print(f"[runner][ERROR] Applio 推理失败 (exit={proc.returncode})",
              flush=True)
        sys.exit(proc.returncode)

    if not os.path.exists(args.output):
        print(f"[runner][ERROR] 推理未生成输出文件: {args.output}", flush=True)
        sys.exit(1)

    print(f"[runner] output written: {args.output}", flush=True)


if __name__ == "__main__":
    main()
