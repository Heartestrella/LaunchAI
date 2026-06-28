#!/usr/bin/env python3
"""
测试 CLIProxy API 脚本
"""

import requests
import json
import sys

# ============ 配置区域 ============
API_KEY = "sk-abc123xyz789"  # 替换成你的 API Key
API_BASE_URL = "http://localhost:8080"  # 替换成你的 API 地址
# =================================


def test_chat_completion():
    """测试聊天补全功能"""

    headers = {
        "Content-Type": "application/json",
        "X-API-Key": API_KEY,  # 如果程序使用这个 Header
        # "Authorization": f"Bearer {API_KEY}",  # 或者用这个 Header
    }

    # 根据你的 API 服务调整 endpoint
    # 常见格式：
    # - OpenAI 格式: /v1/chat/completions
    # - 代理格式: /api/chat 或 /completions

    payload = {
        "model": "gpt-3.5-turbo",  # 或 gpt-4, claude-3 等
        "messages": [
            {"role": "user", "content": "你好，请用一句话介绍自己"}
        ],
        "max_tokens": 100,
        "temperature": 0.7
    }

    try:
        # 尝试不同的 endpoint
        endpoints = [
            f"{API_BASE_URL}/v1/chat/completions",
            f"{API_BASE_URL}/api/chat",
            f"{API_BASE_URL}/completions",
        ]

        for endpoint in endpoints:
            print(f"尝试连接: {endpoint}")
            response = requests.post(
                endpoint,
                headers=headers,
                json=payload,
                timeout=30
            )

            if response.status_code == 200:
                print("=" * 50)
                print("✅ 请求成功！")
                print("=" * 50)
                print(json.dumps(response.json(), indent=2, ensure_ascii=False))
                return
            else:
                print(f"❌ 状态码 {response.status_code}: {response.text[:200]}")
                print("-" * 30)

        print("\n⚠️  所有 endpoint 都失败了，请检查 API 地址和配置")

    except requests.exceptions.ConnectionError:
        print(f"❌ 无法连接到 {API_BASE_URL}")
        print("请检查：")
        print("1. 服务是否已启动")
        print("2. 端口号是否正确")
        print("3. 防火墙是否放行")
    except Exception as e:
        print(f"❌ 发生错误: {e}")


def test_health_check():
    """测试健康检查接口（如果有）"""
    headers = {
        "X-API-Key": API_KEY,
    }

    endpoints = [
        f"{API_BASE_URL}/health",
        f"{API_BASE_URL}/ping",
        f"{API_BASE_URL}/api/health",
    ]

    for endpoint in endpoints:
        try:
            response = requests.get(endpoint, headers=headers, timeout=5)
            if response.status_code == 200:
                print(f"✅ 健康检查成功: {endpoint}")
                print(f"   响应: {response.text[:100]}")
                return True
        except:
            pass

    print("⚠️  健康检查端点未找到，跳过")
    return False


if __name__ == "__main__":
    print("=" * 50)
    print("CLIProxy API 测试脚本")
    print("=" * 50)
    print(f"API Key: {API_KEY[:10]}...")
    print(f"API 地址: {API_BASE_URL}")
    print("=" * 50)

    # 先检查服务是否存活
    test_health_check()
    print("\n")

    # 测试主要功能
    test_chat_completion()
