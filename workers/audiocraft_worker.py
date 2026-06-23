# workers/audiocraft_worker.py

import os
import re
import sys
import subprocess
import time
from pathlib import Path

from PyQt6.QtCore import QThread, pyqtSignal

from utils import paths as _paths


_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_RUNNER = os.path.join(_THIS_DIR, "_audiocraft_runner.py")


def _html(text, color=None, bold=False):
    if not color and not bold:
        return text
    style = []
    if color:
        style.append(f"color:{color}")
    if bold:
        style.append("font-weight:bold")
    return f'<span style="{";".join(style)}">{text}</span>'


class AudiocraftWorker(QThread):
    """MusicGen / AudioGen 生成工作线程。

    params 字段：
        - task:           "musicgen" | "audiogen"
        - model:          短名 ("small"/"medium"/"large"/"melody") 或完整 HF id
        - device:         "cuda" / "cuda:0" / "cpu" / "mps"
        - prompts:        list[str] 或单条 str
        - melody:         可选，MusicGen-melody 用的旋律音频路径
        - output:         输出目录（不传走 _paths.output_dir("audiocraft")）
        - output_format:  "wav" / "mp3"
        - duration / top_k / top_p / temperature / cfg_coef
    """

    progress = pyqtSignal(int, str)
    output = pyqtSignal(str)
    finished = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self, params: dict):
        super().__init__()
        self.params = params
        self.process = None
        self._is_cancelled = False

    def _get_prompt_list(self) -> list:
        prompts = self.params.get("prompts")
        if prompts is None:
            prompts = self.params.get("prompt", "")
        if isinstance(prompts, str):
            # 文本框里多条用换行分隔
            return [p.strip() for p in prompts.splitlines() if p.strip()]
        if isinstance(prompts, list):
            return [str(p).strip() for p in prompts if str(p).strip()]
        return []

    def run(self):
        try:
            task = self.params.get("task", "musicgen")
            if task not in ("musicgen", "audiogen"):
                self.error.emit(f"未知 task: {task}")
                return

            prompts = self._get_prompt_list()
            if not prompts:
                self.error.emit("请至少填写一条提示词")
                return

            output_dir = self.params.get("output") or _paths.output_dir("audiocraft")
            os.makedirs(output_dir, exist_ok=True)

            model = self.params.get("model") or ("small" if task == "musicgen" else "medium")
            device = self.params.get("device", "cuda")
            melody = self.params.get("melody") or ""
            output_format = self.params.get("output_format", "wav")
            duration = float(self.params.get("duration", 8.0))
            top_k = int(self.params.get("top_k", 250))
            top_p = float(self.params.get("top_p", 0.0))
            temperature = float(self.params.get("temperature", 1.0))
            cfg_coef = float(self.params.get("cfg_coef", 3.0))

            cmd = [sys.executable, _RUNNER,
                   "--task", task,
                   "--model", model,
                   "--device", device,
                   "--duration", str(duration),
                   "--top-k", str(top_k),
                   "--top-p", str(top_p),
                   "--temperature", str(temperature),
                   "--cfg-coef", str(cfg_coef),
                   "--output-dir", output_dir,
                   "--output-format", output_format]
            for p in prompts:
                cmd.extend(["--prompt", p])
            if melody and task == "musicgen":
                cmd.extend(["--melody", melody])

            self.progress.emit(0, "准备启动 audiocraft…")
            self.output.emit(_html(f"任务: {task}", "#888888"))
            self.output.emit(_html(f"模型: {model}", "#888888"))
            self.output.emit(_html(f"设备: {device}", "#888888"))
            self.output.emit(_html(f"提示词数: {len(prompts)}", "#888888"))
            self.output.emit(_html(f"输出目录: {output_dir}", "#888888"))
            self.output.emit(_html(f"执行命令: {' '.join(cmd)}", "#888888"))

            # HF_HOME 落到 <root>/models/audiocraft/，避免模型权重塞到 C 盘用户目录
            env = _paths.subprocess_env(hf_home=_paths.model_dir("audiocraft"))
            # encodec / xformers 在 Windows 上有时候吐 utf-8 之外的字节，强制兜底
            env.setdefault("PYTHONIOENCODING", "utf-8")
            # 子进程 stdout 被 PIPE 后默认是块缓冲，下载阶段的 [download] 行会卡在
            # 缓冲区里直到模型加载完才一次性吐出来 —— 关掉缓冲让进度实时可见
            env["PYTHONUNBUFFERED"] = "1"

            self.process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                encoding="utf-8",
                errors="replace",
                env=env,
            )

            for line in iter(self.process.stdout.readline, ""):
                if self._is_cancelled:
                    self.process.terminate()
                    self.output.emit(_html("用户取消了生成任务", "#FF9800"))
                    return
                if not line:
                    break
                line = line.rstrip("\r\n")
                if line:
                    self._parse_output(line)

            self.process.wait()
            if self._is_cancelled:
                return

            if self.process.returncode == 0:
                self.progress.emit(100, "生成完成")
                self.output.emit(_html(
                    f"✨ 所有输出文件已保存到: {output_dir}", "#4CAF50"))
                self.finished.emit(output_dir)
            else:
                self.error.emit(
                    f"生成失败 (返回码: {self.process.returncode})")

        except Exception as e:
            self.error.emit(f"生成过程中发生异常: {e}")

    # runner 的输出协议：
    #   [progress] N/M (P%)
    #   [runner] ...
    #   [runner][ERROR] ...
    _RE_PROGRESS = re.compile(r"^\[progress\]\s+(\d+)\s*/\s*(\d+)\s*(?:\((\d+)%\))?")

    def _parse_output(self, line: str):
        m = self._RE_PROGRESS.match(line)
        if m:
            cur = int(m.group(1))
            total = int(m.group(2)) or 1
            pct = int(m.group(3)) if m.group(3) else int(cur * 100 / total)
            pct = max(0, min(100, pct))
            self.progress.emit(pct, f"生成中 {cur}/{total} ({pct}%)")
            return

        if line.startswith("[download]"):
            # 包含 "下载进度" 让 LogTextEdit 把同一文件的多行折叠成一行原地刷新
            # （参见 widgets/subpage/subpage_switch_pages.py 里 LogTextEdit 的渲染逻辑）
            payload = line[len("[download]"):].strip()
            self.output.emit(_html(f"下载进度 {payload}", "#2196F3"))
            # 同时抽出 pct 走 progress 信号:LLM 路径下 ToolCallCard 只接 progress,
            # 不接 output,如果不在这里发一遍,下载阶段在 LLM 卡片上是静默的。
            # GUI 路径会拿到「下载 0~100% → 生成 0~100%」两段,因为下载先于生成串行。
            m = re.search(r"\((\d+)%\)", payload)
            if m:
                self.progress.emit(int(m.group(1)), f"下载模型 · {payload}")
            return

        if line.startswith("[runner][ERROR]"):
            self.output.emit(_html(line, "#F44336"))
            return
        if line.startswith("[runner]"):
            color = "#4CAF50" if ("已保存" in line or "完成" in line) else "#9C27B0"
            self.output.emit(_html(line, color))
            return

        low = line.lower()
        if "traceback" in low or "error" in low:
            self.output.emit(_html(line, "#F44336"))
        elif "warning" in low:
            self.output.emit(_html(line, "#FF9800"))
        elif "download" in low or "fetching" in low or "%" in line:
            self.output.emit(_html(line, "#2196F3"))
        else:
            self.output.emit(_html(line, "#CCCCCC"))

    def cancel(self):
        self._is_cancelled = True
        if self.process:
            self.process.terminate()
            time.sleep(0.5)
            if self.process.poll() is None:
                self.process.kill()
