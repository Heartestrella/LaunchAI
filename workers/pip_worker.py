import subprocess
import sys
import os
import re
import time
import zipfile
from logger import info, warning, debug, error
from PyQt6.QtCore import QThread, pyqtSignal
import requests
import tempfile
import tarfile
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import traceback
import shutil
from dulwich import porcelain
from dulwich.repo import Repo
from utils.configer import get_field, set_field, get_global_config

PYTHON_PATH = sys.executable
GIT_PROJECTS_ROOT = os.path.join(os.getcwd(), "_git_projects")
LOCKS_ROOT = os.path.join(os.getcwd(), "locks")
cache_ = os.path.join(os.getcwd(), "torch_cache")
fork_map = {
    "demucs": "main",
    "Real-ESRGAN": "master",
    "Applio": "3.6.2",
    "GPT-SoVITS": "main",
    "audiocraft": "main",
}
# 工具名 → locks/ 目录下的 lock 文件名。命中即用锁版安装,代替
# 直接 pip install <pkg> / pip install -r <上游 requirements.txt>。
# 缺锁的工具(目前是 Real-ESRGAN, IOPaint)继续走原有上游 requirements 路径。
LOCK_MAP = {
    "demucs": "demucs.txt",
    "openai-whisper": "whisper.txt",
    "ultralytics": "ultralytics.txt",
    "Applio": "applio.txt",
    "GPT-SoVITS": "gptsovits.txt",
    "audiocraft": "audiocraft.txt",
}
# 「LaunchAI 工具」识别包 → SwitchPage page_name 反查表。
# 卸载时:1) set_field("installed.<pkg>", False) 让下次启动检测到未安装;
#        2) PackageManagerPage 据此找到对应的 SwitchPage 调 switch_page(0)
#           直接把当前工具页翻回「未安装」状态,无需重启。
# key = pip 实际包名(即 PipWorker.is_package_installed 用的字符串),
# value = app.py 里 Window 实例上的属性名(去掉 "interface" 后缀的小写)。
TOOL_PACKAGE_TO_PAGE = {
    "demucs":         "demucs",
    "openai-whisper": "whisper",
    "ultralytics":    "yolo",
    "Applio":         "rvc",
    "GPT-SoVITS":     "gptsovits",
    "audiocraft":     "audiocraft",
    "Real-ESRGAN":    "ESRGAN",
    "iopaint":        "iopaint",
}
MIRROR_URLS = get_field("git_mirror_hosts", [])
info(f"获取到GIT加速镜像: {MIRROR_URLS}")


def _lock_path(tool_name: str):
    """根据工具名定位 locks/<file>。文件不存在或工具未在 LOCK_MAP 时返回 None。"""
    fname = LOCK_MAP.get(tool_name)
    if not fname:
        return None
    path = os.path.join(LOCKS_ROOT, fname)
    return path if os.path.exists(path) else None


class PipWorker(QThread):
    output_signal = pyqtSignal(str)
    finished_signal = pyqtSignal(bool, str)

    def __init__(self, package_name: list = None, mirror_url: str = None, force: bool = False, is_torch: int = None, from_git: tuple = (False, "", "")):
        """from_git : True/False 项目地址 tag version tag为空默认最新"""
        super().__init__()
        self.save_path = "./_cache"
        self.pip_worker = None
        self.package_name = package_name
        self.mirror_url = mirror_url
        self.force = force
        self.is_torch = is_torch
        self.from_git = from_git
        self._is_cancelled = False
        self._current_proc = None

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
                self.install_from_git()  # 独立处理
                return
                # self.command.extend(["-U", self.from_git[1]])
            else:
                # is_torch 走 torch wheel 直链分支,不应被 lock 拦截。
                # 单包安装(whisper / ultralytics / iopaint) 命中 LOCK_MAP 时,
                # 改成 pip install -r locks/<tool>.txt;否则保持原行为。
                lock = None
                if not self.is_torch and self.package_name and len(self.package_name) == 1:
                    lock = _lock_path(self.package_name[0])
                if lock:
                    self.output_signal.emit(self._html(
                        f"使用锁版依赖: locks/{os.path.basename(lock)}", "#4FC3F7"))
                    self.command.extend(["-r", lock])
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

    def install_packages(self, commandlist: list = []):
        env = os.environ.copy()
        env["PIP_PROGRESS_BAR"] = "raw"
        debug(f"pip command: {self.command}")
        if commandlist:
            command = commandlist
        else:
            command = self.command
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            encoding='utf-8',
            errors='replace',
            env=env
        )
        self._current_proc = process

        last_percent = -1
        last_time = None
        last_downloaded = 0
        current = 0
        total = 0

        for line in iter(process.stdout.readline, ''):
            if self._is_cancelled:
                self._kill_proc(process)
                break
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
        self._current_proc = None

        if self._is_cancelled:
            self.finished_signal.emit(False, "已取消安装")
            return

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
                file_name = f"{package}-{torch_version}+cu{self.is_torch}-cp311-cp311-win_amd64.whl"
            elif package == "torchvision":
                file_name = f"{package}-{torchvision_version}+cu{self.is_torch}-cp311-cp311-win_amd64.whl"
            elif package == "torchaudio":
                file_name = f"{package}-{torchaudio_version}+cu{self.is_torch}-cp311-cp311-win_amd64.whl"
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

    def download_file(self, url: str, save_path: str, filename: str = None, extract: bool = False, extract_to: str = None, mirror: bool = False):
        """
        下载文件并实时显示进度，可选择解压

        Args:
            url: 下载地址
            save_path: 保存目录
            filename: 保存的文件名(可选，默认从URL提取)
            extract: 是否解压(仅支持 .zip 文件)
            extract_to: 解压目标文件夹(默认为 save_path/文件名(不含扩展名))
            mirror: 是否使用镜像加速下载(True: 优先使用镜像列表，失败后回退到原版)

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

            # 构建下载 URL 列表（镜像模式）
            download_urls = []
            if mirror:
                # 获取全局 MIRROR_URLS
                global MIRROR_URLS
                if MIRROR_URLS:
                    # 构建镜像 URL 列表
                    for mirror_url in MIRROR_URLS:
                        mirror_clean = mirror_url.rstrip('/')
                        # 处理不同类型的 URL
                        if url.startswith('https://github.com/'):
                            # GitHub 文件，直接在镜像后拼接完整 URL
                            mirror_download_url = f"{mirror_clean}/{url}"
                        elif 'raw.githubusercontent.com' in url:
                            # raw 文件，也需要拼接
                            mirror_download_url = f"{mirror_clean}/{url}"
                        else:
                            # 非 GitHub URL，不适用镜像
                            mirror_download_url = None

                        if mirror_download_url:
                            download_urls.append(mirror_download_url)

                # 最后添加原始 URL 作为回退
                download_urls.append(url)
            else:
                download_urls = [url]

            # 尝试下载
            last_error = None
            for idx, download_url in enumerate(download_urls):
                is_mirror = idx < len(download_urls) - 1
                try:
                    if is_mirror:
                        self.output_signal.emit(self._html(
                            f"尝试镜像下载 [{idx+1}/{len(download_urls)-1}]: {download_url}", "#888888"))
                    else:
                        self.output_signal.emit(self._html(
                            f"尝试原始下载: {download_url}", "#888888"))

                    self.output_signal.emit(self._html(
                        f"开始下载: {filename}", "#4FC3F7"))
                    self.output_signal.emit(self._html(
                        f"下载地址: {download_url}", "#888888"))

                    # 配置重试策略
                    session = requests.Session()
                    retry = Retry(total=3, backoff_factor=1,
                                  status_forcelist=[500, 502, 503, 504])
                    adapter = HTTPAdapter(max_retries=retry)
                    session.mount('http://', adapter)
                    session.mount('https://', adapter)

                    # 发送请求
                    response = session.get(
                        download_url, stream=True, timeout=30)
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
                                if os.path.exists(full_path):
                                    os.remove(full_path)
                                self.output_signal.emit(
                                    self._html("下载已取消", "#FF9800"))
                                return None

                            if chunk:
                                f.write(chunk)
                                downloaded += len(chunk)

                                if total_size > 0:
                                    percent = int(
                                        (downloaded / total_size) * 100)

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

                    if is_mirror:
                        self.output_signal.emit(self._html(
                            f"镜像下载成功: {full_path}", "#4CAF50"))
                    else:
                        self.output_signal.emit(self._html(
                            f"文件保存至: {full_path}", "#4CAF50"))

                    # 解压 ZIP 文件
                    if extract and filename.endswith('.zip'):
                        return self._extract_zip(full_path, extract_to)

                    return full_path

                except requests.exceptions.RequestException as e:
                    last_error = e
                    if is_mirror:
                        self.output_signal.emit(self._html(
                            f"镜像下载失败: {str(e)}", "#FF9800"))
                        # 清理失败下载的文件
                        if os.path.exists(full_path):
                            os.remove(full_path)
                        continue  # 尝试下一个镜像
                    else:
                        raise  # 原始下载失败，抛出异常
                except Exception as e:
                    last_error = e
                    if is_mirror:
                        self.output_signal.emit(self._html(
                            f"镜像下载异常: {str(e)}", "#FF9800"))
                        if os.path.exists(full_path):
                            os.remove(full_path)
                        continue
                    else:
                        raise

            # 所有尝试都失败
            raise last_error or Exception("所有下载方式均失败")

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

    def _extract_github_zip(self, zip_path: str, target_root: str) -> str:
        os.makedirs(target_root, exist_ok=True)

        # 先解压到临时目录
        temp_extract = target_root + "_temp"
        os.makedirs(temp_extract, exist_ok=True)

        with zipfile.ZipFile(zip_path, 'r') as zf:
            zf.extractall(temp_extract)

        # 查找真正的项目根目录
        real_root = self._find_project_root(temp_extract)

        if real_root and real_root != temp_extract:
            # 将 real_root 下的所有内容移动到 target_root
            for item in os.listdir(real_root):
                src = os.path.join(real_root, item)
                dst = os.path.join(target_root, item)
                if os.path.exists(dst):
                    if os.path.isdir(dst):
                        shutil.rmtree(dst)
                    else:
                        os.remove(dst)
                shutil.move(src, dst)
            # 清理临时目录
            shutil.rmtree(temp_extract)
        else:
            # 没有嵌套，直接移动
            for item in os.listdir(temp_extract):
                src = os.path.join(temp_extract, item)
                dst = os.path.join(target_root, item)
                shutil.move(src, dst)
            shutil.rmtree(temp_extract)

        return target_root

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

    @staticmethod
    def _test_git_mirrors():
        """
        测试 Git 镜像可用性，删除不可用的镜像，按延迟排序返回

        Returns:
            按延迟排序后的可用镜像列表（最快的在前）
        """
        global MIRROR_URLS
        test_url = "https://github.com/git/git/archive/refs/heads/master.zip"

        info("开始测试 Git 镜像可用性...")

        results = []  # (mirror, response_time_ms)
        if not MIRROR_URLS:
            warning("不存在可用的GIT镜像源 下载可能超时")
            return
        browser_headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
            'Referer': 'https://github.com/',
        }
        original_mirrors = MIRROR_URLS.copy() if MIRROR_URLS else []
        for mirror in MIRROR_URLS:
            # 智能拼接：确保中间只有一个斜杠
            mirror = mirror.rstrip('/')
            test_url_clean = test_url.lstrip('/')
            url = f"{mirror}/{test_url_clean}"

            debug(f"测试URL: {url}")
            try:
                start_time = time.time()
                response = requests.head(
                    url,
                    timeout=10,
                    allow_redirects=True,
                    headers=browser_headers
                )
                elapsed_ms = (time.time() - start_time) * 1000

                if response.status_code == 200:
                    results.append((mirror, elapsed_ms))
                    info(f"[OK] {mirror} - {elapsed_ms:.2f}ms")
                else:
                    warning(f"[FAIL] {mirror} - HTTP {response.status_code}")

            except requests.exceptions.Timeout:
                warning(f"[TIMEOUT] {mirror} - 连接超时")
            except requests.exceptions.ConnectionError:
                warning(f"[CONNECTION ERROR] {mirror} - 连接失败")
            except Exception as e:
                error(f"[ERROR] {mirror} - {str(e)}")

        # 按延迟排序
        results.sort(key=lambda x: x[1])

        # 更新全局 MIRROR_URLS 为排序后的可用镜像列表
        MIRROR_URLS = [mirror for mirror, _ in results]

        # 输出汇总
        info("=" * 50)
        total_before = len(original_mirrors)
        total_after = len(MIRROR_URLS)
        info(f"可用镜像: {total_after}/{total_before}")
        if MIRROR_URLS:
            info(f"最快镜像: {MIRROR_URLS[0]} ({results[0][1]:.2f}ms)")
        else:
            warning("没有可用的镜像")
        info("=" * 50)

    def _emit_install_finished(self, package: str):
        """安装结束时统一出口：被取消则发取消，否则发成功"""
        if self._is_cancelled:
            self.finished_signal.emit(False, "已取消安装")
        else:
            # 整条安装流水线跑完，才在配置里落 flag——比扫目录/算 MD5 都可靠
            set_field(f"installed.{package}", True)
            self.finished_signal.emit(True, f"{package} 源码安装成功")

    def install_from_git(self):
        git_url = self.from_git[1]
        package = self.package_name[0]  # 只处理单包
        fork = fork_map.get(package, "main")

        # 项目存放的专属目录
        project_parent = GIT_PROJECTS_ROOT
        project_dir = os.path.join(project_parent, f"{package}_{fork}")

        # 检查是否已经存在且有效（用 .git 目录判定，兼容无 setup.py 的仓库）
        if os.path.exists(project_dir) and os.path.exists(os.path.join(project_dir, ".git")):
            self.output_signal.emit(self._html(
                f"项目已存在，跳过克隆: {project_dir}", "#4CAF50"))
            project_root = project_dir
        else:
            # 使用 dulwich 克隆（支持镜像加速）
            clone_success = self._clone_with_dulwich(
                git_url, project_dir, fork)
            if not clone_success:
                self.output_signal.emit(self._html("克隆失败，回滚 PIP 安装", "red"))
                return [PYTHON_PATH, "-m", "pip", "install", package]
            project_root = project_dir

        if self._is_cancelled:
            self._emit_install_finished(package)
            return None

        # 后续处理：依赖安装、develop 模式
        # 他哥的 没注意到项目Real-ESRGAN-ncnn-vulkan 全白写了
        # 现已把Real-ESRGAN-ncnn-vulkan 集成到底包内 不需要安装
        if package == "Real-ESRGAN":
            # 安装其他依赖
            self.output_signal.emit(self._html("正在安装 basicsr...", "#4FC3F7"))
            self._run_pip_install([
                "tb-nightly", "-i", "https://mirrors.aliyun.com/pypi/simple"
            ])  # 先后顺序不能变
            self._run_pip_install(
                ["basicsr", "-i", "https://pypi.tuna.tsinghua.edu.cn/simple"])
            site_packages = [p for p in sys.path if 'site-packages' in p][0]
            degradations_path = os.path.join(
                site_packages, "basicsr", "data", "degradations.py")
            if os.path.exists(degradations_path):
                self.output_signal.emit(self._html(
                    f"修复文件: {degradations_path}", "#FF9800"))
                with open(degradations_path, 'r', encoding='utf-8') as f:
                    content = f.read()

                old_import = 'from torchvision.transforms.functional_tensor import rgb_to_grayscale'
                new_import = 'from torchvision.transforms.functional import rgb_to_grayscale'

                if old_import in content:
                    content = content.replace(old_import, new_import)
                    with open(degradations_path, 'w', encoding='utf-8') as f:
                        f.write(content)
                    self.output_signal.emit(self._html(
                        "✅ basicsr 兼容性修复完成", "#4CAF50"))
                else:
                    self.output_signal.emit(
                        self._html("⚠️ 无需修复或已修复", "#FF9800"))
            else:
                self.output_signal.emit(self._html(
                    f"⚠️ 未找到 degradations.py 文件 如果后续报错 请参考:https://github.com/XPixelGroup/BasicSR/issues/649", "#FF9800"))

            # others
            for dep in ["facexlib", "gfpgan"]:
                self._run_pip_install([dep,])

            # 处理 requirements.txt（过滤 torch）
            req_file = os.path.join(project_root, "requirements.txt")
            if os.path.exists(req_file):
                self._filter_torch_requirements(req_file)
                self._run_pip_install(["-r", req_file])

            # 执行 develop 安装 Real-ESRGAN
            self._run_setup_develop(project_root)
            self._emit_install_finished(package)
            return None

        elif package == "demucs":
            lock = _lock_path(package)
            if lock:
                self.output_signal.emit(self._html(
                    f"使用锁版依赖: locks/{os.path.basename(lock)}", "#4FC3F7"))
                self._run_pip_install(["-r", lock])
            else:
                req_file = os.path.join(project_root, "requirements_minimal.txt")
                if os.path.exists(req_file):
                    self._filter_torch_requirements(req_file)
                    if sys.platform == "win32":
                        with open(req_file, 'a', encoding='utf-8') as f:
                            f.write("\nsoundfile\n")
                    self._run_pip_install(["-r", req_file])
            self._run_pip_install([project_root])
            self._emit_install_finished(package)
            return None

        elif package == "Applio":
            # Applio 是仓库形式运行（python core.py ...），不是 pip 包，不需要 develop/install
            # 优先用 locks/applio.txt 锁版安装,回退到上游 requirements.txt(过滤 torch*)
            lock = _lock_path(package)
            if lock:
                self.output_signal.emit(self._html(
                    f"使用锁版依赖: locks/{os.path.basename(lock)}", "#4FC3F7"))
                ok = self._run_pip_install(["-r", lock])
                if not ok and not self._is_cancelled:
                    self.finished_signal.emit(False, f"{package} 依赖安装失败")
                    return None
            else:
                req_file = os.path.join(project_root, "requirements.txt")
                if os.path.exists(req_file):
                    self._filter_torch_requirements(req_file)
                    ok = self._run_pip_install(["-r", req_file])
                    if not ok and not self._is_cancelled:
                        self.finished_signal.emit(False, f"{package} 依赖安装失败")
                        return None
                else:
                    self.output_signal.emit(self._html(
                        f"⚠️ 未找到 {req_file}，跳过依赖安装", "#FF9800"))
            self._emit_install_finished(package)
            return None

        elif package == "GPT-SoVITS":
            # 仓库形式运行,预训练权重需用户自行放到 GPT_SoVITS/pretrained_models/
            # 优先用 locks/gptsovits.txt —— 里面已经含三个替代品 (pyopenjtalk-plus /
            # opencc-python-reimplemented / pytorch-lightning),无需再单独补装
            lock = _lock_path(package)
            if lock:
                self.output_signal.emit(self._html(
                    f"使用锁版依赖: locks/{os.path.basename(lock)}", "#4FC3F7"))
                ok = self._run_pip_install(["-r", lock])
                if not ok and not self._is_cancelled:
                    self.finished_signal.emit(False, f"{package} 依赖安装失败")
                    return None
            else:
                # 回退:走上游 requirements.txt + 过滤 + 剥不编译的包 + 补装替代品
                req_file = os.path.join(project_root, "requirements.txt")
                # 上次失败留下的过滤后 requirements 会缺行,
                # 先 git checkout 一下让本次过滤从原始内容开始。
                if os.path.exists(os.path.join(project_root, ".git")):
                    try:
                        subprocess.run(
                            ["git", "checkout", "--", "requirements.txt"],
                            cwd=project_root, capture_output=True, timeout=10)
                    except Exception as e:
                        self.output_signal.emit(self._html(
                            f"⚠️ 重置 requirements.txt 失败(将沿用现有内容): {e}", "#FF9800"))
                if os.path.exists(req_file):
                    self._filter_torch_requirements(req_file)
                    # 剥掉需要本地 C/C++/CMake 编译且没 win-py311 预编译 wheel 的依赖,
                    # 后面用纯 Python / 预编译替代版单独装回来:
                    #   jieba_fast → jieba (纯 Python, requirements 里本来就有)
                    #   pyopenjtalk → pyopenjtalk-plus (drop-in, 有预编译 wheel)
                    #   opencc → opencc-python-reimplemented (纯 Python, API 兼容)
                    removed = self._strip_requirements(
                        req_file, ("jieba_fast", "pyopenjtalk", "opencc"))
                    if removed:
                        self.output_signal.emit(self._html(
                            f"已从 requirements 移除: {', '.join(removed)}"
                            "(将以纯 Python / 预编译替代版安装)",
                            "#4FC3F7"))
                    ok = self._run_pip_install(["-r", req_file])
                    if not ok and not self._is_cancelled:
                        self.finished_signal.emit(False, f"{package} 依赖安装失败")
                        return None
                else:
                    self.output_signal.emit(self._html(
                        f"⚠️ 未找到 {req_file}，跳过依赖安装", "#FF9800"))

                # 补装三个替代品 / 遗漏包
                self.output_signal.emit(self._html(
                    "正在安装 pyopenjtalk-plus(日文 G2P 预编译版)...", "#4FC3F7"))
                ok_jtalk = self._run_pip_install(["pyopenjtalk-plus"])
                self.output_signal.emit(self._html(
                    "正在安装 opencc-python-reimplemented(繁简转换纯 Python 版)...",
                    "#4FC3F7"))
                ok_opencc = self._run_pip_install(["opencc-python-reimplemented"])
                self.output_signal.emit(self._html(
                    "正在安装 pytorch-lightning(推理依赖,upstream requirements 遗漏)...",
                    "#4FC3F7"))
                ok_pl = self._run_pip_install(["pytorch-lightning"])
                if not (ok_jtalk and ok_opencc and ok_pl) and not self._is_cancelled:
                    self.output_signal.emit(self._html(
                        "⚠️ 部分补装依赖失败,相关功能可能不可用", "#FF9800"))

            # 源码里硬编码 `import jieba_fast`,该包是 jieba 的 C 加速 fork,
            # 在 win-py311 上没有预编译 wheel,源码装也需要 VS Build Tools。
            # 既然 requirements 里已经把 jieba_fast 剥掉换成 jieba(API 完全兼容),
            # 这里把仓库源码里几处 `jieba_fast` 字面量也改成 `jieba`,
            # 否则 inference_webui import 阶段就会 ModuleNotFoundError。
            self._patch_gptsovits_source(project_root)

            # 预下载英文 g2p 用到的 NLTK 资源。新版 nltk 的 pos_tag 找的是
            # `averaged_perceptron_tagger_eng`(旧名 `averaged_perceptron_tagger`
            # 没用),不预下载,中英混文本第一次推理就会 LookupError。
            self.output_signal.emit(self._html(
                "正在下载 NLTK 英文 g2p 资源(averaged_perceptron_tagger_eng / cmudict)...",
                "#4FC3F7"))
            self._run_subprocess([
                PYTHON_PATH, "-m", "nltk.downloader", "-q",
                "averaged_perceptron_tagger_eng", "cmudict",
            ])

            self.output_signal.emit(self._html(
                "ℹ️ 请把预训练权重放到仓库 GPT_SoVITS/pretrained_models/ 目录,"
                "下载地址见 https://github.com/RVC-Boss/GPT-SoVITS#pretrained-models",
                "#4FC3F7"))
            self._emit_install_finished(package)
            return None

        elif package == "audiocraft":
            # audiocraft 1.3.0 把 av 钉死在 11.0.0,可这个版本：
            #   - 已从官方 PyPI yank,任何镜像都拿不到 wheel；
            #   - 源码构建要 Windows SDK 头 (io.h),嵌入式 py311 拿不到。
            # 解法：克隆源码,放宽 av 版本约束,改装一个有 wheel 的 av (>=12)。
            # 实测 audiocraft 用的 av API (av.open / Stream / Frame) 在 12.x 仍然兼容。
            req_file = os.path.join(project_root, "requirements.txt")
            setup_file = os.path.join(project_root, "setup.py")
            cfg_file = os.path.join(project_root, "setup.cfg")
            pyproject_file = os.path.join(project_root, "pyproject.toml")

            # Step 1: 把 `av==11.0.0` 替换成 `av>=11.0.0`,setup.py / requirements
            # / setup.cfg / pyproject.toml 里都扫一遍。
            # 即便走 lock 装依赖,Step 5 装 audiocraft 本体时 setup.py 仍会读到
            # av==11 约束并触发源码构建,所以这一步必须保留。
            av_pin = re.compile(r'av\s*==\s*11\.0\.0')
            patched = []
            for path in (req_file, setup_file, cfg_file, pyproject_file):
                if not os.path.exists(path):
                    continue
                try:
                    with open(path, 'r', encoding='utf-8') as f:
                        content = f.read()
                    new = av_pin.sub('av>=11.0.0', content)
                    if new != content:
                        with open(path, 'w', encoding='utf-8') as f:
                            f.write(new)
                        patched.append(os.path.basename(path))
                except Exception as e:
                    self.output_signal.emit(self._html(
                        f"⚠️ 放宽 {path} av 约束失败: {e}", "#FF9800"))
            if patched:
                self.output_signal.emit(self._html(
                    f"✅ 已把 {', '.join(patched)} 里的 av==11.0.0 放宽到 av>=11.0.0",
                    "#4CAF50"))

            # Step 2-4: 装依赖。优先走 locks/audiocraft.txt(已干净,不含 torch/
            # pesq/torchtext/xformers,av 也是已知能装 wheel 的版本);
            # 没锁文件时回退到上游 requirements,过滤 torch* + 剥 pesq/torchtext/xformers,
            # 再单独装 av --only-binary。
            lock = _lock_path(package)
            if lock:
                self.output_signal.emit(self._html(
                    f"使用锁版依赖: locks/{os.path.basename(lock)}", "#4FC3F7"))
                ok_req = self._run_pip_install(["-r", lock])
                if not ok_req and not self._is_cancelled:
                    self.finished_signal.emit(False, "audiocraft 依赖安装失败")
                    return None
            else:
                # 没锁:照旧过滤上游 requirements,然后单独装 av,再装其余
                if os.path.exists(req_file):
                    self._filter_torch_requirements(req_file)
                    # 这几个会把 CUDA torch 拖成 CPU 或源码构建失败,必须剥:
                    #   torchtext: 钉 torch>=2.1.0,<2.2.0; 仓库 grep 0 命中纯死代码
                    #   xformers<0.0.23: 钉 torch==2.0.1; Step 6 用 --no-deps 单装新版
                    #   pesq: 无 win wheel; LaunchAI 只跑推理,代码路径走不到
                    removed = self._strip_requirements(
                        req_file, ("pesq", "torchtext", "xformers"))
                    if removed:
                        self.output_signal.emit(self._html(
                            f"已从 requirements 移除: {', '.join(removed)} "
                            "(torchtext/xformers 会把 CUDA torch 拖成 CPU;pesq 无 wheel)",
                            "#4FC3F7"))

                self.output_signal.emit(self._html(
                    "正在安装 av (绕过 11.0.0 缺 wheel,改装最新可用版本)...",
                    "#4FC3F7"))
                ok_av = self._run_pip_install(["av", "--only-binary=:all:"])
                if not ok_av and not self._is_cancelled:
                    self.finished_signal.emit(False, "av 安装失败 (无可用 wheel)")
                    return None

                if os.path.exists(req_file):
                    self.output_signal.emit(self._html(
                        "正在安装 audiocraft 依赖 (requirements.txt)...", "#4FC3F7"))
                    ok_req = self._run_pip_install(["-r", req_file])
                    if not ok_req and not self._is_cancelled:
                        self.finished_signal.emit(False, "audiocraft 依赖安装失败")
                        return None

            # Step 5: 装 audiocraft 本体 (此时 av 已满足,setup.py 不会再尝试装 av==11)
            self.output_signal.emit(self._html(
                "正在安装 audiocraft 本体...", "#4FC3F7"))
            ok_pkg = self._run_pip_install([project_root])
            if not ok_pkg and not self._is_cancelled:
                self.finished_signal.emit(False, "audiocraft 本体安装失败")
                return None

            # Step 6: 按当前 torch 主.次版本挑 ABI 匹配的 xformers wheel,
            # --no-deps 单装,防止 pip 顺手把 torch 一起换掉。
            # 不能直接 `pip install xformers --no-deps`——pip 默认抓最新版,
            # 而 xformers 的 C++/CUDA 扩展是按 torch 版本编译的,装错版本会:
            #   WARNING[XFORMERS]: xFormers can't load C++/CUDA extensions
            #   ImportError: cannot import name 'GroupName' from
            #                torch.distributed.distributed_c10d (新 xformers
            #                引用了只有新 torch 才有的符号)
            # 而 audiocraft transformer.py 顶层就是 `from xformers import ops`,
            # 不装或装错都会让 import audiocraft 直接挂掉。
            ok_xf = self._install_xformers_matching_torch()
            if not ok_xf and not self._is_cancelled:
                self.output_signal.emit(self._html(
                    "⚠️ xformers 安装失败,audiocraft import 会报错。"
                    "可在确认 torch 版本后手动执行 "
                    "`pip install xformers==<对应版本> --no-deps`",
                    "#FF9800"))

            self._emit_install_finished(package)
            return None

        else:
            self._run_pip_install(["-e", project_root])
            self._emit_install_finished(package)
            return None

    def _clone_with_dulwich(self, repo_url: str, target_dir: str, branch: str = "master") -> bool:
        """
        使用 Dulwich 克隆仓库，支持镜像加速

        Args:
            repo_url: Git 仓库地址
            target_dir: 目标目录
            branch: 分支名

        Returns:
            是否克隆成功
        """
        try:
            # 如果目录已存在且有效，跳过克隆（用 .git 目录判定）
            if os.path.exists(target_dir) and os.path.exists(os.path.join(target_dir, ".git")):
                self.output_signal.emit(self._html(
                    f"仓库已存在，跳过克隆: {target_dir}", "#4CAF50"))
                return True

            parent_dir = os.path.dirname(target_dir)
            if parent_dir and not os.path.exists(parent_dir):
                os.makedirs(parent_dir, exist_ok=True)
                self.output_signal.emit(self._html(
                    f"创建目录: {parent_dir}", "#888888"))

            # 构建实际的克隆 URL（支持镜像）
            actual_url = self._get_mirror_url(repo_url)

            self.output_signal.emit(self._html(
                f"正在克隆仓库: {actual_url}", "#4FC3F7"))

            # 使用 Dulwich 克隆
            porcelain.clone(actual_url, target_dir,
                            checkout=True, branch=branch, depth=1)

            self.output_signal.emit(self._html(
                f"✅ 克隆成功: {target_dir}", "#4CAF50"))
            return True

        except Exception as e:
            self.output_signal.emit(self._html(f"❌ 克隆失败: {str(e)}", "#F44336"))
            return False

    def _get_mirror_url(self, original_url: str) -> str:
        """
        获取镜像加速 URL

        Args:
            original_url: 原始 GitHub URL

        Returns:
            镜像 URL（如果有可用镜像），否则返回原始 URL
        """
        global MIRROR_URLS

        if not MIRROR_URLS:
            return original_url

        # 只对 GitHub URL 使用镜像
        if 'github.com' not in original_url:
            return original_url

        # 使用第一个可用的镜像
        mirror = MIRROR_URLS[0].rstrip('/')

        # 构建镜像 URL
        # 例如: https://github.com/XPixelGroup/BasicSR.git
        # 转换为: https://mirror.com/https://github.com/XPixelGroup/BasicSR.git
        mirror_url = f"{mirror}/{original_url}"

        self.output_signal.emit(self._html(f"使用镜像加速: {mirror_url}", "#888888"))
        return mirror_url

    def _run_pip_install(self, args: list) -> bool:
        """同步执行 pip install，将输出通过信号发送"""
        if self._is_cancelled:
            return False
        cmd = [PYTHON_PATH, "-m", "pip", "install"] + args
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   text=True, bufsize=1, encoding='utf-8', errors='replace')
        self._current_proc = process
        for line in iter(process.stdout.readline, ''):
            if self._is_cancelled:
                self._kill_proc(process)
                break
            if line.strip():
                self.output_signal.emit(self._html(line.strip(), "#CCCCCC"))
        process.wait()
        self._current_proc = None
        return (not self._is_cancelled) and process.returncode == 0

    def _run_subprocess(self, cmd: list, cwd: str = None) -> bool:
        """同步执行任意 subprocess,把输出转成日志信号。失败返回 False(取消也算)。"""
        if self._is_cancelled:
            return False
        process = subprocess.Popen(
            cmd, cwd=cwd,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1, encoding='utf-8', errors='replace')
        self._current_proc = process
        for line in iter(process.stdout.readline, ''):
            if self._is_cancelled:
                self._kill_proc(process)
                break
            if line.strip():
                self.output_signal.emit(self._html(line.strip(), "#CCCCCC"))
        process.wait()
        self._current_proc = None
        return (not self._is_cancelled) and process.returncode == 0

    def _run_setup_develop(self, cwd: str) -> bool:
        """在指定目录执行 python setup.py develop"""
        if self._is_cancelled:
            return False
        self.output_signal.emit(self._html(
            f"执行 python setup.py develop in {cwd}", "#4FC3F7"))
        process = subprocess.Popen(
            [PYTHON_PATH, "setup.py", "develop"],
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            encoding='utf-8',
            errors='replace'
        )
        self._current_proc = process
        for line in iter(process.stdout.readline, ''):
            if self._is_cancelled:
                self._kill_proc(process)
                break
            if line.strip():
                self.output_signal.emit(self._html(line.strip(), "#CCCCCC"))
        process.wait()
        self._current_proc = None
        return (not self._is_cancelled) and process.returncode == 0

    # torch 主.次 → xformers wheel 版本。数据源:每个 xformers release 的
    # PyPI install_requires 里都钉死了 `torch==X.Y.Z`,这里取对应那一版。
    # 装错会触发 ImportError: cannot import name 'GroupName' from
    # torch.distributed.distributed_c10d (新 xformers 引用了只有新 torch
    # 才有的符号),或者 xFormers C++ 扩展加载失败的 WARNING。
    _XFORMERS_FOR_TORCH = {
        "2.0": "0.0.22.post7",
        "2.1": "0.0.23.post1",
        "2.2": "0.0.25.post1",
        "2.3": "0.0.27",
        "2.4": "0.0.28",
        "2.5": "0.0.28.post3",
        "2.6": "0.0.29.post2",
        "2.7": "0.0.31",
        "2.8": "0.0.32.post2",
        "2.9": "0.0.33",
    }

    def _install_xformers_matching_torch(self) -> bool:
        """读当前 torch 版本 + CUDA 后缀,装 ABI 匹配的 xformers wheel(--no-deps)。

        清华/阿里镜像对 xformers 老版本只有 sdist,没 win wheel,pip 回退
        源码编译会因 Windows 长路径限制 + 没装 MSVC 直接挂掉,所以
        --index-url 钉死 PyTorch 官方源(每个 torch+cu 都有预编译 wheel),
        并加 --only-binary=:all: 强制只用 wheel,绝不走源码构建。
        """
        # 1. 读 torch 主.次 + CUDA 后缀
        try:
            result = subprocess.run(
                [PYTHON_PATH, "-c",
                 "import torch,sys;raw=torch.__version__;"
                 "ver,_,cu=raw.partition('+');"
                 "sys.stdout.write('.'.join(ver.split('.')[:2])+'|'+(cu or 'cpu'))"],
                capture_output=True, text=True, timeout=10,
                encoding='utf-8', errors='replace'
            )
            out = (result.stdout or "").strip()
            if result.returncode != 0 or "|" not in out:
                self.output_signal.emit(self._html(
                    "⚠️ 无法读取 torch 版本,跳过 xformers 自动安装", "#FF9800"))
                return False
            torch_minor, cu_tag = out.split("|", 1)
        except Exception as e:
            self.output_signal.emit(self._html(
                f"⚠️ 读取 torch 版本失败({e}),跳过 xformers 自动安装", "#FF9800"))
            return False

        # 2. 找匹配的 xformers 版本
        xf_ver = self._XFORMERS_FOR_TORCH.get(torch_minor)
        if not xf_ver:
            self.output_signal.emit(self._html(
                f"⚠️ 未给 torch {torch_minor} 预置 xformers 映射,跳过自动安装。"
                f"请手动: pip install xformers==<对应版本> --no-deps "
                f"--index-url https://download.pytorch.org/whl/{cu_tag}",
                "#FF9800"))
            return False

        # 3. 装 (PyTorch 官方源 + only-binary, 绝不走 sdist 编译)
        index_url = f"https://download.pytorch.org/whl/{cu_tag}"
        self.output_signal.emit(self._html(
            f"按 torch {torch_minor}+{cu_tag} 匹配 xformers {xf_ver},"
            f"从 {index_url} 安装...", "#4FC3F7"))
        return self._run_pip_install([
            f"xformers=={xf_ver}", "--no-deps", "--force-reinstall",
            "--only-binary=:all:", "--index-url", index_url,
        ])

    def _kill_proc(self, proc):
        """优雅终止 subprocess：先 terminate，5s 不退强 kill"""
        try:
            if proc.poll() is None:
                proc.terminate()
                for _ in range(20):  # 5s 上限
                    if proc.poll() is not None:
                        break
                    time.sleep(0.25)
                if proc.poll() is None:
                    proc.kill()
        except Exception as e:
            error(f"终止子进程出错: {e}")

    def cancel(self):
        """用户主动取消：标记取消标志 + 立刻终止当前 subprocess"""
        if self._is_cancelled:
            return
        self._is_cancelled = True
        self.output_signal.emit(self._html("⚠️ 用户已请求取消，正在终止子进程...", "#FF9800"))
        if self._current_proc is not None:
            self._kill_proc(self._current_proc)
        self.requestInterruption()

    def _filter_torch_requirements(self, req_path: str):
        """从 requirements 文件中移除 torch/torchvision/torchaudio 三件套。

        精确匹配 distribution 名,不会误伤 pytorch-lightning / torchcrepe /
        torchmetrics / pytorch-pretrained-bert 等带 torch 字串的其它包。
        """
        TORCH_CORE = {"torch", "torchvision", "torchaudio"}
        with open(req_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        filtered = []
        for line in lines:
            s = line.strip()
            if s and not s.startswith('#') and not s.startswith('--'):
                head = re.split(r'[<>=!;~\s\[]', s, maxsplit=1)[0].lower()
                # 处理下划线 vs 短横线:torch_xxx 和 torch-xxx 都不该匹配,
                # 三件套本身没有任何分隔符,直接小写比对就行
                if head in TORCH_CORE:
                    continue
            filtered.append(line)
        with open(req_path, 'w', encoding='utf-8') as f:
            f.writelines(filtered)

    def _strip_requirements(self, req_path: str, packages: tuple) -> list:
        """从 requirements 文件里移除指定包(按 distribution 名匹配,大小写不敏感)。

        同时处理 `--no-binary=pkg` 这类 pip 选项行,避免残留触发强制源码编译。
        返回实际删掉的包名列表。
        """
        targets = tuple(p.lower() for p in packages)
        removed = []

        with open(req_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()

        def _hit(line: str) -> str:
            s = line.strip()
            if not s or s.startswith('#'):
                return ""
            if s.startswith('--no-binary'):
                for pkg in targets:
                    if pkg in s.lower():
                        return pkg
                return ""
            head = re.split(r'[<>=!;~\s]', s, maxsplit=1)[0].lower()
            return head if head in targets else ""

        filtered = []
        for line in lines:
            pkg = _hit(line)
            if pkg:
                if pkg not in removed:
                    removed.append(pkg)
                continue
            filtered.append(line)

        with open(req_path, 'w', encoding='utf-8') as f:
            f.writelines(filtered)
        return removed

    def _patch_gptsovits_source(self, project_root: str):
        """把 GPT-SoVITS 源码里硬编码的 `jieba_fast` 改成 `jieba`。

        jieba_fast 在 win-py311 上没有预编译 wheel,我们已用纯 Python 的 jieba 替代,
        但仓库源码里(chinese.py / chinese2.py / tone_sandhi.py)还写着
        `import jieba_fast`,启动时会 ModuleNotFoundError。
        匹配 `jieba_fast` 整词(后面不能跟字母数字下划线),避免误伤其它命名。
        """
        targets = [
            os.path.join(project_root, "GPT_SoVITS", "text", "chinese.py"),
            os.path.join(project_root, "GPT_SoVITS", "text", "chinese2.py"),
            os.path.join(project_root, "GPT_SoVITS", "text", "tone_sandhi.py"),
        ]
        pattern = re.compile(r'\bjieba_fast\b')
        patched = []
        for path in targets:
            if not os.path.exists(path):
                continue
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    content = f.read()
                if 'jieba_fast' not in content:
                    continue
                new_content = pattern.sub('jieba', content)
                if new_content != content:
                    with open(path, 'w', encoding='utf-8') as f:
                        f.write(new_content)
                    patched.append(os.path.basename(path))
            except Exception as e:
                self.output_signal.emit(self._html(
                    f"⚠️ 修补 {path} 失败: {e}", "#FF9800"))
        if patched:
            self.output_signal.emit(self._html(
                f"✅ 已把 {', '.join(patched)} 里的 jieba_fast 替换为 jieba",
                "#4CAF50"))

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

            result = subprocess.run(
                [sys.executable, "-m", "pip", "show", package_name],
                capture_output=True,
                text=True,
                timeout=10
            )
            return result.returncode == 0

        except Exception:
            return False

    @staticmethod
    def list_installed_packages() -> list:
        """pip list --format=json,返回 [{name, version}, ...]。

        Returns:
            失败/超时 → 空列表。调用方需要处理空列表(展示"无法获取包列表")。
        """
        import json
        try:
            result = subprocess.run(
                [PYTHON_PATH, "-m", "pip", "list", "--format=json", "--disable-pip-version-check"],
                capture_output=True, text=True, timeout=20,
                encoding='utf-8', errors='replace',
            )
            if result.returncode != 0 or not result.stdout.strip():
                error(f"pip list 失败 rc={result.returncode} stderr={result.stderr[:200]}")
                return []
            data = json.loads(result.stdout)
            # 标准化为 [{name, version}],pip 自己返回 [{"name":..., "version":...}]
            return [{"name": d.get("name", ""), "version": d.get("version", "")}
                    for d in data if isinstance(d, dict) and d.get("name")]
        except Exception as e:
            error(f"list_installed_packages 异常: {e}")
            return []

    @staticmethod
    def list_dependency_edges() -> dict:
        """构建已安装包之间的依赖边,供「树状图」展示递进关系。

        返回 {规范化包名: [它依赖且确实已安装的规范化包名, ...]}。
        - 只保留运行期依赖:extras 触发的、marker 不满足的(如仅 Windows / 仅 py<3.8)依赖跳过;
        - 只保留本环境真正装了的边,避免把没装的可选依赖画进树里。
        PYTHON_PATH == sys.executable,所以直接读本进程的 importlib.metadata 即可,无需起子进程。
        """
        import importlib.metadata as im
        try:
            from packaging.requirements import Requirement
        except Exception as e:
            error(f"list_dependency_edges 缺少 packaging: {e}")
            return {}

        def _norm(n: str) -> str:
            return (n or "").lower().replace("_", "-").strip()

        installed = set()
        raw_requires = {}   # norm_name -> [Requirement 字符串]
        try:
            for dist in im.distributions():
                name = _norm(dist.metadata.get("Name", ""))
                if not name:
                    continue
                installed.add(name)
                # 同名包可能因多份 .dist-info 出现多次,合并 requires 即可
                raw_requires.setdefault(name, [])
                raw_requires[name].extend(dist.requires or [])
        except Exception as e:
            error(f"list_dependency_edges 枚举失败: {e}")
            return {}

        edges = {}
        for name, reqs in raw_requires.items():
            deps = []
            seen = set()
            for spec in reqs:
                try:
                    r = Requirement(spec)
                except Exception:
                    continue
                # extras 触发的依赖(marker 含 extra == "...")默认不装,跳过
                if r.marker is not None:
                    try:
                        if not r.marker.evaluate():
                            continue
                    except Exception:
                        # marker 含 extra 等无法在空环境求值 → 视为可选,跳过
                        continue
                dep = _norm(r.name)
                if dep in installed and dep != name and dep not in seen:
                    seen.add(dep)
                    deps.append(dep)
            edges[name] = deps
        return edges

    @staticmethod
    def get_torch_devices():
        """获取所有可用的 torch 设备

        Returns:
            dict: 设备字典，如 {"NVIDIA GeForce RTX 3060 Ti": "cuda:0", "cpu": "cpu"}
        """
        try:
            test_code = """
import torch
devices = {}
# 始终添加 CPU
devices["cpu"] = "cpu"
# 添加 GPU（如果有）
if torch.cuda.is_available():
    for i in range(torch.cuda.device_count()):
        gpu_name = torch.cuda.get_device_name(i)
        devices[gpu_name] = f"cuda:{i}"
print(devices)
    """
            result = subprocess.run(
                [PYTHON_PATH, "-c", test_code],
                capture_output=True,
                text=True,
                timeout=10,
                encoding='utf-8',
                errors='replace'
            )

            if result.returncode == 0 and result.stdout.strip():
                import ast
                devices = ast.literal_eval(result.stdout.strip())
                return devices
            else:
                return {}

        except Exception as e:
            error(f"获取设备失败: {e}")
            return {}

    def stop(self):
        self.requestInterruption()
        self.quit()
        self.wait()


class UninstallWorker(QThread):
    """卸载工作线程: 跑 `pip uninstall -y <pkg>` 并流式回吐输出。

    与 PipWorker 共用信号形状(output_signal/finished_signal),
    PackageManagerPage 直接复用 LogTextEdit。

    完成后:
      - 成功 → set_field("installed.<pkg>", False),让仓库式工具
        (Applio / GPT-SoVITS)的 SwitchPage 重启后也认为未安装。
      - 失败 → 不动 config,避免 UI 显示「未安装」但环境里残留半成品。
    """
    output_signal = pyqtSignal(str)
    finished_signal = pyqtSignal(bool, str)

    def __init__(self, packages, purge_dirs=None, parent=None):
        super().__init__(parent)
        self.packages = packages if isinstance(packages, list) else [packages]
        # purge_dirs: pip uninstall 之后要额外 rmtree 的目录(克隆的源码工程,
        # 如 _git_projects/<package>_<fork>)。pip 卸载只清 site-packages,
        # setup.py develop / 克隆下来的仓库目录不会被清,需要这里补刀。
        self.purge_dirs = list(purge_dirs) if purge_dirs else []
        self._proc = None
        self._cancelled = False

    def _html(self, text, color=None, bold=False):
        if not color and not bold:
            return text
        style = []
        if color:
            style.append(f"color:{color}")
        if bold:
            style.append("font-weight:bold")
        return f'<span style="{";".join(style)}">{text}</span>'

    def cancel(self):
        self._cancelled = True
        try:
            if self._proc and self._proc.poll() is None:
                self._proc.kill()
        except Exception:
            pass

    def run(self):
        if not self.packages and not self.purge_dirs:
            self.finished_signal.emit(False, "未指定要卸载的包")
            return

        ok_pkgs, fail_pkgs = [], []
        for pkg in self.packages:
            if self._cancelled:
                break
            self.output_signal.emit(self._html(
                f"▶ 卸载 {pkg} …", "#4FC3F7", bold=True))
            cmd = [PYTHON_PATH, "-m", "pip", "uninstall", "-y",
                   "--disable-pip-version-check", pkg]
            try:
                self._proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    bufsize=1,
                )
                for line in iter(self._proc.stdout.readline, ""):
                    if not line:
                        break
                    self.output_signal.emit(line.rstrip())
                rc = self._proc.wait()
            except Exception as e:
                self.output_signal.emit(self._html(
                    f"✗ {pkg} 卸载异常: {e}", "#F44336"))
                fail_pkgs.append(pkg)
                continue

            if rc == 0:
                ok_pkgs.append(pkg)
                set_field(f"installed.{pkg}", False)
                self.output_signal.emit(self._html(
                    f"✅ {pkg} 已卸载", "#4CAF50"))
            else:
                fail_pkgs.append(pkg)
                self.output_signal.emit(self._html(
                    f"✗ {pkg} 卸载失败 (rc={rc})", "#F44336"))

        # 清理克隆的源码工程目录(pip uninstall 不会动这些)。
        # 即便某些包 pip 卸载失败,这里仍尝试删目录:用户选的是「彻底删除」,
        # 残留的半成品克隆只会让下次重装更乱。ignore_errors 容忍占用/缺失。
        for d in self.purge_dirs:
            if self._cancelled:
                break
            if not d or not os.path.isdir(d):
                continue
            self.output_signal.emit(self._html(
                f"🗑 删除目录 {d} …", "#FF9800"))
            try:
                shutil.rmtree(d, ignore_errors=True)
                if os.path.isdir(d):
                    self.output_signal.emit(self._html(
                        f"⚠ 目录未能完全删除(可能被占用): {d}", "#FF9800"))
                else:
                    self.output_signal.emit(self._html(
                        f"✅ 已删除 {d}", "#4CAF50"))
            except Exception as e:
                self.output_signal.emit(self._html(
                    f"✗ 删除目录失败 {d}: {e}", "#F44336"))

        if self._cancelled:
            self.finished_signal.emit(False, "已取消卸载")
            return
        if fail_pkgs:
            self.finished_signal.emit(
                False,
                f"成功 {len(ok_pkgs)} 个,失败 {len(fail_pkgs)} 个: {', '.join(fail_pkgs)}")
        elif ok_pkgs:
            self.finished_signal.emit(
                True, f"已卸载 {len(ok_pkgs)} 个包: {', '.join(ok_pkgs)}")
        else:
            # 纯目录清理(packages 为空,如 Real-ESRGAN 内置工具只删源码残留)
            self.finished_signal.emit(True, "已清理源码残留")


PipWorker._test_git_mirrors()
