# LaunchAI 依赖版本锁
#
# 这一目录下的所有文件都是从 `py311/` 嵌入式 Python 当前安装状态生成的
# 顶层包版本快照。供 `workers/pip_worker.py` 在装新机器时使用,
# 或在打整合包前作为"已验证可跑"的版本组合。
#
# 跨工具共享版本(必须四个 lock 同步)
#   whisper.txt / audiocraft.txt / applio.txt / gptsovits.txt 这四个文件
#   都涉及 numpy / numba / llvmlite,因为 whisper 走 numba,而 numba 0.63
#   的 numpy 上限是 2.3。任何一个 lock 把 numpy 锁到 2.4+,装完之后 whisper
#   都会 ImportError: Numba needs NumPy 2.3 or less。
#   目前统一锁版:
#     numpy==1.26.4 / numba==0.63.1 / llvmlite==0.46.0
#   改任一文件的这三个版本,必须把其它三个一起改。
#
# 文件清单
#   full-freeze.txt        py311 完整 pip freeze (255 个包,传递依赖也在内)
#                          作为兜底快照,不直接喂给 pip。注意里面 torch/demucs/
#                          audiocraft 是 file:// 本地引用,换机器前需要替换。
#                          torch 三件套不在本目录单独 lock —— 它由设置页的
#                          PyTorch 安装卡通过 is_torch=True 直接走阿里云
#                          wheel 镜像,不经过 lock 流程。
#   demucs.txt             facebookresearch/demucs (main) + 其 requirements_minimal
#   whisper.txt            openai-whisper
#   ultralytics.txt        YOLO (Ultralytics)
#   applio.txt             IAHispano/Applio 3.6.2 (RVC 前端) 的全部顶层依赖
#   gptsovits.txt          RVC-Boss/GPT-SoVITS (main) + pip_worker 补装的三个
#                          (pyopenjtalk-plus / opencc-python-reimplemented /
#                           pytorch-lightning)
#   audiocraft.txt         facebookresearch/audiocraft (main) + 单装的
#                          av / xformers
#
# 未生成 lock 的工具 (py311 里尚未安装,无法快照,请先装一次后重跑此流程)
#   Real-ESRGAN           需要 basicsr / facexlib / gfpgan 三件
#   IOPaint               需要 iopaint
#
# 工具仓库本身的 commit / tag 不在这里 lock —— 那是 `workers/pip_worker.py:23`
# 的 fork_map 的责任。本目录只锁 pip 包版本。
#
# 用法
#   方式 A (per-tool 严格复现):
#     py311/python.exe -m pip install -r locks/<tool>.txt
#   方式 B (完整还原 py311 当前状态,需先把 file:// 行改成可解析的形式):
#     py311/python.exe -m pip install -r locks/full-freeze.txt
#
# 重新生成 (在干净安装完所有工具后):
#   py311/python.exe -m pip freeze > locks/full-freeze.txt
#   并手动同步各 per-tool 文件
