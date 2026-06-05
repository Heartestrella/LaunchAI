from PyQt6.QtCore import QThread, pyqtSignal, QMutex
from PyQt6.QtCore import QThread, pyqtSignal
import re
import cpuinfo
import platform
import psutil
import subprocess
from datetime import datetime
import sys
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer, QRectF
from PyQt6.QtGui import QColor, QFont, QPainter, QPen
from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QSizePolicy,
    QGridLayout, QFrame, QGridLayout
)
import HardView
import json
from workers.cpu_score import GeekbenchScraper
from qfluentwidgets import (ElevatedCardWidget,
                            PrimaryPushButton, TransparentPushButton,
                            TitleLabel, SubtitleLabel, BodyLabel, CaptionLabel, StrongBodyLabel,
                            SmoothScrollArea, ToolTipFilter,
                            InfoBar, InfoBarPosition,
                            IconWidget, FluentIcon as FIF,
                            ProgressRing, IndeterminateProgressRing,
                            isDarkTheme,
                            )
# ══════════════════════════════════════════════════════════════════════
#  工具函数
# ══════════════════════════════════════════════════════════════════════


def fmt_bytes(n):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024.0:
            return f"{n:.1f} {unit}"
        n /= 1024.0
    return f"{n:.1f} PB"


def pct_color(pct: float) -> str:
    if pct < 60:
        return "#0DB37E"
    elif pct < 80:
        return "#F7B731"
    else:
        return "#FC5C65"


def get_windows_version() -> str:
    """winreg 读真实版本号，正确区分 Win10 / Win11"""
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Windows NT\CurrentVersion"
        )
        build = int(winreg.QueryValueEx(key, "CurrentBuildNumber")[0])
        name = winreg.QueryValueEx(key, "ProductName")[0]
        winreg.CloseKey(key)
        # Build 22000+ 是 Windows 11
        if build >= 22000 and "10" in name:
            name = name.replace("Windows 10", "Windows 11")
        return f"{name}  (Build {build})"
    except Exception:
        return f"{platform.system()} {platform.release()}"


# ══════════════════════════════════════════════════════════════════════
#  后台线程：采集系统信息
# ══════════════════════════════════════════════════════════════════════


class SystemInfoWorker(QThread):
    dataReady = pyqtSignal(dict)

    # 静态信息缓存（类级别，所有实例共享）
    _static_info = None
    _static_lock = QMutex()

    def run(self):
        info = {}

        # ========== 获取或缓存静态信息（只获取一次）==========

        self._static_lock.lock()
        if SystemInfoWorker._static_info is None:
            SystemInfoWorker._static_info = self._get_static_info()
        static = SystemInfoWorker._static_info
        self._static_lock.unlock()

        # 复制静态信息
        info.update(static)

        self._update_dynamic_info(info)

        # info_json = json.dumps(info, ensure_ascii=False, default=str)
        # print(f"Info 大小: {len(info_json)/1024:.1f} KB, 字段数: {len(info)}")

        self.dataReady.emit(info)

    def _get_static_info(self):

        static = {}

        # ---------- CPU 静态信息 ----------
        try:
            cpu_info = cpuinfo.get_cpu_info()
            static["cpu_name"] = ' '.join(cpu_info.get(
                'brand_raw', platform.processor()).split())
        except Exception:
            static["cpu_name"] = platform.processor()

        static["cpu_cores"] = psutil.cpu_count(logical=False)
        static["cpu_threads"] = psutil.cpu_count(logical=True)

        # 基础频率（静态）- 保持兼容性
        freq = psutil.cpu_freq()
        static["cpu_freq_ghz"] = f"{freq.min / 1000:.2f} GHz" if freq and freq.min else "—"
        static["cpu_base_freq"] = static["cpu_freq_ghz"]  # 别名

        # ---------- 内存静态信息 ----------
        vm = psutil.virtual_memory()
        static["ram_total"] = vm.total

        try:
            ram_json = HardView.get_ram_info()
            ram_data = json.loads(ram_json)

            memory_modules = ram_data.get('memory_modules', [])

            if memory_modules:
                first = memory_modules[0]
                speed = first.get('speed_mhz', 0)
                static["ram_type"] = self._get_ddr_type(speed)
                static["ram_speed"] = f"{speed} MHz"

                # 生成容量列表
                capacities = []
                for m in memory_modules:
                    gb = round(m.get('capacity_bytes', 0) / (1024**3), 1)
                    capacities.append(
                        f"{int(gb) if gb.is_integer() else gb}GB")
                static["ram_capacity_list"] = "+".join(capacities)
                static["ram_module_count"] = len(memory_modules)
            else:
                static["ram_type"] = "未知"
                static["ram_speed"] = "未知"
                static["ram_capacity_list"] = "未知"
                static["ram_module_count"] = 0

        except Exception as e:
            print(f"HardView RAM 获取失败: {e}")
            static["ram_type"] = "未知"
            static["ram_speed"] = "未知"
            static["ram_capacity_list"] = "未知"
            static["ram_module_count"] = 0

        # ---------- 磁盘静态信息 ----------
        total_d = used_d = 0
        disk_partitions = []
        for part in psutil.disk_partitions(all=False):
            try:
                du = psutil.disk_usage(part.mountpoint)
                total_d += du.total
                used_d += du.used
                disk_partitions.append({
                    "device": part.device,
                    "mountpoint": part.mountpoint,
                    "total": du.total,
                    "used": du.used,
                    "free": du.free,
                    "percent": du.percent
                })
            except Exception:
                pass
        static["disk_total"] = total_d
        static["disk_used"] = used_d
        static["disk_pct"] = (used_d / total_d * 100) if total_d else 0
        static["disk_partitions"] = disk_partitions

        # ---------- 系统静态信息 ----------
        static["os_display"] = self._get_os_version()
        static["hostname"] = platform.node()
        static["arch"] = platform.machine()
        static["boot_time"] = datetime.fromtimestamp(
            psutil.boot_time()).strftime("%Y-%m-%d %H:%M")

        # ---------- GPU 静态信息 ----------
        self._get_gpu_static_info(static)

        # ---------- Python 包信息 ----------
        static["pip_packages"] = self._get_pip_packages()

        # ---------- CPU 跑分信息 ----------
        scraper = GeekbenchScraper()
        single, multi = scraper.get_local_cpu_scores()
        print(f"CPU 跑分 - 单核: {single}, 多核: {multi}")
        static["cpu_single_score"] = single
        static["cpu_multi_score"] = multi

        return static

    def _update_dynamic_info(self, info):
        """更新动态信息（每次刷新都获取）"""

        # CPU 使用率
        info["cpu_usage"] = psutil.cpu_percent(interval=0.3)

        # CPU 当前频率（动态，会覆盖静态的 cpu_freq_ghz）
        freq = psutil.cpu_freq()
        if freq:
            info["cpu_freq_ghz"] = f"{freq.current / 1000:.2f} GHz"
            info["cpu_cur_freq"] = info["cpu_freq_ghz"]

        # 内存使用情况
        vm = psutil.virtual_memory()
        info["ram_used"] = vm.used
        info["ram_avail"] = vm.available
        info["ram_pct"] = vm.percent

        # GPU 动态信息
        self._update_gpu_dynamic_info(info)

    def _get_gpu_static_info(self, static):
        """获取 GPU 静态信息（名称、显存大小、驱动版本、CUDA版本）"""
        static["gpu_name"] = "—"
        static["gpu_vram"] = "—"
        static["gpu_driver"] = "—"
        static["gpu_cuda_version"] = "—"
        static["gpu_type"] = "integrated"

        try:
            # 获取 GPU 基本信息
            out = subprocess.check_output(
                ["nvidia-smi", "--query-gpu=name,memory.total,driver_version",
                 "--format=csv,noheader,nounits"],
                timeout=3, stderr=subprocess.DEVNULL
            ).decode().strip().split("\n")[0]
            p = [x.strip() for x in out.split(",")]
            static["gpu_name"] = p[0] if p else "—"
            static["gpu_vram"] = f"{int(p[1]) / 1024:.1f} GB" if len(p) > 1 else "—"
            static["gpu_driver"] = p[2] if len(p) > 2 else "—"
            static["gpu_type"] = "dedicated"

            # CUDA 版本
            nvidia_out = subprocess.check_output(
                ["nvidia-smi"], timeout=3, stderr=subprocess.DEVNULL
            ).decode()
            match = re.search(r'CUDA Version:\s+(\d+\.\d+)', nvidia_out)
            static["gpu_cuda_version"] = match.group(1) if match else "未检测到"

        except (subprocess.CalledProcessError, FileNotFoundError, IndexError, ValueError):
            # 非 NVIDIA 显卡
            static["gpu_name"] = self._get_integrated_gpu_name()

    def _update_gpu_dynamic_info(self, info):
        """更新 GPU 动态信息（使用率、显存使用量）"""
        info["gpu_usage"] = 0
        info["gpu_mem_used"] = 0
        info["gpu_mem_total"] = 0

        # 如果已经有静态的显存总量，先用着
        if "gpu_mem_total" not in info:
            info["gpu_mem_total"] = 0

        try:
            # 获取 GPU 使用率和显存使用量
            out = subprocess.check_output(
                ["nvidia-smi", "--query-gpu=utilization.gpu,memory.used,memory.total",
                 "--format=csv,noheader,nounits"],
                timeout=3, stderr=subprocess.DEVNULL
            ).decode().strip().split("\n")[0]
            p = [x.strip() for x in out.split(",")]
            info["gpu_usage"] = float(p[0]) if len(p) > 0 else 0
            info["gpu_mem_used"] = int(p[1]) if len(p) > 1 else 0
            info["gpu_mem_total"] = int(p[2]) if len(p) > 2 else 0

        except (subprocess.CalledProcessError, FileNotFoundError, IndexError, ValueError):
            # 非 NVIDIA 显卡，使用估算
            vm = psutil.virtual_memory()
            info["gpu_usage"] = min(
                100, psutil.cpu_percent(interval=0.1) * 0.5)
            info["gpu_mem_used"] = int(vm.used * 0.1 / (1024*1024))

            # 如果静态信息中没有显存总量，才使用估算值
            if info["gpu_mem_total"] == 0:
                info["gpu_mem_total"] = int(vm.total / (1024*1024))

    # ========== 辅助方法 ==========

    @staticmethod
    def _get_ddr_type(speed_mhz):
        """根据频率推断 DDR 类型"""
        if speed_mhz >= 4800:
            return "DDR5"
        elif 2133 <= speed_mhz < 4800:
            return "DDR4"
        elif 800 <= speed_mhz < 2133:
            return "DDR3"
        elif 400 <= speed_mhz < 800:
            return "DDR2"
        return "DDR"

    @staticmethod
    def _get_os_version():
        """获取操作系统版本"""
        if platform.system() == "Windows":
            try:
                import winreg
                key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                     r"SOFTWARE\Microsoft\Windows NT\CurrentVersion")
                build = int(winreg.QueryValueEx(key, "CurrentBuildNumber")[0])
                name = winreg.QueryValueEx(key, "ProductName")[0]
                winreg.CloseKey(key)
                if build >= 22000 and "10" in name:
                    name = name.replace("Windows 10", "Windows 11")
                return f"{name} (Build {build})"
            except Exception:
                pass
        return f"{platform.system()} {platform.release()}"

    @staticmethod
    def _get_integrated_gpu_name():
        """获取集成显卡名称"""
        try:
            import wmi
            c = wmi.WMI()
            for gpu in c.Win32_VideoController():
                name = gpu.Name
                if name and ('Intel' in name or 'AMD' in name or 'Radeon' in name or 'UHD' in name):
                    return name.strip()
        except Exception:
            pass

        try:
            cpu_info = cpuinfo.get_cpu_info()
            brand = cpu_info.get('brand_raw', '')
            if 'Intel' in brand:
                return "Intel 集成显卡"
            elif 'AMD' in brand:
                return "AMD Radeon 集成显卡"
        except Exception:
            pass

        return "集成显卡"

    @staticmethod
    def _get_pip_packages():
        """获取 Python 包信息"""
        targets = {
            "torch", "torchvision", "torchaudio", "tensorflow",
            "onnx", "onnxruntime", "numpy", "scipy", "pandas",
            "pyqt6", "PyQt6-Fluent-Widgets"
        }
        result = {}
        try:
            output = subprocess.check_output(
                [sys.executable, "-m", "pip", "list", "--format=freeze"],
                timeout=10, stderr=subprocess.DEVNULL
            ).decode("utf-8", errors="ignore")
            for line in output.splitlines():
                if "==" in line:
                    name, ver = line.split("==", 1)
                    if name.lower() in targets:
                        result[name.lower()] = ver
        except Exception:
            pass
        return result


# ══════════════════════════════════════════════════════════════════════
#  圆形仪表盘小控件
# ══════════════════════════════════════════════════════════════════════


class GaugeDial(QWidget):
    """纯 QPainter 绘制的圆弧仪表盘，支持动态更新"""

    def __init__(self, label: str, size: int = 110, parent=None):
        super().__init__(parent)
        self.label = label
        self._value = 0          # 0-100
        self._color = "#0DB37E"
        self.setFixedSize(size, size)

    def setValue(self, v: int, color: str):
        self._value = max(0, min(100, v))
        self._color = color
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        w, h = self.width(), self.height()
        margin = 10
        rect = QRectF(margin, margin, w - 2*margin, h - 2*margin)

        # 背景弧（灰色）
        bg_color = QColor(180, 180, 180, 60)
        pen = QPen(bg_color, 9, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.drawArc(rect, 225 * 16, -270 * 16)

        # 前景弧（彩色）
        span = int(-270 * 16 * self._value / 100)
        if span != 0:
            pen2 = QPen(QColor(self._color), 9,
                        Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
            p.setPen(pen2)
            p.drawArc(rect, 225 * 16, span)

        # 百分比数字
        p.setPen(QColor(self._color))
        font = QFont("Segoe UI", int(w * 0.18), QFont.Weight.Bold)
        p.setFont(font)
        p.drawText(QRectF(0, h * 0.28, w, h * 0.32),
                   Qt.AlignmentFlag.AlignCenter,
                   f"{self._value}%")

        # 标签
        dark = isDarkTheme()
        p.setPen(QColor(200, 200, 200) if dark else QColor(100, 100, 100))
        font2 = QFont("Segoe UI", int(w * 0.11))
        p.setFont(font2)
        p.drawText(QRectF(0, h * 0.58, w, h * 0.28),
                   Qt.AlignmentFlag.AlignCenter,
                   self.label)
        p.end()


# ══════════════════════════════════════════════════════════════════════
#  实时概览卡（三个仪表盘 + 数值）
# ══════════════════════════════════════════════════════════════════════
class LiveMonitorCard(ElevatedCardWidget):
    def __init__(self, parent=None):
        super().__init__(parent)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(22, 16, 22, 16)
        outer.setSpacing(8)

        title = SubtitleLabel("实时概览", self)
        outer.addWidget(title)

        # 四表盘横排（CPU、内存、GPU、GPU显存）
        dial_row = QHBoxLayout()
        dial_row.setSpacing(0)

        self.cpu_dial = GaugeDial("CPU",  100, self)
        self.ram_dial = GaugeDial("内存", 100, self)
        self.gpu_dial = GaugeDial("GPU",  100, self)
        self.vram_dial = GaugeDial("显存", 100, self)  # 新增显存仪表盘

        for d in (self.cpu_dial, self.ram_dial, self.gpu_dial, self.vram_dial):
            dial_row.addWidget(d, 0, Qt.AlignmentFlag.AlignHCenter)

        outer.addLayout(dial_row)

        # 数值行
        val_row = QHBoxLayout()
        self.cpu_val = self._make_val_col("CPU 占用",  "—")
        self.ram_val = self._make_val_col("已用内存",  "—")
        self.gpu_val = self._make_val_col("GPU 占用",  "—")
        self.vram_val = self._make_val_col("显存占用",  "—")
        for col in (self.cpu_val, self.ram_val, self.gpu_val, self.vram_val):
            val_row.addLayout(col)
        outer.addLayout(val_row)

    def _make_val_col(self, label: str, value: str):
        col = QVBoxLayout()
        col.setSpacing(1)
        col.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        lbl = CaptionLabel(label, self)
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl.setStyleSheet("color: rgba(128,128,128,200);")
        val = StrongBodyLabel(value, self)
        val.setAlignment(Qt.AlignmentFlag.AlignCenter)
        col.addWidget(lbl)
        col.addWidget(val)
        col._lbl = lbl
        col._val = val
        return col

    def update_data(self, cpu_pct, ram_pct, gpu_pct, vram_pct,
                    ram_used_str, gpu_usage_str, vram_usage_str):
        self.cpu_dial.setValue(int(cpu_pct), pct_color(cpu_pct))
        self.ram_dial.setValue(int(ram_pct), pct_color(ram_pct))
        self.gpu_dial.setValue(int(gpu_pct), pct_color(gpu_pct))
        self.vram_dial.setValue(int(vram_pct), pct_color(vram_pct))

        self.cpu_val._val.setText(f"{cpu_pct:.1f}%")
        self.ram_val._val.setText(ram_used_str)
        self.gpu_val._val.setText(gpu_usage_str)
        self.vram_val._val.setText(vram_usage_str)

# ══════════════════════════════════════════════════════════════════════
#  顶部摘要卡片
# ══════════════════════════════════════════════════════════════════════


class SummaryCard(ElevatedCardWidget):
    def __init__(self, icon, title: str, subtitle: str, parent=None):
        super().__init__(parent)
        self.setFixedHeight(120)

        root = QHBoxLayout(self)
        root.setContentsMargins(18, 14, 18, 14)
        root.setSpacing(14)

        ico = IconWidget(icon, self)
        ico.setFixedSize(32, 32)
        root.addWidget(ico, 0, Qt.AlignmentFlag.AlignVCenter)

        col = QVBoxLayout()
        col.setSpacing(3)
        self.titleLbl = SubtitleLabel(title, self)
        self.titleLbl.setFont(QFont("Segoe UI", 15, QFont.Weight.Bold))
        self.subLbl = CaptionLabel(subtitle, self)
        self.subLbl.setWordWrap(True)
        col.addWidget(self.titleLbl)
        col.addWidget(self.subLbl)
        root.addLayout(col, 1)

        self.ring = ProgressRing(self)
        self.ring.setFixedSize(48, 48)
        self.ring.setTextVisible(True)
        self.ring.setValue(0)
        self.ring.hide()
        root.addWidget(self.ring, 0, Qt.AlignmentFlag.AlignVCenter)

    def setRing(self, pct: int, color: str):
        self.ring.show()
        self.ring.setValue(pct)
        self.ring.setCustomBarColor(QColor(color), QColor(color))


# ══════════════════════════════════════════════════════════════════════
#  详情行
# ══════════════════════════════════════════════════════════════════════
class DetailRow(QWidget):
    def __init__(self, label: str, value: str, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 3, 0, 3)
        lbl = BodyLabel(label, self)
        lbl.setFixedWidth(140)
        lbl.setStyleSheet("color: rgba(128,128,128,200);")
        val = StrongBodyLabel(value, self)
        val.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        val.setWordWrap(True)
        lay.addWidget(lbl)
        lay.addWidget(val, 1)


# ══════════════════════════════════════════════════════════════════════
#  设备评分卡
# ══════════════════════════════════════════════════════════════════════

class DeviceRatingCard(ElevatedCardWidget):
    """设备性能评分卡 - 评分标准由你实现"""

    def __init__(self, parent=None):
        super().__init__(parent)

        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Fixed)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(20, 16, 20, 16)
        main_layout.setSpacing(12)

        # 标题行
        title_row = QHBoxLayout()
        title_icon = IconWidget(FIF.CERTIFICATE, self)
        title_icon.setFixedSize(20, 20)
        title_label = SubtitleLabel("设备性能评估", self)
        title_row.addWidget(title_icon)
        title_row.addWidget(title_label)
        title_row.addStretch()
        self.help_icon = IconWidget(FIF.HELP, self)
        self.help_icon.setFixedSize(18, 18)

        self.help_icon.setToolTip(
            "分数仅供参考 基于CPU多核跑分 显存容量 内存容量等指标综合评估得出\n实际运行流畅度收多维影响\n显存 内存容量决定模型是否能运行 不决定运行流畅度")
        self.help_icon.installEventFilter(ToolTipFilter(self.help_icon, 500))
        title_row.addWidget(self.help_icon)

        # 总分标签
        self.total_score_label = StrongBodyLabel("待评估", self)
        self.total_score_label.setStyleSheet(
            "font-size: 24px; font-weight: bold;")
        title_row.addWidget(self.total_score_label)
        main_layout.addLayout(title_row)

        # 分隔线
        line = QFrame(self)
        line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet("background-color: rgba(128,128,128,50);")
        main_layout.addWidget(line)

        # 评分项网格
        self.score_grid = QGridLayout()
        self.score_grid.setSpacing(12)
        self.score_grid.setColumnStretch(1, 1)

        # 预定义评分项（状态文本和颜色留给你填充）
        self.score_items = {
            "cpu": {"label": "CPU 性能", "status": "—", "color": "#999", "value": 0},
            "vram": {"label": "显存容量", "status": "—", "color": "#999", "value": 0},
            "ram": {"label": "系统内存", "status": "—", "color": "#999", "value": 0},
            "disk": {"label": "磁盘空间", "status": "—", "color": "#999", "value": 0},
        }

        row = 0
        for key, item in self.score_items.items():
            # 标签
            label = BodyLabel(item["label"], self)
            label.setFixedWidth(90)
            label.setStyleSheet("color: rgba(128,128,128,200);")
            self.score_grid.addWidget(label, row, 0)

            # 状态（进度条或文本）
            status_label = BodyLabel(item["status"], self)
            status_label.setStyleSheet(f"color: {item['color']};")
            self.score_grid.addWidget(status_label, row, 1)

            # 存储引用以便更新
            self.score_items[key]["widget"] = status_label

            row += 1

        main_layout.addLayout(self.score_grid)

    def update_rating(self, info: dict):
        # ========== 1. CPU 评分 ==========
        cpu_multi_score = info.get('cpu_multi_score', 0)
        if cpu_multi_score == '—' or cpu_multi_score == 0 or cpu_multi_score == "0":
            cpu_status, cpu_color, cpu_tip = "未知", "#999", "无法获取CPU性能评分"
            cpu_score = 0
        elif cpu_multi_score > 5000:
            cpu_status, cpu_color, cpu_tip = "优秀", "#0DB37E", "适合运行大型模型和复杂计算任务"
            cpu_score = 100
        elif 3000 < cpu_multi_score <= 5000:
            cpu_status, cpu_color, cpu_tip = "良好", "#0DB37E", "适合运行中等模型"
            cpu_score = 75
        elif 2000 < cpu_multi_score <= 3000:
            cpu_status, cpu_color, cpu_tip = "及格", "#F7B731", "适合运行小型模型和轻量级任务"
            cpu_score = 50
        else:
            cpu_status, cpu_color, cpu_tip = "较弱", "#FC5C65", "性能较弱，可能无法流畅运行大模型任务"
            cpu_score = 25

        # ========== 2. 显存评分 ==========
        vram_str = info.get('gpu_vram', '—')
        vram_gb = 0
        try:
            if 'GB' in vram_str:
                vram_gb = float(vram_str.replace('GB', '').strip())
        except:
            pass

        if vram_gb > 8:
            vram_status, vram_color, vram_tip = "充裕", "#0DB37E", "适合轻量大语言模型及大部分AI工具"
            vram_score = 100
        elif vram_gb == 8:
            vram_status, vram_color, vram_tip = "良好", "#0DB37E", "适合1024*1024绘图、20B以内大模型"
            vram_score = 85
        elif 4 < vram_gb < 8:
            vram_status, vram_color, vram_tip = "紧张", "#F7B731", "勉强跑1024*1024绘图，不推荐大模型"
            vram_score = 50
        elif 0 < vram_gb <= 4:
            vram_status, vram_color, vram_tip = "不足", "#FC5C65", "仅适合轻量级AI工具"
            vram_score = 25
        else:
            vram_status, vram_color, vram_tip = "未知", "#999", "无法获取显存容量"
            vram_score = 0

        # ========== 3. 内存评分 ==========
        ram_bytes = info.get('ram_total', 0)
        ram_gb = ram_bytes / (1024**3)
        ram_str = f"{ram_gb:.0f} GB"

        if ram_gb >= 64:
            ram_status, ram_color, ram_tip = "充裕", "#0DB37E", "可同时运行多个大模型"
            ram_score = 100
        elif ram_gb >= 32:
            ram_status, ram_color, ram_tip = "良好", "#0DB37E", "适合大模型推理"
            ram_score = 85
        elif ram_gb >= 16:
            ram_status, ram_color, ram_tip = "够用", "#F7B731", "适合中等模型"
            ram_score = 70
        elif ram_gb >= 8:
            ram_status, ram_color, ram_tip = "紧张", "#FC5C65", "仅适合小模型"
            ram_score = 50
        elif ram_gb > 0:
            ram_status, ram_color, ram_tip = "不足", "#FC5C65", "建议升级内存"
            ram_score = 25
        else:
            ram_status, ram_color, ram_tip = "未知", "#999", "无法获取内存容量"
            ram_score = 0

        # ========== 4. 磁盘评分 ==========
        disk_bytes = info.get('disk_total', 0)
        disk_gb = disk_bytes / (1024**3)
        disk_str = f"{disk_gb:.0f} GB"

        if disk_gb >= 500:
            disk_status, disk_color, disk_tip = "充足", "#0DB37E", "可存储多个服务"
            disk_score = 100
        elif disk_gb >= 100:
            disk_status, disk_color, disk_tip = "够用", "#F7B731", "可存储少量服务"
            disk_score = 70
        elif disk_gb >= 50:
            disk_status, disk_color, disk_tip = "紧张", "#FC5C65", "勉强够用"
            disk_score = 50
        elif disk_gb > 0:
            disk_status, disk_color, disk_tip = "不足", "#FC5C65", "空间不足，可能无法安装或更新服务，建议预留30G以上"
            disk_score = 25
        else:
            disk_status, disk_color, disk_tip = "未知", "#999", "无法获取磁盘容量"
            disk_score = 0

        # ========== 5. 更新 UI 显示 ==========
        # CPU 显示
        self.score_items["cpu"]["widget"].setText(
            f"{cpu_status}\n(单核: {info.get('cpu_single_score', '—')} | 多核: {cpu_multi_score})\n{cpu_tip}"
        )
        self.score_items["cpu"]["widget"].setStyleSheet(f"color: {cpu_color};")

        # 显存显示
        self.score_items["vram"]["widget"].setText(
            f"{vram_status}\n({vram_str})\n{vram_tip}"
        )
        self.score_items["vram"]["widget"].setStyleSheet(
            f"color: {vram_color};")

        # 内存显示
        self.score_items["ram"]["widget"].setText(
            f"{ram_status}\n({ram_str})\n{ram_tip}"
        )
        self.score_items["ram"]["widget"].setStyleSheet(f"color: {ram_color};")

        # 磁盘显示
        self.score_items["disk"]["widget"].setText(
            f"{disk_status}\n({disk_str})\n{disk_tip}"
        )
        self.score_items["disk"]["widget"].setStyleSheet(
            f"color: {disk_color};")

        # ========== 6. 计算总分（权重） ==========
        # 权重分配（你可以调整）
        weights = {
            "cpu": 0.20,
            "vram": 0.45,
            "ram": 0.25,
            "disk": 0.10,
        }

        total_score = int(
            cpu_score * weights["cpu"] +
            vram_score * weights["vram"] +
            ram_score * weights["ram"] +
            disk_score * weights["disk"]
        )

        # 总分评级
        if total_score >= 85:
            total_status = "旗舰"
            total_color = "#FFD700"
        elif total_score >= 70:
            total_status = "高性能"
            total_color = "#0DB37E"
        elif total_score >= 50:
            total_status = "合格"
            total_color = "#F7B731"
        elif total_score >= 25:
            total_status = "入门级"
            total_color = "#FC5C65"
        else:
            total_status = "未知"
            total_color = "#999"

        self.total_score_label.setText(f"{total_status} {total_score}分")
        self.total_score_label.setStyleSheet(
            f"font-size: 24px; font-weight: bold; color: {total_color};")
# ══════════════════════════════════════════════════════════════════════
#  主页面 Widget
# ══════════════════════════════════════════════════════════════════════


class SystemInfoPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("SystemInfoPage")
        self._info: dict = {}
        self._cards_built = False

        # ── 外层滚动 ──
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        scroll = SmoothScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("""
            SmoothScrollArea { background: transparent; border: none; }
            QWidget#container { background: transparent; }
        """)
        outer.addWidget(scroll)

        self._container = QWidget()
        self._container.setObjectName("container")
        scroll.setWidget(self._container)

        self.mainLay = QVBoxLayout(self._container)
        self.mainLay.setContentsMargins(36, 28, 36, 36)
        self.mainLay.setSpacing(16)

        # 标题
        hdr = QHBoxLayout()
        title = TitleLabel("系统信息", self)
        hdr.addWidget(title)
        hdr.addStretch()
        self.refreshBtn = PrimaryPushButton(FIF.SYNC, " 立即刷新", self)
        self.refreshBtn.clicked.connect(self._manual_refresh)
        hdr.addWidget(self.refreshBtn)
        self.copyBtn = TransparentPushButton(FIF.COPY, " 复制", self)
        self.copyBtn.clicked.connect(self._copyInfo)
        hdr.addWidget(self.copyBtn)
        self.mainLay.addLayout(hdr)

        # 加载动画
        spin_row = QHBoxLayout()
        self._spin = IndeterminateProgressRing(self)
        self._spin.setFixedSize(44, 44)
        spin_row.addStretch()
        spin_row.addWidget(self._spin)
        spin_row.addStretch()
        self.mainLay.addLayout(spin_row)
        self._loadLbl = SubtitleLabel("正在采集系统信息…", self)
        self._loadLbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.mainLay.addWidget(self._loadLbl)
        self.mainLay.addStretch()

        # 定时器
        self._auto_timer = QTimer(self)
        self._auto_timer.setInterval(3000)
        self._auto_timer.timeout.connect(self._start_worker)

        self._start_worker()

    # ── 线程启动 ──
    def _start_worker(self):
        if hasattr(self, '_worker') and self._worker.isRunning():
            return
        self._worker = SystemInfoWorker()
        self._worker.dataReady.connect(self._onData)
        self._worker.start()
        self.refreshBtn.setEnabled(False)

    def _manual_refresh(self):
        self._start_worker()

    # ── 数据回调 ──
    def _onData(self, info: dict):
        self._info = info
        self.refreshBtn.setEnabled(True)

        if not self._cards_built:
            self._spin.hide()
            self._loadLbl.hide()
            self._build_cards(info)
            self._update_dynamic(info)
            self._cards_built = True
            self._auto_timer.start()
        else:
            self._update_dynamic(info)

    # ────────── 构建静态卡片（只调用一次）──────────
    def _build_cards(self, d: dict):
        freq = d["cpu_freq_ghz"]

        # ─── 第一行：4 张摘要卡（等大网格） ───
        grid = QGridLayout()
        grid.setSpacing(12)
        for i in range(4):
            grid.setColumnStretch(i, 1)

        # 计算显存使用率
        vram_pct = 0
        if d.get("gpu_mem_total", 0) > 0:
            vram_pct = (d.get("gpu_mem_used", 0) /
                        d.get("gpu_mem_total", 1)) * 100

        # 四个卡片统一高度 140，内容垂直居中
        cards = [
            self._make_summary_card(FIF.DEVELOPER_TOOLS, "CPU",
                                    d["cpu_name"],
                                    f"{d['cpu_cores']}核{d['cpu_threads']}线程  {freq}",
                                    d["cpu_usage"]),
            self._make_summary_card(FIF.SAVE, "内存",
                                    f"{d['ram_total']/1024**3:.1f} GB",
                                    f"{d.get('ram_type', '无法识别到的内存类型')}  {d.get('ram_capacity_list', '未知的内存容量组合')}",
                                    d["ram_pct"]),
            self._make_summary_card(FIF.PALETTE, "GPU",
                                    d["gpu_name"],
                                    f"驱动 {d.get('gpu_driver', '—')}  |  CUDA {d.get('gpu_cuda_version', '—')}",
                                    d.get("gpu_usage", 0)),
            self._make_summary_card(FIF.VIDEO, "显存",
                                    f"{d.get('gpu_vram', '—')}",
                                    f"已用 {d.get('gpu_mem_used', 0):.0f} / {d.get('gpu_mem_total', 0):.0f} MB" if d.get(
                                        'gpu_type') == 'dedicated' else f"已用 {fmt_bytes(d.get('gpu_mem_used', 0) * 1024 * 1024)} / {fmt_bytes(d.get('gpu_mem_total', 0) * 1024 * 1024)}",
                                    vram_pct),
        ]
        self._summary_cards = cards  # 保存引用用于更新
        for i, card in enumerate(cards):
            grid.addWidget(card, 0, i)
        self.mainLay.addLayout(grid)

        # ─── 第二行：实时概览卡片 ───
        self._live_card = LiveMonitorCard(self)
        self.mainLay.addWidget(self._live_card)

        # ─── 第三行：设备评分卡 ───
        self._rating_card = DeviceRatingCard(self)
        self._rating_card.update_rating(d)  # 初始评分
        self.mainLay.addWidget(self._rating_card)

        # ─── 第四行：设备详情（两列等高） ───
        self.mainLay.addWidget(SubtitleLabel("设备详情", self))

        detail_row = QHBoxLayout()
        detail_row.setSpacing(12)

        left_card = self._make_detail_card("处理器 & 内存", [
            ("处理器", d["cpu_name"]),
            ("核心 / 线程", f"{d['cpu_cores']} / {d['cpu_threads']}"),
            ("基础频率", freq),
            ("CPU 占用", f"{d['cpu_usage']:.1f}%"),
            ("", ""),  # 空行留白
            ("内存总量", fmt_bytes(d["ram_total"])),
            ("已用内存", fmt_bytes(d["ram_used"])),
            ("可用内存", fmt_bytes(d["ram_avail"])),
        ])
        right_card = self._make_detail_card("显卡 & 系统", [
            ("GPU", d["gpu_name"]),
            ("显存总量", d["gpu_vram"] if d["gpu_vram"] !=
             "—" else f"{d.get('gpu_mem_total', 0):.0f} MB"),
            ("显存已用", f"{d.get('gpu_mem_used', 0):.0f} MB" if d.get(
                'gpu_type') == 'dedicated' else fmt_bytes(d.get('gpu_mem_used', 0) * 1024 * 1024)),
            ("GPU 使用率", f"{d.get('gpu_usage', 0):.1f}%"),
            ("显存使用率", f"{vram_pct:.1f}%"),
            ("", ""),
            ("操作系统", d["os_display"]),
            ("计算机名", d["hostname"]),
            ("系统架构", d["arch"]),
            ("开机时间", d["boot_time"]),
        ])
        detail_row.addWidget(left_card, 1)
        detail_row.addWidget(right_card, 1)
        self.mainLay.addLayout(detail_row)

        # ─── 第五行：软件环境 ───
        self.mainLay.addWidget(SubtitleLabel("软件环境", self))
        env_card = self._make_env_card(d)
        self.mainLay.addWidget(env_card)
        self.mainLay.addStretch()

        # 保存动态更新的 label 引用
        self._cpu_usage_label = left_card.findChild(StrongBodyLabel, "CPU占用")
        self._ram_used_label = left_card.findChild(StrongBodyLabel, "已用内存")
        self._ram_avail_label = left_card.findChild(StrongBodyLabel, "可用内存")
        self._gpu_usage_label = right_card.findChild(StrongBodyLabel, "GPU使用率")
        self._vram_used_label = right_card.findChild(StrongBodyLabel, "显存已用")
        self._vram_pct_label = right_card.findChild(StrongBodyLabel, "显存使用率")

    def _make_env_card(self, d: dict):
        card = ElevatedCardWidget(self)
        card.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Fixed)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.setSpacing(12)

        grid = QGridLayout()
        grid.setSpacing(8)
        grid.setColumnStretch(0, 0)
        grid.setColumnStretch(1, 0)
        grid.setColumnStretch(2, 0)
        grid.setColumnStretch(3, 0)
        grid.setColumnStretch(4, 1)  # 最后一列填充空白

        packages = d.get("pip_packages", {})
        rows_data = [
            ("Python",
             f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"),
            ("PyQt6", packages.get("pyqt6", "—")),
            ("PyTorch", packages.get("torch", "未安装")),
            ("TorchVision", packages.get("torchvision", "—")),
            ("TorchAudio", packages.get("torchaudio", "—")),
            ("TensorFlow", packages.get("tensorflow", "—")),
            ("ONNX", packages.get("onnx", "—")),
            ("ONNX Runtime", packages.get("onnxruntime", "—")),
            ("NumPy", packages.get("numpy", "—")),
            ("SciPy", packages.get("scipy", "—")),
            ("Pandas", packages.get("pandas", "—")),
        ]

        for i, (name, ver) in enumerate(rows_data):
            row = i // 3
            col = (i % 3) * 2
            lbl = CaptionLabel(name, self)
            lbl.setFixedWidth(100)
            lbl.setStyleSheet("color: rgba(128,128,128,200);")
            val = StrongBodyLabel(ver, self)
            val.setFixedWidth(100)
            grid.addWidget(lbl, row, col)
            grid.addWidget(val, row, col + 1)

        lay.addLayout(grid)
        return card

    # ────────── 摘要卡（统一高度 140）──────────
    def _make_summary_card(self, icon, title: str, main_text: str, sub_text: str, pct: float):
        card = ElevatedCardWidget(self)
        card.setFixedHeight(140)
        card.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Fixed)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(16, 12, 16, 12)
        lay.setSpacing(4)

        top = QHBoxLayout()
        ico = IconWidget(icon, self)
        ico.setFixedSize(20, 20)
        lbl = CaptionLabel(title, self)
        lbl.setStyleSheet("color: rgba(128,128,128,200);")
        top.addWidget(ico)
        top.addWidget(lbl)
        top.addStretch()

        # 进度环（右上角）
        ring = ProgressRing(self)
        ring.setFixedSize(32, 32)
        ring.setTextVisible(False)
        ring.setValue(int(pct))
        ring.setCustomBarColor(QColor(pct_color(pct)), QColor(pct_color(pct)))
        top.addWidget(ring)
        lay.addLayout(top)

        lay.addStretch()

        main_lbl = StrongBodyLabel(main_text, self)
        main_lbl.setWordWrap(True)
        lay.addWidget(main_lbl)

        sub_lbl = CaptionLabel(sub_text, self)
        sub_lbl.setWordWrap(True)
        sub_lbl.setStyleSheet("color: rgba(128,128,128,200);")
        lay.addWidget(sub_lbl)

        lay.addStretch()
        return card

    # ────────── 详情卡片（两列等高） ──────────
    def _make_detail_card(self, title: str, items: list):
        card = ElevatedCardWidget(self)
        card.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Preferred)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.setSpacing(2)
        lay.addWidget(StrongBodyLabel(title, self))
        lay.addSpacing(8)

        for label, value in items:
            if label == "" and value == "":
                lay.addSpacing(8)
                continue
            row = QHBoxLayout()
            lbl = CaptionLabel(label, self)
            lbl.setFixedWidth(100)
            lbl.setStyleSheet("color: rgba(128,128,128,200);")
            val = StrongBodyLabel(value, self)
            val.setWordWrap(True)
            # 设置 objectName 以便后续更新
            if label:
                val.setObjectName(label.replace(" ", "").replace("&", ""))
            row.addWidget(lbl)
            row.addWidget(val, 1)
            lay.addLayout(row)

        lay.addStretch()
        return card

    # ────────── 动态更新 ──────────
    def _update_dynamic(self, d: dict):
        # 计算显存使用率
        vram_pct = 0
        if d.get("gpu_mem_total", 0) > 0:
            vram_pct = (d.get("gpu_mem_used", 0) /
                        d.get("gpu_mem_total", 1)) * 100

        # 更新摘要卡进度环
        summaries = [
            (d["cpu_usage"],
             f"{d['cpu_cores']}核{d['cpu_threads']}线程  {d['cpu_freq_ghz']}"),
            (d["ram_pct"],
             f"已用 {fmt_bytes(d['ram_used'])}  /  {fmt_bytes(d['ram_avail'])} 可用"),
            (d.get("gpu_usage", 0),
             f"使用率 {d.get('gpu_usage', 0):.1f}%"),
            (vram_pct,
             f"已用 {d.get('gpu_mem_used', 0):.0f} / {d.get('gpu_mem_total', 0):.0f} MB" if d.get('gpu_type') == 'dedicated' else f"已用 {fmt_bytes(d.get('gpu_mem_used', 0) * 1024 * 1024)} / {fmt_bytes(d.get('gpu_mem_total', 0) * 1024 * 1024)}"),
        ]

        for i, (pct, sub) in enumerate(summaries):
            if i < len(self._summary_cards):
                card = self._summary_cards[i]
                ring = card.findChild(ProgressRing)
                if ring:
                    ring.setValue(int(pct))
                    ring.setCustomBarColor(
                        QColor(pct_color(pct)), QColor(pct_color(pct)))
                # 更新副标题文本
                sub_lbl = card.findChild(CaptionLabel)
                if sub_lbl:
                    sub_lbl.setText(sub)

        # 更新实时概览卡片
        if hasattr(self, '_live_card'):
            # 准备显存使用率字符串
            if d.get("gpu_type") == "dedicated":
                vram_usage_str = f"{d.get('gpu_mem_used', 0):.0f} / {d.get('gpu_mem_total', 0):.0f} MB ({vram_pct:.1f}%)"
                gpu_usage_str = f"{d.get('gpu_usage', 0):.1f}%"
            else:
                vram_usage_str = f"{fmt_bytes(d.get('gpu_mem_used', 0) * 1024 * 1024)} / {fmt_bytes(d.get('gpu_mem_total', 0) * 1024 * 1024)} ({vram_pct:.1f}%)"
                gpu_usage_str = f"{d.get('gpu_usage', 0):.1f}%"

            self._live_card.update_data(
                cpu_pct=d["cpu_usage"],
                ram_pct=d["ram_pct"],
                gpu_pct=d.get("gpu_usage", 0),
                vram_pct=vram_pct,
                ram_used_str=fmt_bytes(d["ram_used"]),
                gpu_usage_str=gpu_usage_str,
                vram_usage_str=vram_usage_str
            )

        # 更新详情数值
        if hasattr(self, '_cpu_usage_label') and self._cpu_usage_label:
            self._cpu_usage_label.setText(f"{d['cpu_usage']:.1f}%")
        if hasattr(self, '_gpu_usage_label') and self._gpu_usage_label:
            self._gpu_usage_label.setText(f"{d.get('gpu_usage', 0):.1f}%")
        if hasattr(self, '_vram_pct_label') and self._vram_pct_label:
            self._vram_pct_label.setText(f"{vram_pct:.1f}%")

        # 更新内存和显存详情
        for widget in self.findChildren(StrongBodyLabel):
            if widget.objectName() == "已用内存":
                widget.setText(fmt_bytes(d["ram_used"]))
            elif widget.objectName() == "可用内存":
                widget.setText(fmt_bytes(d["ram_avail"]))
            elif widget.objectName() == "显存已用":
                if d.get('gpu_type') == 'dedicated':
                    widget.setText(f"{d.get('gpu_mem_used', 0):.0f} MB")
                else:
                    widget.setText(
                        fmt_bytes(d.get('gpu_mem_used', 0) * 1024 * 1024))

    # ──────────────────────────────────────────────────────────────────
    def _copyInfo(self):
        if not self._info:
            return
        d = self._info
        vram_pct = 0
        if d.get("gpu_mem_total", 0) > 0:
            vram_pct = (d.get("gpu_mem_used", 0) /
                        d.get("gpu_mem_total", 1)) * 100

        text = "\n".join([
            "═══ 系统信息 ═══",
            f"计算机名:   {d.get('hostname','—')}",
            f"操作系统:   {d.get('os_display','—')}",
            f"系统架构:   {d.get('arch','—')}",
            f"处理器:     {d.get('cpu_name','—')}",
            f"核心/线程:  {d.get('cpu_cores','—')} / {d.get('cpu_threads','—')}",
            f"内存:       {fmt_bytes(d.get('ram_total',0))}",
            f"GPU:        {d.get('gpu_name','—')}",
            f"GPU使用率:  {d.get('gpu_usage', 0):.1f}%",
            f"显存:       {d.get('gpu_vram', '—')}",
            f"显存使用率: {vram_pct:.1f}%",
            f"磁盘:       {fmt_bytes(d.get('disk_total',0))}",
            f"开机时间:   {d.get('boot_time','—')}",
        ])
        QApplication.clipboard().setText(text)
        InfoBar.success(
            title="已复制", content="系统信息已复制到剪贴板",
            orient=Qt.Orientation.Horizontal, isClosable=True,
            position=InfoBarPosition.TOP_RIGHT, duration=2000, parent=self,
        )


def _detail_row_find_val(self):
    return self.layout().itemAt(1).widget()


DetailRow._find_val = _detail_row_find_val
