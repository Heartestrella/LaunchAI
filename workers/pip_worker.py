import subprocess
import sys
import os
import time
from PyQt6.QtCore import QThread, pyqtSignal
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import traceback
PYTHON_PATH = sys.executable
cache_ = os.path.join(os.getcwd(), "torch_cache")
fork_map = {
    "demucs": "main"
}


class PipWorker(QThread):
    output_signal = pyqtSignal(str)
    finished_signal = pyqtSignal(bool, str)

    def __init__(self, package_name: list = None, mirror_url: str = None, force: bool = False, is_torch: int = None, from_git: tuple = (False, "")):
        """from_git : True/False 项目地址"""
        super().__init__()
        self.save_path = "./_cache"
        self.pip_worker = None
        self.package_name = package_name
        self.mirror_url = mirror_url
        self.force = force
        self.is_torch = is_torch
        self.from_git = from_git

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
            if self.from_git[0]:
                self.command = self.install_from_git()
                # return
                # self.command.extend(["-U", self.from_git[1]])
            else:
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

            elif self.mirror_url:
                self.command.extend(["-i", self.mirror_url])

            self.install_packages()

        except Exception as e:
            error_details = traceback.format_exc()
            self.output_signal.emit(self._html(
                f"异常: {str(error_details)}", "#F44336"))
            self.finished_signal.emit(
                False, f"安装 {self.package_name} 时发生异常: {str(e)}")

    def install_packages(self):
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

                        now = time.time()

                        # 计算速率(每秒更新一次)
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

            if self.is_torch:
                self.output_signal.emit(
                    self._html(f"成功安装 {self.package_name}\n进行测试", "##FFFF00"))
                self.test_torch()
            else:
                self.finished_signal.emit(True, self._html(
                    f"{self.package_name} 安装成功", "#4CAF50"))
        else:
            self.finished_signal.emit(False, f"安装 {self.package_name} 失败")

    def test_torch(self):
        """测试 PyTorch 安装是否成功"""
        try:
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
                encoding='utf-8',
                errors='replace'
            )
            if result.stdout:
                output = result.stdout.strip()
                self.output_signal.emit(self._html("安装完成请重启软件!", "red"))
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

    def download_file(self, url: str, save_path: str, filename: str = None, extract: bool = False, extract_to: str = None):
        """
        下载文件并实时显示进度，可选择解压

        Args:
            url: 下载地址
            save_path: 保存目录
            filename: 保存的文件名(可选，默认从URL提取)
            extract: 是否解压(仅支持 .zip 文件)
            extract_to: 解压目标文件夹(默认为 save_path/文件名(不含扩展名))

        Returns:
            下载的文件路径，如果解压则返回解压后的文件夹路径
        """
        try:
            # 确定保存路径
            if filename is None:
                filename = url.split('/')[-1].split('?')[0]

            os.makedirs(save_path, exist_ok=True)
            full_path = os.path.join(save_path, filename)

            # 检查文件是否已存在
            if os.path.exists(full_path):
                self.output_signal.emit(self._html(
                    f"文件已存在: {full_path}", "#FF9800"))
                # 如果需要解压且文件存在，直接解压
                if extract and filename.endswith('.zip'):
                    return self._extract_zip(full_path, extract_to)
                return full_path

            self.output_signal.emit(self._html(f"开始下载: {filename}", "#4FC3F7"))
            self.output_signal.emit(self._html(f"下载地址: {url}", "#888888"))

            # 配置重试策略
            session = requests.Session()
            retry = Retry(total=3, backoff_factor=1,
                          status_forcelist=[500, 502, 503, 504])
            adapter = HTTPAdapter(max_retries=retry)
            session.mount('http://', adapter)
            session.mount('https://', adapter)

            # 发送请求
            response = session.get(url, stream=True, timeout=30)
            response.raise_for_status()

            # 获取文件大小
            total_size = int(response.headers.get('content-length', 0))

            # 下载进度跟踪
            downloaded = 0
            last_percent = -1
            last_time = time.time()
            last_downloaded = 0

            with open(full_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if self.isInterruptionRequested():
                        f.close()
                        os.remove(full_path)
                        self.output_signal.emit(self._html("下载已取消", "#FF9800"))
                        return None

                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)

                        if total_size > 0:
                            percent = int((downloaded / total_size) * 100)

                            # 计算下载速度
                            now = time.time()
                            elapsed = now - last_time
                            if elapsed >= 1.0:
                                bytes_downloaded = downloaded - last_downloaded
                                speed = bytes_downloaded / elapsed / 1024
                                last_time = now
                                last_downloaded = downloaded
                                speed_text = f" {speed:.1f} KB/s"
                            else:
                                speed_text = ""

                            downloaded_mb = downloaded / (1024 * 1024)
                            total_mb = total_size / (1024 * 1024)

                            if percent != last_percent or percent % 5 == 0:
                                progress_text = f"下载进度: {percent}% ({downloaded_mb:.1f}/{total_mb:.1f} MB){speed_text}"
                                self.output_signal.emit(
                                    f'\r{self._html(progress_text, "#2196F3")}')
                                last_percent = percent

            # 下载完成
            if total_size > 0:
                self.output_signal.emit(
                    f'\r{self._html(f"下载进度: 100% ({total_size / (1024*1024):.1f} MB) 完成", "#4CAF50", bold=True)}')

            self.output_signal.emit(self._html(
                f"✅ 文件保存至: {full_path}", "#4CAF50"))

            # 解压 ZIP 文件
            if extract and filename.endswith('.zip'):
                return self._extract_zip(full_path, extract_to)

            return full_path

        except requests.exceptions.RequestException as e:
            self.output_signal.emit(self._html(f"下载失败: {str(e)}", "#F44336"))
            return None
        except Exception as e:
            self.output_signal.emit(self._html(f"异常: {str(e)}", "#F44336"))
            return None

    def _extract_zip(self, zip_path: str, extract_to: str = None):
        """
        解压 ZIP 文件

        Args:
            zip_path: ZIP 文件路径
            extract_to: 解压目标文件夹(默认为 zip 文件所在目录/文件名(不含扩展名))

        Returns:
            解压后的文件夹路径
        """
        try:
            import zipfile

            # 确定解压目标路径
            if extract_to is None:
                # 默认解压到同目录下与 ZIP 文件名相同的文件夹(去掉 .zip 扩展名)
                extract_to = os.path.splitext(zip_path)[0]

            os.makedirs(extract_to, exist_ok=True)

            self.output_signal.emit(self._html(
                f"开始解压: {os.path.basename(zip_path)}", "#4FC3F7"))

            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                # 获取文件列表，计算总大小用于进度显示
                file_list = zip_ref.namelist()
                total_files = len(file_list)

                for i, file_name in enumerate(file_list, 1):
                    if self.isInterruptionRequested():
                        self.output_signal.emit(self._html("解压已取消", "#FF9800"))
                        return None

                    zip_ref.extract(file_name, extract_to)

                    # 每解压 10 个文件或完成时显示进度
                    if i % 10 == 0 or i == total_files:
                        percent = int((i / total_files) * 100)
                        progress_text = f"解压进度: {percent}% ({i}/{total_files})"
                        self.output_signal.emit(
                            f'\r{self._html(progress_text, "#2196F3")}')

            self.output_signal.emit(
                f'\r{self._html(f"解压进度: 100% ({total_files}/{total_files}) 完成", "#4CAF50", bold=True)}')
            self.output_signal.emit(self._html(
                f"✅ 解压完成，保存至: {extract_to}", "#4CAF50"))

            return extract_to

        except zipfile.BadZipFile:
            self.output_signal.emit(self._html(
                f"解压失败: {zip_path} 不是有效的 ZIP 文件", "#F44336"))
            return None
        except Exception as e:
            self.output_signal.emit(self._html(f"解压异常: {str(e)}", "#F44336"))
            return None

    def _find_project_root(self, extracted_path: str) -> str:
        """
        查找项目根目录（包含 setup.py 的目录）

        Args:
            extracted_path: 解压后的路径

        Returns:
            项目根目录路径，未找到返回 None
        """
        # 检查当前目录是否包含 setup.py
        if os.path.exists(os.path.join(extracted_path, "setup.py")):
            return extracted_path

        # 查找子目录中包含 setup.py 的目录
        for item in os.listdir(extracted_path):
            item_path = os.path.join(extracted_path, item)
            if os.path.isdir(item_path):
                if os.path.exists(os.path.join(item_path, "setup.py")):
                    return item_path

        return None

    def install_from_git(self):
        git_url = self.from_git[1]
        print(self.package_name)
        fork = fork_map.get(self.package_name[0], "main")  # 仅单安装的时
        # https://github.com/adefossez/demucs/archive/refs/heads/main.zip
        # https://github.com/adefossez/demucs
        full_url = f"{git_url}/archive/refs/heads/{fork}.zip"
        downloaded_file = self.download_file(
            full_url, self.save_path, extract=True)
        if downloaded_file:
            project_root = self._find_project_root(downloaded_file)

            if not project_root:
                self.output_signal.emit(self._html(
                    "未找到项目根目录（缺少 setup.py）", "#F44336"))
                self.finished_signal.emit(False, "未找到项目根目录")
                return
            if self.package_name[0] == "demucs":
                requirements_path = os.path.join(
                    project_root, "requirements_minimal.txt")

                if os.path.exists(requirements_path):
                    self.output_signal.emit(self._html(
                        "正在修改 requirements_minimal.txt...", "#FF9800"))

                    try:
                        # 读取原始内容
                        with open(requirements_path, 'r', encoding='utf-8') as f:
                            lines = f.readlines()

                        # 过滤掉 torch 和 torchaudio 相关的行
                        filtered_lines = []
                        removed_lines = []

                        for line in lines:
                            line_stripped = line.strip()
                            # 检查是否包含 torch 或 torchaudio（忽略注释和空行）
                            if line_stripped and not line_stripped.startswith('#'):
                                if 'torch' in line_stripped.lower():
                                    removed_lines.append(line_stripped)
                                    continue
                            filtered_lines.append(line)
                        if sys.platform == "win32":
                            filtered_lines.append("\nsoundfile\n")
                        # 写回文件
                        with open(requirements_path, 'w', encoding='utf-8') as f:
                            f.writelines(filtered_lines)

                        # 输出被删除的行
                        if removed_lines:
                            self.output_signal.emit(self._html(
                                f"已从 requirements_minimal.txt 中移除以下依赖:", "#4CAF50"))
                            for removed in removed_lines:
                                self.output_signal.emit(
                                    self._html(f"  - {removed}", "#FF9800"))
                        else:
                            self.output_signal.emit(self._html(
                                "未找到 torch/torchaudio 依赖行", "#FF9800"))
                    except Exception as e:
                        self.output_signal.emit(self._html(
                            f"修改 requirements_minimal.txt 失败: {str(e)}", "#F44336"))

                self.commands = [PYTHON_PATH, "-m", "pip",
                                 "install", project_root, ]
                return self.commands
        self.output_signal.emit(self._html("从GITHUB安装失败 回滚默认PIP安装", "red"))
        return [PYTHON_PATH, "-m", "pip", "install", self.package_name[0], ]  # 回滚默认安装

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
