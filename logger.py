# coding:utf-8
import logging
import os
import sys

# Windows 10+ 控制台 ANSI 颜色支持
if os.name == "nt":
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
    except Exception:
        pass

COLOR_RESET = "\x1b[0m"
COLOR_MAP = {
    logging.DEBUG: "\x1b[36m",    # 青色
    logging.INFO: "\x1b[32m",     # 绿色
    logging.WARNING: "\x1b[33m",  # 黄色
    logging.ERROR: "\x1b[31m",    # 红色
}


class ColorFormatter(logging.Formatter):
    def format(self, record):
        if record.levelno == logging.WARNING:
            record.levelname = "WARMING"
        message = super().format(record)
        color = COLOR_MAP.get(record.levelno, "")
        if color:
            return f"{color}{message}{COLOR_RESET}"
        return message


LOG_FILE_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "log.txt")

_plain_fmt = logging.Formatter(
    "%(asctime)s %(levelname)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S")


def _make_file_handler(level: int, fmt: logging.Formatter):
    """构建写 log.txt 的 FileHandler;打不开就返回 None(不阻塞启动)"""
    try:
        h = logging.FileHandler(LOG_FILE_PATH, encoding="utf-8", mode="a")
        h.setLevel(level)
        h.setFormatter(fmt)
        return h
    except Exception:
        return None


logger = logging.getLogger("LaunchAI")
logger.setLevel(logging.DEBUG)
if not logger.handlers:
    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setLevel(logging.DEBUG)
    fmt = logging.Formatter("%(levelname)s:%(message)s")
    handler.setFormatter(ColorFormatter(fmt._fmt))
    logger.addHandler(handler)

    _fh = _make_file_handler(logging.DEBUG, _plain_fmt)
    if _fh is not None:
        logger.addHandler(_fh)


# ---------------------------------------------------------------------------
# UI 落盘:LogTextEdit 追加的纯文本走这个 child logger,只写 log.txt 不刷控制台。
# 用 child logger 复用 logging 自带的线程锁,避免与主 logger 争同一 FileHandler。
# ---------------------------------------------------------------------------
_ui_logger = logging.getLogger("LaunchAI.ui")
_ui_logger.setLevel(logging.INFO)
_ui_logger.propagate = False
if not _ui_logger.handlers:
    _ui_fh = _make_file_handler(
        logging.INFO,
        logging.Formatter("%(asctime)s UI: %(message)s",
                          datefmt="%Y-%m-%d %H:%M:%S"),
    )
    if _ui_fh is not None:
        _ui_logger.addHandler(_ui_fh)


def log_ui(text: str) -> None:
    """把 LogTextEdit 追加的纯文本写到 log.txt(空串跳过)"""
    if not text:
        return
    try:
        _ui_logger.info(text)
    except Exception:
        pass


# 简化调用
info = logger.info
warning = logger.warning
debug = logger.debug
error = logger.error

__all__ = ["logger", "info", "warning", "debug",
           "error", "log_ui", "LOG_FILE_PATH"]
