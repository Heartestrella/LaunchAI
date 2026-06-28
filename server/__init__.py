"""server/ —— LaunchAI 的 HTTP API 服务(FastAPI),把各工具开放成接口。

设置页的「API 服务」卡片通过 server.api_server.ApiServerManager 启停;
请求落到 server.api_server,实际调用复用 server.tool_runners(对接现有 worker)。
"""
