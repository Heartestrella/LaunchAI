"""workers/health_worker.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~
「自检 / 运行测试」页面的后台 worker。

对每个待测工具,以**子进程**方式跑 ``python -m server.health_check --tool X --stream``,
逐行解析 ``@EVENT`` / ``@RESULT`` 协议并转发为 Qt 信号。用子进程而非直接 import
run_tool 的原因:某个工具(尤其进程内的 yolo / iopaint)可能段错误 / 崩溃,隔离在
子进程里不会拖垮主程序;这也与项目里其它 worker「subprocess.Popen 一个 CLI」的惯例一致。

信号形状遵循项目统一契约(见 CLAUDE.md),额外加 tool_started / tool_result 驱动行状态。
"""
import os
import sys
import json
import subprocess

from PyQt6.QtCore import QThread, pyqtSignal

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 用脚本文件路径而非 `-m server.health_check`:py311 是嵌入式发行版,
# `-m`/`-c` 不会把 cwd 加进 sys.path(只有跑脚本文件时脚本目录才进 path),
# 而 health_check.py 顶部会自行把项目根插入 sys.path。
_SCRIPT = os.path.join(_PROJECT_ROOT, "server", "health_check.py")


class HealthCheckWorker(QThread):
    progress    = pyqtSignal(int, str)     # 百分比, 状态文本
    output      = pyqtSignal(str)          # HTML 日志行
    error       = pyqtSignal(str)          # 致命错误
    finished    = pyqtSignal(str)          # 结束总结
    tool_started = pyqtSignal(str)         # tool —— 该工具开始测试
    tool_result  = pyqtSignal(str, bool, str)  # tool, ok, detail

    def __init__(self, tools: list[str], device: str | None = None, parent=None):
        super().__init__(parent)
        self._tools = list(tools)
        self._device = device
        self._cancelled = False
        self._proc = None

    def cancel(self):
        self._cancelled = True
        if self._proc and self._proc.poll() is None:
            try:
                self._proc.terminate()
            except Exception:
                pass

    def _html(self, text: str, color: str = "#DDDDDD", bold: bool = False) -> str:
        w = "font-weight:bold;" if bold else ""
        return f'<span style="color:{color};{w}">{text}</span>'

    def run(self):
        total = len(self._tools)
        passed = failed = skipped = 0
        try:
            for idx, tool in enumerate(self._tools):
                if self._cancelled:
                    break
                self.tool_started.emit(tool)
                self.progress.emit(int(idx / max(total, 1) * 100),
                                   f"正在测试 {tool} ({idx + 1}/{total})")
                r = self._run_one(tool)
                if r is None:
                    continue
                if not r.get("installed"):
                    skipped += 1
                elif r.get("ok"):
                    passed += 1
                else:
                    failed += 1
        except Exception as e:  # noqa: BLE001
            self.error.emit(f"自检 worker 异常: {type(e).__name__}: {e}")
            return

        self.progress.emit(100, "完成")
        if self._cancelled:
            self.finished.emit("已取消")
        else:
            self.finished.emit(
                f"完成:通过 {passed} · 失败 {failed} · 跳过(未安装) {skipped}")

    def _run_one(self, tool: str) -> dict | None:
        argv = [sys.executable, _SCRIPT,
                "--tool", tool, "--stream", "--json"]
        if self._device:
            argv += ["--device", self._device]

        result = None
        try:
            self._proc = subprocess.Popen(
                argv, cwd=_PROJECT_ROOT,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1, encoding="utf-8", errors="replace")
        except Exception as e:  # noqa: BLE001
            detail = f"无法启动自检子进程: {e}"
            self.tool_result.emit(tool, False, detail)
            self.output.emit(self._html(f"[{tool}] {detail}", "#FF6B6B"))
            return None

        for raw in iter(self._proc.stdout.readline, ""):
            if self._cancelled:
                break
            line = raw.rstrip("\n")
            if not line:
                continue
            if line.startswith("@EVENT "):
                self._handle_event(tool, line[len("@EVENT "):])
            elif line.startswith("@RESULT "):
                result = self._handle_result(tool, line[len("@RESULT "):])
            else:
                self.output.emit(self._html(line))
        self._proc.wait()
        self._proc = None
        return result

    def _handle_event(self, tool: str, payload: str):
        try:
            ev = json.loads(payload)
        except Exception:
            self.output.emit(self._html(payload))
            return
        kind = ev.get("kind")
        if kind == "progress":
            self.output.emit(self._html(f"[{tool}] {ev.get('text', '')}", "#4FC3F7"))
        elif kind == "log":
            # 子工具日志本就是 HTML,原样透传
            self.output.emit(ev.get("line", ""))
        elif kind == "tool_start":
            self.output.emit(self._html(
                f"开始测试 {ev.get('display', tool)}", "#FFD54F", bold=True))

    def _handle_result(self, tool: str, payload: str) -> dict | None:
        try:
            r = json.loads(payload)
        except Exception:
            return None
        installed = r.get("installed", False)
        ok = bool(r.get("ok"))
        detail = r.get("detail", "")
        self.tool_result.emit(tool, ok, detail)
        if not installed:
            color = "#9E9E9E"
        elif ok:
            color = "#4CAF50"
        else:
            color = "#F44336"
        self.output.emit(self._html(f"[{tool}] {detail}", color, bold=True))
        return r


class InstallProbeWorker(QThread):
    """轻量探测:跑 `--list --json` 拿到各工具安装状态,别在 UI 线程上做
    (pip show 有多次子进程调用,会卡界面)。"""
    done = pyqtSignal(list)   # [{tool, display, installed, mode}, ...]

    def run(self):
        argv = [sys.executable, _SCRIPT, "--list", "--json"]
        try:
            r = subprocess.run(argv, cwd=_PROJECT_ROOT, capture_output=True,
                               text=True, encoding="utf-8", errors="replace",
                               timeout=180)
            data = json.loads(r.stdout)
        except Exception:
            data = []
        self.done.emit(data)
