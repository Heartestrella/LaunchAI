import subprocess
import sys
import os
from PyQt6.QtCore import QThread, pyqtSignal

PYTHON_PATH = sys.executable
cache_ = os.path.join(os.getcwd(), "torch_cache")


class PipWorker(QThread):
    output_signal = pyqtSignal(str)
    finished_signal = pyqtSignal(bool, str)

    def __init__(self, package_name: list, mirror_url: str = None, force: bool = False, is_torch: int = None):
        super().__init__()
        self.package_name = package_name
        self.mirror_url = mirror_url
        self.force = force
        self.is_torch = is_torch

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
        try:
            self.command = [PYTHON_PATH, "-m", "pip", "install"]
            self.command.extend(self.package_name)
            if self.force:
                self.command.extend(["--force-reinstall", "--no-cache-dir"])

            if self.mirror_url and self.is_torch:
                if "aliyun" in self.mirror_url:
                    download_urls = self.download_torch_from_aliyun()
                    self.command.extend(download_urls)
                    urls_html = '<br>'.join(
                        [f'<span style="color:#FF6B6B">{url}</span>' for url in download_urls])
                    self.output_signal.emit(
                        f'<div style="margin:5px 0">'
                        f'<span style="color:#4FC3F7; font-weight:bold">开始安装来自{self.mirror_url}的 torch</span><br>'
                        f'{urls_html}<br>'
                        f'<span style="color:#FFB74D">提示: 如果下载速度过慢，请在浏览器中完成下载并复制到:</span>'
                        f'<span style="color:#FFD54F; font-weight:bold">{cache_}</span>'
                        f'<span style="color:#FFB74D"> 文件夹内(需手动创建,完成后可删除)，将优先从本地安装</span><br>'
                        f'</div>'
                    )
                else:
                    self.command.extend(["--index-url", self.mirror_url])
                    self.output_signal.emit(self._html(
                        "从官方源安装 如果速度过慢请切换阿里云源", color="#FF0000"))
            else:
                self.command.extend(["-i", self.mirror_url])
            env = os.environ.copy()
            env["PIP_PROGRESS_BAR"] = "raw"
            print(self.command)
            process = subprocess.Popen(
                self.command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                encoding='utf-8',
                errors='replace',
                env=env
            )

            last_percent = -1
            last_time = None
            last_downloaded = 0
            current = 0
            total = 0

            for line in iter(process.stdout.readline, ''):
                if not line:
                    break

                line = line.strip()
                if not line:
                    continue

                # 处理进度行
                if line.startswith('Progress'):
                    parts = line.split()
                    if len(parts) >= 4:
                        try:
                            current = int(parts[1])
                            total = int(parts[3])
                            percent = int((current / total) * 100)

                            import time
                            now = time.time()

                            # 计算速率（每秒更新一次）
                            if last_time is None:
                                last_time = now
                                last_downloaded = current
                                speed = 0
                            else:
                                elapsed = now - last_time
                                if elapsed >= 1.0:  # 每秒更新一次速率
                                    bytes_downloaded = current - last_downloaded
                                    speed = bytes_downloaded / elapsed / 1024 / 1024
                                    last_time = now
                                    last_downloaded = current
                                else:
                                    # 使用上次的速率
                                    speed = getattr(self, '_last_speed', 0)

                            # 保存速率供下次使用
                            self._last_speed = speed

                            # 每次百分比变化或每 5% 更新一次显示
                            if percent != last_percent or percent % 5 == 0:
                                # 格式化已下载大小
                                if current > 0:
                                    downloaded_gb = current / \
                                        (1024 * 1024)
                                    total_gb = total / (1024 * 1024)
                                    size_text = f"{downloaded_gb:.1f}/{total_gb:.1f} MB"
                                else:
                                    size_text = ""

                                if speed > 0:
                                    progress_text = f"下载进度: {percent}% ({size_text}) {speed:.1f} MB/s"
                                else:
                                    progress_text = f"下载进度: {percent}% ({size_text})"

                                self.output_signal.emit(
                                    f'\r{self._html(progress_text, "#2196F3")}')
                                last_percent = percent

                        except ValueError:
                            pass
                    continue

                # 普通输出行
                if "ERROR" in line:
                    self.output_signal.emit(self._html(line, "#F44336"))
                elif "WARNING" in line:
                    self.output_signal.emit(self._html(line, "#FF9800"))
                elif "Successfully" in line:
                    self.output_signal.emit(
                        self._html(line, "#4CAF50", bold=True))
                elif "Downloading" in line and ".whl" in line:
                    self.output_signal.emit(self._html(line, "#2196F3"))
                else:
                    self.output_signal.emit(self._html(line, "#CCCCCC"))

            # 进度完成后显示
            if total > 0:
                final_speed = getattr(self, '_last_speed', 0)
                if final_speed > 0:
                    self.output_signal.emit(
                        '\r' + self._html(f"下载进度: 100% ({final_speed:.1f} MB/s) 完成", "#4CAF50", bold=True))
                else:
                    self.output_signal.emit(
                        '\r' + self._html("下载进度: 100% 完成", "#4CAF50", bold=True))

            process.wait()

            if process.returncode == 0:
                self.output_signal.emit(
                    self._html(f"成功安装 {self.package_name}\n进行测试", "##FFFF00"))
                self.test_torch()
            else:
                self.finished_signal.emit(False, f"安装 {self.package_name} 失败")

        except Exception as e:
            self.output_signal.emit(self._html(f"异常: {str(e)}", "#F44336"))
            self.finished_signal.emit(
                False, f"安装 {self.package_name} 时发生异常: {str(e)}")

    def test_torch(self):
        """测试 PyTorch 安装是否成功"""
        try:
            import subprocess
            test_code = """
import torch
print(f"PyTorch 版本: {torch.__version__}")
print(f"CUDA 可用: {torch.cuda.is_available()}")
if torch.cuda.is_available():
    print(f"CUDA 版本: {torch.version.cuda}")
    print(f"GPU 型号: {torch.cuda.get_device_name(0)}")
    print(f"GPU 数量: {torch.cuda.device_count()}")
else:
    print("CUDA 不可用，使用 CPU 模式")
"""
            result = subprocess.run(
                [PYTHON_PATH, "-c", test_code],
                capture_output=True,
                text=True,
                timeout=30,
                encoding='utf-8',  # 指定 UTF-8 编码
                errors='replace'
            )
            if result.stdout:
                output = result.stdout.strip()
                self.finished_signal.emit(True, self._html(output, "#4CAF50"))
                print(result.stdout.strip())
                return output

            if result.stderr:
                print("错误:", result.stderr.strip())
                self.finished_signal.emit(
                    False, self._html(f"测试异常: {str(e)}", "#F44336"))
                return None

        except Exception as e:
            self.finished_signal.emit(
                False, self._html(f"测试异常: {str(e)}", "#F44336"))
            return None

    def download_torch_from_aliyun(self) -> list:
        urls = []
        cache_files = []

        if os.path.exists(cache_) and os.path.isdir(cache_):
            cache_files = [f for f in os.listdir(
                cache_) if os.path.isfile(os.path.join(cache_, f))]
        else:
            self.output_signal.emit(self._html(
                f"未找到本地文件夹: {cache_}", "#FF9800"))

        for package in self.package_name:
            if int(self.is_torch) <= 124:
                torch_version = "2.5.1"
                torchvision_version = "0.20.1"
                torchaudio_version = "2.5.1"
            else:
                torch_version = "2.12.0"
                torchvision_version = "0.27.0"
                torchaudio_version = "2.12.0"

            if package == "torch":
                file_name = f"{package}-{torch_version}+cu{self.is_torch}-cp310-cp310-win_amd64.whl"
            elif package == "torchvision":
                file_name = f"{package}-{torchvision_version}+cu{self.is_torch}-cp310-cp310-win_amd64.whl"
            elif package == "torchaudio":
                file_name = f"{package}-{torchaudio_version}+cu{self.is_torch}-cp310-cp310-win_amd64.whl"
            else:
                continue

            down_url = f"{self.mirror_url}/{file_name}"

            if cache_files and file_name in cache_files:
                self.output_signal.emit(self._html(
                    f"[本地] 识别到本地文件: {file_name}", "#4CAF50"))
                down_url = os.path.join(cache_, file_name)
            else:
                self.output_signal.emit(self._html(
                    f"[网络] 未识别到本地文件: {file_name}，将从网络安装", "#FF9800"))

            urls.append(down_url)

        return urls

    @staticmethod
    def is_package_installed(package_name: str) -> bool:
        """
        检查指定包是否已安装

        Args:
            package_name: 包名，如 "torch", "torchvision"

        Returns:
            True: 已安装, False: 未安装
        """
        try:
            import subprocess
            import sys

            result = subprocess.run(
                [sys.executable, "-m", "pip", "show", package_name],
                capture_output=True,
                text=True,
                timeout=10
            )
            return result.returncode == 0

        except Exception:
            return False

    def stop(self):
        self.requestInterruption()
        self.quit()
        self.wait()
