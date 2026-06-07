# import torch

# # 1. 打印 PyTorch 版本
# print(f"PyTorch 版本: {torch.__version__}")

# # 2. 核心检测：CUDA 是否可用
# print(f"CUDA 是否可用: {torch.cuda.is_available()}")

# # 3. 如果 CUDA 可用，检查 PyTorch 自带的 CUDA 版本
# if torch.cuda.is_available():
#     print(f"PyTorch 绑定的 CUDA 版本: {torch.version.cuda}")


import sys
import subprocess

PYTHON_PATH = sys.executable


def test_torch():
    """测试 PyTorch 安装是否成功"""
    test_code = """
import torch
print(f"PyTorch 版本: {torch.__version__}")
print(f"CUDA 可用: {torch.cuda.is_available()}")
if torch.cuda.is_available():
    print(f"CUDA 版本: {torch.version.cuda}")
    print(f"GPU 型号: {torch.cuda.get_device_name(0)}")
    print(f"GPU 数量: {torch.cuda.device_count()}")
else:
    print("CUDA 不可用，使用 CPU 模式")
"""
    try:
        result = subprocess.run(
            [PYTHON_PATH, "-c", test_code],
            capture_output=True,
            text=True,
            timeout=30,
            encoding='utf-8',  # 指定 UTF-8 编码
            errors='replace'   # 遇到无法解码的字符用 ? 替换
        )

        if result.stdout:
            print(result.stdout.strip())
        if result.stderr:
            print("错误:", result.stderr.strip())

    except subprocess.TimeoutExpired:
        print("测试超时")
    except Exception as e:
        print(f"测试失败: {e}")


if __name__ == "__main__":
    test_torch()
