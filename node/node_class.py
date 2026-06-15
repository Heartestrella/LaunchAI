from tempfile import NamedTemporaryFile


class audio:
    def __init__(self, file_path: str = None, binary: bytes = None):
        self.file_path = file_path
        self.audio_binary = binary

    def get_file_path(self) -> str:
        if self.file_path:
            return self.file_path
        elif self.audio_binary:
            with NamedTemporaryFile(delete=False, suffix=".tmp") as tmp_file:
                tmp_file.write(self.audio_binary)
                self.file_path = tmp_file.name
            return self.file_path
        else:
            raise ValueError("没有有效的音频数据")

    def get_audio_binary(self) -> bytes:
        if self.audio_binary:
            return self.audio_binary
        elif self.file_path:
            with open(self.file_path, "rb") as f:
                self.audio_binary = f.read()
            return self.audio_binary
        else:
            raise ValueError("没有有效的音频数据")


class image:
    def __init__(self, file_path: str = None, binary: bytes = None):
        self.file_path = file_path
        self.image_binary = binary

    def get_file_path(self) -> str:
        if self.file_path:
            return self.file_path
        elif self.image_binary:
            with NamedTemporaryFile(delete=False, suffix=".tmp") as tmp_file:
                tmp_file.write(self.image_binary)
                self.file_path = tmp_file.name
            return self.file_path
        else:
            raise ValueError("没有有效的图像数据")

    def get_image_binary(self) -> bytes:
        if self.image_binary:
            return self.image_binary
        elif self.file_path:
            with open(self.file_path, "rb") as f:
                self.image_binary = f.read()
            return self.image_binary
        else:
            raise ValueError("没有有效的图像数据")
