import subprocess
import sys
from logger import info, warning, debug, error
PYTHON_PATH = sys.executable


def get_torch_devices():
    """获取所有可用的 torch 设备

    Returns:
        dict: 设备字典，如 {"NVIDIA GeForce RTX 3060 Ti": "cuda:0", "cpu": "cpu"}
    """
    try:
        test_code = """
import torch
devices = {}
# 始终添加 CPU
devices["cpu"] = "cpu"
# 添加 GPU（如果有）
if torch.cuda.is_available():
    for i in range(torch.cuda.device_count()):
        gpu_name = torch.cuda.get_device_name(i)
        devices[gpu_name] = f"cuda:{i}"
print(devices)
"""
        result = subprocess.run(
            [PYTHON_PATH, "-c", test_code],
            capture_output=True,
            text=True,
            timeout=10,
            encoding='utf-8',
            errors='replace'
        )

        if result.returncode == 0 and result.stdout.strip():
            import ast
            devices = ast.literal_eval(result.stdout.strip())
            return devices
        else:
            return {"cpu": "cpu"}

    except Exception as e:
        error(f"获取设备失败: {e}")
        return {"cpu": "cpu"}


devices = get_torch_devices()
info(devices)
