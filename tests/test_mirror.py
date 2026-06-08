# test_mirror.py
import requests
import urllib.request
from urllib.error import URLError

mirror = "https://git.13ee.icu"
test_url = "https://github.com/git/git/archive/refs/heads/master.zip"
url = f"{mirror.rstrip('/')}/{test_url}"

print(f"测试URL: {url}")
print("=" * 60)

# 1. 测试 HEAD 请求（默认）
try:
    print("1. 测试 requests.head (默认):")
    r1 = requests.head(url, timeout=10)
    print(f"   状态码: {r1.status_code}")
except Exception as e:
    print(f"   失败: {type(e).__name__} - {e}")

print("-" * 60)

# 2. 测试 HEAD 请求 + 浏览器头
try:
    print("2. 测试 requests.head (浏览器头):")
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9',
        'Accept-Language': 'zh-CN,zh;q=0.9',
        'Referer': 'https://github.com/',
    }
    r2 = requests.head(url, timeout=10, headers=headers)
    print(f"   状态码: {r2.status_code}")
except Exception as e:
    print(f"   失败: {type(e).__name__} - {e}")

print("-" * 60)

# 3. 测试 GET 请求 + stream（只读头）
try:
    print("3. 测试 requests.get (stream=True):")
    r3 = requests.get(url, timeout=10, stream=True, headers=headers)
    print(f"   状态码: {r3.status_code}")
    r3.close()  # 关闭连接，不下载内容
except Exception as e:
    print(f"   失败: {type(e).__name__} - {e}")

print("-" * 60)

# 4. 测试 urllib（标准库）
try:
    print("4. 测试 urllib.request:")
    req = urllib.request.Request(url, method='HEAD', headers=headers)
    with urllib.request.urlopen(req, timeout=10) as resp:
        print(f"   状态码: {resp.status}")
except URLError as e:
    print(f"   失败: {e.reason}")
except Exception as e:
    print(f"   失败: {type(e).__name__} - {e}")

print("-" * 60)

# 5. 测试直接访问根路径（验证服务是否可访问）
try:
    print("5. 测试访问根路径 (验证服务可达性):")
    r5 = requests.get(mirror, timeout=10, headers=headers)
    print(f"   状态码: {r5.status_code}")
    print(f"   标题存在: {'GitHub 文件加速' in r5.text}")
except Exception as e:
    print(f"   失败: {type(e).__name__} - {e}")
