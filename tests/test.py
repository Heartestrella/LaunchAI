"""
RealESRGANWorker
~~~~~~~~~~~~~~~~
realesrgan-ncnn-vulkan.exe 的后台推理工作线程。
代码风格对齐 DemucsWorker，通过 QThread + subprocess 调用可执行文件，
逐行解析 stderr 输出获取进度，所有状态通过 Qt 信号传出。

完整 CLI 参数速查：
  realesrgan-ncnn-vulkan.exe
    -i  input-path      输入图片/目录（jpg/png/webp）
    -o  output-path     输出图片/目录（jpg/png/webp）
    -s  scale           放大倍数（2/3/4，默认 4）
    -t  tile-size       图块大小（>=32 / 0=自动，默认 0）
    -m  model-path      模型目录（默认 ./models）
    -n  model-name      模型名称
    -g  gpu-id          GPU 设备（-1=CPU，默认 auto，多卡 0,1,2）
    -j  load:proc:save  线程数（默认 1:2:2）
    -x                  启用 TTA 模式
    -f  format          输出格式（png/jpg/webp，部分版本支持）
"""

import os
import re
import sys
import time
import subprocess
from pathlib import Path

from PyQt6.QtCore import QThread, pyqtSignal


# ── 支持的模型列表 ──────────────────────────────────────────────────────
MODELS = {
    "realesrgan-x4plus":       {"scale": [4],       "desc": "通用 4× 超分，适合真实照片"},
    "realesrgan-x4plus-anime": {"scale": [4],       "desc": "动漫/插画专用 4×"},
    "realesrnet-x4plus":       {"scale": [4],       "desc": "轻量 4× 超分"},
    "realesr-animevideov3":    {"scale": [2, 3, 4], "desc": "动漫视频帧专用"},
}

# 可执行文件默认路径（相对于项目根目录）
DEFAULT_EXE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "resource", "realesrgan-ncnn-vulkan", "realesrgan-ncnn-vulkan.exe"
)


class RealESRGANWorker(QThread):
    """
    realesrgan-ncnn-vulkan 推理工作线程。

    Parameters (params dict)
    ------------------------
    exe_path   : str   可执行文件路径（默认 DEFAULT_EXE）
    input      : str   输入图片路径或目录
    output_dir : str   输出目录（Worker 会自动构建完整输出路径）
    model      : str   模型名称（MODELS 中的键）
    scale      : int   放大倍数（2/3/4，默认 4）
    tile       : int   图块大小（0=自动，默认 0）
    gpu_id     : str   GPU 设备 ID，"-1"=CPU，"auto"=自动（默认 "auto"）
    tta        : bool  启用 TTA 模式（默认 False）
    fmt        : str   输出格式 "png"/"jpg"/"webp"（默认 "png"）
    threads    : str   线程数 "load:proc:save"（默认 "1:2:2"）
    model_dir  : str   模型目录（默认与 exe 同级的 models/）
    """

    # 信号
    progress = pyqtSignal(int, str)   # (0-100, 状态描述)
    output = pyqtSignal(str)        # 实时 HTML 日志行
    finished = pyqtSignal(str, float)  # (输出目录路径, 总耗时秒)
    error = pyqtSignal(str)        # 错误消息

    def __init__(self, params: dict):
        super().__init__()
        self.params = params
        self._process: subprocess.Popen | None = None
        self._cancelled = False

    # ── 公共接口 ────────────────────────────────────────────────────────

    def cancel(self):
        """取消当前任务，强制终止子进程。"""
        self._cancelled = True
        if self._process:
            self._process.terminate()
            time.sleep(0.4)
            if self._process.poll() is None:
                self._process.kill()

    # ── 主线程逻辑 ──────────────────────────────────────────────────────

    def run(self):
        try:
            self._run_inference()
        except Exception as e:
            self.error.emit(f"推理过程中发生异常: {e}")

    def _run_inference(self):
        p = self.params

        # ── 参数解析 ──────────────────────────────────────────────────
        exe_path = p.get("exe_path",   DEFAULT_EXE)
        input_path = p.get("input",      "")
        output_dir = p.get("output_dir", "./results")
        model_name = p.get("model",      "realesrgan-x4plus")
        scale = int(p.get("scale",  4))
        tile = int(p.get("tile",   0))
        gpu_id = str(p.get("gpu_id", "auto"))
        tta = bool(p.get("tta",   False))
        fmt = p.get("fmt",        "png").lower()
        threads = p.get("threads",    "1:2:2")
        model_dir = p.get("model_dir",  "")

        # ── 基础校验 ──────────────────────────────────────────────────
        if not input_path:
            self.error.emit("未指定输入文件或目录")
            return

        if not os.path.exists(input_path):
            self.error.emit(f"输入路径不存在: {input_path}")
            return

        if not os.path.isfile(exe_path):
            self.error.emit(
                f"找不到可执行文件:\n{exe_path}\n"
                "请确认 resource/realesrgan-ncnn-vulkan/ 目录完整。"
            )
            return

        # ── 确定模型目录 ──────────────────────────────────────────────
        if not model_dir:
            model_dir = os.path.join(os.path.dirname(exe_path), "models")

        if not os.path.isdir(model_dir):
            self.error.emit(f"模型目录不存在: {model_dir}")
            return

        # ── 构建输出路径 ──────────────────────────────────────────────
        # 输入是文件 → 输出是同名文件（放到 output_dir）
        # 输入是目录 → 输出是目录
        os.makedirs(output_dir, exist_ok=True)

        if os.path.isfile(input_path):
            stem = Path(input_path).stem
            out_path = os.path.join(output_dir, f"{stem}_x{scale}.{fmt}")
        else:
            out_path = output_dir

        # ── 构建命令行 ────────────────────────────────────────────────
        cmd = [exe_path,
               "-i", input_path,
               "-o", out_path,
               "-s", str(scale),
               "-t", str(tile),
               "-n", model_name,
               "-m", model_dir,
               "-j", threads]

        if gpu_id != "auto":
            cmd.extend(["-g", gpu_id])

        if tta:
            cmd.append("-x")

        # 部分版本支持 -f 指定输出格式
        if fmt in ("jpg", "webp"):
            cmd.extend(["-f", fmt])

        # ── 日志：打印完整命令 ────────────────────────────────────────
        cmd_str = " ".join(f'"{c}"' if " " in c else c for c in cmd)
        self.output.emit(self._html(f"▶ 执行命令:", "#888888"))
        self.output.emit(self._html(cmd_str, "#555555"))
        self.output.emit(self._html("─" * 50, "#333333"))
        self.progress.emit(0, "启动推理引擎…")

        # ── 启动子进程 ────────────────────────────────────────────────
        # realesrgan-ncnn-vulkan 将日志打到 stderr
        t0 = time.time()
        try:
            self._process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,   # 合并到 stdout 读取
                text=True,
                bufsize=1,
                encoding="utf-8",
                errors="replace",
            )
        except FileNotFoundError:
            self.error.emit(f"无法启动进程，请检查路径:\n{exe_path}")
            return
        except PermissionError:
            self.error.emit(f"权限不足，无法执行:\n{exe_path}")
            return

        # ── 逐行读取输出 ──────────────────────────────────────────────
        all_lines: list[str] = []
        for raw in iter(self._process.stdout.readline, ""):
            if self._cancelled:
                self._process.terminate()
                self.output.emit(self._html(
                    "⚠ 用户取消了推理任务", "#FF9800", bold=True))
                return

            line = raw.rstrip()
            if not line:
                continue

            all_lines.append(line)
            self._parse_line(line)

        self._process.wait()
        elapsed = time.time() - t0

        if self._cancelled:
            return

        rc = self._process.returncode
        if rc == 0:
            self.output.emit(self._html(
                f"✔ 推理完成，耗时 {elapsed:.1f}s → {out_path}", "#4CAF50", bold=True))
            self.progress.emit(100, "推理完成")
            self.finished.emit(out_path, elapsed)
        else:
            # 打印完整错误上下文
            self.output.emit(self._html(
                f"✘ 进程异常退出（返回码 {rc}）", "#F44336", bold=True))
            self.output.emit(self._html("── 完整输出 ──", "#888888"))
            for ln in all_lines[-30:]:          # 最后 30 行防刷屏
                self.output.emit(self._html(ln, "#FF6B6B"))
            self.error.emit(f"推理失败，返回码: {rc}")

    # ── 行解析：识别进度 / 警告 / 错误 ─────────────────────────────────

    def _parse_line(self, line: str):
        """
        realesrgan-ncnn-vulkan 输出示例：
            0.00%
            10.23%
            100.00%
            [WARN] ...
            [ERROR] ...
        """
        # 百分比进度（最常见格式：单独一行 "12.34%"）
        m = re.search(r'(\d+(?:\.\d+)?)\s*%', line)
        if m:
            pct = min(99, int(float(m.group(1))))   # 保留 100% 给 finished
            self.progress.emit(pct, f"{pct}%")
            self.output.emit(self._html(line, "#2196F3"))
            return

        low = line.lower()

        if "error" in low or "failed" in low or "exception" in low:
            self.output.emit(self._html(line, "#F44336"))
        elif "warn" in low:
            self.output.emit(self._html(line, "#FF9800"))
        elif any(kw in low for kw in ("saving", "done", "finish", "complete")):
            self.output.emit(self._html(line, "#4CAF50"))
        elif any(kw in low for kw in ("load", "model", "init", "gpu", "vulkan", "ncnn")):
            self.output.emit(self._html(line, "#9E9E9E"))
        else:
            self.output.emit(self._html(line, "#CCCCCC"))

    # ── HTML 工具 ────────────────────────────────────────────────────────

    @staticmethod
    def _html(text: str, color: str = "", bold: bool = False) -> str:
        """生成内联 HTML 片段（供 QLabel/QTextEdit 富文本显示）。"""
        if not color and not bold:
            return text
        styles = []
        if color:
            styles.append(f"color:{color}")
        if bold:
            styles.append("font-weight:bold")
        return f'<span style="{";".join(styles)}">{text}</span>'


# ══════════════════════════════════════════════════════════════════════
#  批量推理辅助：将 InferencePage 的文件列表逐张送入 Worker
# ══════════════════════════════════════════════════════════════════════

class BatchRealESRGANWorker(QThread):
    """
    批量处理版本：逐文件调用 realesrgan-ncnn-vulkan，
    整合进度并向 UI 汇报，与 InferencePage._stat_widgets 对应。

    每张图独立启动一次子进程（与 ncnn-vulkan 单次处理一张的行为一致）。
    若需要目录批处理，将 input_files 替换为单个目录路径即可。
    """

    # ── UI 对齐信号（与 InferenceWorker 接口保持一致）────────────────
    progress = pyqtSignal(int, str)    # (全局 0-100, 当前文件名)
    finished = pyqtSignal(int, float)  # (总处理数, 总耗时)
    error = pyqtSignal(str)
    log_line = pyqtSignal(str)         # HTML 日志行

    # 单张完成信号（供 FileListItem 更新状态）
    file_done = pyqtSignal(str, str)  # (input_path, output_path)
    file_error = pyqtSignal(str, str)  # (input_path, error_msg)

    def __init__(self, files: list[str], params: dict):
        """
        Parameters
        ----------
        files  : 输入文件路径列表
        params : 与 RealESRGANWorker 相同的参数字典（无需 'input' 键）
        """
        super().__init__()
        self._files = files
        self._params = params
        self._cancelled = False
        self._cur_worker: RealESRGANWorker | None = None

    def cancel(self):
        self._cancelled = True
        if self._cur_worker:
            self._cur_worker.cancel()

    def run(self):
        total = len(self._files)
        t0 = time.time()
        success = 0

        for idx, fpath in enumerate(self._files):
            if self._cancelled:
                self.log_line.emit(
                    RealESRGANWorker._html("⚠ 批处理已取消", "#FF9800", bold=True))
                break

            name = Path(fpath).name
            # 全局进度：以文件数量为粒度
            global_pct = int(idx / total * 100)
            self.progress.emit(global_pct, name)
            self.log_line.emit(
                RealESRGANWorker._html(f"[{idx+1}/{total}] 开始处理: {name}", "#888888"))

            # 为当前文件创建独立 Worker（在同一线程内同步运行）
            params_for_file = {**self._params, "input": fpath}
            single = RealESRGANWorker(params_for_file)

            # 转接日志到批量日志信号
            single.output.connect(self.log_line.emit)

            # 进度：将单张的 0-100 映射到本张在全局中的区间
            def _relay_progress(pct: int, _desc: str,
                                _idx=idx, _total=total):
                seg_start = int(_idx / _total * 100)
                seg_end = int((_idx + 1) / _total * 100)
                mapped = seg_start + int(pct / 100 * (seg_end - seg_start))
                self.progress.emit(mapped, name)
            single.progress.connect(_relay_progress)

            # 收集结果
            _out_path = [""]
            _err_msg = [""]

            def _on_fin(out: str, _elapsed: float, _p=_out_path):
                _p[0] = out

            def _on_err(msg: str, _e=_err_msg):
                _e[0] = msg

            single.finished.connect(_on_fin)
            single.error.connect(_on_err)

            # 同步执行（在当前线程中直接调用 run，不新开线程）
            self._cur_worker = single
            single.run()   # 注意：直接调用 run()，而非 start()
            self._cur_worker = None

            if self._cancelled:
                break

            if _err_msg[0]:
                self.file_error.emit(fpath, _err_msg[0])
                self.log_line.emit(
                    RealESRGANWorker._html(
                        f"✘ [{name}] 失败: {_err_msg[0]}", "#F44336"))
            else:
                success += 1
                self.file_done.emit(fpath, _out_path[0])
                self.log_line.emit(
                    RealESRGANWorker._html(
                        f"✔ [{name}] → {_out_path[0]}", "#4CAF50"))

        elapsed = time.time() - t0
        self.progress.emit(100, "批处理完成")
        self.log_line.emit(
            RealESRGANWorker._html(
                f"[完成] 成功 {success}/{total} 张，总耗时 {elapsed:.1f}s",
                "#4CAF50", bold=True))
        self.finished.emit(success, elapsed)


# ══════════════════════════════════════════════════════════════════════
#  与 InferencePage 对接的桥接函数
# ══════════════════════════════════════════════════════════════════════

def build_worker_from_ui_params(files: list[str], ui_params: dict,
                                exe_path: str = DEFAULT_EXE) -> BatchRealESRGANWorker:
    """
    将 ParamPanel.get_params() 返回的字典转换为 BatchRealESRGANWorker 所需格式。

    ui_params 键（来自 ParamPanel）：
        model, device, scale, tile, tile_pad, pre_pad,
        fp16, face_enh, face_str, out_dir, out_fmt, keep_suffix

    ncnn-vulkan 不支持 fp16 / face_enh / pre_pad，忽略这些字段。
    """
    # 解析 scale（"4× (默认)" → 4）
    scale_raw = ui_params.get("scale", "4× (默认)")
    scale_m = re.match(r'(\d+)', str(scale_raw))
    scale = int(scale_m.group(1)) if scale_m else 4

    # 解析 gpu_id（"GPU · CUDA (cuda:0)" → "0"，"CPU" → "-1"）
    device_str = ui_params.get("device", "auto")
    if "CPU" in device_str.upper():
        gpu_id = "-1"
    else:
        gm = re.search(r'cuda:(\d+)', device_str, re.I)
        gpu_id = gm.group(1) if gm else "auto"

    # 输出格式（"PNG（无损）" → "png"）
    fmt_raw = ui_params.get("out_fmt", "PNG").split("（")[0].strip().lower()

    # 模型名映射（UI 显示名 → ncnn-vulkan 模型名）
    model_map = {
        "realesrgan_x4plus":          "realesrgan-x4plus",
        "realesrgan_x4plus_anime_6b":  "realesrgan-x4plus-anime",
        "realesrgan_x2plus":           "realesrnet-x4plus",
        "realesrganv3_x4":             "realesr-animevideov3",
    }
    model_raw = ui_params.get("model", "realesrgan-x4plus")
    model_key = model_raw.lower().replace(" ", "_").replace("-", "_")
    model = model_map.get(model_key, model_raw)

    worker_params = {
        "exe_path":   exe_path,
        "output_dir": ui_params.get("out_dir", "./results"),
        "model":      model,
        "scale":      scale,
        "tile":       ui_params.get("tile", 0),
        "gpu_id":     gpu_id,
        "fmt":        fmt_raw,
        "threads":    "1:2:2",
        "tta":        False,
    }

    return BatchRealESRGANWorker(files, worker_params)
