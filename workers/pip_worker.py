import subprocess
import sys
import os
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
cache_ = os.path.join(os.getcwd(), "torch_cache")
fork_map = {
    "demucs": "main",
    "Real-ESRGAN": "master"
}
MIRROR_URLS = get_field("git_mirror_hosts", [])
info(f"获取到GIT加速镜像: {MIRROR_URLS}")


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
                self.install_from_git()  # 独立处理
                return
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

    def install_from_git(self):
        git_url = self.from_git[1]
        package = self.package_name[0]  # 只处理单包
        fork = fork_map.get(package, "main")

        # 项目存放的专属目录
        project_parent = GIT_PROJECTS_ROOT
        project_dir = os.path.join(project_parent, f"{package}_{fork}")

        # 检查是否已经存在且有效
        if os.path.exists(project_dir) and os.path.exists(os.path.join(project_dir, "setup.py")):
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
            self.finished_signal.emit(True, f"{package} 源码安装成功")
            return None

        elif package == "demucs":
            req_file = os.path.join(project_root, "requirements_minimal.txt")
            if os.path.exists(req_file):
                self._filter_torch_requirements(req_file)
                if sys.platform == "win32":
                    with open(req_file, 'a', encoding='utf-8') as f:
                        f.write("\nsoundfile\n")
                self._run_pip_install(["-r", req_file])
            self._run_pip_install([project_root])
            self.finished_signal.emit(True, f"{package} 源码安装成功")
            return None

        else:
            self._run_pip_install(["-e", project_root])
            self.finished_signal.emit(True, f"{package} 源码安装成功")
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
            # 如果目录已存在且有效，跳过克隆
            if os.path.exists(target_dir) and os.path.exists(os.path.join(target_dir, "setup.py")):
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
                            checkout=True, branch=branch)

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
        cmd = [PYTHON_PATH, "-m", "pip", "install"] + args
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   text=True, bufsize=1, encoding='utf-8', errors='replace')
        for line in iter(process.stdout.readline, ''):
            if line.strip():
                self.output_signal.emit(self._html(line.strip(), "#CCCCCC"))
        process.wait()
        return process.returncode == 0

    def _run_setup_develop(self, cwd: str) -> bool:
        """在指定目录执行 python setup.py develop"""
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
        for line in iter(process.stdout.readline, ''):
            if line.strip():
                self.output_signal.emit(self._html(line.strip(), "#CCCCCC"))
        process.wait()
        return process.returncode == 0

    def _filter_torch_requirements(self, req_path: str):
        """从 requirements 文件中移除 torch/torchvision/torchaudio 行"""
        with open(req_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        filtered = []
        for line in lines:
            line_stripped = line.strip()
            if line_stripped and not line_stripped.startswith('#'):
                if 'torch' in line_stripped.lower():
                    continue
            filtered.append(line)
        with open(req_path, 'w', encoding='utf-8') as f:
            f.writelines(filtered)

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


PipWorker._test_git_mirrors()
