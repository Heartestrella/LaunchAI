# workers/material_fetch_worker.py
#
# 给 LLM 聊天的工具调用层用的轻量 worker。
# 把 utils.material_fetcher 的三种操作(搜索 / 指 id 下载 / 一键搜+下)
# 包成统一信号形状:
#     progress = pyqtSignal(int, str)
#     finished = pyqtSignal(str)   # search 时是 JSON 列表;download 时是本地文件路径
#     error    = pyqtSignal(str)
#
# 与 subpage_llm_chat.py 的 _on_tool_progress / _on_tool_done 直接对接。

from __future__ import annotations

import json

from PyQt6.QtCore import QThread, pyqtSignal

from utils.material_fetcher import CancelledError


class MaterialFetchWorker(QThread):
    progress = pyqtSignal(int, str)
    finished = pyqtSignal(str)
    error    = pyqtSignal(str)

    def __init__(self, op: str, payload: dict, parent=None):
        """
        Args:
            op: 'search' | 'download' | 'fetch_first'
            payload: 操作参数:
                - search:      {keyword, source, limit, drop_instrumental}
                - download:    {source, id, out_dir, title?}
                - fetch_first: {keyword, source, out_dir, drop_instrumental}
        """
        super().__init__(parent)
        self.op = op
        self.payload = dict(payload or {})
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    # 桥接到 material_fetcher 的两个回调
    def _progress_cb(self, percent: int, status: str):
        self.progress.emit(int(percent), str(status))

    def _cancel_cb(self) -> bool:
        return self._cancelled

    def run(self):
        try:
            from utils import material_fetcher as mf

            if self.op == "search":
                self._run_search(mf)
            elif self.op == "download":
                self._run_download(mf)
            elif self.op == "fetch_first":
                self._run_fetch_first(mf)
            else:
                self.error.emit(f"未知操作: {self.op}")
        except CancelledError as e:
            self.error.emit(f"已取消: {e}")
        except Exception as e:
            self.error.emit(f"{type(e).__name__}: {e}")

    # ── 实际操作 ──────────────────────────────────────────────────────
    def _run_search(self, mf):
        kw = (self.payload.get("keyword") or "").strip()
        if not kw:
            self.error.emit("keyword 为空")
            return
        source = (self.payload.get("source") or "netease").lower()
        try:
            limit = int(self.payload.get("limit") or 10)
        except (TypeError, ValueError):
            limit = 10
        limit = max(1, min(30, limit))
        drop_inst = bool(self.payload.get("drop_instrumental", True))

        self.progress.emit(0, f"在 {source} 搜索: {kw}")
        if source == "netease":
            hits = mf.search_netease(
                kw, limit=limit, drop_instrumental=drop_inst)
        elif source == "bilibili":
            hits = mf.search_bilibili(kw, limit=limit)
        else:
            self.error.emit(f"未知 source: {source}, 仅支持 netease / bilibili")
            return

        self.progress.emit(100, f"命中 {len(hits)} 条")
        payload = {"source": source, "keyword": kw,
                   "count": len(hits), "hits": hits}
        self.finished.emit(json.dumps(payload, ensure_ascii=False))

    def _run_download(self, mf):
        source = (self.payload.get("source") or "").lower()
        rid = self.payload.get("id")
        out_dir = self.payload.get("out_dir")
        title = self.payload.get("title") or None
        if not source:
            self.error.emit("source 为空")
            return
        if rid is None or rid == "":
            self.error.emit("id 为空(网易云传 song_id 整数,B 站传 bvid 字符串)")
            return
        if not out_dir:
            self.error.emit("out_dir 为空")
            return

        if source == "netease":
            try:
                song_id = int(rid)
            except (TypeError, ValueError):
                self.error.emit(f"网易云 id 必须可转 int,收到: {rid!r}")
                return
            path = mf.download_netease(
                song_id, out_dir, title=title,
                progress_cb=self._progress_cb, cancel_cb=self._cancel_cb)
        elif source == "bilibili":
            bvid = str(rid).strip()
            if not bvid:
                self.error.emit("B 站 bvid 为空")
                return
            path = mf.download_bilibili(
                bvid, out_dir, title=title,
                progress_cb=self._progress_cb, cancel_cb=self._cancel_cb)
        else:
            self.error.emit(f"未知 source: {source}, 仅支持 netease / bilibili")
            return

        self.finished.emit(path)

    def _run_fetch_first(self, mf):
        kw = (self.payload.get("keyword") or "").strip()
        source = (self.payload.get("source") or "netease").lower()
        out_dir = self.payload.get("out_dir")
        drop_inst = bool(self.payload.get("drop_instrumental", True))
        if not kw:
            self.error.emit("keyword 为空")
            return
        if not out_dir:
            self.error.emit("out_dir 为空")
            return

        path = mf.fetch_first_match(
            kw, source, out_dir,
            drop_instrumental=drop_inst,
            progress_cb=self._progress_cb, cancel_cb=self._cancel_cb,
        )
        self.finished.emit(path)
