"""
utils/material_fetcher.py
~~~~~~~~~~~~~~~~~~~~~~~~~
内置 B站 / 网易云音乐 轻量化素材抓取。

特性
====
- **无 cookie / 无 token / 无登录** 走平台公开 Web 接口
- **默认浏览器 UA + Referer** 必要时可在 ``configs/config.json`` 的
  ``materials.user_agent`` / ``materials.proxy`` 覆盖
- **网易云**：``/api/search/get/web`` + ``/song/media/outer/url``
  仅 128 kbps mp3 VIP 曲目走不通(此时 raise 明确错误)
- **B 站**：``/x/web-interface/search/type`` + ``/x/player/playurl``
  fnval=16 取 DASH 音频流(m4a)

对外 API（节点和子页面共用）::

    search_netease(keyword)            -> list[dict]
    download_netease(song_id, out_dir) -> str (本地路径)
    search_bilibili(keyword)           -> list[dict]
    download_bilibili(bvid, out_dir)   -> str
    fetch_first_match(keyword, source) -> str  # 节点用 一键搜+下

所有下载函数支持 ``progress_cb(percent:int, status:str)`` 与
``cancel_cb() -> bool`` 二选一注入 cancel_cb 返回 True 时 raise CancelledError

免责声明: 详见素材库子页面首启对话框 此模块仅作技术实现 不对使用
后果负责
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import time
from typing import Callable, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from utils.configer import get_field


# ── 常量 ──────────────────────────────────────────────────────────────

_DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

_NETEASE_SEARCH = "https://music.163.com/api/search/get/web"
_NETEASE_OUTER = "https://music.163.com/song/media/outer/url?id={sid}.mp3"

_BILI_SEARCH = "https://api.bilibili.com/x/web-interface/search/type"
_BILI_VIEW = "https://api.bilibili.com/x/web-interface/view"
_BILI_PLAYURL = "https://api.bilibili.com/x/player/playurl"
_BILI_QR_GENERATE = "https://passport.bilibili.com/x/passport-login/web/qrcode/generate"
_BILI_QR_POLL = "https://passport.bilibili.com/x/passport-login/web/qrcode/poll"

_INSTRUMENTAL_TAGS = (
    "伴奏", "纯音乐", "(instrumental)", "[instrumental]",
    "inst.", "消音", "off vocal", "karaoke", "卡拉",
)

_TIMEOUT = 15
_CHUNK = 64 * 1024


class CancelledError(RuntimeError):
    """用户在下载途中主动取消"""


# ── Session 工厂 ──────────────────────────────────────────────────────

def _platform_from_referer(referer: str) -> str:
    if "163.com" in referer or "music.163" in referer:
        return "netease"
    if "bilibili.com" in referer:
        return "bilibili"
    return ""


def _user_cookie(platform: str) -> str:
    """读取保存的登录 cookie 字符串 空串表示未登录"""
    if not platform:
        return ""
    return (get_field(f"materials.{platform}_cookie", "") or "").strip()


def _make_session(referer: str) -> requests.Session:
    """每次调用都新建 避免跨平台 Referer 串台

    会自动注入对应平台的登录 cookie (如果用户登录了)
    """
    s = requests.Session()
    ua = (get_field("materials.user_agent", "") or "").strip() or _DEFAULT_UA
    s.headers.update({
        "User-Agent": ua,
        "Accept": "*/*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Referer": referer,
    })
    # 注入登录 cookie (匿名时为空)
    platform = _platform_from_referer(referer)
    user_cookie = _user_cookie(platform)
    if user_cookie:
        s.headers["Cookie"] = user_cookie

    proxy = (get_field("materials.proxy", "") or "").strip()
    if proxy:
        s.proxies = {"http": proxy, "https": proxy}

    retry = Retry(
        total=3, backoff_factor=0.8,
        status_forcelist=[500, 502, 503, 504],
        allowed_methods=frozenset(["GET", "HEAD", "POST"]),
    )
    adapter = HTTPAdapter(max_retries=retry)
    s.mount("http://", adapter)
    s.mount("https://", adapter)
    return s


# ── 工具 ──────────────────────────────────────────────────────────────

def _is_instrumental(title: str) -> bool:
    t = (title or "").lower()
    return any(tag in t for tag in _INSTRUMENTAL_TAGS)


_INVALID_FN_CHARS = re.compile(r'[\\/:*?"<>|\r\n\t]+')


def _safe_filename(name: str, max_len: int = 80) -> str:
    cleaned = _INVALID_FN_CHARS.sub("_", name or "").strip(" .")
    if not cleaned:
        cleaned = f"track_{int(time.time())}"
    if len(cleaned) > max_len:
        cleaned = cleaned[:max_len].rstrip()
    return cleaned


def _streamed_download(
    session: requests.Session,
    url: str,
    out_path: str,
    progress_cb: Optional[Callable[[int, str], None]] = None,
    cancel_cb: Optional[Callable[[], bool]] = None,
    label: str = "下载中",
) -> str:
    """通用流式下载 写到 out_path 完成返回 out_path"""
    with session.get(url, stream=True, timeout=_TIMEOUT, allow_redirects=True) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length", 0))

        if total and total < 4096:
            # 公开 CDN 拦截页通常很小 ( 网易云 VIP 拦截 / B站 403 兜底页 )
            raise RuntimeError(
                f"下载失败 服务端只返回了 {total} 字节 可能是 VIP/付费内容 "
                f"或抓取被拒绝"
            )

        downloaded = 0
        last_pct = -1
        last_ts = time.time()
        last_dl = 0

        tmp_path = out_path + ".part"
        try:
            with open(tmp_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=_CHUNK):
                    if cancel_cb and cancel_cb():
                        raise CancelledError("用户取消下载")
                    if not chunk:
                        continue
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total > 0 and progress_cb:
                        pct = int(downloaded * 100 / total)
                        now = time.time()
                        if pct != last_pct or now - last_ts >= 0.5:
                            speed = (downloaded - last_dl) / max(now - last_ts, 0.001) / 1024
                            mb_dl = downloaded / 1048576
                            mb_tot = total / 1048576
                            progress_cb(
                                pct,
                                f"{label} {pct}% ({mb_dl:.1f}/{mb_tot:.1f} MB) {speed:.0f} KB/s",
                            )
                            last_pct = pct
                            last_ts = now
                            last_dl = downloaded
        except BaseException:
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except OSError:
                pass
            raise

        os.replace(tmp_path, out_path)
        if progress_cb:
            progress_cb(100, f"{label} 完成")
        return out_path


# ══════════════════════════════════════════════════════════════════════
#  网易云音乐
# ══════════════════════════════════════════════════════════════════════

def search_netease(keyword: str, limit: int = 20,
                   drop_instrumental: bool = True) -> list[dict]:
    """搜索网易云歌曲 返回 [{id, name, artists, album, duration_ms, ...}]"""
    keyword = (keyword or "").strip()
    if not keyword:
        return []
    s = _make_session("https://music.163.com/")
    data = {"s": keyword, "type": "1", "offset": "0",
            "total": "true", "limit": str(max(1, int(limit)))}
    r = s.post(_NETEASE_SEARCH, data=data, timeout=_TIMEOUT)
    r.raise_for_status()
    js = r.json() or {}
    songs = (js.get("result") or {}).get("songs") or []
    out: list[dict] = []
    for it in songs:
        name = it.get("name") or ""
        if drop_instrumental and _is_instrumental(name):
            continue
        artists = ", ".join(a.get("name", "") for a in (it.get("artists") or [])
                            if a.get("name"))
        album = (it.get("album") or {}).get("name", "")
        out.append({
            "id":          it.get("id"),
            "name":        name,
            "artists":     artists,
            "album":       album,
            "duration_ms": it.get("duration") or 0,
            "source":      "netease",
        })
    return out


def _maybe_save_netease_lyrics(song_id: int, out_dir: str, stem: str) -> str | None:
    """尝试抓歌词并落盘 ``<stem>.lrc`` 返回路径或 None 失败永远静默

    优先把翻译并入原 LRC(每行末尾追加 ``「翻译」``) 多数播放器兼容
    若只有翻译没原词或都为空 直接返回 None
    """
    try:
        from utils._netease_weapi import fetch_lyrics, merge_lrc_with_translation
        ua = (get_field("materials.user_agent", "") or "").strip() or None
        proxy = (get_field("materials.proxy", "") or "").strip() or None
        d = fetch_lyrics(song_id, user_agent=ua, proxy=proxy)
        merged = merge_lrc_with_translation(d.get("lrc") or "",
                                            d.get("tlyric") or "")
        if not merged.strip():
            return None
        lrc_path = os.path.join(out_dir, stem + ".lrc")
        with open(lrc_path, "w", encoding="utf-8") as f:
            f.write(merged)
        return lrc_path
    except Exception:
        return None


def download_netease(song_id: int, out_dir: str,
                     title: Optional[str] = None,
                     progress_cb: Optional[Callable[[int, str], None]] = None,
                     cancel_cb: Optional[Callable[[], bool]] = None) -> str:
    """优先用内嵌 weapi 协议拿直链 失败回退公开 outer/url CDN

    weapi 走的是网易云 PC 客户端协议 ``/weapi/song/enhance/player/url/v1``
    比 outer/url 命中率高、码率高 (一般 320 kbps mp3) 见
    ``utils/_netease_weapi.py`` 的协议说明。
    """
    if not song_id:
        raise RuntimeError("download_netease: song_id 为空")
    os.makedirs(out_dir, exist_ok=True)

    if progress_cb:
        progress_cb(0, "解析直链…")

    # ── 1. 优先走 weapi ─────────────────────────────────────────
    entry: dict | None = None
    weapi_err: Exception | None = None
    try:
        from utils._netease_weapi import fetch_song_url
        ua = (get_field("materials.user_agent", "") or "").strip() or None
        proxy = (get_field("materials.proxy", "") or "").strip() or None
        entry = fetch_song_url(song_id, level="exhigh",
                               user_agent=ua, proxy=proxy)
    except Exception as e:
        weapi_err = e

    if entry and entry.get("url"):
        url = entry["url"]
        # 按返回 type 选扩展名 (默认 mp3)
        ft = (entry.get("type") or "mp3").lower()
        if ft not in ("mp3", "flac", "wav", "m4a", "ape"):
            ft = "mp3"
        s = _make_session("https://music.163.com/")
        stem = _safe_filename(title or f"netease_{song_id}")
        out_path = os.path.join(out_dir, stem + f".{ft}")
        result = _streamed_download(s, url, out_path,
                                    progress_cb=progress_cb, cancel_cb=cancel_cb,
                                    label="网易云下载")
        # 顺便尝试抓歌词 — 失败 / 纯音乐都静默跳过 不影响主流程
        _maybe_save_netease_lyrics(song_id, out_dir, stem)
        return result

    # ── 2. weapi 解不到 兜底 outer/url (128 kbps 非 VIP) ──────
    if progress_cb:
        progress_cb(2, "weapi 失败 回退公开 CDN…")
    s = _make_session("https://music.163.com/")
    url = _NETEASE_OUTER.format(sid=song_id)
    fname = _safe_filename(title or f"netease_{song_id}") + ".mp3"
    out_path = os.path.join(out_dir, fname)
    try:
        return _streamed_download(s, url, out_path,
                                  progress_cb=progress_cb, cancel_cb=cancel_cb,
                                  label="网易云下载")
    except Exception as fallback_err:
        # 同时抛 weapi 的报错 让用户看到为什么 (主线索)
        if weapi_err is not None:
            raise RuntimeError(
                f"weapi 解析失败 ({weapi_err}) 公开 CDN 兜底也失败: {fallback_err}"
            ) from fallback_err
        raise


# ══════════════════════════════════════════════════════════════════════
#  Bilibili (视频音频提取)
# ══════════════════════════════════════════════════════════════════════

def _strip_html(s: str) -> str:
    return re.sub(r"<[^>]+>", "", s or "")


def search_bilibili(keyword: str, limit: int = 20) -> list[dict]:
    """搜索 B 站视频 返回 [{bvid, title, author, duration, pic, ...}]"""
    keyword = (keyword or "").strip()
    if not keyword:
        return []
    s = _make_session("https://www.bilibili.com/")
    # 第一次没有 cookie 直接调可能被 412 此时先访问主页获取 buvid 再重试一次
    try:
        s.get("https://www.bilibili.com/", timeout=_TIMEOUT)
    except requests.RequestException:
        pass

    params = {
        "search_type": "video",
        "keyword":     keyword,
        "page":        1,
        "page_size":   max(1, min(50, int(limit))),
    }
    r = s.get(_BILI_SEARCH, params=params, timeout=_TIMEOUT)
    r.raise_for_status()
    js = r.json() or {}
    if js.get("code") not in (0, None):
        raise RuntimeError(f"B 站搜索失败: code={js.get('code')} msg={js.get('message')}")
    items = ((js.get("data") or {}).get("result")) or []
    out: list[dict] = []
    for it in items[:limit]:
        bvid = it.get("bvid")
        if not bvid:
            continue
        pic = it.get("pic") or ""
        if pic.startswith("//"):
            pic = "https:" + pic
        out.append({
            "bvid":     bvid,
            "title":    _strip_html(it.get("title", "")),
            "author":   it.get("author") or "",
            "duration": it.get("duration") or "",   # 已是 "mm:ss" 字符串
            "pic":      pic,
            "play":     it.get("play") or 0,
            "source":   "bilibili",
        })
    return out


def _bili_get_cid(session: requests.Session, bvid: str) -> int:
    r = session.get(_BILI_VIEW, params={"bvid": bvid}, timeout=_TIMEOUT)
    r.raise_for_status()
    js = r.json() or {}
    if js.get("code") != 0:
        raise RuntimeError(f"获取视频信息失败: {js.get('message')}")
    cid = ((js.get("data") or {}).get("cid"))
    if not cid:
        raise RuntimeError("视频无 cid 字段")
    return int(cid)


def _bili_best_audio_url(session: requests.Session, bvid: str, cid: int) -> str:
    params = {"bvid": bvid, "cid": cid, "fnval": 16, "fnver": 0, "fourk": 1}
    r = session.get(_BILI_PLAYURL, params=params, timeout=_TIMEOUT)
    r.raise_for_status()
    js = r.json() or {}
    if js.get("code") != 0:
        raise RuntimeError(f"获取播放地址失败: {js.get('message')}")
    data = js.get("data") or {}
    dash = data.get("dash") or {}
    audios = dash.get("audio") or []
    if not audios:
        # 老接口 / 番剧片段 / 互动视频可能没有 dash 这里直接放弃
        raise RuntimeError("该视频无可下载的 DASH 音频流 (可能是付费视频/番剧/试看片段)")
    # 选 bandwidth 最高的一路
    best = max(audios, key=lambda x: x.get("bandwidth", 0))
    base = best.get("baseUrl") or best.get("base_url")
    if not base:
        raise RuntimeError("DASH audio 字段缺 baseUrl")
    return base


def _ffmpeg_exe() -> str:
    """优先用 resource/ffmepg/bin/ffmpeg.exe 找不到退回 PATH"""
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    bundled = os.path.join(project_root, "resource", "ffmepg", "bin", "ffmpeg.exe")
    if os.path.isfile(bundled):
        return bundled
    found = shutil.which("ffmpeg")
    return found or "ffmpeg"


# B 站 qn 码与显示名映射 (preview 页 ComboBox 用)
BILI_QUALITIES: list[tuple[int, str]] = [
    (16,  "360P"),
    (32,  "480P"),
    (64,  "720P"),
    (80,  "1080P"),
    (112, "1080P+"),
    (116, "1080P60"),
    (120, "4K"),
]


def download_bilibili_preview(bvid: str, out_dir: str,
                              title: Optional[str] = None,
                              quality: int = 16,
                              progress_cb: Optional[Callable[[int, str], None]] = None,
                              cancel_cb: Optional[Callable[[], bool]] = None) -> str:
    """下载 B 站视频指定画质的 DASH 视频流 + 音频流 用 ffmpeg copy-mux 成 mp4

    用途
    ====
    供素材库预览页 (VideoWidget) 加载播放 比 download_bilibili 多了 video 轨

    Args:
        quality: 期望画质 qn 码 见 ``BILI_QUALITIES``
                 默认 16 = 360P 体积最小 流量最省 适合预览
                 80+ 一般要登录 cookie 否则后端会自动降级到当前账号可见的最高档
                 实际拉到的不一定是请求的 qn 取最接近且不高于的可用流

    成功返回 out_dir/<safe_title>.mp4 路径 失败 raise
    """
    if not bvid:
        raise RuntimeError("download_bilibili_preview: bvid 为空")
    os.makedirs(out_dir, exist_ok=True)
    s = _make_session("https://www.bilibili.com/")

    if progress_cb:
        progress_cb(0, "解析视频…")
    cid = _bili_get_cid(s, bvid)
    if cancel_cb and cancel_cb():
        raise CancelledError("用户取消")

    # 取 DASH 列表 — qn 传请求 不同账号能拿到的档不同
    if progress_cb:
        progress_cb(2, "获取播放地址…")
    params = {"bvid": bvid, "cid": cid, "qn": int(quality),
              "fnval": 16, "fnver": 0, "fourk": 1}
    r = s.get(_BILI_PLAYURL, params=params, timeout=_TIMEOUT)
    r.raise_for_status()
    js = r.json() or {}
    if js.get("code") != 0:
        raise RuntimeError(f"获取播放地址失败: {js.get('message')}")
    dash = (js.get("data") or {}).get("dash") or {}
    videos = dash.get("video") or []
    audios = dash.get("audio") or []
    if not videos or not audios:
        raise RuntimeError("该视频无可下载的 DASH 流 (可能是付费视频/番剧/试看片段)")

    # 优先匹配请求 qn 找不到取 ≤quality 中最高的 仍找不到取最低
    exact = [v for v in videos if v.get("id") == int(quality)]
    if exact:
        v_pick = max(exact, key=lambda x: x.get("bandwidth", 0))
    else:
        le = [v for v in videos if (v.get("id") or 0) <= int(quality)]
        if le:
            v_pick = max(le, key=lambda x: x.get("id", 0))
        else:
            v_pick = min(videos, key=lambda x: x.get("id", 0))

    a_low = min(audios, key=lambda x: x.get("bandwidth", 0))
    v_url = v_pick.get("baseUrl") or v_pick.get("base_url")
    a_url = a_low.get("baseUrl") or a_low.get("base_url")
    if not v_url or not a_url:
        raise RuntimeError("DASH 流缺 baseUrl")

    picked_qn = v_pick.get("id", quality)
    label_name = next((n for q, n in BILI_QUALITIES if q == picked_qn), str(picked_qn))
    if progress_cb:
        progress_cb(3, f"实际画质: {label_name}")

    fname_stem = _safe_filename(title or bvid)
    out_path = os.path.join(out_dir, fname_stem + ".mp4")
    tmp_dir = tempfile.mkdtemp(prefix="bili_prev_")
    v_path = os.path.join(tmp_dir, "video.m4s")
    a_path = os.path.join(tmp_dir, "audio.m4s")

    def _scaled_cb(lo: int, hi: int, label: str):
        if not progress_cb:
            return None
        def _cb(p: int, _t: str):
            mapped = lo + int(max(0, min(100, p)) * (hi - lo) / 100)
            progress_cb(mapped, f"{label} {p}%")
        return _cb

    try:
        _streamed_download(
            s, v_url, v_path,
            progress_cb=_scaled_cb(5, 55, "视频"),
            cancel_cb=cancel_cb, label="视频",
        )
        if cancel_cb and cancel_cb():
            raise CancelledError("用户取消")

        _streamed_download(
            s, a_url, a_path,
            progress_cb=_scaled_cb(55, 90, "音频"),
            cancel_cb=cancel_cb, label="音频",
        )
        if cancel_cb and cancel_cb():
            raise CancelledError("用户取消")

        if progress_cb:
            progress_cb(92, "合成视频…")
        ffmpeg = _ffmpeg_exe()
        cmd = [ffmpeg, "-y", "-loglevel", "error",
               "-i", v_path, "-i", a_path,
               "-c", "copy", "-movflags", "+faststart",
               out_path]
        rc = subprocess.run(cmd, capture_output=True, text=True,
                            encoding="utf-8", errors="replace")
        if rc.returncode != 0:
            tail = (rc.stderr or rc.stdout or "")[-500:]
            raise RuntimeError(f"ffmpeg mux 失败 (rc={rc.returncode}): {tail}")
        if progress_cb:
            progress_cb(100, "完成")
        return out_path
    finally:
        try:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        except OSError:
            pass


def download_bilibili(bvid: str, out_dir: str,
                      title: Optional[str] = None,
                      progress_cb: Optional[Callable[[int, str], None]] = None,
                      cancel_cb: Optional[Callable[[], bool]] = None) -> str:
    """下载 B 站视频的音频流 (m4a) 到 out_dir 返回本地路径"""
    if not bvid:
        raise RuntimeError("download_bilibili: bvid 为空")
    os.makedirs(out_dir, exist_ok=True)
    s = _make_session("https://www.bilibili.com/")
    if progress_cb:
        progress_cb(0, "解析视频…")
    cid = _bili_get_cid(s, bvid)
    if cancel_cb and cancel_cb():
        raise CancelledError("用户取消下载")
    if progress_cb:
        progress_cb(2, "获取音频流地址…")
    audio_url = _bili_best_audio_url(s, bvid, cid)

    # B站音频 CDN 要求带 Referer 沿用 session.headers 已经设好
    fname = _safe_filename(title or bvid) + ".m4a"
    out_path = os.path.join(out_dir, fname)
    return _streamed_download(s, audio_url, out_path,
                              progress_cb=progress_cb, cancel_cb=cancel_cb,
                              label="B站下载")


# ══════════════════════════════════════════════════════════════════════
#  节点入口: 搜索 → 取第一个 → 下载
# ══════════════════════════════════════════════════════════════════════

# ══════════════════════════════════════════════════════════════════════
#  Bilibili QR 登录 (公开 web 接口 无加密)
# ══════════════════════════════════════════════════════════════════════

def bili_qr_generate(timeout: int = 10) -> tuple[str, str]:
    """生成 B 站登录 二维码 key + 待编码 URL

    Returns:
        ``(qrcode_key, url)`` 把 url 编进二维码 用 B 站手机 App 扫描
    """
    s = _make_session("https://passport.bilibili.com/login")
    r = s.get(_BILI_QR_GENERATE, timeout=timeout)
    r.raise_for_status()
    js = r.json() or {}
    if js.get("code") != 0:
        raise RuntimeError(f"bili QR generate code={js.get('code')} {js.get('message','')}")
    d = js.get("data") or {}
    qk = d.get("qrcode_key") or ""
    url = d.get("url") or ""
    if not qk or not url:
        raise RuntimeError("bili QR generate 返回字段缺失")
    return qk, url


def bili_qr_poll(qrcode_key: str, timeout: int = 10) -> tuple[int, str]:
    """轮询 B 站 QR 登录状态

    Returns:
        ``(code, cookie_string)`` code 字典(取自 ``data.code``):
            0     = 已登录 cookie_string 含 SESSDATA bili_jct DedeUserID 等
            86038 = 二维码已过期
            86090 = 已扫码 待手机端确认
            86101 = 等待扫码
    """
    s = _make_session("https://passport.bilibili.com/login")
    r = s.get(_BILI_QR_POLL, params={"qrcode_key": qrcode_key}, timeout=timeout)
    r.raise_for_status()
    js = r.json() or {}
    if js.get("code") != 0:
        # 接口本身报错(rare) 当作过期处理
        return 86038, ""
    d = js.get("data") or {}
    code = int(d.get("code", -1))
    cookie_str = ""
    if code == 0:
        # 登录成功 Set-Cookie 留下 SESSDATA 等
        wanted = ("SESSDATA", "bili_jct", "DedeUserID",
                  "DedeUserID__ckMd5", "sid", "buvid3", "buvid4")
        pairs = []
        for c in r.cookies:
            if c.name in wanted:
                pairs.append(f"{c.name}={c.value}")
        # 兜底: 有的接口把 cookie 拼在 data.url 的 query 上(罕见 但保险)
        cookie_str = "; ".join(pairs)
    return code, cookie_str


def get_bilibili_user_info(timeout: int = 8) -> dict[str, object] | None:
    """读取当前 cookie 对应 B 站账号信息 未登录返回 None

    Returns:
        ``{"mid": int, "uname": str, "vip": bool}`` 或 None
    """
    cookie = _user_cookie("bilibili")
    if not cookie or "SESSDATA=" not in cookie:
        return None
    s = _make_session("https://www.bilibili.com/")
    try:
        r = s.get("https://api.bilibili.com/x/web-interface/nav", timeout=timeout)
        r.raise_for_status()
        js = r.json() or {}
        if js.get("code") != 0:
            return None
        d = js.get("data") or {}
        if not d.get("isLogin"):
            return None
        vip = (d.get("vipStatus") or 0) == 1 or (d.get("vip") or {}).get("status") == 1
        return {
            "mid":   d.get("mid"),
            "uname": d.get("uname") or "",
            "vip":   bool(vip),
        }
    except Exception:
        return None


def fetch_first_match(keyword: str, source: str, out_dir: str,
                      drop_instrumental: bool = True,
                      progress_cb: Optional[Callable[[int, str], None]] = None,
                      cancel_cb: Optional[Callable[[], bool]] = None) -> str:
    """匹配第一个非伴奏结果并下载 返回本地文件路径"""
    keyword = (keyword or "").strip()
    if not keyword:
        raise RuntimeError("fetch_first_match: keyword 为空")

    source = (source or "netease").lower()
    if source not in ("netease", "bilibili"):
        raise RuntimeError(f"未知 source: {source!r} 仅支持 netease / bilibili")

    if progress_cb:
        progress_cb(0, f"在 {source} 搜索: {keyword}")
    if cancel_cb and cancel_cb():
        raise CancelledError("用户取消")

    if source == "netease":
        hits = search_netease(keyword, limit=20, drop_instrumental=drop_instrumental)
        if not hits:
            raise RuntimeError(f"网易云未搜到非伴奏结果: {keyword}")
        first = hits[0]
        title = f"{first['name']} - {first['artists']}".strip(" -")
        return download_netease(first["id"], out_dir, title=title,
                                progress_cb=progress_cb, cancel_cb=cancel_cb)
    else:  # bilibili
        hits = search_bilibili(keyword, limit=20)
        if not hits:
            raise RuntimeError(f"B 站未搜到结果: {keyword}")
        first = hits[0]
        title = f"{first['title']} - {first['author']}".strip(" -")
        return download_bilibili(first["bvid"], out_dir, title=title,
                                 progress_cb=progress_cb, cancel_cb=cancel_cb)
