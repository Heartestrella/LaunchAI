# workers/audio_mix_worker.py
#
# ffmpeg amix / concat 多路音频合并 给 LLM 聊天的 mix_audio 工具用。
# 与 node/node_worker.py::AudioMergeExec 的逻辑一致 但走独立的 QThread
# 不依赖节点引擎的 ctx 上下文。

from __future__ import annotations

import os
import shutil
import subprocess

from PyQt6.QtCore import QThread, pyqtSignal


def _ffmpeg_exe() -> str:
    """优先用 resource/ffmepg/bin/ffmpeg.exe(注意是 ffmepg 不是 ffmpeg —— 项目沿用的目录名)。"""
    try:
        from utils.atool import resource_path
        bundled = resource_path("resource/ffmepg/bin/ffmpeg.exe")
        if os.path.isfile(bundled):
            return bundled
    except Exception:
        pass
    return shutil.which("ffmpeg") or "ffmpeg"


class AudioMixWorker(QThread):
    """多路音频合并 信号形状与聊天页 _on_tool_progress / _on_tool_done 对齐。"""

    progress = pyqtSignal(int, str)
    finished = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self, params: dict):
        """
        params:
          inputs:  list[str]  必填 至少 1 路本地音频绝对路径
          output:  str        必填 输出文件绝对路径
          mode:    "mix" | "concat"   默认 "mix"
          weights: list[float] | None 仅 mode=mix 用 长度对齐 inputs 缺省 1.0
        """
        super().__init__()
        self.params = dict(params or {})
        self.process: subprocess.Popen | None = None
        self._cancelled = False

    def cancel(self):
        self._cancelled = True
        p = self.process
        if p is not None and p.poll() is None:
            try:
                p.terminate()
            except Exception:
                pass

    def run(self):
        try:
            inputs = [p for p in (self.params.get("inputs") or [])
                      if isinstance(p, str) and p.strip()]
            if not inputs:
                self.error.emit("缺少 inputs(音频文件路径数组)")
                return
            for p in inputs:
                if not os.path.isfile(p):
                    self.error.emit(f"输入不存在: {p}")
                    return

            mode = (self.params.get("mode") or "mix").lower()
            if mode not in ("mix", "concat"):
                self.error.emit(f"未知 mode: {mode}, 仅支持 mix / concat")
                return

            output = self.params.get("output")
            if not output:
                self.error.emit("缺少 output 路径")
                return

            os.makedirs(os.path.dirname(output) or ".", exist_ok=True)

            n = len(inputs)
            if n == 1:
                # 单路:直接复制,与节点 AudioMergeExec 行为一致
                shutil.copy2(inputs[0], output)
                self.progress.emit(100, f"仅 1 路输入,直接复制 → {output}")
                self.finished.emit(output)
                return

            cmd_inputs: list[str] = []
            for p in inputs:
                cmd_inputs += ["-i", p]

            joined = "".join(f"[{i}:a]" for i in range(n))
            if mode == "mix":
                raw_w = self.params.get("weights") or []
                weights: list[float] = []
                for w in raw_w[:n]:
                    try:
                        weights.append(float(w))
                    except (TypeError, ValueError):
                        weights.append(1.0)
                while len(weights) < n:
                    weights.append(1.0)
                weights_str = "|".join(f"{w:.4f}" for w in weights)
                # normalize=0 让权重显式生效,不被 amix 自动归一化抵消
                filter_complex = (
                    f"{joined}amix=inputs={n}:duration=longest"
                    f":normalize=0:weights={weights_str}[out]"
                )
                self.progress.emit(
                    0, f"开始 amix {n} 路 (weights={weights})...")
            else:
                # concat 模式:各路采样率/声道不同时 ffmpeg 会自己报错
                filter_complex = f"{joined}concat=n={n}:v=0:a=1[out]"
                self.progress.emit(0, f"开始 concat {n} 路...")

            cmd = [_ffmpeg_exe(),
                   "-hide_banner", "-loglevel", "error", "-nostats",
                   "-y", *cmd_inputs,
                   "-filter_complex", filter_complex,
                   "-map", "[out]", output]

            self.process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                encoding="utf-8",
                errors="replace",
            )

            tail: list[str] = []
            for line in iter(self.process.stdout.readline, ''):
                if self._cancelled:
                    try:
                        self.process.terminate()
                    except Exception:
                        pass
                    self.error.emit("已取消")
                    return
                if not line:
                    break
                line = line.rstrip()
                if line:
                    tail.append(line)
                    if len(tail) > 20:
                        tail.pop(0)

            self.process.wait()
            if self._cancelled:
                self.error.emit("已取消")
                return
            rc = self.process.returncode
            if rc != 0:
                msg = "\n".join(tail) if tail else f"ffmpeg 返回 {rc}"
                self.error.emit(f"ffmpeg 失败 (rc={rc}): {msg}")
                return
            if not os.path.isfile(output):
                self.error.emit(f"输出文件不存在: {output}")
                return

            self.progress.emit(100, "完成")
            self.finished.emit(output)

        except Exception as e:
            self.error.emit(f"{type(e).__name__}: {e}")
