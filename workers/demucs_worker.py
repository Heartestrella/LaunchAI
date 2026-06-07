import os
import sys
import subprocess
import time
from PyQt6.QtCore import QThread, pyqtSignal


class DemucsWorker(QThread):
    """Demucs 音频分离工作线程"""

    # 信号定义
    progress = pyqtSignal(int, str)  # 进度百分比, 状态文字
    output = pyqtSignal(str)          # 实时输出日志
    finished = pyqtSignal(str)        # 完成时发送输出目录
    error = pyqtSignal(str)           # 错误信息

    def __init__(self, params: dict):
        """
        初始化 Demucs 工作线程

        Args:
            params: 参数字典，包含:
                - input: 输入音频文件路径
                - output: 输出目录
                - model: 模型名称 (htdemucs, htdemucs_ft, mdx, mdx_extra, mdx_q)
                - device: 设备 (cuda, cpu, mps)
                - tracks: 音轨字典 {'vocals': True, 'drums': True, 'bass': True, 'other': True}
                - shifts: 移位量 (1-8)
                - segment: 分段长度 (1-20)
                - overlap: 重叠率 (0.0-0.5)
                - format: 输出格式 (wav, flac, mp3)
                - two_stems: 仅分离指定音轨 (可选，如 'vocals')
        """
        super().__init__()
        self.params = params
        self.process = None
        self._is_cancelled = False

    def _html(self, text, color=None, bold=False):
        """生成 HTML 格式文本"""
        if not color and not bold:
            return text
        style = []
        if color:
            style.append(f"color:{color}")
        if bold:
            style.append("font-weight:bold")
        return f'<span style="{";".join(style)}">{text}</span>'

    def run(self):
        """执行分离任务"""
        try:
            # 验证输入文件
            input_path = self.params.get('input')
            if not input_path or not os.path.exists(input_path):
                self.error.emit(f"输入文件不存在: {input_path}")
                return

            output_dir = self.params.get('output', './separated')
            model = self.params.get('model', 'htdemucs')
            device = self.params.get('device', 'cuda')
            shifts = self.params.get('shifts', 1)
            segment = self.params.get('segment', 10)
            overlap = self.params.get('overlap', 0.25)
            fmt = self.params.get('format', 'wav')
            tracks = self.params.get('tracks', {})
            two_stems = self.params.get('two_stems', None)

            # 构建 demucs 命令
            cmd = [sys.executable, "-m", "demucs.separate"]

            # 基本参数
            cmd.extend(["-n", model])           # 模型
            cmd.extend(["-d", device])          # 设备
            cmd.extend(["-o", output_dir])      # 输出目录

            # 可选参数
            if shifts > 1:
                cmd.extend(["--shifts", str(shifts)])

            if segment < 20:
                cmd.extend(["--segment", str(segment)])

            if overlap != 0.25:
                cmd.extend(["--overlap", str(overlap)])

            # 输出格式
            if fmt == 'mp3':
                cmd.append("--mp3")
            elif fmt == 'flac':
                cmd.append("--flac")

            # 音轨选择
            if two_stems:
                cmd.extend(["--two-stems", two_stems])
            else:
                # 选择要分离的音轨
                stems = []
                if tracks.get('vocals'):
                    stems.append('vocals')
                if tracks.get('drums'):
                    stems.append('drums')
                if tracks.get('bass'):
                    stems.append('bass')
                if tracks.get('other'):
                    stems.append('other')

                if stems and len(stems) < 4:
                    # 只分离选中的音轨
                    cmd.extend(["--stem", ",".join(stems)])

            # 输入文件
            cmd.append(input_path)

            # 输出命令信息
            self.progress.emit(0, "准备开始分离...")
            self.output.emit(self._html(f"执行命令: {' '.join(cmd)}", "#888888"))

            # 启动进程
            self.process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                encoding='utf-8',
                errors='replace'
            )

            # 读取输出
            output_lines = []
            for line in iter(self.process.stdout.readline, ''):
                if self._is_cancelled:
                    self.process.terminate()
                    self.output.emit(self._html("用户取消了分离任务", "#FF9800"))
                    return

                if not line:
                    break

                line = line.strip()
                if line:
                    output_lines.append(line)
                    self._parse_output(line)
                if not line:
                    continue

                # 解析进度信息
                self._parse_output(line)

            self.process.wait()

            if self._is_cancelled:
                return

            if self.process.returncode == 0:
                # 计算输出路径
                track_name = os.path.splitext(os.path.basename(input_path))[0]
                sep_dir = os.path.join(output_dir, model, track_name)

                self.output.emit(self._html("音频分离完成！", "#4CAF50"))
                self.finished.emit(sep_dir)
            else:
                print("\n" + "=" * 60)
                print(f"Demucs 分离失败 (返回码: {self.process.returncode})")
                print("完整输出:")
                print("-" * 60)
                for line in output_lines:
                    print(line)
                print("=" * 60)
                self.error.emit(f"分离失败，返回码: {self.process.returncode}")

        except Exception as e:
            self.error.emit(f"分离过程中发生异常: {str(e)}")

    def _parse_output(self, line: str):
        """解析 demucs 输出"""
        # 检测进度信息
        if '%' in line and ('|' in line or '[' in line):
            # 尝试提取百分比
            import re
            match = re.search(r'(\d+)%', line)
            if match:
                percent = int(match.group(1))
                self.progress.emit(percent, line)
                self.output.emit(self._html(line, "#2196F3"))
            else:
                self.output.emit(self._html(line, "#CCCCCC"))
        elif "ERROR" in line or "error" in line:
            self.output.emit(self._html(line, "#F44336"))
        elif "WARNING" in line or "warning" in line:
            self.output.emit(self._html(line, "#FF9800"))
        elif "Saving" in line:
            self.output.emit(self._html(line, "#4CAF50"))
        else:
            self.output.emit(self._html(line, "#CCCCCC"))

    def cancel(self):
        """取消分离任务"""
        self._is_cancelled = True
        if self.process:
            self.process.terminate()
            # 等待进程结束
            time.sleep(0.5)
            if self.process.poll() is None:
                self.process.kill()
