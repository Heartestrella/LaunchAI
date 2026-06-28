# workers/rvc_worker.py

import os
import sys
import subprocess
import time
from pathlib import Path
from PyQt6.QtCore import QThread, pyqtSignal

from utils import paths as _paths


_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_INFER_RUNNER = os.path.join(_THIS_DIR, "_rvc_runner.py")
_REALTIME_RUNNER = os.path.join(_THIS_DIR, "_rvc_realtime_runner.py")


def _html(text, color=None, bold=False):
    if not color and not bold:
        return text
    style = []
    if color:
        style.append(f"color:{color}")
    if bold:
        style.append("font-weight:bold")
    return f'<span style="{";".join(style)}">{text}</span>'


class RVCInferWorker(QThread):
    """RVC 批量推理工作线程（基于 Applio core.py infer）"""

    progress = pyqtSignal(int, str)
    output = pyqtSignal(str)
    finished = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self, params: dict):
        """
        Args:
            params: 参数字典，包含:
                - input: 输入音频文件路径 (str 或 list[str])
                - output: 输出目录
                - model_path: 模型 .pth 路径
                - index_path: 检索索引 .index 路径 (可为空)
                - device: 设备 (cuda:0, cpu, ...)
                - f0_method: 音高提取算法 (rmvpe/crepe/harvest/pm)
                - transpose: 变调（半音，整数；男->女约 +12，女->男约 -12）
                - index_rate: 检索特征占比 (0.0-1.0)
                - filter_radius: 中值滤波半径 (>=3 可降噪)
                - resample_sr: 后处理重采样率 (0=不重采样)
                - rms_mix_rate: 音量包络融合比例 (0.0-1.0)
                - protect: 清辅音保护强度 (0.0-0.5)
                - split_infer: 是否按静音分段推理
                - format: 输出格式 (wav/flac/mp3)
        """
        super().__init__()
        self.params = params
        self.process = None
        self._is_cancelled = False

    def _get_file_list(self) -> list:
        input_param = self.params.get('input')
        if isinstance(input_param, list):
            return input_param
        elif isinstance(input_param, str):
            return [input_param]
        return []

    # ── Qt-free 调用核(GUI 的 run() 与 HTTP API 共用) ──────────────────
    @staticmethod
    def file_list(params: dict) -> list:
        ip = params.get('input')
        if isinstance(ip, list):
            return ip
        if isinstance(ip, str):
            return [ip]
        return []

    @staticmethod
    def output_path(params: dict, input_path: str) -> str:
        output_dir = params.get('output') or _paths.output_dir("rvc")
        fmt = params.get('format', 'wav')
        return os.path.join(output_dir, f"{Path(input_path).stem}_rvc.{fmt}")

    @staticmethod
    def build_argv(params: dict, input_path: str, out_path: str) -> list:
        """构建单文件的 RVC runner 命令(指向 _rvc_runner.py)。GUI / API 共用。"""
        index_path = params.get('index_path', '') or ''
        cmd = [sys.executable, _INFER_RUNNER,
               "--input",         input_path,
               "--output",        out_path,
               "--model-path",    params.get('model_path', ''),
               "--device",        params.get('device', 'cuda:0'),
               "--f0-method",     params.get('f0_method', 'rmvpe'),
               "--transpose",     str(int(params.get('transpose', 0))),
               "--index-rate",    str(float(params.get('index_rate', 0.75))),
               "--filter-radius", str(int(params.get('filter_radius', 3))),
               "--resample-sr",   str(int(params.get('resample_sr', 0))),
               "--rms-mix-rate",  str(float(params.get('rms_mix_rate', 0.25))),
               "--protect",       str(float(params.get('protect', 0.33)))]
        if index_path:
            cmd.extend(["--index-path", index_path])
        if bool(params.get('split_infer', False)):
            cmd.append("--split-infer")
        return cmd

    @staticmethod
    def parse_percent(line: str):
        if '%' in line:
            import re
            m = re.search(r'(\d+)%', line)
            if m:
                return int(m.group(1))
        return None

    def run(self):
        try:
            file_list = self._get_file_list()
            if not file_list:
                self.error.emit("未指定输入音频")
                return

            for file_path in file_list:
                if not os.path.exists(file_path):
                    self.error.emit(f"输入文件不存在: {file_path}")
                    return

            model_path = self.params.get('model_path', '')
            if not model_path or not os.path.exists(model_path):
                self.error.emit(f"模型文件不存在: {model_path}")
                return

            index_path = self.params.get('index_path', '') or ''
            if index_path and not os.path.exists(index_path):
                self.output.emit(_html(
                    f"⚠️ 索引文件不存在，将忽略检索: {index_path}", "#FF9800"))
                index_path = ''

            output_dir = self.params.get('output') or _paths.output_dir("rvc")
            device = self.params.get('device', 'cuda:0')
            f0_method = self.params.get('f0_method', 'rmvpe')
            transpose = int(self.params.get('transpose', 0))
            index_rate = float(self.params.get('index_rate', 0.75))
            filter_radius = int(self.params.get('filter_radius', 3))
            resample_sr = int(self.params.get('resample_sr', 0))
            rms_mix_rate = float(self.params.get('rms_mix_rate', 0.25))
            protect = float(self.params.get('protect', 0.33))
            split_infer = bool(self.params.get('split_infer', False))
            fmt = self.params.get('format', 'wav')

            os.makedirs(output_dir, exist_ok=True)

            total_files = len(file_list)
            self.progress.emit(0, f"准备推理 {total_files} 个文件...")
            self.output.emit(_html(
                f"RVC 模型: {Path(model_path).name}", "#888888"))
            if index_path:
                self.output.emit(_html(
                    f"检索索引: {Path(index_path).name}", "#888888"))
            self.output.emit(_html(f"计算设备: {device}", "#888888"))
            self.output.emit(_html(f"音高算法: {f0_method}", "#888888"))
            self.output.emit(_html(f"变调: {transpose:+d} 半音", "#888888"))
            self.output.emit(_html(f"输出目录: {output_dir}", "#888888"))

            for idx, input_path in enumerate(file_list):
                if self._is_cancelled:
                    self.output.emit(_html("用户取消了推理任务", "#FF9800"))
                    return

                progress_pct = int((idx / total_files) * 100)
                file_name = Path(input_path).stem
                self.progress.emit(
                    progress_pct, f"正在处理 ({idx+1}/{total_files}): {file_name}")

                out_path = os.path.join(
                    output_dir, f"{file_name}_rvc.{fmt}")

                cmd = self.build_argv(self.params, input_path, out_path)

                self.output.emit(_html(
                    f"执行命令: {' '.join(cmd)}", "#888888"))

                self.process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                    encoding='utf-8',
                    errors='replace'
                )

                for line in iter(self.process.stdout.readline, ''):
                    if self._is_cancelled:
                        self.process.terminate()
                        self.output.emit(_html("用户取消了推理任务", "#FF9800"))
                        return
                    if not line:
                        break
                    line = line.strip()
                    if line:
                        self._parse_output(line)

                self.process.wait()

                if self._is_cancelled:
                    return

                if self.process.returncode == 0:
                    self.output.emit(_html(
                        f"✓ {file_name} 推理完成 → {out_path}", "#4CAF50"))
                    if total_files > 1:
                        self.progress.emit(int(((idx + 1) / total_files) * 100),
                                           f"已完成 {idx+1}/{total_files} 个文件")
                else:
                    self.error.emit(
                        f"推理失败: {file_name} (返回码: {self.process.returncode})")
                    return

            self.progress.emit(100, "全部推理完成！")
            self.output.emit(_html(
                f"✨ 所有输出文件已保存到: {output_dir}", "#4CAF50"))
            self.finished.emit(output_dir)

        except Exception as e:
            self.error.emit(f"推理过程中发生异常: {str(e)}")

    def _parse_output(self, line: str):
        import re
        if '%' in line:
            match = re.search(r'(\d+)%', line)
            if match:
                percent = int(match.group(1))
                self.progress.emit(percent, line)
                self.output.emit(_html(line, "#2196F3"))
                return
        if line.startswith("[runner]"):
            self.output.emit(_html(line, "#9C27B0"))
        elif line.startswith("[runner][ERROR]") or "Traceback" in line or "ERROR" in line:
            self.output.emit(_html(line, "#F44336"))
        elif "WARNING" in line or "warning" in line.lower():
            self.output.emit(_html(line, "#FF9800"))
        elif "loading" in line.lower() or "load model" in line.lower():
            self.output.emit(_html(line, "#9C27B0"))
        elif "infer" in line.lower() or "processing" in line.lower():
            self.output.emit(_html(line, "#2196F3"))
        elif "saved" in line.lower() or "done" in line.lower() or "output written" in line.lower():
            self.output.emit(_html(line, "#4CAF50"))
        else:
            self.output.emit(_html(line, "#CCCCCC"))

    def cancel(self):
        self._is_cancelled = True
        if self.process:
            self.process.terminate()
            time.sleep(0.5)
            if self.process.poll() is None:
                self.process.kill()


class RVCRealtimeWorker(QThread):
    """RVC 实时变声工作线程（基于 Applio tabs.realtime.realtime）

    实时模式下子进程是常驻进程，由 stop() 触发 terminate 优雅退出。
    """

    status_changed = pyqtSignal(str)   # "starting" / "running" / "stopped" / "error"
    output = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self, params: dict):
        """
        Args:
            params: 参数字典，包含:
                - model_path:     .pth 模型路径
                - index_path:     .index 检索文件路径 (可为空)
                - input_device:   输入声卡 sounddevice 索引 (int)
                - output_device:  输出声卡 sounddevice 索引 (int)
                - pitch:          变调（半音，整数）
                - f0_method:      音高提取算法
                - block_time:     单块音频时长（秒）
                - crossfade_time: 交叉淡入淡出时长（秒）
                - extra_time:     额外缓冲时长（秒）
                - index_rate / protect / rms_mix_rate
                - device:         "cuda:N" / "cpu"
        """
        super().__init__()
        self.params = params
        self.process = None
        self._is_stopped = False

    def _device_to_gpu(self) -> int:
        device = self.params.get('device', 'cuda:0')
        if not device or device.lower() == 'cpu':
            return -1
        if device.lower().startswith('cuda:'):
            try:
                return int(device.split(':', 1)[1])
            except (ValueError, IndexError):
                return 0
        return 0

    def run(self):
        try:
            model_path = self.params.get('model_path', '')
            if not model_path or not os.path.exists(model_path):
                self.status_changed.emit("error")
                self.error.emit(f"模型文件不存在: {model_path}")
                return

            index_path = self.params.get('index_path', '') or ''
            if index_path and not os.path.exists(index_path):
                self.output.emit(_html(
                    f"⚠️ 索引文件不存在，将忽略: {index_path}", "#FF9800"))
                index_path = ''

            cmd = [sys.executable, _REALTIME_RUNNER,
                   "--model-path",     model_path,
                   "--input-device",   str(int(self.params.get('input_device', -1))),
                   "--output-device",  str(int(self.params.get('output_device', -1))),
                   "--pitch",          str(int(self.params.get('pitch', 0))),
                   "--f0-method",      str(self.params.get('f0_method', 'rmvpe')),
                   "--block-time",     str(float(self.params.get('block_time', 0.25))),
                   "--crossfade-time", str(float(self.params.get('crossfade_time', 0.05))),
                   "--extra-time",     str(float(self.params.get('extra_time', 2.5))),
                   "--index-rate",     str(float(self.params.get('index_rate', 0.75))),
                   "--protect",        str(float(self.params.get('protect', 0.33))),
                   "--rms-mix-rate",   str(float(self.params.get('rms_mix_rate', 0.25))),
                   "--input-gain",     str(float(self.params.get('input_gain', 1.0))),
                   "--output-gain",    str(float(self.params.get('output_gain', 1.5))),
                   "--gpu",            str(self._device_to_gpu()),
                   "--embedder-model", str(self.params.get('embedder_model', 'contentvec'))]

            if index_path:
                cmd.extend(["--index-path", index_path])

            self.status_changed.emit("starting")
            self.output.emit(_html(
                f"启动实时变声: {' '.join(cmd)}", "#888888"))

            self.process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                encoding='utf-8',
                errors='replace'
            )

            running_announced = False
            for line in iter(self.process.stdout.readline, ''):
                if not line:
                    break
                line = line.rstrip("\r\n")
                if not line:
                    continue
                self._parse_output(line)
                if not running_announced and "实时变声已启动" in line:
                    self.status_changed.emit("running")
                    running_announced = True

            self.process.wait()

            if self._is_stopped:
                self.status_changed.emit("stopped")
                self.output.emit(_html("实时变声已停止", "#FF9800"))
            elif self.process.returncode != 0:
                self.status_changed.emit("error")
                self.error.emit(
                    f"实时变声异常退出 (返回码: {self.process.returncode})")
            else:
                self.status_changed.emit("stopped")

        except Exception as e:
            self.status_changed.emit("error")
            self.error.emit(f"实时变声异常: {str(e)}")

    def _parse_output(self, line: str):
        if line.startswith("[realtime][ERROR]"):
            self.output.emit(_html(line, "#F44336"))
        elif line.startswith("[realtime]"):
            self.output.emit(_html(line, "#9C27B0"))
        elif "WARNING" in line or "warning" in line.lower():
            self.output.emit(_html(line, "#FF9800"))
        elif "Traceback" in line or "Error" in line:
            self.output.emit(_html(line, "#F44336"))
        else:
            self.output.emit(_html(line, "#CCCCCC"))

    def stop(self):
        self._is_stopped = True
        if self.process and self.process.poll() is None:
            self.process.terminate()
            for _ in range(20):  # 最长等 5s
                if self.process.poll() is not None:
                    break
                time.sleep(0.25)
            if self.process.poll() is None:
                self.process.kill()
