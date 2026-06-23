"""
workers/model_hub_worker.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~
RVC / GPT-SoVITS 模型搜索/下载的 QThread 封装。

信号与 LaunchAI 其它 Worker 保持一致::

    progress(int, str)   #  0-100, 状态文本
    output(str)          #  HTML 日志行 含「下载进度」前缀的会被 LogTextEdit 覆盖
    finished(str)        #  Download: 最后一个文件的本地路径
    error(str)
    results(list)        #  Search 专用 ModelItem 转 dict 后的列表
    files(list)          #  ListFiles 专用 ModelFile 转 dict 后的列表

线程主体只做 I/O 不动 UI 控件 所有控件操作走信号槽连接。
"""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import QThread, pyqtSignal

from utils import model_hub as _mh
from utils.model_hub import CancelledError, ModelItem, ModelFile


# ── 颜色 ──────────────────────────────────────────────────────────────

def _html(text: str, color: str = "#cccccc", bold: bool = False) -> str:
    t = text.replace("<", "&lt;").replace(">", "&gt;")
    if bold:
        t = f"<b>{t}</b>"
    return f'<span style="color:{color};">{t}</span>'


# ══════════════════════════════════════════════════════════════════════
#  搜索
# ══════════════════════════════════════════════════════════════════════

class ModelSearchWorker(QThread):
    results = pyqtSignal(list)        # list[dict]
    error   = pyqtSignal(str)

    def __init__(self, kind: str, keyword: str,
                 sources: Optional[list[str]] = None,
                 per_source_limit: int = 15, parent=None):
        super().__init__(parent)
        self.kind = kind
        self.keyword = keyword
        self.sources = list(sources) if sources else None
        self.per_source_limit = int(per_source_limit)

    def run(self):
        try:
            items = _mh.search(
                self.kind, self.keyword,
                sources=self.sources,
                per_source_limit=self.per_source_limit,
            )
            self.results.emit([it.to_dict() for it in items])
        except Exception as e:
            self.error.emit(f"搜索失败: {e}")


# ══════════════════════════════════════════════════════════════════════
#  列文件
# ══════════════════════════════════════════════════════════════════════

class ModelListFilesWorker(QThread):
    files = pyqtSignal(list)          # list[dict]
    error = pyqtSignal(str)

    def __init__(self, item: dict, parent=None):
        super().__init__(parent)
        self._item = ModelItem.from_dict(item)

    def run(self):
        try:
            fs = _mh.list_files(self._item)
            self.files.emit([f.to_dict() for f in fs])
        except Exception as e:
            self.error.emit(f"列文件失败: {e}")


# ══════════════════════════════════════════════════════════════════════
#  下载
# ══════════════════════════════════════════════════════════════════════

class ModelDownloadWorker(QThread):
    progress = pyqtSignal(int, str)
    output   = pyqtSignal(str)
    finished = pyqtSignal(str)
    error    = pyqtSignal(str)

    def __init__(self, item: dict, files: list[dict],
                 dest_dir: Optional[str] = None, parent=None):
        super().__init__(parent)
        self._item = ModelItem.from_dict(item)
        self._files = [ModelFile(
            path=f.get("path", ""),
            size=int(f.get("size") or 0),
            is_model=bool(f.get("is_model")),
            is_aux=bool(f.get("is_aux")),
        ) for f in files if (f.get("path") or "").strip()]
        self._dest_dir = dest_dir
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def _emit_progress(self, pct: int, text: str):
        self.progress.emit(int(pct), text)
        # 用「下载进度」前缀触发 LogTextEdit 单行覆盖
        self.output.emit(_html(f"下载进度 {text}", "#80CBC4"))

    def _emit_output(self, text: str):
        # 普通日志行（非覆盖）
        self.output.emit(_html(text, "#cccccc"))

    def run(self):
        try:
            if not self._files:
                self.error.emit("没有勾选任何文件")
                return
            dest = self._dest_dir or _mh.model_dest_dir(self._item)
            self.output.emit(_html(
                f"目标目录: {dest}", "#9E9E9E"))
            saved = _mh.download_files(
                self._item, self._files, dest_dir=dest,
                progress_cb=self._emit_progress,
                output_cb=self._emit_output,
                cancel_cb=lambda: self._cancelled,
            )
            last = saved[-1] if saved else dest
            self.output.emit(_html(
                f"全部完成 共 {len(saved)} 个文件 保存到 {dest}",
                "#4CAF50", bold=True))
            self.finished.emit(last)
        except CancelledError:
            self.error.emit("已取消")
        except Exception as e:
            self.error.emit(f"下载失败: {e}")
