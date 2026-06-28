# workers/whisper_worker.py

import os
import sys
import subprocess
import time
from pathlib import Path
from PyQt6.QtCore import QThread, pyqtSignal

from utils import paths as _paths


class WhisperWorker(QThread):
    """Whisper 语音转录工作线程"""

    progress = pyqtSignal(int, str)
    output = pyqtSignal(str)
    finished = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self, params: dict):
        super().__init__()
        self.params = params
        self.process = None
        self._is_cancelled = False

    def _html(self, text, color=None, bold=False):
        if not color and not bold:
            return text
        style = []
        if color:
            style.append(f"color:{color}")
        if bold:
            style.append("font-weight:bold")
        return f'<span style="{";".join(style)}">{text}</span>'

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
        """把 input(str | list[str])归一成路径列表。"""
        ip = params.get('input')
        if isinstance(ip, list):
            return ip
        if isinstance(ip, str):
            return [ip]
        return []

    @staticmethod
    def build_argv(params: dict, input_path: str) -> list:
        """构建单个文件的 whisper CLI 命令。GUI / API 共用,无 Qt 依赖。"""
        output_dir = params.get('output') or _paths.output_dir("whisper")
        whisper_model_dir = _paths.model_dir("whisper")
        model = params.get('model', 'small')
        device = params.get('device', 'cpu')
        language = params.get('language', None)
        task = params.get('task', 'transcribe')
        output_format = params.get('output_format', 'all')
        beam_size = params.get('beam_size', 5)
        best_of = params.get('best_of', 1)
        temperature = params.get('temperature', 0.0)
        word_timestamps = params.get('word_timestamps', False)
        condition_on_previous_text = params.get('condition_on_previous_text', True)

        cmd = [sys.executable, "-m", "whisper", input_path,
               "--model", model, "--device", device,
               "--output_dir", output_dir,
               "--model_dir", whisper_model_dir,
               "--output_format", "all" if output_format == 'all' else output_format]

        if language and language != "auto":
            cmd.extend(["--language", language])
        if task == "translate":
            cmd.extend(["--task", "translate"])
        if beam_size != 5:
            cmd.extend(["--beam_size", str(beam_size)])
        if best_of != 1:
            cmd.extend(["--best_of", str(best_of)])
        if temperature != 0.0:
            cmd.extend(["--temperature", str(temperature)])
        if word_timestamps:
            cmd.append("--word_timestamps")
        if not condition_on_previous_text:
            cmd.append("--no_condition_on_previous_text")
        return cmd

    @staticmethod
    def parse_percent(line: str):
        if '%' in line or ('[' in line and ']' in line and '/' in line):
            import re
            m = re.search(r'(\d+)%', line)
            if m:
                return int(m.group(1))
        return None

    def run(self):
        try:
            file_list = self._get_file_list()
            if not file_list:
                self.error.emit("未指定输入文件")
                return

            for file_path in file_list:
                if not os.path.exists(file_path):
                    self.error.emit(f"输入文件不存在: {file_path}")
                    return

            output_dir = self.params.get('output') or _paths.output_dir("whisper")
            whisper_model_dir = _paths.model_dir("whisper")
            model = self.params.get('model', 'small')
            device = self.params.get('device', 'cpu')
            task = self.params.get('task', 'transcribe')

            os.makedirs(output_dir, exist_ok=True)

            total_files = len(file_list)
            self.progress.emit(0, f"准备转录 {total_files} 个文件...")
            self.output.emit(self._html(f"Whisper 模型: {model}", "#888888"))
            self.output.emit(self._html(f"计算设备: {device}", "#888888"))
            self.output.emit(self._html(f"任务类型: {task}", "#888888"))
            self.output.emit(self._html(f"输出目录: {output_dir}", "#888888"))

            for idx, input_path in enumerate(file_list):
                if self._is_cancelled:
                    self.output.emit(self._html("用户取消了转录任务", "#FF9800"))
                    return

                progress_pct = int((idx / total_files) * 100)
                file_name = Path(input_path).stem
                self.progress.emit(
                    progress_pct, f"正在处理 ({idx+1}/{total_files}): {file_name}")

                cmd = self.build_argv(self.params, input_path)

                self.output.emit(self._html(
                    f"执行命令: {' '.join(cmd)}", "#888888"))

                env = _paths.subprocess_env(torch_home=whisper_model_dir)
                self.process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                    encoding='utf-8',
                    errors='replace',
                    env=env,
                )

                for line in iter(self.process.stdout.readline, ''):
                    if self._is_cancelled:
                        self.process.terminate()
                        self.output.emit(self._html("用户取消了转录任务", "#FF9800"))
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
                    self.output.emit(self._html(
                        f"✓ {file_name} 转录完成", "#4CAF50"))
                    if total_files > 1:
                        self.progress.emit(int(((idx + 1) / total_files) * 100),
                                           f"已完成 {idx+1}/{total_files} 个文件")
                else:
                    self.error.emit(
                        f"转录失败: {file_name} (返回码: {self.process.returncode})")
                    return

            self.progress.emit(100, "全部转录完成！")
            self.output.emit(self._html(
                f"✨ 所有转录文件已保存到: {output_dir}", "#4CAF50"))
            self.finished.emit(output_dir)

        except Exception as e:
            self.error.emit(f"转录过程中发生异常: {str(e)}")

    def _parse_output(self, line: str):
        import re
        if '%' in line or ('[' in line and ']' in line and '/' in line):
            match = re.search(r'(\d+)%', line)
            if match:
                percent = int(match.group(1))
                self.progress.emit(percent, line)
                self.output.emit(self._html(line, "#2196F3"))
            else:
                self.output.emit(self._html(line, "#CCCCCC"))
        elif "Detecting language" in line:
            self.output.emit(self._html(line, "#9C27B0"))
        elif "Transcribing" in line or "Processing" in line:
            self.output.emit(self._html(line, "#2196F3"))
        elif "Saving" in line or "saved" in line:
            self.output.emit(self._html(line, "#4CAF50"))
        elif "ERROR" in line or "error" in line:
            self.output.emit(self._html(line, "#F44336"))
        elif "WARNING" in line or "warning" in line:
            self.output.emit(self._html(line, "#FF9800"))
        else:
            self.output.emit(self._html(line, "#CCCCCC"))

    def cancel(self):
        self._is_cancelled = True
        if self.process:
            self.process.terminate()
            time.sleep(0.5)
            if self.process.poll() is None:
                self.process.kill()
