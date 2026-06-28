# workers/gptsovits_worker.py

import os
import sys
import subprocess
import time
from pathlib import Path

from PyQt6.QtCore import QThread, pyqtSignal


_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_INFER_RUNNER = os.path.join(_THIS_DIR, "_gptsovits_runner.py")


def _html(text, color=None, bold=False):
    if not color and not bold:
        return text
    style = []
    if color:
        style.append(f"color:{color}")
    if bold:
        style.append("font-weight:bold")
    return f'<span style="{";".join(style)}">{text}</span>'


class GPTSoVITSInferWorker(QThread):
    """GPT-SoVITS TTS 推理工作线程"""

    progress = pyqtSignal(int, str)
    output = pyqtSignal(str)
    finished = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self, params: dict):
        """
        Args:
            params: 参数字典，包含:
                - gpt_model:        GPT 权重 .ckpt 路径
                - sovits_model:     SoVITS 权重 .pth 路径
                - ref_audio:        主参考音频 wav 路径(3~10s)
                - aux_ref_audios:   可选 list[str]，辅助参考音频路径列表，
                                    透传给 inference_webui.get_tts_wav 的 inp_refs
                                    做音色融合 (v3/v4 模型会被静默忽略)
                - ref_text:         参考音频文本
                - ref_language:     参考语种 (中文/英文/日文/粤语/韩文/中英混合/日英混合/粤英混合/韩英混合/多语种混合/多语种混合(粤语))
                - target_text:      目标文本
                - target_language:  目标语种
                - output:           输出 wav 路径
                - how_to_cut:       切分策略
                - top_k / top_p / temperature / speed
                - device:           "cuda:0" / "cpu"
        """
        super().__init__()
        self.params = params
        self.process = None
        self._is_cancelled = False

    # ── Qt-free 调用核(GUI 的 run() 与 HTTP API 共用) ──────────────────
    @staticmethod
    def build_argv(params: dict) -> list:
        """构建 GPT-SoVITS runner 命令(指向 _gptsovits_runner.py)。GUI / API 共用。"""
        cmd = [sys.executable, _INFER_RUNNER,
               "--gpt-model",       params.get("gpt_model", ""),
               "--sovits-model",    params.get("sovits_model", ""),
               "--ref-audio",       params.get("ref_audio", ""),
               "--ref-text",        params.get("ref_text", ""),
               "--ref-language",    params.get("ref_language", "中文"),
               "--target-text",     params.get("target_text", ""),
               "--target-language", params.get("target_language", "中文"),
               "--output",          params.get("output", ""),
               "--how-to-cut",      params.get("how_to_cut", "不切"),
               "--top-k",           str(int(params.get("top_k", 15))),
               "--top-p",           str(float(params.get("top_p", 1.0))),
               "--temperature",     str(float(params.get("temperature", 1.0))),
               "--speed",           str(float(params.get("speed", 1.0))),
               "--device",          params.get("device", "cuda:0")]
        for aux in list(params.get("aux_ref_audios", []) or []):
            cmd += ["--aux-ref-audio", aux]
        return cmd

    def run(self):
        try:
            gpt_model = self.params.get("gpt_model", "")
            sovits_model = self.params.get("sovits_model", "")
            ref_audio = self.params.get("ref_audio", "")
            aux_ref_audios = list(self.params.get("aux_ref_audios", []) or [])
            ref_text = self.params.get("ref_text", "")
            target_text = self.params.get("target_text", "")
            output_path = self.params.get("output", "")

            for label, path in (("GPT 模型", gpt_model),
                                ("SoVITS 模型", sovits_model),
                                ("参考音频", ref_audio)):
                if not path or not os.path.exists(path):
                    self.error.emit(f"{label}不存在: {path}")
                    return
            for path in aux_ref_audios:
                if not path or not os.path.exists(path):
                    self.error.emit(f"辅助参考音频不存在: {path}")
                    return
            if not ref_text.strip():
                self.error.emit("参考文本为空")
                return
            if not target_text.strip():
                self.error.emit("目标文本为空")
                return
            if not output_path:
                self.error.emit("未指定输出路径")
                return

            cmd = self.build_argv(self.params)

            self.progress.emit(0, "准备启动 GPT-SoVITS...")
            self.output.emit(_html(
                f"GPT 模型: {Path(gpt_model).name}", "#888888"))
            self.output.emit(_html(
                f"SoVITS 模型: {Path(sovits_model).name}", "#888888"))
            self.output.emit(_html(
                f"参考音频: {Path(ref_audio).name}", "#888888"))
            if aux_ref_audios:
                self.output.emit(_html(
                    f"辅助参考音频: {len(aux_ref_audios)} 个（音色融合）",
                    "#888888"))
            self.output.emit(_html(
                f"目标语种: {self.params.get('target_language', '中文')} · "
                f"切分: {self.params.get('how_to_cut', '不切')}", "#888888"))
            self.output.emit(_html(
                f"输出文件: {output_path}", "#888888"))
            self.output.emit(_html(
                f"执行命令: {' '.join(cmd)}", "#888888"))

            self.process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                encoding="utf-8",
                errors="replace",
            )

            chunk_count = 0
            for line in iter(self.process.stdout.readline, ""):
                if self._is_cancelled:
                    self.process.terminate()
                    self.output.emit(_html("用户取消了合成任务", "#FF9800"))
                    return
                if not line:
                    break
                line = line.rstrip("\r\n")
                if not line:
                    continue

                # 进度估算：runner 每生成一块发一条 "已生成 N 块"
                if "已生成" in line and "块" in line:
                    chunk_count += 1
                    # 让进度条动起来：每块涨 8%（封顶 95%，等收尾再到 100）
                    pct = min(20 + chunk_count * 8, 95)
                    self.progress.emit(pct, f"生成中（第 {chunk_count} 块）")
                elif "加载 GPT 模型" in line:
                    self.progress.emit(5, "加载 GPT 模型...")
                elif "加载 SoVITS 模型" in line:
                    self.progress.emit(10, "加载 SoVITS 模型...")
                elif "开始合成语音" in line:
                    self.progress.emit(20, "开始合成...")

                self._parse_output(line)

            self.process.wait()

            if self._is_cancelled:
                return

            if self.process.returncode == 0:
                self.progress.emit(100, "完成！")
                self.output.emit(_html(
                    f"✅ 合成完成 → {output_path}", "#4CAF50"))
                self.finished.emit(output_path)
            else:
                self.error.emit(
                    f"合成失败 (返回码: {self.process.returncode})")

        except Exception as e:
            self.error.emit(f"合成过程发生异常: {str(e)}")

    def _parse_output(self, line: str):
        if line.startswith("[runner][ERROR]") or "Traceback" in line:
            self.output.emit(_html(line, "#F44336"))
        elif line.startswith("[runner]"):
            self.output.emit(_html(line, "#9C27B0"))
        elif "WARNING" in line or "warning" in line.lower():
            self.output.emit(_html(line, "#FF9800"))
        elif "loading" in line.lower() or "load" in line.lower():
            self.output.emit(_html(line, "#9C27B0"))
        else:
            self.output.emit(_html(line, "#CCCCCC"))

    def cancel(self):
        self._is_cancelled = True
        if self.process:
            self.process.terminate()
            time.sleep(0.5)
            if self.process.poll() is None:
                self.process.kill()
