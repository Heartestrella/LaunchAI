# widgets/log_text_edit.py
"""统一的日志显示控件。

原本 7 个 subpage 各自复制了一份 LogTextEdit(subpage_switch_pages /
subpage_setting_page / subpage_audiocraft / subpage_gptsovits / subpage_rvc /
subpage_whisper / subpage_yolov),另有 subpage_model_hub 的 `_LogTextEdit`
差在方法名。这里合成一份:

- `下载进度` 前缀行覆盖当前行,避免下载刷屏
- URL 自动转成可点击超链接,点击走系统浏览器打开
- 追加内容同步落盘到项目根 log.txt(经 logger.log_ui 走 LaunchAI.ui child logger),
  下载进度行因每秒多条会刷屏日志,跳过落盘
- `append_html` 作为 `append_colored` 的别名,兼容原 `_LogTextEdit` 调用点
"""

from __future__ import annotations

import re

from PyQt6.QtCore import QUrl
from PyQt6.QtGui import QDesktopServices, QTextCursor
from qfluentwidgets import TextEdit

from logger import log_ui


_URL_PATTERN = re.compile(r'(https?://[^\s<>"\'{}|\\^`\[\]]+)')
_TAG_PATTERN = re.compile(r"<[^>]+>")


def _strip_html(html: str) -> str:
    """把 HTML 拆成人类可读的纯文本,保留换行"""
    text = (html
            .replace("<br>", "\n")
            .replace("<br/>", "\n")
            .replace("<br />", "\n"))
    text = _TAG_PATTERN.sub("", text)
    text = (text
            .replace("&nbsp;", " ")
            .replace("&amp;", "&")
            .replace("&lt;", "<")
            .replace("&gt;", ">")
            .replace("&quot;", '"')
            .replace("&#39;", "'"))
    return text.strip()


class LogTextEdit(TextEdit):
    """支持彩色 HTML、URL 超链接、下载进度覆盖的只读日志控件"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptRichText(True)
        self.setReadOnly(True)

    # ---- 追加接口 -------------------------------------------------------
    def append_colored(self, html_text: str) -> None:
        if html_text is None:
            return

        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self.setTextCursor(cursor)

        html_with_links = self._convert_urls_to_links(html_text)
        is_progress = "下载进度" in html_text

        if is_progress:
            cursor.movePosition(
                QTextCursor.MoveOperation.StartOfLine,
                QTextCursor.MoveMode.KeepAnchor,
            )
            cursor.removeSelectedText()
            cursor.insertHtml(html_with_links)
        else:
            cursor.insertHtml(html_with_links + "<br>")

        self.ensureCursorVisible()

        # 下载进度每秒多条,落盘会撑爆 log.txt,只落普通行
        if not is_progress:
            plain = _strip_html(html_text)
            if plain:
                log_ui(plain)

    # 兼容原 subpage_model_hub._LogTextEdit 的方法名
    def append_html(self, html_text: str) -> None:
        self.append_colored(html_text)

    # ---- URL → 超链接 ---------------------------------------------------
    def _convert_urls_to_links(self, text: str) -> str:
        def _repl(match: re.Match) -> str:
            url = match.group(1)
            display = url if len(url) <= 80 else url[:40] + "..." + url[-30:]
            return (f'<a href="{url}" '
                    f'style="color:#4FC3F7; text-decoration:underline;">'
                    f'{display}</a>')

        return _URL_PATTERN.sub(_repl, text)

    # ---- 点击超链接走系统浏览器 -----------------------------------------
    def mousePressEvent(self, event):
        cursor = self.cursorForPosition(event.pos())
        if cursor.charFormat().isAnchor():
            anchor = cursor.charFormat().anchorHref()
            if anchor:
                QDesktopServices.openUrl(QUrl(anchor))
                return
        super().mousePressEvent(event)


__all__ = ["LogTextEdit"]
