"""
utils/_netease_weapi.py
~~~~~~~~~~~~~~~~~~~~~~~
内嵌 minimal 网易云音乐 weapi 加密 + 「歌曲直链解析」端点封装。

为什么内嵌
==========
PyPI 上的 pyncm 当前不可达，且依赖一个第三方 fork 引入额外风险。
weapi 协议本身长期稳定，开源世界有大量相同算法的实现（Binaryify/
NeteaseCloudMusicApi、jixunmoe/netease-cloud-music-api、各 Python
SDK 等等）。这里按公开协议解读 + ``pycryptodome`` AES 原语手写最小
实现，仅供 LaunchAI 素材库 / music_fetch 节点自用，不对外暴露。

协议要点
========
1. payload 先用固定 key ``0CoJUm6Qyw8W8jud`` 走 AES-128-CBC，IV
   ``0102030405060708``，PKCS7 padding → base64。
2. 生成 16 字符 ascii 随机串 secKey，再用 secKey 做一遍同样的 AES，
   结果是请求体的 ``params``。
3. secKey 字符串先反转，按 big-endian 整数计算 ``m^e mod n``，左侧
   补 0 到 256 hex chars，就是请求体的 ``encSecKey``。
4. 反 hex 大数其实就是 RSA 公钥加密 (e=65537, modulus 见下)，
   pycryptodome 在这一步只用来做 AES；RSA 直接 ``pow(m,e,n)`` 即可。

免责
====
该 API 直接对接网易云公开 web 接口，遵循 web 客户端协议；其请求频率、
返回内容、付费曲目封锁策略均由网易云控制。LaunchAI 项目仅提供协议
实现，不对 API 的可用性、合法性、返回结果及由此产生的任何后果负责。
"""

from __future__ import annotations

import base64
import json
import secrets
from typing import Any

import requests
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad


# ── 协议常量 ──────────────────────────────────────────────────────────

_PRESET_KEY = b"0CoJUm6Qyw8W8jud"
_IV = b"0102030405060708"

# 网易云 web 客户端硬编码的 RSA 公钥(modulus, exponent=65537)
_PUBKEY_N = int(
    "e0b509f6259df8642dbc35662901477d"
    "f22677ec152b5ff68ace615bb7b72515"
    "2b3ab17a876aea8a5aa76d2e417629ec"
    "4ee341f56135fccf695280104e0312ec"
    "bda92557c93870114af6c9d05c4f7f0c"
    "3685b7a46bee255932575cce10b424d8"
    "13cfe4875d3e82047b97ddef52741d54"
    "6b8e289dc6935b3ece0462db0a22b8e7",
    16,
)
_PUBKEY_E = 0x10001

_SECKEY_CHARSET = (
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
)

_PLAYER_URL_API = "https://music.163.com/weapi/song/enhance/player/url/v1"
_LYRIC_API = "https://music.163.com/api/song/lyric"
_QR_UNIKEY_API = "https://music.163.com/weapi/login/qrcode/unikey"
_QR_POLL_API = "https://music.163.com/weapi/login/qrcode/client/login"
_QR_URL_TMPL = "https://music.163.com/login?codekey={unikey}"
_USER_INFO_API = "https://music.163.com/api/nuser/account/get"

# 模拟 PC 客户端的 Cookie 必带 否则有时返回 400
_PC_COOKIE = "os=pc; appver=2.7.1.198277; osver=Microsoft-Windows-10"


def _merged_cookie() -> str:
    """合并匿名 PC 协议头 + 登录态 cookie (configs/config.json::materials.netease_cookie)

    用户已登录时 MUSIC_U 会让 weapi 解到 VIP / 付费曲目的直链
    未登录时退化到匿名仅能取免费曲目
    """
    try:
        # 延迟引入避免循环依赖 / 启动期触发 config 读
        from utils.configer import get_field
        extra = (get_field("materials.netease_cookie", "") or "").strip()
    except Exception:
        extra = ""
    if extra:
        return f"{_PC_COOKIE}; {extra}"
    return _PC_COOKIE


# ── 加密原语 ──────────────────────────────────────────────────────────

def _aes_cbc(plain: bytes, key: bytes) -> bytes:
    return AES.new(key, AES.MODE_CBC, _IV).encrypt(pad(plain, AES.block_size))


def _rsa_enc(reversed_seckey: bytes) -> str:
    """RSA: m^e mod n hex 输出 256 chars (零填充)"""
    m = int.from_bytes(reversed_seckey, "big")
    c = pow(m, _PUBKEY_E, _PUBKEY_N)
    return f"{c:0256x}"


def _weapi(payload: dict) -> dict:
    """把任意 JSON 可序列化的 payload 转成 {params, encSecKey}"""
    text = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    first = base64.b64encode(_aes_cbc(text, _PRESET_KEY))

    seckey = "".join(secrets.choice(_SECKEY_CHARSET) for _ in range(16))
    seckey_b = seckey.encode("ascii")
    second = base64.b64encode(_aes_cbc(first, seckey_b))

    reversed_seckey = seckey[::-1].encode("ascii")
    return {
        "params":    second.decode("ascii"),
        "encSecKey": _rsa_enc(reversed_seckey),
    }


# ── 公开 API: 直链解析 ───────────────────────────────────────────────

def fetch_song_url(song_id: int,
                   level: str = "exhigh",
                   *,
                   user_agent: str | None = None,
                   proxy: str | None = None,
                   timeout: int = 15) -> dict[str, Any]:
    """调 ``/weapi/song/enhance/player/url/v1`` 拿单曲直链

    Args:
        song_id: 网易云内部 song id
        level:   音质档位 ``standard`` / ``higher`` / ``exhigh`` /
                 ``lossless`` / ``hires`` —— 未登录账户能拿到的上限
                 一般是 ``exhigh`` (320 kbps mp3)
        user_agent: 自定义 UA 不填走默认 Chrome
        proxy:   形如 ``http://host:port`` 不填走系统直连
        timeout: 单次请求超时秒

    Returns:
        网易云返回 data[0] 字典 —— 包含 ``url``、``br``、``size`` 等

    Raises:
        RuntimeError:  网易云返回非 200 或 url 为 null（VIP / 下架）
        requests.RequestException: 网络层异常
    """
    payload = {
        "ids":        json.dumps([int(song_id)]),
        "level":      level,
        "encodeType": "mp3" if level != "lossless" else "flac",
        "csrf_token": "",
    }
    body = _weapi(payload)

    headers = {
        "User-Agent": user_agent or (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Referer": "https://music.163.com/",
        "Cookie":  _merged_cookie(),
        "Content-Type": "application/x-www-form-urlencoded",
    }
    proxies = ({"http": proxy, "https": proxy} if proxy else None)

    r = requests.post(_PLAYER_URL_API, data=body,
                      headers=headers, proxies=proxies, timeout=timeout)
    r.raise_for_status()
    js = r.json() or {}
    if js.get("code") != 200:
        raise RuntimeError(
            f"weapi 返回 code={js.get('code')} message={js.get('message','')}"
        )
    data = js.get("data") or []
    if not data:
        raise RuntimeError("weapi 返回 data 字段为空")
    entry = data[0] or {}
    if not entry.get("url"):
        # url=null 通常是 VIP / 已下架 / 区域限制
        raise RuntimeError(
            f"该曲目无可用直链 (可能是 VIP / 付费 / 已下架) "
            f"weapi.code={entry.get('code')} fee={entry.get('fee')}"
        )
    return entry


def fetch_lyrics(song_id: int,
                 *,
                 user_agent: str | None = None,
                 proxy: str | None = None,
                 timeout: int = 10) -> dict[str, str]:
    """调非加密 ``/api/song/lyric`` 端点拿歌词

    Returns:
        ``{"lrc": str, "tlyric": str}`` 任一缺失为 ""
        请求出错或歌曲无歌词 (纯音乐) 也返回 ``{"lrc": "", "tlyric": ""}``
        而不是 raise —— 歌词只是附赠 不应阻断主流程
    """
    out = {"lrc": "", "tlyric": ""}
    headers = {
        "User-Agent": user_agent or (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Referer": "https://music.163.com/",
    }
    proxies = ({"http": proxy, "https": proxy} if proxy else None)
    try:
        r = requests.get(_LYRIC_API,
                         params={"id": int(song_id), "lv": 1,
                                 "kv": 1, "tv": -1},
                         headers=headers, proxies=proxies, timeout=timeout)
        r.raise_for_status()
        js = r.json() or {}
        if js.get("code") != 200:
            return out
        out["lrc"] = ((js.get("lrc") or {}).get("lyric") or "").strip()
        out["tlyric"] = ((js.get("tlyric") or {}).get("lyric") or "").strip()
    except Exception:
        # 歌词失败永不阻断主流程
        pass
    return out


# ── QR 登录 ─────────────────────────────────────────────────────

def qr_create_unikey(*, user_agent: str | None = None,
                     proxy: str | None = None,
                     timeout: int = 10) -> tuple[str, str]:
    """生成 NetEase 登录 unikey

    Returns:
        ``(unikey, qr_url)`` qr_url 编码到二维码图就是登录二维码
        手机网易云 App 扫码后会跳出"是否登录"确认
    """
    headers = {
        "User-Agent": user_agent or (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Referer": "https://music.163.com/",
        "Cookie":  _merged_cookie(),
        "Content-Type": "application/x-www-form-urlencoded",
    }
    body = _weapi({"type": 1, "csrf_token": ""})
    proxies = ({"http": proxy, "https": proxy} if proxy else None)
    r = requests.post(_QR_UNIKEY_API, data=body,
                      headers=headers, proxies=proxies, timeout=timeout)
    r.raise_for_status()
    js = r.json() or {}
    if js.get("code") != 200:
        raise RuntimeError(
            f"NetEase QR unikey 接口返回 code={js.get('code')} {js.get('message','')}"
        )
    unikey = js.get("unikey") or ""
    if not unikey:
        raise RuntimeError("NetEase QR unikey 返回字段缺失")
    return unikey, _QR_URL_TMPL.format(unikey=unikey)


def qr_poll(unikey: str,
            *,
            user_agent: str | None = None,
            proxy: str | None = None,
            timeout: int = 10) -> tuple[int, str]:
    """轮询 NetEase QR 登录状态

    Returns:
        ``(code, cookie_string)``
        code:  800 = 已过期 重新生成
               801 = 等待扫码
               802 = 已扫码 待手机端确认
               803 = 已登录 cookie_string 是组装好的 ``MUSIC_U=...; __csrf=...``
    """
    headers = {
        "User-Agent": user_agent or (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Referer": "https://music.163.com/",
        "Cookie":  _merged_cookie(),
        "Content-Type": "application/x-www-form-urlencoded",
    }
    body = _weapi({"type": 1, "key": unikey, "csrf_token": ""})
    proxies = ({"http": proxy, "https": proxy} if proxy else None)
    r = requests.post(_QR_POLL_API, data=body,
                      headers=headers, proxies=proxies, timeout=timeout)
    r.raise_for_status()
    js = r.json() or {}
    code = int(js.get("code", 0))
    cookie_str = ""
    if code == 803:
        # 把 response Set-Cookie 拼成 client 端用的格式 只保留关键字段
        pairs = []
        for c in r.cookies:
            if c.name in ("MUSIC_U", "MUSIC_A", "__csrf", "MUSIC_R", "MUSIC_SNS"):
                pairs.append(f"{c.name}={c.value}")
        cookie_str = "; ".join(pairs)
    return code, cookie_str


def get_user_info(*, user_agent: str | None = None,
                  proxy: str | None = None,
                  timeout: int = 8) -> dict[str, object] | None:
    """读取当前 cookie 对应账号的基本信息 未登录返回 None

    Returns:
        ``{"userId": int, "nickname": str, "vipType": int}`` 或 None
    """
    cookie = _merged_cookie()
    # 没塞用户登录态时返回 None 而不是发请求(省一次失败)
    if "MUSIC_U=" not in cookie:
        return None
    headers = {
        "User-Agent": user_agent or (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Referer": "https://music.163.com/",
        "Cookie":  cookie,
    }
    proxies = ({"http": proxy, "https": proxy} if proxy else None)
    try:
        r = requests.get(_USER_INFO_API, headers=headers,
                         proxies=proxies, timeout=timeout)
        r.raise_for_status()
        js = r.json() or {}
        if js.get("code") != 200:
            return None
        prof = js.get("profile") or {}
        if not prof:
            return None
        return {
            "userId":   prof.get("userId"),
            "nickname": prof.get("nickname") or "",
            "vipType":  prof.get("vipType", 0),
        }
    except Exception:
        return None


def merge_lrc_with_translation(lrc: str, tlyric: str) -> str:
    """把翻译版逐行并到原 LRC 后面 时间戳对齐的行末追加 [t]…[/t]

    没翻译的行原样保留 翻译里多出来的(版本号/作词等元数据) 不并

    输出仍是合法 LRC 大多数播放器(网易云/QQ/foobar2000)兼容
    """
    if not lrc:
        return lrc
    if not tlyric:
        return lrc
    import re as _re
    ts_re = _re.compile(r"\[(\d{2}:\d{2}\.\d{1,3})\]")
    tmap: dict[str, str] = {}
    for line in tlyric.splitlines():
        m = ts_re.search(line)
        if not m:
            continue
        ts = m.group(1)
        text = ts_re.sub("", line).strip()
        if text:
            tmap[ts] = text

    out_lines = []
    for line in lrc.splitlines():
        m = ts_re.search(line)
        if m and m.group(1) in tmap:
            out_lines.append(f"{line.rstrip()} 「{tmap[m.group(1)]}」")
        else:
            out_lines.append(line)
    return "\n".join(out_lines)
