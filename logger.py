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


logger = logging.getLogger("LaunchAI")
logger.setLevel(logging.DEBUG)
if not logger.handlers:
    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setLevel(logging.DEBUG)
    fmt = logging.Formatter("%(levelname)s:%(message)s")
    handler.setFormatter(ColorFormatter(fmt._fmt))
    logger.addHandler(handler)

# 简化调用
info = logger.info
warning = logger.warning
debug = logger.debug
error = logger.error

__all__ = ["logger", "info", "warning", "debug", "error"]
