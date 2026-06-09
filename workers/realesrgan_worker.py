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
    os.getcwd(),
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

        # 如果 model_dir 是相对路径，解析为以 exe 所在目录为基准的绝对路径
        if not os.path.isabs(model_dir):
            model_dir = os.path.join(os.path.dirname(exe_path), model_dir)

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

        # gpu_id 说明:
        #   "auto" → 不传 -g，ncnn-vulkan 自动选第一块 GPU
        #   "-1"   → CPU 模式，ncnn-vulkan 不接受 -g -1，也不传 -g
        #   "0"/"1" → 指定 GPU 编号
        if gpu_id not in ("auto", "-1", ""):
            cmd.extend(["-g", gpu_id])

        if tta:
            cmd.append("-x")

        # 部分版本支持 -f 指定输出格式
        if fmt in ("jpg", "webp"):
            cmd.extend(["-f", fmt])

        # ── 日志：打印完整命令 ────────────────────────────────────────
        # 调试信息：打印解析后的模型名与模型目录
        try:
            self.output.emit(self._html(f"▶ 模型: {model_name}", "#888888"))
            self.output.emit(self._html(f"▶ 模型目录: {model_dir}", "#888888"))
        except Exception:
            pass
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
    # scale：ParamPanel.get_params() 返回的是纯数字字符串 "4"/"3"/"2"
    scale_raw = ui_params.get("scale", 4)
    try:
        scale = int(str(scale_raw).split("×")[0].strip())
    except (ValueError, AttributeError):
        scale = 4

    # gpu_id：ParamPanel.get_params() 直接返回 "auto" / "0" / "-1"
    # 不再需要从 device 字符串解析，直接取 gpu_id 键
    gpu_id = str(ui_params.get("gpu_id", "auto"))

    # 输出格式：ParamPanel 返回的是纯小写 "png"/"jpg"/"webp"
    fmt_raw = ui_params.get("fmt", ui_params.get("out_fmt", "png"))
    fmt_raw = fmt_raw.split("（")[0].strip().lower()
    if fmt_raw not in ("png", "jpg", "webp"):
        fmt_raw = "png"

    # 模型名：ParamPanel.get_params() 中 "model" 已经是 ncnn-vulkan 的模型名
    # （如 "realesrgan-x4plus"），无需再映射；但保留映射作为向后兼容
    model_map = {
        "realesrgan_x4plus":           "realesrgan-x4plus",
        "realesrgan_x4plus_anime_6b":  "realesrgan-x4plus-anime",
        "realesrgan_x2plus":           "realesrnet-x4plus",
        "realesrganv3_x4":             "realesr-animevideov3",
    }
    model_raw = ui_params.get("model", "realesrgan-x4plus")
    model_key = model_raw.lower().replace(" ", "_").replace("-", "_")
    model = model_map.get(model_key, model_raw)   # 未命中直接用原值
    # 尝试在 exe 同级的 models/ 目录中解析实际可用的模型名（.param 文件）
    model_dir = os.path.join(os.path.dirname(exe_path), "models")
    if os.path.isdir(model_dir):
        try:
            def _norm(s: str) -> str:
                return re.sub(r'[^a-z0-9]', '', s.lower())

            target = _norm(model)
            for fname in os.listdir(model_dir):
                if not fname.lower().endswith('.param'):
                    continue
                stem = Path(fname).stem
                if target in _norm(stem) or _norm(stem) in target:
                    model = stem
                    break
        except Exception:
            pass

    # 为避免 ncnn-vulkan 在内部拼接路径导致重复前缀，传递给 exe 的 model_dir 使用相对于 exe 的相对路径
    try:
        model_dir_param = os.path.relpath(model_dir, os.path.dirname(exe_path))
    except Exception:
        model_dir_param = model_dir

    worker_params = {
        "exe_path":   exe_path,
        "output_dir": ui_params.get("out_dir", "./results"),
        "model":      model,
        "scale":      scale,
        "tile":       int(ui_params.get("tile", 0)),
        "gpu_id":     gpu_id,
        "fmt":        fmt_raw,
        "threads":    ui_params.get("threads", "1:2:2"),
        "tta":        bool(ui_params.get("tta", False)),
        "keep_suffix": bool(ui_params.get("keep_suffix", True)),
        "model_dir":  model_dir_param,
    }

    return BatchRealESRGANWorker(files, worker_params)


# ══════════════════════════════════════════════════════════════════════
#  GPU 枚举工具  —  调用 exe -h 解析可用 GPU 列表
# ══════════════════════════════════════════════════════════════════════

def detect_gpus(exe_path: str = DEFAULT_EXE) -> dict[str, str]:
    """
    尝试通过 realesrgan-ncnn-vulkan -h 或 vulkaninfo 枚举可用 GPU。

    Returns
    -------
    dict  {显示名: gpu_id_str}
    例如：{"GPU 0 · NVIDIA GeForce RTX 4090": "0",
           "GPU 1 · AMD Radeon RX 7900":      "1",
           "CPU（软件渲染）":                  "-1"}

    若枚举失败则返回最小集：{"GPU 0 (默认)": "auto", "CPU": "-1"}
    """
    result: dict[str, str] = {}

    if os.path.isfile(exe_path):
        try:
            # realesrgan-ncnn-vulkan 运行时会在 stderr 打印 GPU 信息
            # 用一个不存在的输入触发它打印帮助/设备信息后退出
            proc = subprocess.run(
                [exe_path, "-i", "__probe__", "-o", "__probe_out__"],
                capture_output=True, text=True, timeout=6,
                encoding="utf-8", errors="replace"
            )
            combined = proc.stdout + proc.stderr

            # 匹配形如 "GPU 0: NVIDIA GeForce RTX 3080 (device_id=...)"
            # 或      "[0] NVIDIA GeForce RTX 3080"
            for m in re.finditer(
                    r'(?:GPU\s*)?[\[\(]?(\d+)[\]\)]?\s*[:\-]?\s*(.+?)(?:\s*\(|$)',
                    combined, re.MULTILINE):
                idx = m.group(1).strip()
                name = m.group(2).strip()
                # 过滤掉明显不是 GPU 名的行
                if len(name) > 3 and not any(
                        skip in name.lower()
                        for skip in ("usage", "option", "help", "error",
                                     "infile", "outfile", "default")):
                    display = f"GPU {idx} · {name}"
                    result[display] = idx

        except Exception:
            pass

    # 若没枚举到任何 GPU，给出默认选项
    if not result:
        result["GPU 0 (默认自动)"] = "auto"

    # 始终附加 CPU 选项
    result["CPU（禁用 GPU）"] = "-1"
    return result


# ══════════════════════════════════════════════════════════════════════
#  输出路径构建  —  支持"保留原始文件名后缀"选项
# ══════════════════════════════════════════════════════════════════════

def build_output_path(input_path: str, output_dir: str,
                      scale: int, fmt: str,
                      keep_suffix: bool = True) -> str:
    """
    根据输入文件名、放大倍数、格式和后缀策略构建输出路径。

    keep_suffix=True  → landscape.jpg  →  landscape_x4.jpg
    keep_suffix=False → landscape.jpg  →  landscape_x4.png  （统一使用 fmt）
    """
    stem = Path(input_path).stem
    ext = Path(input_path).suffix.lstrip(".").lower() if keep_suffix else fmt
    # 确保格式合法
    if ext not in ("png", "jpg", "jpeg", "webp"):
        ext = fmt
    filename = f"{stem}_x{scale}.{ext}"
    return os.path.join(output_dir, filename)


# ══════════════════════════════════════════════════════════════════════
#  InferencePage 接入补丁
#  ──────────────────────────────────────────────────────────────────
#  在你的 real_esrgan_ui.py 中，将 _start_run 方法替换为下方的
#  patch_inference_page(page) 调用，或直接参照 _patched_start_run
#  的实现修改 InferencePage。
# ══════════════════════════════════════════════════════════════════════

def patch_inference_page(page, exe_path: str = DEFAULT_EXE):
    """
    将 InferencePage 的推理后端替换为真实的 BatchRealESRGANWorker。

    使用方式（在 MainWindow.__init__ 里）：
        from realesrgan_worker import patch_inference_page
        patch_inference_page(self.inference_page)

    或者在构造时传入 exe 路径：
        patch_inference_page(self.inference_page,
                             exe_path="path/to/realesrgan-ncnn-vulkan.exe")
    """
    import types

    def _start_run(self):
        # ── 校验 ──────────────────────────────────────────────────────
        if not self._files:
            from qfluentwidgets import InfoBar, InfoBarPosition
            InfoBar.warning(
                title="未选择文件", content="请先添加要处理的图像文件",
                parent=self, position=InfoBarPosition.TOP_RIGHT, duration=3000)
            return

        # ── 状态重置 ──────────────────────────────────────────────────
        self._running = True
        self._run_btn.setText("停止推理")
        from qfluentwidgets import FluentIcon as FIF
        self._run_btn.setIcon(FIF.PAUSE)
        self._param_panel.setEnabled(False)

        ui_params = self._param_panel.get_params()
        self._stat_widgets["processed"].setText(f"0 / {len(self._files)}")
        self._stat_widgets["device"].setText(
            "CPU" if ui_params.get("gpu_id", "auto") == "-1" else "GPU")

        for item in self._file_items.values():
            item.set_status("等待")

        # ── 构建 Worker ───────────────────────────────────────────────
        worker = build_worker_from_ui_params(
            self._files, ui_params, exe_path=exe_path)

        self._thread = worker   # BatchRealESRGANWorker 本身是 QThread

        # ── 连接信号 ──────────────────────────────────────────────────
        worker.progress.connect(self._on_progress)
        worker.finished.connect(self._on_finished)
        worker.error.connect(self._on_error)
        worker.log_line.connect(self._append_log)

        # 如果页面实现了 update_preview，连接 file_done 信号以显示预览
        if hasattr(page, 'update_preview'):
            worker.file_done.connect(
                lambda inp, out, p=page: p.update_preview(inp, out))

        # 单张完成 → 更新文件列表状态
        worker.file_done.connect(
            lambda inp, _out: self._file_items[inp].set_status("完成")
            if inp in self._file_items else None)
        worker.file_error.connect(
            lambda inp, _msg: self._file_items[inp].set_status("失败")
            if inp in self._file_items else None)

        # ── 启动 ──────────────────────────────────────────────────────
        self._append_log(
            RealESRGANWorker._html("▶ 推理开始…", "#0078D4", bold=True))
        worker.start()

    # 在页面初始化时，尝试把 models/ 目录的实际模型名列到右侧的 model_combo
    # 过滤掉已知不兼容的模型（如包含 Clip 层的 *-wdn-* 版本）
    try:
        model_dir_abs = os.path.join(os.path.dirname(exe_path), "models")
        if os.path.isdir(model_dir_abs) and hasattr(page, "_param_panel"):
            items = []
            for fn in sorted(os.listdir(model_dir_abs)):
                if fn.lower().endswith('.param'):
                    model_name = Path(fn).stem
                    # 过滤掉包含 Clip 层的模型版本（通常是 *-wdn-* 版本）
                    if "wdn" not in model_name.lower():
                        items.append(model_name)
            if items:
                try:
                    page._param_panel.model_combo.clear()
                    page._param_panel.model_combo.addItems(items)
                    # 选中第一个默认项
                    page._param_panel.model_combo.setCurrentText(items[0])
                except Exception:
                    pass
    except Exception:
        pass

    def _abort_run(self):
        if hasattr(self, '_thread') and self._thread is not None:
            if isinstance(self._thread, BatchRealESRGANWorker):
                self._thread.cancel()
        self._running = False
        from qfluentwidgets import FluentIcon as FIF
        self._run_btn.setText("开始推理")
        self._run_btn.setIcon(FIF.PLAY)
        self._param_panel.setEnabled(True)

    # 绑定到实例（覆盖原有方法）
    page._start_run = types.MethodType(_start_run,  page)
    page._abort_run = types.MethodType(_abort_run,  page)

    # 同步更新 _toggle_run 引用（原方法内部调用 _start_run/_abort_run）
    # 由于 _toggle_run 通过 self. 调用，已自动路由到新版本，无需额外处理。

    return page
