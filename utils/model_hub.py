"""
utils/model_hub.py
~~~~~~~~~~~~~~~~~~
RVC / GPT-SoVITS 角色模型搜索 + 下载后端。

定位
====
和 ``utils/material_fetcher.py`` 平级 都是 LaunchAI 的「公开数据抓取」层
具体做的事是给「找模型」子页面（参考 绘世启动器 的模型抽屉）提供：

1. ``search(kind, keyword, sources=None)``  → 多源混合 按热度排序
2. ``list_files(item)``                     → 仓库文件清单（B站源无文件）
3. ``download_files(item, files, dest)``    → 流式下载到 ``data/models/<kind>/<name>/``

支持的源
========
- **huggingface**   公开仓库 无需登录 完整文件下载
- **modelscope**    魔搭社区 PUT /api/v1/dolphin/models 搜索 + resolve 风格下载
- **bilibili**      视频搜索 *只做发现* 没有真正的文件清单
                    用户在详情页拿到的是「打开 B 站视频」按钮 模型链接通常埋
                    在视频简介里 让用户自己抓

排序按统一字段 ``ModelItem.downloads`` 降序
- HF: ``downloads`` (API 原生)
- MS: ``Downloads`` (API 原生)
- B站: ``play`` (播放量 也代表热度)

镜像与代理
==========
``configs/config.json`` 的 ``model_hub.*``:
- ``hf_endpoint``    HF 镜像 例如 ``https://hf-mirror.com``
- ``hf_token``       HF Bearer token 提升匿名速率限制
- ``proxy``          HTTP/HTTPS 代理 https://user:pass@host:port
- ``user_agent``     覆盖默认 UA

进度回调约定与 material_fetcher 一致::

    progress_cb(percent: int, status: str)
    cancel_cb() -> bool          # True 时 raise CancelledError

LaunchAI 子页面拿到 percent 后既可以喂给 ProgressBar 也可以包成 HTML
（含「下载进度」前缀）让 LogTextEdit 单行覆盖。
"""

from __future__ import annotations

import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Callable, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from utils.configer import get_field
from utils import paths as _paths
from logger import warning as _warn


# ── 常量 ──────────────────────────────────────────────────────────────

_DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

_HF_DEFAULT_ENDPOINT = "https://huggingface.co"
_MS_ENDPOINT = "https://modelscope.cn"
_BILI_VIDEO_PAGE = "https://www.bilibili.com/video/{bvid}/"


def _hf_endpoint() -> str:
    """读 model_hub.hf_endpoint 用户可改成 https://hf-mirror.com 走镜像"""
    ep = (get_field("model_hub.hf_endpoint", "") or "").strip()
    if not ep:
        return _HF_DEFAULT_ENDPOINT
    return ep.rstrip("/")


# 支持的源 顺序决定 UI CheckBox 排布
SOURCES: list[tuple[str, str]] = [
    ("huggingface", "Hugging Face"),
    ("modelscope",  "ModelScope"),
    ("bilibili",    "B 站"),
]
SOURCE_KEYS: tuple[str, ...] = tuple(k for k, _ in SOURCES)

# 「找模型」UI 上的两个类别 → 默认追加到搜索 query 末尾的关键词
# 用户可以再敲自己的关键词 比如 "Hatsune Miku" 我们最终拼 "Hatsune Miku RVC"
_KIND_QUERY_HINT = {
    "rvc":       "RVC",
    "gptsovits": "GPT-SoVITS",
}

# 各类型「真正算模型」的文件扩展名 列详情卡时按这个高亮 + 标记建议下载
_KIND_MODEL_EXTS = {
    "rvc":       (".pth", ".index"),
    "gptsovits": (".ckpt", ".pth"),
}

# 各类型可一起带走的辅助文件扩展名 主要是参考音频 / 数据集列表 / 配置
_KIND_AUX_EXTS = {
    "rvc":       (".json", ".npy", ".wav", ".flac", ".mp3"),
    "gptsovits": (".json", ".list", ".wav", ".flac", ".mp3", ".txt", ".yaml"),
}

# 一刀切忽略：体积大但与推理无关的训练副产物
_IGNORED_DIR_PREFIXES = (
    "logs/", "eval/", "tensorboard/", ".git/", ".gitattributes",
)

_TIMEOUT = 15
_CHUNK = 256 * 1024
_INVALID_FN_CHARS = re.compile(r'[\\/:*?"<>|\r\n\t]+')


class CancelledError(RuntimeError):
    """用户在下载途中主动取消"""


# ── 数据结构 ──────────────────────────────────────────────────────────

@dataclass
class ModelItem:
    """单条搜索结果 → 详情页 → 下载 全程传递"""
    source: str                      # "huggingface" | "voice-models"
    kind: str                        # "rvc" | "gptsovits"
    id: str                          # 唯一 id 比如 hf repo_id
    title: str                       # 显示名 一般等于 id
    author: str = ""                 # 上传者
    downloads: int = 0               # 下载数（HF 有 voice-models 没有）
    likes: int = 0
    last_modified: str = ""          # ISO 时间字符串 或 ""
    tags: list[str] = field(default_factory=list)
    description: str = ""            # 简短描述/README 摘要 没有就为空
    url: str = ""                    # 浏览页 URL 给用户点开看
    revision: str = "main"           # HF 分支/commit 一般 main
    extra: dict = field(default_factory=dict)   # 各源自留字段

    def to_dict(self) -> dict:
        return {
            "source": self.source, "kind": self.kind,
            "id": self.id, "title": self.title, "author": self.author,
            "downloads": self.downloads, "likes": self.likes,
            "last_modified": self.last_modified, "tags": list(self.tags),
            "description": self.description, "url": self.url,
            "revision": self.revision, "extra": dict(self.extra),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ModelItem":
        return cls(
            source=d.get("source", ""), kind=d.get("kind", ""),
            id=d.get("id", ""), title=d.get("title") or d.get("id", ""),
            author=d.get("author", ""),
            downloads=int(d.get("downloads") or 0),
            likes=int(d.get("likes") or 0),
            last_modified=d.get("last_modified", ""),
            tags=list(d.get("tags") or []),
            description=d.get("description", ""),
            url=d.get("url", ""),
            revision=d.get("revision", "main"),
            extra=dict(d.get("extra") or {}),
        )


@dataclass
class ModelFile:
    """详情视图里展示的单个文件 用户勾选 → 一起喂进 download_files()"""
    path: str               # 仓库内相对路径
    size: int               # 字节
    is_model: bool          # 是否核心权重（.pth/.ckpt/.index 之类）
    is_aux: bool            # 是否辅助文件

    def to_dict(self) -> dict:
        return {"path": self.path, "size": self.size,
                "is_model": self.is_model, "is_aux": self.is_aux}


# ── Session ──────────────────────────────────────────────────────────

def _make_session(referer: str = "https://huggingface.co/") -> requests.Session:
    s = requests.Session()
    ua = (get_field("model_hub.user_agent", "") or "").strip() or _DEFAULT_UA
    s.headers.update({
        "User-Agent": ua,
        "Accept": "application/json, */*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Referer": referer,
    })
    token = (get_field("model_hub.hf_token", "") or "").strip()
    if token:
        s.headers["Authorization"] = f"Bearer {token}"
    proxy = (get_field("model_hub.proxy", "") or "").strip()
    if proxy:
        s.proxies = {"http": proxy, "https": proxy}

    retry = Retry(
        total=3, backoff_factor=0.8,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=frozenset(["GET", "HEAD"]),
    )
    adapter = HTTPAdapter(max_retries=retry)
    s.mount("http://", adapter)
    s.mount("https://", adapter)
    return s


# ── 通用工具 ─────────────────────────────────────────────────────────

def _safe_dirname(name: str, max_len: int = 80) -> str:
    cleaned = _INVALID_FN_CHARS.sub("_", name or "").strip(" .")
    if not cleaned:
        cleaned = f"model_{int(time.time())}"
    if len(cleaned) > max_len:
        cleaned = cleaned[:max_len].rstrip()
    return cleaned


def _classify_path(path: str, kind: str) -> tuple[bool, bool]:
    """(is_model, is_aux) —— is_model 排他于 is_aux"""
    low = path.lower()
    if any(low.startswith(p) for p in _IGNORED_DIR_PREFIXES):
        return False, False
    if low.endswith(_KIND_MODEL_EXTS.get(kind, ())):
        return True, False
    if low.endswith(_KIND_AUX_EXTS.get(kind, ())):
        return False, True
    return False, False


def _streamed_download(
    session: requests.Session, url: str, out_path: str,
    progress_cb: Optional[Callable[[int, str], None]] = None,
    cancel_cb: Optional[Callable[[], bool]] = None,
    label: str = "下载",
) -> str:
    """通用流式下载 写到 out_path 完成返回 out_path"""
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with session.get(url, stream=True, timeout=_TIMEOUT, allow_redirects=True) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length", 0))
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
                    if progress_cb:
                        now = time.time()
                        if total > 0:
                            pct = int(downloaded * 100 / total)
                        else:
                            pct = min(99, int(downloaded / (1024 * 1024)))   # 兜底 1MB=1%
                        if pct != last_pct or now - last_ts >= 0.5:
                            speed = (downloaded - last_dl) / max(now - last_ts, 0.001) / 1024
                            mb_dl = downloaded / 1048576
                            if total > 0:
                                mb_tot = total / 1048576
                                progress_cb(
                                    pct,
                                    f"{label} {pct}% ({mb_dl:.1f}/{mb_tot:.1f} MB) {speed:.0f} KB/s",
                                )
                            else:
                                progress_cb(
                                    pct,
                                    f"{label} {mb_dl:.1f} MB {speed:.0f} KB/s",
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
#  Hugging Face Hub
# ══════════════════════════════════════════════════════════════════════

def search_huggingface(kind: str, keyword: str,
                       limit: int = 30) -> list[ModelItem]:
    """搜 HuggingFace Hub 上类型与关键词匹配的仓库

    kind 影响默认追加的关键字 ("RVC" / "GPT-SoVITS")
    用户 keyword 为空时只用 kind hint
    """
    hint = _KIND_QUERY_HINT.get(kind, "")
    q = (keyword or "").strip()
    full_q = f"{q} {hint}".strip() if q else hint
    if not full_q:
        return []

    session = _make_session()
    params = {
        "search": full_q,
        "limit":  max(1, min(int(limit), 100)),
        "sort":   "downloads",
        "direction": "-1",
        "full":   "true",
    }
    api_url = f"{_hf_endpoint()}/api/models"
    r = session.get(api_url, params=params, timeout=_TIMEOUT)
    r.raise_for_status()
    raw = r.json() or []

    out: list[ModelItem] = []
    for it in raw:
        repo_id = it.get("modelId") or it.get("id") or ""
        if not repo_id:
            continue
        author = repo_id.split("/")[0] if "/" in repo_id else ""
        tags = list(it.get("tags") or [])
        # 简单兜底分类：tags / readme 没办法时只信关键词
        desc = ""
        card = it.get("cardData") or {}
        if isinstance(card, dict):
            desc = (card.get("description") or card.get("license") or "")[:200]

        out.append(ModelItem(
            source="huggingface", kind=kind,
            id=repo_id, title=repo_id, author=author,
            downloads=int(it.get("downloads") or 0),
            likes=int(it.get("likes") or 0),
            last_modified=str(it.get("lastModified") or ""),
            tags=tags, description=desc,
            # 浏览页 URL 始终用官方域名 镜像页常没有人类可读页面
            url=f"{_HF_DEFAULT_ENDPOINT}/{repo_id}",
            revision="main",
        ))
    return out


def list_files_huggingface(item: ModelItem) -> list[ModelFile]:
    """递归列 HF 仓库根目录的全部文件"""
    session = _make_session()
    url = f"{_hf_endpoint()}/api/models/{item.id}/tree/{item.revision}"
    files: list[ModelFile] = []
    # HF 的 tree 接口支持 ?recursive=true 但部分仓库要分页 这里手动走 cursor
    params = {"recursive": "true", "limit": 1000}
    while True:
        r = session.get(url, params=params, timeout=_TIMEOUT)
        r.raise_for_status()
        # tree 接口返回的是数组 不是对象 分页 cursor 在响应头
        arr = r.json() or []
        for node in arr:
            if (node.get("type") or "") != "file":
                continue
            path = node.get("path") or ""
            if not path:
                continue
            is_model, is_aux = _classify_path(path, item.kind)
            files.append(ModelFile(
                path=path, size=int(node.get("size") or 0),
                is_model=is_model, is_aux=is_aux,
            ))
        # 分页 link 头：<...?cursor=xxx>; rel="next"
        link = r.headers.get("Link") or r.headers.get("link") or ""
        m = re.search(r'<([^>]+)>;\s*rel="next"', link)
        if not m:
            break
        next_url = m.group(1)
        url = next_url
        params = {}   # cursor 已经在 URL query 里
    return files


# ══════════════════════════════════════════════════════════════════════
#  ModelScope (魔搭社区)
# ══════════════════════════════════════════════════════════════════════
#
# 公开搜索接口是 PUT /api/v1/dolphin/models 返回 JSON
# 返回的 Model.Models[] 字段 (主要):
#   Path           作者/组织 (URL 前段)
#   Name           仓库名    (URL 后段)
#   ChineseName    中文别名
#   Downloads      下载数
#   Stars          收藏数
#   License        许可证字串
#   GmtModified    最近修改时间 (ms epoch)
# 文件清单走 GET /api/v1/models/<Path>/<Name>/repo/files?Revision=master&Recursive=True
#   返回 Data.Files[] {Path, Name, Size, Type=blob|tree, IsLFS, CommittedDate}
# 文件下载走 GET https://modelscope.cn/models/<Path>/<Name>/resolve/<rev>/<file>
# (和 Hugging Face 的 resolve 模式一样)

def search_modelscope(kind: str, keyword: str,
                      limit: int = 30) -> list[ModelItem]:
    hint = _KIND_QUERY_HINT.get(kind, "")
    q = (keyword or "").strip()
    full_q = f"{q} {hint}".strip() if q else hint
    if not full_q:
        return []

    session = _make_session(referer=f"{_MS_ENDPOINT}/")
    body = {
        "PageSize":   max(1, min(int(limit), 100)),
        "PageNumber": 1,
        "SortBy":     "Default",
        "Target":     "",
        "SingleCriterion": [],
        "Name":       full_q,
    }
    r = session.put(
        f"{_MS_ENDPOINT}/api/v1/dolphin/models",
        json=body, timeout=_TIMEOUT,
        headers={"Content-Type": "application/json"},
    )
    r.raise_for_status()
    js = r.json() or {}
    if js.get("Code") not in (200, None):
        raise RuntimeError(f"ModelScope 搜索失败: {js.get('Message')}")
    models = ((js.get("Data") or {}).get("Model") or {}).get("Models") or []

    out: list[ModelItem] = []
    for m in models:
        path = (m.get("Path") or "").strip()
        name = (m.get("Name") or "").strip()
        if not path or not name:
            continue
        repo_id = f"{path}/{name}"
        title = m.get("ChineseName") or name
        gmt = m.get("GmtModified") or m.get("LastUpdatedTime") or 0
        try:
            # GmtModified 是秒级 epoch；早期字段可能毫秒级
            ts = float(gmt) / (1000.0 if gmt > 2_000_000_000 else 1.0)
            iso = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(ts))
        except (TypeError, ValueError, OSError):
            iso = ""
        out.append(ModelItem(
            source="modelscope", kind=kind,
            id=repo_id, title=title, author=path,
            downloads=int(m.get("Downloads") or 0),
            likes=int(m.get("Stars") or 0),
            last_modified=iso,
            tags=[t for t in (m.get("Tags") or []) if isinstance(t, str)],
            description=(m.get("License") or "")[:200],
            url=f"{_MS_ENDPOINT}/models/{repo_id}",
            revision="master",
            extra={"ms_name": name, "ms_path": path},
        ))
    return out


def list_files_modelscope(item: ModelItem) -> list[ModelFile]:
    session = _make_session(referer=f"{_MS_ENDPOINT}/")
    url = f"{_MS_ENDPOINT}/api/v1/models/{item.id}/repo/files"
    params = {"Revision": item.revision, "Recursive": "True"}
    r = session.get(url, params=params, timeout=_TIMEOUT)
    r.raise_for_status()
    js = r.json() or {}
    if js.get("Code") not in (200, None):
        raise RuntimeError(f"ModelScope 列文件失败: {js.get('Message')}")
    files = (js.get("Data") or {}).get("Files") or []
    out: list[ModelFile] = []
    for f in files:
        if (f.get("Type") or "").lower() != "blob":
            continue
        path = (f.get("Path") or f.get("Name") or "").strip()
        if not path:
            continue
        is_model, is_aux = _classify_path(path, item.kind)
        out.append(ModelFile(
            path=path, size=int(f.get("Size") or 0),
            is_model=is_model, is_aux=is_aux,
        ))
    return out


# ══════════════════════════════════════════════════════════════════════
#  B 站 视频发现
# ══════════════════════════════════════════════════════════════════════
#
# 复用 utils.material_fetcher 里已经写好的 search_bilibili。B 站不托管
# 模型文件 这里只把搜到的视频包装成 ModelItem 让卡片有「打开 B 站视频」
# 按钮 用户跳过去自己抓简介里的网盘/HF 链接。

def search_bilibili_videos(kind: str, keyword: str,
                           limit: int = 20) -> list[ModelItem]:
    """走素材库已有的 B 站搜索 把视频结果包成 ModelItem"""
    hint = _KIND_QUERY_HINT.get(kind, "")
    q = (keyword or "").strip()
    full_q = f"{q} {hint} 模型".strip() if q else f"{hint} 模型".strip()
    if not full_q:
        return []

    # 延迟 import 避免循环
    from utils.material_fetcher import search_bilibili
    raw = search_bilibili(full_q, limit=limit)
    out: list[ModelItem] = []
    for v in raw:
        bvid = v.get("bvid") or ""
        if not bvid:
            continue
        out.append(ModelItem(
            source="bilibili", kind=kind,
            id=bvid, title=v.get("title") or bvid,
            author=v.get("author") or "",
            downloads=int(v.get("play") or 0),   # play 复用 downloads 字段做排序
            likes=0,
            last_modified="",
            tags=[],
            description=v.get("duration") or "",
            url=_BILI_VIDEO_PAGE.format(bvid=bvid),
            revision="",
            extra={
                "pic":      v.get("pic") or "",
                "duration": v.get("duration") or "",
                "play":     int(v.get("play") or 0),
            },
        ))
    return out


# ══════════════════════════════════════════════════════════════════════
#  统一对外
# ══════════════════════════════════════════════════════════════════════

# 源 → 搜索函数 (内部用) 新增源在这一行注册即可
_SEARCH_FUNCS = {
    "huggingface": search_huggingface,
    "modelscope":  search_modelscope,
    "bilibili":    search_bilibili_videos,
}


def search(kind: str, keyword: str,
           sources: Optional[list[str]] = None,
           per_source_limit: int = 15) -> list[ModelItem]:
    """跨源混合搜索 合并结果按 downloads 降序返回

    sources           要查询的源 None = 全部
                      传不认识的 key 会被静默忽略
    per_source_limit  单源最多保留多少条 默认 15 三源 → 最多 45 条
    """
    if kind not in _KIND_QUERY_HINT:
        raise ValueError(f"unknown kind: {kind!r}")

    src_keys = list(sources) if sources else list(SOURCE_KEYS)
    src_keys = [s for s in src_keys if s in _SEARCH_FUNCS]
    if not src_keys:
        return []

    results: list[ModelItem] = []
    errors: list[str] = []
    # 并行调三家 哪家慢都不阻塞别人 总耗时 ≈ max(三家延迟)
    with ThreadPoolExecutor(max_workers=len(src_keys)) as ex:
        futures = {
            ex.submit(_SEARCH_FUNCS[s], kind, keyword, per_source_limit): s
            for s in src_keys
        }
        for fut in as_completed(futures):
            src = futures[fut]
            try:
                items = fut.result() or []
                results.extend(items)
            except Exception as e:
                # 单源挂掉不阻塞其余源 记到日志里 调用方仍能拿到其他源结果
                errors.append(f"{src}: {e}")
                _warn(f"[model_hub] {src} 搜索失败: {e}")

    # 三家都挂才报错 否则有几条返几条
    if not results and errors:
        raise RuntimeError("所有源均失败: " + "; ".join(errors))

    results.sort(key=lambda x: x.downloads, reverse=True)
    return results


def list_files(item: ModelItem) -> list[ModelFile]:
    if item.source == "huggingface":
        return list_files_huggingface(item)
    if item.source == "modelscope":
        return list_files_modelscope(item)
    if item.source == "bilibili":
        # B 站不托管模型 让 UI 自己识别走「打开网页」分支
        return []
    raise ValueError(f"unknown source: {item.source!r}")


def _resolve_url(item: ModelItem, path: str) -> str:
    if item.source == "huggingface":
        return f"{_hf_endpoint()}/{item.id}/resolve/{item.revision}/{path}"
    if item.source == "modelscope":
        return f"{_MS_ENDPOINT}/models/{item.id}/resolve/{item.revision}/{path}"
    raise ValueError(f"unknown source: {item.source!r} 不支持下载")


def model_dest_dir(item: ModelItem) -> str:
    """目标安装目录 data/models/<kind>/<safe_repo_name>/

    保留作者前缀避免重名 比如 lj1995_VoiceConversionWebUI
    """
    base = _paths.model_dir(item.kind)
    sub = _safe_dirname(item.id.replace("/", "_"))
    dest = os.path.join(base, sub)
    os.makedirs(dest, exist_ok=True)
    return dest


def download_files(
    item: ModelItem, files: list[ModelFile], dest_dir: Optional[str] = None,
    progress_cb: Optional[Callable[[int, str], None]] = None,
    output_cb: Optional[Callable[[str], None]] = None,
    cancel_cb: Optional[Callable[[], bool]] = None,
) -> list[str]:
    """按勾选的文件清单依次下载 返回本地路径列表

    progress_cb     当前文件的百分比 + 状态文本（含「下载进度」前缀）
    output_cb       普通日志行（无覆盖）
    """
    if not files:
        return []
    session = _make_session()
    dest_dir = dest_dir or model_dest_dir(item)
    os.makedirs(dest_dir, exist_ok=True)

    saved: list[str] = []
    total_files = len(files)
    for i, f in enumerate(files, 1):
        if cancel_cb and cancel_cb():
            raise CancelledError("用户取消下载")
        url = _resolve_url(item, f.path)
        # 保留仓库目录结构 比如 some_dir/model.pth
        rel_path = f.path.replace("\\", "/")
        out_path = os.path.join(dest_dir, *rel_path.split("/"))
        label = f"下载进度 [{i}/{total_files}] {os.path.basename(f.path)}"
        if output_cb:
            output_cb(f"开始下载 [{i}/{total_files}] {f.path}  → {out_path}")
        _streamed_download(
            session, url, out_path,
            progress_cb=progress_cb, cancel_cb=cancel_cb, label=label,
        )
        saved.append(out_path)
        if output_cb:
            mb = f.size / 1048576 if f.size else 0
            output_cb(f"完成 [{i}/{total_files}] {f.path} ({mb:.1f} MB)")
    return saved
