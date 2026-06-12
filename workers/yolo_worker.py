"""
yolo_worker.py
~~~~~~~~~~~~~~
ultralytics YOLO 后台推理工作线程。
代码风格对齐 RealESRGANWorker —— QThread + 信号驱动，UI 解耦。

完整能力：
  - 加载 .pt 权重，逐张推理
  - 进度 / 单张结果 / 错误 / HTML 日志四类信号
  - 三种保存模式：图片 / 图片+YOLO TXT / 图片+COCO JSON
  - 类别过滤、置信度/IoU 阈值、半精度、TTA、agnostic NMS
  - 加载模型后自动发出 model_loaded(class_names) 用于 UI 同步

参数说明 (params dict)
---------------------
weights     : str   权重路径或名称（如 "yolov8n.pt"，会尝试自动下载）
imgsz       : int   输入尺寸（默认 640）
conf        : float 置信度阈值 0-1（默认 0.25）
iou         : float NMS IoU 阈值 0-1（默认 0.45）
max_det     : int   最大检测数（默认 300）
device      : str   "0"/"1"/"-1"(CPU)/"auto"（默认 "auto"）
fp16        : bool  半精度推理（默认 True）
tta         : bool  TTA 测试时增强（默认 False）
agnostic    : bool  类别无关 NMS（默认 False）
classes     : list  类别 id 过滤，None=全部
out_dir     : str   输出目录（默认 "./runs/detect"）
save_mode   : str   "不保存" / "图片+TXT(YOLO)" / "图片+JSON(COCO)" / "仅图片"
draw_boxes  : bool  绘制检测框（默认 True）
draw_label  : bool  绘制类别标签（默认 True）
line_w      : int   线宽（默认 2）
"""

from __future__ import annotations

import os
import re
import sys
import time
import json
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QThread, pyqtSignal


# ── 内置权重列表（仅用于 UI 默认下拉项；用户也可手动指定路径）──────────
YOLO_MODELS = {
    "yolov8n.pt":  "YOLOv8 Nano · 最快",
    "yolov8s.pt":  "YOLOv8 Small · 平衡",
    "yolov8m.pt":  "YOLOv8 Medium · 推荐",
    "yolov8l.pt":  "YOLOv8 Large · 高精度",
    "yolov8x.pt":  "YOLOv8 X-Large · 极致",
    "yolo11n.pt":  "YOLOv11 Nano",
    "yolo11s.pt":  "YOLOv11 Small",
    "yolo11m.pt":  "YOLOv11 Medium",
    "yolo11l.pt":  "YOLOv11 Large",
    "yolo11x.pt":  "YOLOv11 X-Large",
}

DEFAULT_WEIGHTS_DIR = os.path.join(os.getcwd(), "resource", "yolo", "weights")


# ══════════════════════════════════════════════════════════════════════
#  YoloWorker  —  批量推理线程
# ══════════════════════════════════════════════════════════════════════
class YoloWorker(QThread):
    """
    YOLO 批量推理线程。一次加载模型，逐张推理并实时回报。

    与 YoloInferencePage 的信号契约保持一致：
        progress(int, str)
        image_done(str, list, float)
        finished(int, float)
        error(str)
        log_line(str)
    额外提供：
        file_done(input, output)        — 单张完成（含落盘路径）
        file_error(input, msg)          — 单张失败
        model_loaded(class_names: list) — 模型加载完成，可用于 UI 同步
    """

    progress = pyqtSignal(int, str)
    image_done = pyqtSignal(str, list, float)
    finished = pyqtSignal(int, float)
    error = pyqtSignal(str)
    log_line = pyqtSignal(str)

    file_done = pyqtSignal(str, str)
    file_error = pyqtSignal(str, str)

    model_loaded = pyqtSignal(list)

    def __init__(self, files: list[str], params: dict):
        super().__init__()
        self._files = list(files)
        self._params = dict(params)
        self._cancelled = False
        self._model = None
        self._names: list[str] = []

    # ── 公共接口 ──────────────────────────────────────────────────────
    def cancel(self):
        """请求取消。已加载的批次会在下一张图片前停止。"""
        self._cancelled = True

    # ── 主流程 ────────────────────────────────────────────────────────
    def run(self):
        try:
            self._run_inference()
        except Exception as e:
            import traceback
            self.log_line.emit(self._html(traceback.format_exc(), "#FF6B6B"))
            self.error.emit(f"推理过程中发生异常: {e}")

    def _run_inference(self):
        p = self._params

        # ── 参数解析 ─────────────────────────────────────────────────
        weights = p.get("weights",  "yolov8n.pt")
        imgsz = int(p.get("imgsz",     640))
        conf = float(p.get("conf",   0.25))
        iou = float(p.get("iou",    0.45))
        max_det = int(p.get("max_det",   300))
        device = str(p.get("device",  "auto"))
        fp16 = bool(p.get("fp16",     True))
        tta = bool(p.get("tta",     False))
        agnostic = bool(p.get("agnostic", False))
        classes = p.get("classes",      None)
        out_dir = p.get("out_dir",      "./runs/detect")
        save_mode = p.get("save_mode",    "图片+TXT(YOLO)")
        draw_boxes = bool(p.get("draw_boxes", True))
        draw_label = bool(p.get("draw_label", True))
        line_w = int(p.get("line_w", 2))

        # ── 校验 ─────────────────────────────────────────────────────
        if not self._files:
            self.error.emit("未指定输入文件")
            return

        try:
            from ultralytics import YOLO
        except ImportError:
            self.error.emit("未安装 ultralytics，请先 `pip install ultralytics`")
            return

        # ── 设备字符串规范化 ────────────────────────────────────────
        # ultralytics 的 device 接受："cpu" / "0" / "0,1" / "cuda:0"
        if device in ("auto", ""):
            ul_device = None              # 让 ultralytics 自己挑
        elif device == "-1":
            ul_device = "cpu"
        else:
            ul_device = device            # 直接用 "0" / "0,1"

        # ── 加载模型 ─────────────────────────────────────────────────
        self.progress.emit(0, "加载模型…")
        self.log_line.emit(self._html(f"▶ 加载权重: {weights}", "#888888"))
        t_load = time.time()

        try:
            self._model = YOLO(weights)
        except Exception as e:
            self.error.emit(f"模型加载失败: {e}")
            return

        # 类别名（dict {id: name} 或 list）
        names = self._model.names
        if isinstance(names, dict):
            self._names = [names[i] for i in sorted(names.keys())]
        else:
            self._names = list(names)
        self.model_loaded.emit(self._names)

        self.log_line.emit(self._html(
            f"  ↳ 加载完成，{len(self._names)} 类，耗时 {time.time()-t_load:.2f}s",
            "#888888"))

        # ── 输出目录 ─────────────────────────────────────────────────
        save_anything = save_mode != "不保存"
        if save_anything:
            os.makedirs(out_dir, exist_ok=True)
            self.log_line.emit(self._html(
                f"▶ 输出目录: {os.path.abspath(out_dir)}", "#888888"))

        self.log_line.emit(self._html("─" * 50, "#333333"))
        self.log_line.emit(self._html(
            f"▶ 开始推理：{len(self._files)} 张  "
            f"imgsz={imgsz} conf={conf:.2f} iou={iou:.2f} "
            f"device={ul_device or 'auto'} fp16={fp16}",
            "#0078D4", bold=True))

        # ── 逐张推理 ─────────────────────────────────────────────────
        total = len(self._files)
        success = 0
        t_total = time.time()

        for idx, fpath in enumerate(self._files):
            if self._cancelled:
                self.log_line.emit(self._html(
                    "⚠ 用户取消推理", "#FF9800", bold=True))
                break

            name = Path(fpath).name
            base_pct = int(idx / total * 100)
            self.progress.emit(base_pct, name)

            try:
                t0 = time.time()
                results = self._model.predict(
                    source=fpath,
                    imgsz=imgsz,
                    conf=conf,
                    iou=iou,
                    max_det=max_det,
                    device=ul_device,
                    half=fp16,
                    augment=tta,
                    agnostic_nms=agnostic,
                    classes=classes,
                    verbose=False,
                )
                infer_ms = (time.time() - t0) * 1000.0

                if not results:
                    raise RuntimeError("ultralytics 返回空结果")

                r = results[0]
                detections = self._extract_detections(r)

                # 保存
                out_path = ""
                if save_anything:
                    out_path = self._save_outputs(
                        r, fpath, out_dir, save_mode,
                        draw_boxes, draw_label, line_w)

                # 单张结果
                self.image_done.emit(fpath, detections, infer_ms)
                if out_path:
                    self.file_done.emit(fpath, out_path)
                else:
                    self.file_done.emit(fpath, "")

                self.log_line.emit(self._html(
                    f"✔ [{idx+1}/{total}] {name} → {len(detections)} 框  "
                    f"{infer_ms:.1f}ms",
                    "#4CAF50"))
                success += 1

            except Exception as e:
                msg = f"{type(e).__name__}: {e}"
                self.file_error.emit(fpath, msg)
                self.log_line.emit(self._html(
                    f"✘ [{idx+1}/{total}] {name} 失败: {msg}", "#F44336"))

            # 推理后立即更新进度（按完成数）
            done_pct = int((idx + 1) / total * 100)
            done_pct = min(99, done_pct) if idx + 1 < total else done_pct
            self.progress.emit(done_pct, name)

        # ── 收尾 ─────────────────────────────────────────────────────
        elapsed = time.time() - t_total
        if not self._cancelled:
            self.progress.emit(100, "全部完成")
        self.log_line.emit(self._html(
            f"[完成] 成功 {success}/{total} 张，总耗时 {elapsed:.1f}s",
            "#4CAF50", bold=True))
        self.finished.emit(success, elapsed)

    # ── 结果解析 ──────────────────────────────────────────────────────
    def _extract_detections(self, result) -> list[dict]:
        """将 ultralytics Results 转为页面期望的 dict 列表。"""
        dets: list[dict] = []
        boxes = getattr(result, "boxes", None)
        if boxes is None or len(boxes) == 0:
            return dets

        try:
            xyxy = boxes.xyxy.cpu().numpy()
            cls = boxes.cls.cpu().numpy().astype(int)
            conf = boxes.conf.cpu().numpy()
        except Exception:
            return dets

        for (x1, y1, x2, y2), c, p in zip(xyxy, cls, conf):
            cid = int(c)
            cname = self._names[cid] if 0 <= cid < len(
                self._names) else str(cid)
            dets.append({
                "class_id":   cid,
                "class_name": cname,
                "conf":       float(p),
                "bbox":       [float(x1), float(y1), float(x2), float(y2)],
            })
        return dets

    # ── 保存 ──────────────────────────────────────────────────────────
    def _save_outputs(self, result, src_path: str, out_dir: str,
                      save_mode: str, draw_boxes: bool,
                      draw_label: bool, line_w: int) -> str:
        """根据 save_mode 落盘图片 + 标注。返回主输出路径（标注图）。"""
        stem = Path(src_path).stem
        img_out = os.path.join(out_dir, f"{stem}_det.jpg")

        # 标注图（仅在需要时生成）
        if save_mode in ("图片+TXT(YOLO)", "图片+JSON(COCO)", "仅图片"):
            try:
                annotated = result.plot(
                    line_width=line_w,
                    labels=draw_label,
                    boxes=draw_boxes,
                )    # numpy BGR
                self._save_image(annotated, img_out)
            except Exception as e:
                self.log_line.emit(self._html(
                    f"  ↳ 标注图保存失败: {e}", "#F44336"))
                img_out = ""

        # 标注文件
        if save_mode == "图片+TXT(YOLO)":
            self._save_yolo_txt(result, os.path.join(out_dir, f"{stem}.txt"))
        elif save_mode == "图片+JSON(COCO)":
            self._save_coco_json(
                result, src_path, os.path.join(out_dir, f"{stem}.json"))

        return img_out

    @staticmethod
    def _save_image(arr_bgr, path: str):
        """优先 cv2，回退 PIL。"""
        try:
            import cv2
            cv2.imwrite(path, arr_bgr)
            return
        except Exception:
            pass
        try:
            from PIL import Image
            import numpy as np
            rgb = arr_bgr[:, :, ::-1].copy()
            Image.fromarray(rgb).save(path)
        except Exception as e:
            raise RuntimeError(f"无法写出图片: {e}")

    def _save_yolo_txt(self, result, path: str):
        """YOLO 格式: class cx cy w h（归一化）。"""
        try:
            h, w = result.orig_shape
            boxes = result.boxes
            if boxes is None or len(boxes) == 0:
                Path(path).write_text("", encoding="utf-8")
                return

            xyxy = boxes.xyxy.cpu().numpy()
            cls = boxes.cls.cpu().numpy().astype(int)
            conf = boxes.conf.cpu().numpy()

            lines = []
            for (x1, y1, x2, y2), c, p in zip(xyxy, cls, conf):
                cx = ((x1 + x2) / 2) / w
                cy = ((y1 + y2) / 2) / h
                bw = (x2 - x1) / w
                bh = (y2 - y1) / h
                lines.append(f"{int(c)} {cx:.6f} {cy:.6f} "
                             f"{bw:.6f} {bh:.6f} {float(p):.4f}")
            Path(path).write_text("\n".join(lines), encoding="utf-8")
        except Exception as e:
            self.log_line.emit(self._html(
                f"  ↳ TXT 保存失败: {e}", "#F44336"))

    def _save_coco_json(self, result, src_path: str, path: str):
        """COCO 风格的单文件 JSON（便于查看 / 后续转换）。"""
        try:
            h, w = result.orig_shape
            boxes = result.boxes
            dets = []
            if boxes is not None and len(boxes) > 0:
                xyxy = boxes.xyxy.cpu().numpy()
                cls = boxes.cls.cpu().numpy().astype(int)
                conf = boxes.conf.cpu().numpy()
                for (x1, y1, x2, y2), c, p in zip(xyxy, cls, conf):
                    cid = int(c)
                    dets.append({
                        "category_id":   cid,
                        "category_name": self._names[cid]
                        if 0 <= cid < len(self._names) else str(cid),
                        "bbox":          [float(x1), float(y1),
                                          float(x2 - x1), float(y2 - y1)],
                        "score":         float(p),
                    })
            payload = {
                "image":      Path(src_path).name,
                "image_size": [int(w), int(h)],
                "detections": dets,
            }
            Path(path).write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8")
        except Exception as e:
            self.log_line.emit(self._html(
                f"  ↳ JSON 保存失败: {e}", "#F44336"))

    # ── HTML 工具 ─────────────────────────────────────────────────────
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
#  GPU 枚举（基于 torch.cuda）
# ══════════════════════════════════════════════════════════════════════
def detect_gpus() -> dict[str, str]:
    """
    返回 {显示名: device_str}，供 UI 下拉框使用。
    例如：{"GPU 0 · NVIDIA RTX 4090": "0", "CPU": "-1"}
    """
    result: dict[str, str] = {}
    try:
        import torch
        if torch.cuda.is_available():
            for i in range(torch.cuda.device_count()):
                try:
                    name = torch.cuda.get_device_name(i)
                except Exception:
                    name = f"CUDA Device {i}"
                result[f"GPU {i} · {name}"] = str(i)
    except Exception:
        pass

    if not result:
        # torch 未装 / 无 CUDA 时给一个占位项，让用户可选 auto
        result["GPU 0 (默认自动)"] = "auto"
    result["CPU（禁用 GPU）"] = "-1"
    return result


# ══════════════════════════════════════════════════════════════════════
#  权重发现 —— 扫描 weights/ 目录
# ══════════════════════════════════════════════════════════════════════
def discover_weights(weights_dir: str = DEFAULT_WEIGHTS_DIR) -> list[str]:
    """返回目录下所有 .pt 文件的绝对路径列表。"""
    if not os.path.isdir(weights_dir):
        return []
    out = []
    for fn in sorted(os.listdir(weights_dir)):
        if fn.lower().endswith((".pt", ".onnx", ".engine")):
            out.append(os.path.join(weights_dir, fn))
    return out


# ══════════════════════════════════════════════════════════════════════
#  UI 参数 → Worker 参数 桥接
# ══════════════════════════════════════════════════════════════════════
def build_worker_from_ui_params(files: list[str], ui_params: dict,
                                weights_dir: str = DEFAULT_WEIGHTS_DIR
                                ) -> YoloWorker:
    """
    将 YoloParamPanel.get_params() 返回的字典转换为 Worker 所需格式。

    UI 中 "model" 字段可能是：
      - "yolov8n"             → 加上 .pt 后缀
      - "yolov8n.pt"          → 直接使用
      - 绝对路径 / 含路径分隔符 → 直接使用
    若 weights_dir 内能找到匹配的本地文件，优先用本地路径。
    """
    model_field = str(ui_params.get("model", "yolov8n"))

    if any(s in model_field for s in ("/", "\\")):
        weights = model_field
    else:
        stem = model_field if model_field.endswith(
            (".pt", ".onnx", ".engine")) else f"{model_field}.pt"

        # 优先查本地权重目录
        local = os.path.join(weights_dir, stem)
        weights = local if os.path.isfile(local) else stem

    # device：UI 里若是 "GPU 0 · 0" 这种字串，page 已解析为 "0"/"-1"/"auto"
    device = str(ui_params.get("device", "auto"))

    worker_params = {
        "weights":    weights,
        "imgsz":      int(ui_params.get("imgsz", 640)),
        "conf":       float(ui_params.get("conf", 0.25)),
        "iou":        float(ui_params.get("iou", 0.45)),
        "max_det":    int(ui_params.get("max_det", 300)),
        "device":     device,
        "fp16":       bool(ui_params.get("fp16", True)),
        "tta":        bool(ui_params.get("tta", False)),
        "agnostic":   bool(ui_params.get("agnostic", False)),
        "classes":    ui_params.get("classes", None),
        "out_dir":    ui_params.get("out_dir", "./runs/detect"),
        "save_mode":  ui_params.get("save_mode", "图片+TXT(YOLO)"),
        "draw_boxes": bool(ui_params.get("draw_boxes", True)),
        "draw_label": bool(ui_params.get("draw_label", True)),
        "line_w":     int(ui_params.get("line_w", 2)),
    }
    return YoloWorker(files, worker_params)


# ══════════════════════════════════════════════════════════════════════
#  YoloInferencePage 一键接入
# ══════════════════════════════════════════════════════════════════════
def patch_yolo_page(page, weights_dir: str = DEFAULT_WEIGHTS_DIR):
    """
    把 YoloInferencePage 的模拟 worker 替换为真实 ultralytics 推理。

    使用方式：
        from yolo_worker import patch_yolo_page
        patch_yolo_page(self.yolo_page)

    本函数会：
      1. 把 page._params.model_combo 的项替换成本地权重文件 + 内置默认列表
      2. 替换 page._start / page._abort 为真实实现
      3. 把 worker.model_loaded 连接到 page._params.class_filter.set_classes
    """
    import types

    # ── 1. 用本地权重 + 内置列表填充模型下拉 ───────────────────────
    try:
        local_pts = [Path(p).name for p in discover_weights(weights_dir)]
        items = local_pts + [
            m for m in YOLO_MODELS.keys() if m not in local_pts
        ]
        if items and hasattr(page, "_params"):
            page._params.model_combo.clear()
            page._params.model_combo.addItems(items)
            page._params.model_combo.setCurrentIndex(0)
    except Exception:
        pass

    # ── 2. 替换 _start / _abort ───────────────────────────────────
    def _start(self):
        from qfluentwidgets import InfoBar, InfoBarPosition, FluentIcon as FIF

        if not self._files:
            InfoBar.warning(
                title="未选择文件", content="请先添加图像",
                parent=self.window(),
                position=InfoBarPosition.TOP_RIGHT, duration=3000)
            return

        self._running = True
        self._run_btn.setText("停止检测")
        self._run_btn.setIcon(FIF.PAUSE)
        self._params.setEnabled(False)
        self._results.clear()
        self._infer_times.clear()

        self._stats["processed"].setText(f"0 / {len(self._files)}")
        self._stats["total_det"].setText("0")
        self._stats["avg_conf"].setText("—")
        self._stats["fps"].setText("—")
        for it in self._file_items.values():
            it.set_status("等待")

        ui_params = self._params.get_params()
        worker = build_worker_from_ui_params(
            self._files, ui_params, weights_dir=weights_dir)

        # YoloWorker 本身就是 QThread
        self._worker = worker
        self._thread = worker

        # 标准信号
        worker.progress.connect(self._on_progress)
        worker.image_done.connect(self._on_image_done)
        worker.finished.connect(self._on_finished)
        worker.error.connect(self._on_error)
        worker.log_line.connect(self._append_log)

        # 单张状态 → FileListItem
        worker.file_done.connect(
            lambda inp, _out: self._file_items[inp].set_status("完成")
            if inp in self._file_items else None)
        worker.file_error.connect(
            lambda inp, _msg: self._file_items[inp].set_status("失败")
            if inp in self._file_items else None)

        # 模型加载完成 → 同步类别过滤面板
        try:
            worker.model_loaded.connect(self._params.class_filter.set_classes)
        except Exception:
            pass

        self._append_log(
            f"开始检测，模型 {ui_params.get('model')}，imgsz {ui_params.get('imgsz')}")
        worker.start()

    def _abort(self):
        from qfluentwidgets import FluentIcon as FIF
        if getattr(self, "_worker", None) is not None:
            try:
                self._worker.cancel()
            except Exception:
                pass
        self._running = False
        self._run_btn.setText("开始检测")
        self._run_btn.setIcon(FIF.PLAY)
        self._params.setEnabled(True)

    page._start = types.MethodType(_start, page)
    page._abort = types.MethodType(_abort, page)
    return page
