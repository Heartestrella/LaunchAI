import os
import traceback
from pathlib import Path

from PyQt6.QtCore import QThread, pyqtSignal

# 正确的导入方式
from demucs.api import Separator, save_audio


class DemucsWorker(QThread):
    progress = pyqtSignal(int, str)   # 百分比，状态文字
    finished = pyqtSignal(str)        # 输出目录
    error = pyqtSignal(str)

    def __init__(self, params):
        super().__init__()
        self.params = params
        self._is_cancelled = False

    def cancel(self):
        self._is_cancelled = True

    def run(self):
        try:
            input_path = self.params['input']
            output_dir = self.params['output']
            model_name = self.params['model']          # 'htdemucs', 'mdx' 等
            device = self.params['device']             # 'cuda', 'cpu'
            shifts = self.params['shifts']
            segment = self.params['segment']
            overlap = self.params['overlap']
            selected_tracks = [
                t for t, sel in self.params['tracks'].items() if sel
            ]

            if not selected_tracks:
                self.error.emit("请至少选择一个输出音轨")
                return

            # 创建 Separator 实例
            # callback 参数可用于进度跟踪
            def progress_callback(info):
                # info 字典包含: model_idx_in_bag, shift_idx, segment_offset,
                # state, audio_length, models
                if info.get('state') == 'end':
                    # 计算粗略进度 (基于子模型数量和段偏移)
                    total_models = info.get('models', 1)
                    model_idx = info.get('model_idx_in_bag', 0)
                    # 这里可以估算进度，但 demucs 本身会显示 tqdm 进度条
                    pass

            self.progress.emit(5, "正在加载模型...")

            separator = Separator(
                model=model_name,
                device=device,
                shifts=shifts,
                segment=segment if segment > 0 else None,  # None 表示自动
                overlap=overlap,
                jobs=0,                     # 0 表示自动选择
                progress=True,              # 显示 tqdm 进度条
                callback=progress_callback  # 可选，用于精细控制
            )

            if self._is_cancelled:
                return

            self.progress.emit(10, "正在分离音频...")

            # 执行分离
            # separate_audio_file 返回 (原始音频, 分离结果字典)
            origin, separated = separator.separate_audio_file(input_path)

            if self._is_cancelled:
                return

            # 创建输出目录
            os.makedirs(output_dir, exist_ok=True)

            # 获取原始文件名（不含扩展名）
            base_name = Path(input_path).stem
            saved_files = []

            # 保存用户选中的音轨
            for track in selected_tracks:
                if track in separated:
                    audio = separated[track]
                    output_path = os.path.join(
                        output_dir, f"{base_name}_{track}.wav")
                    save_audio(
                        audio,
                        output_path,
                        samplerate=separator.samplerate,
                        clip='rescale'  # 防止削波
                    )
                    saved_files.append(output_path)

            if not saved_files:
                self.error.emit("未找到任何选中的音轨文件")
                return

            self.progress.emit(100, "完成！")
            self.finished.emit(output_dir)

        except Exception as e:
            self.error.emit(f"分离失败: {str(e)}\n{traceback.format_exc()}")
