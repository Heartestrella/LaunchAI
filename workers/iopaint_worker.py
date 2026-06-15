"""
workers/iopaint_worker.py
~~~~~~~~~~~~~~~~~~~~~~~~~
IOPaint 图像修复后台推理 + SAM 交互式分割两条工作线程。

风格对齐 YoloWorker —— QThread + 项目统一的信号集 + patch_iopaint_page() helper。
仅支持 erase 类模型（lama/ldm/mat/fcf/manga/zits），不做 Stable Diffusion。
"""

from __future__ import annotations

import os
import time
import traceback
import types
from pathlib import Path
from typing import Optional

import numpy as np
from PyQt6.QtCore import QThread, pyqtSignal


# ── 常量 ──────────────────────────────────────────────────────────────
MODELS = ["lama", "ldm", "mat", "fcf", "manga", "zits"]
HD_STRATEGIES = ["CROP", "RESIZE", "ORIGINAL"]
SAM_MODELS = ["mobile_sam", "vit_b", "sam2_tiny", "sam2_small"]

DEFAULT_OUT_DIR = os.path.join(os.getcwd(), "results", "iopaint")


# ══════════════════════════════════════════════════════════════════════
#  IOPaintWorker —— erase 类修复
# ══════════════════════════════════════════════════════════════════════
class IOPaintWorker(QThread):
    """单图修复线程。一次只处理一张（来自当前画布）。"""

    progress = pyqtSignal(int, str)
    log_line = pyqtSignal(str)
    image_done = pyqtSignal(str, str, float)   # input, output, elapsed_ms
    file_done = pyqtSignal(str, str)
    file_error = pyqtSignal(str, str)
    finished = pyqtSignal(int, float)
    error = pyqtSignal(str)
    model_loaded = pyqtSignal(str)

    # 复用模型实例，避免重复加载 ~200MB 权重
    _mgr_cache: dict = {}

    def __init__(self, image_rgb: np.ndarray, mask_gray: np.ndarray,
                 input_path: str, params: dict):
        super().__init__()
        # 拷贝一份，确保线程间隔离 —— 画布在 UI 线程仍可继续被编辑
        self._image_rgb = np.ascontiguousarray(image_rgb)
        self._mask_gray = np.ascontiguousarray(mask_gray)
        self._input_path = input_path
        self._params = dict(params)
        self._cancelled = False

    def cancel(self):
        """请求取消。注意 mgr() 单张张量调用无法中断，
        cancel 只能阻止后续动作（当前实现里就是写盘前的一道关卡）。"""
        self._cancelled = True

    def run(self):
        try:
            self._do_inpaint()
        except Exception as e:
            self.log_line.emit(self._html(traceback.format_exc(), "#FF6B6B"))
            self.error.emit(f"修复过程中发生异常: {e}")

    # ── 主流程 ────────────────────────────────────────────────────────
    def _do_inpaint(self):
        p = self._params

        # 1) 空 mask 短路
        if not self._mask_gray.any():
            self.error.emit("掩码为空，请先在画布上涂抹要修复的区域")
            return

        # 2) 镜像 URL 必须在 import iopaint 之前注入，
        #    因为 iopaint/model/lama.py 在 import 时就锁定了 LAMA_MODEL_URL
        lama_url = p.get("lama_model_url")
        if lama_url:
            os.environ["LAMA_MODEL_URL"] = str(lama_url)

        # 3) 懒导入 —— 让页面在没装 iopaint 时仍能 import 本模块
        try:
            import torch
            import cv2
            from iopaint.model_manager import ModelManager
            from iopaint.model import models as _ERASE_MODELS
            from iopaint.schema import InpaintRequest, HDStrategy
        except ImportError as e:
            self.error.emit(f"未安装 IOPaint 或其依赖：{e}\n"
                            f"请先 `pip install iopaint`")
            return

        device_str = str(p.get("device", "cpu"))
        try:
            device = torch.device(device_str)
        except Exception:
            self.log_line.emit(self._html(
                f"⚠ 无法解析设备 {device_str!r}，回退到 CPU", "#F7B731"))
            device = torch.device("cpu")
            device_str = "cpu"

        model_name = p.get("model", "lama")
        if model_name not in MODELS:
            self.log_line.emit(self._html(
                f"⚠ 未知模型 {model_name!r}，回退到 lama", "#F7B731"))
            model_name = "lama"

        # 4) 权重不在本地时主动下载 —— 否则 IOPaint 的 scan_models 会把
        #    没下载过的模型直接从 available_models 里剔除，造成
        #    "Unsupported model: lama. Available models:['cv2']"
        model_cls = _ERASE_MODELS.get(model_name)
        if model_cls is None:
            self.error.emit(
                f"IOPaint 内部未注册模型 {model_name!r}，请检查 iopaint 版本")
            return
        try:
            already = model_cls.is_downloaded()
        except Exception:
            already = False
        if not already:
            self.progress.emit(
                5, f"首次使用：下载 {model_name} 权重（约 200MB，请耐心等待）")
            self.log_line.emit(self._html(
                f"▶ 下载模型权重 {model_name} …（仅首次需要）", "#888888"))
            try:
                model_cls.download()
            except Exception as e:
                self.log_line.emit(self._html(
                    traceback.format_exc(), "#FF6B6B"))
                self.error.emit(
                    f"{model_name} 权重下载失败：{e}\n"
                    f"可在配置 iopaint.lama_model_url 里设置镜像后重试")
                return
            self.log_line.emit(self._html(
                f"  ↳ {model_name} 权重下载完成", "#0DB37E"))

        # 5) 加载（或命中缓存）模型
        cache_key = (model_name, device_str)
        mgr = self._mgr_cache.get(cache_key)
        if mgr is None:
            self.progress.emit(
                10, f"加载 {model_name} 权重到 {device_str}")
            self.log_line.emit(self._html(
                f"▶ 加载模型 {model_name} → {device_str}", "#888888"))
            t_load = time.time()
            try:
                mgr = ModelManager(name=model_name, device=device)
            except Exception as e:
                self.error.emit(f"模型加载失败：{e}")
                return
            self._mgr_cache[cache_key] = mgr
            self.log_line.emit(self._html(
                f"  ↳ 加载完成，耗时 {time.time()-t_load:.2f}s", "#888888"))
        else:
            self.progress.emit(15, f"复用已加载模型 {model_name}")

        self.model_loaded.emit(model_name)

        if self._cancelled:
            return

        # 6) 构造 InpaintRequest
        try:
            hd_strategy = HDStrategy[p.get("hd_strategy", "CROP")]
        except KeyError:
            hd_strategy = HDStrategy.CROP

        try:
            cfg = InpaintRequest(
                hd_strategy=hd_strategy,
                hd_strategy_crop_trigger_size=int(
                    p.get("hd_strategy_crop_trigger_size", 800)),
                hd_strategy_crop_margin=int(
                    p.get("hd_strategy_crop_margin", 128)),
                hd_strategy_resize_limit=int(
                    p.get("hd_strategy_resize_limit", 1280)),
                ldm_steps=int(p.get("ldm_steps", 20)),
            )
        except Exception as e:
            self.error.emit(f"参数无效：{e}")
            return

        # 7) 推理
        self.progress.emit(50, "推理中…")
        t0 = time.time()
        try:
            # iopaint 内部消费 RGB uint8 图像 + 单通道 uint8 mask（>0 即修复）
            result_bgr = mgr(self._image_rgb, self._mask_gray, cfg)
        except Exception as e:
            self.log_line.emit(self._html(traceback.format_exc(), "#FF6B6B"))
            self.error.emit(f"推理失败：{e}")
            return
        elapsed_ms = (time.time() - t0) * 1000.0

        if self._cancelled:
            self.log_line.emit(self._html("⚠ 已取消，丢弃结果", "#F7B731"))
            return

        # 8) 写盘
        out_dir = p.get("out_dir") or DEFAULT_OUT_DIR
        os.makedirs(out_dir, exist_ok=True)
        out_format = str(p.get("out_format", "png")).lower().lstrip(".")
        if out_format not in ("png", "jpg", "jpeg", "webp"):
            out_format = "png"

        stem = Path(self._input_path).stem if self._input_path else "canvas"
        out_path = os.path.join(out_dir, f"{stem}_inpaint.{out_format}")
        # 避免覆盖：若已存在则加时间戳
        if os.path.exists(out_path):
            ts = int(time.time())
            out_path = os.path.join(
                out_dir, f"{stem}_inpaint_{ts}.{out_format}")

        self.progress.emit(90, "写出…")
        try:
            # cv2.imwrite 期望 BGR
            ok = cv2.imwrite(out_path, result_bgr)
            if not ok:
                raise RuntimeError("cv2.imwrite 返回 False")
        except Exception as e:
            self.error.emit(f"输出失败：{e}")
            return

        self.log_line.emit(self._html(
            f"✔ 修复完成 → {out_path}  ({elapsed_ms:.0f} ms)", "#0DB37E"))

        self.image_done.emit(self._input_path or "", out_path, elapsed_ms)
        self.file_done.emit(self._input_path or "", out_path)
        self.progress.emit(100, "完成")
        self.finished.emit(1, elapsed_ms / 1000.0)

    # ── HTML 工具 ────────────────────────────────────────────────────
    @staticmethod
    def _html(text: str, color: str = "", bold: bool = False) -> str:
        if not color and not bold:
            return text
        styles = []
        if color:
            styles.append(f"color:{color}")
        if bold:
            styles.append("font-weight:bold")
        return f'<span style="{";".join(styles)}">{text}</span>'


# ══════════════════════════════════════════════════════════════════════
#  InteractiveSegWorker —— SAM 点击抠图
# ══════════════════════════════════════════════════════════════════════
class InteractiveSegWorker(QThread):
    """对应"魔棒"工具：把一组 (x, y, label) 点击转成单通道 uint8 蒙版。"""

    mask_ready = pyqtSignal(object)   # np.ndarray uint8 H×W
    error = pyqtSignal(str)
    log_line = pyqtSignal(str)

    _seg_cache: dict = {}

    def __init__(self, image_rgb: np.ndarray,
                 clicks: list,           # [(x, y, label), ...] label 1=正点 0=负点
                 sam_model: str = "mobile_sam",
                 device: str = "cpu"):
        super().__init__()
        self._image_rgb = np.ascontiguousarray(image_rgb)
        self._clicks = list(clicks)
        self._sam_model = sam_model
        self._device_str = device

    def run(self):
        try:
            self._do_seg()
        except Exception as e:
            self.log_line.emit(IOPaintWorker._html(
                traceback.format_exc(), "#FF6B6B"))
            self.error.emit(f"分割失败：{e}")

    def _do_seg(self):
        if not self._clicks:
            self.error.emit("未提供任何点击点")
            return

        try:
            import torch
        except ImportError:
            self.error.emit("未安装 torch")
            return

        # IOPaint 不同小版本 InteractiveSeg 的位置略有差异，先按已知路径试
        InteractiveSeg = None
        last_err: Optional[Exception] = None
        for path in (
            "iopaint.plugins.interactive_seg",
            "iopaint.plugins.interactive_seg_plugin",
            "iopaint.plugins",
        ):
            try:
                mod = __import__(path, fromlist=["InteractiveSeg"])
                InteractiveSeg = getattr(mod, "InteractiveSeg", None)
                if InteractiveSeg is not None:
                    break
            except Exception as e:
                last_err = e
        if InteractiveSeg is None:
            self.error.emit(
                f"未找到 InteractiveSeg 插件（IOPaint 版本不兼容）：{last_err}")
            return

        try:
            device = torch.device(self._device_str)
        except Exception:
            device = torch.device("cpu")

        key = (self._sam_model, str(device))
        seg = self._seg_cache.get(key)
        if seg is None:
            self.log_line.emit(IOPaintWorker._html(
                f"▶ 加载 SAM 模型 {self._sam_model}（首次约 40MB-400MB）",
                "#888888"))
            t_load = time.time()
            try:
                # 多数版本签名是 InteractiveSeg(name, device)；
                # 旧版只接受 (device)，做兼容
                try:
                    seg = InteractiveSeg(self._sam_model, device)
                except TypeError:
                    seg = InteractiveSeg(device)
            except Exception as e:
                self.error.emit(f"SAM 模型加载失败：{e}")
                return
            self._seg_cache[key] = seg
            self.log_line.emit(IOPaintWorker._html(
                f"  ↳ SAM 加载完成，耗时 {time.time()-t_load:.2f}s", "#888888"))

        # 形如 [[x, y, label], ...]，label 1=正点 0=负点
        clicks_arr = [[int(x), int(y), int(lab)]
                      for (x, y, lab) in self._clicks]

        # InteractiveSeg 不是 callable。新版暴露 forward(rgb, clicks, img_md5)
        # 和 gen_mask(rgb, RunPluginRequest)，旧版可能仍可 __call__。
        # img_md5 用于 set_image 缓存：同一张图重复点击时不重新编码。
        import hashlib
        img_md5 = hashlib.md5(self._image_rgb.tobytes()).hexdigest()

        t0 = time.time()
        try:
            if hasattr(seg, "forward") and callable(getattr(seg, "forward")):
                mask = seg.forward(self._image_rgb, clicks_arr, img_md5)
            elif callable(seg):
                mask = seg(self._image_rgb, clicks_arr)
            else:
                raise RuntimeError(
                    "InteractiveSeg 既不可调用也无 forward 方法，IOPaint 版本不兼容")
        except Exception as e:
            self.log_line.emit(IOPaintWorker._html(
                traceback.format_exc(), "#FF6B6B"))
            self.error.emit(f"分割推理失败：{e}")
            return

        # 兜底类型 / 形状归一
        if not isinstance(mask, np.ndarray):
            self.error.emit(f"分割结果类型异常：{type(mask).__name__}")
            return
        if mask.ndim == 3:
            mask = mask[..., 0]
        mask = (mask > 0).astype(np.uint8) * 255

        self.log_line.emit(IOPaintWorker._html(
            f"✔ 分割完成，{int((mask > 0).sum())} 像素，"
            f"耗时 {(time.time()-t0)*1000:.0f} ms", "#0DB37E"))
        self.mask_ready.emit(mask)


# ══════════════════════════════════════════════════════════════════════
#  patch_iopaint_page —— 把 IOPaintPage 的 _start/_abort/_run_seg
#                        替换为真实实现
# ══════════════════════════════════════════════════════════════════════
def patch_iopaint_page(page):
    """
    给 IOPaintPage 后绑真实的推理控制方法。
    用法：在 IOPaintPage.__init__ 末尾调用 patch_iopaint_page(self)。

    要求 page 提供下列属性/方法（由 subpage_iopaint.py 实现）：
      - self._canvas : MaskCanvas 实例，提供
            get_image_rgb()/get_mask_gray()/has_mask()/set_after_image(QPixmap)
      - self._params : IOPaintParamPanel 实例，提供 get_params()/setEnabled(bool)
      - self._run_btn / self._toolpalette / self._current_path
      - 进度回调 _on_progress / _on_image_done / _on_error / _on_finished
      - 日志回调 _append_log(str)
      - InfoBar 上下文 self.window()
    """
    from PyQt6.QtGui import QPixmap
    from qfluentwidgets import (
        InfoBar, InfoBarPosition, FluentIcon as FIF
    )

    def _start(self):
        if self._running:
            return

        # 校验
        img = self._canvas.get_image_rgb()
        if img is None:
            InfoBar.warning(
                title="未加载图像", content="请先拖入或选择一张图像",
                parent=self.window(),
                position=InfoBarPosition.TOP_RIGHT, duration=3000)
            return
        if not self._canvas.has_mask():
            InfoBar.warning(
                title="掩码为空", content="请用画笔/矩形/魔棒在图上标出要修复的区域",
                parent=self.window(),
                position=InfoBarPosition.TOP_RIGHT, duration=3000)
            return

        mask = self._canvas.get_mask_gray()
        params = self._params.get_params()

        self._running = True
        self._run_btn.setText("停止修复")
        self._run_btn.setIcon(FIF.PAUSE)
        self._params.setEnabled(False)
        if hasattr(self, "_toolpalette"):
            self._toolpalette.setEnabled(False)

        self._worker = IOPaintWorker(
            img, mask, self._current_path or "", params)

        self._worker.progress.connect(self._on_progress)
        self._worker.log_line.connect(self._append_log)
        self._worker.error.connect(self._on_error)
        self._worker.finished.connect(self._on_finished)

        def _on_image_done(inp, out, ms):
            pix = QPixmap(out)
            if not pix.isNull():
                self._canvas.set_after_image(pix)
                if hasattr(self, "_toolpalette"):
                    self._toolpalette.enable_compare(True)
            self._last_output = out
            try:
                self._on_image_done(inp, out, ms)
            except Exception:
                pass
        self._worker.image_done.connect(_on_image_done)

        self._append_log(
            f"▶ 开始修复 · 模型={params.get('model')} 设备={params.get('device')} "
            f"策略={params.get('hd_strategy')}")
        self._worker.start()

    def _abort(self):
        w = getattr(self, "_worker", None)
        if w is not None and w.isRunning():
            w.cancel()
            self._append_log("⚠ 已请求取消（注意：当前张推理无法立即中断）")
        self._running = False
        self._run_btn.setText("开始修复")
        self._run_btn.setIcon(FIF.PLAY)
        self._params.setEnabled(True)
        if hasattr(self, "_toolpalette"):
            self._toolpalette.setEnabled(True)

    def _run_seg(self, clicks: list):
        if not clicks:
            return
        params = self._params.get_params()
        device = params.get("device", "cpu")
        sam_model = params.get("sam_model", "mobile_sam")

        # 首次使用提示
        if not getattr(self, "_seg_first_hint_shown", False):
            InfoBar.info(
                title="SAM 模型",
                content="首次使用需下载 SAM 模型权重（mobile_sam ≈ 40MB）",
                parent=self.window(),
                position=InfoBarPosition.TOP_RIGHT, duration=5000)
            self._seg_first_hint_shown = True

        img = self._canvas.get_image_rgb()
        if img is None:
            return

        self._seg_worker = InteractiveSegWorker(
            img, clicks, sam_model=sam_model, device=device)
        self._seg_worker.log_line.connect(self._append_log)

        def _on_mask(mask: np.ndarray):
            self._canvas.merge_segmentation_mask(mask)

        def _on_err(msg: str):
            InfoBar.error(
                title="分割失败", content=msg,
                parent=self.window(),
                position=InfoBarPosition.TOP_RIGHT, duration=5000)

        self._seg_worker.mask_ready.connect(_on_mask)
        self._seg_worker.error.connect(_on_err)
        self._append_log("▶ 提交魔棒点击进行分割…")
        self._seg_worker.start()

    page._start = types.MethodType(_start, page)
    page._abort = types.MethodType(_abort, page)
    page._run_seg = types.MethodType(_run_seg, page)
    return page
