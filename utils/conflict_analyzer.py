"""每个 LaunchAI 工具有独立的 locks/<tool>.txt; 多工具共享同一个 venv,
但不同工具可能把同名包钉到不同版本(目前实测仅 tqdm 一处不一致,
但只要新增工具就会扩散),需要给用户一个直观的诊断面板。

本模块只做纯解析,不引入 PyQt; UI 在 widgets/subpage/subpage_package_manager.py。
"""
from __future__ import annotations

import os
import re
from typing import Iterable

# packaging 通常随 pip/setuptools 一起在 venv 里,但不保证;缺失时退化为
# 纯字符串版本比较,避免整个包管理页因为 import 失败而打不开。
try:
    from packaging.version import Version, InvalidVersion
    _HAS_PACKAGING = True
except Exception:  # pragma: no cover - 仅在极简环境触发
    _HAS_PACKAGING = False

    class InvalidVersion(Exception):
        pass

    def Version(_s):  # noqa: N802 - 故意同名占位,触发回退分支
        raise InvalidVersion("packaging 不可用")

# locks/ 下的文件名 → LaunchAI 内部识别的"工具名"(显示用)。
# 与 widgets/subpage/subpage_switch_pages.py 里 handel_xxx 的 NoInstallWidget
# package_name 保持一致,方便回查 / SwitchPage 反向状态切换。
LOCK_TO_TOOL: dict[str, str] = {
    "demucs.txt":      "demucs",
    "whisper.txt":     "openai-whisper",
    "ultralytics.txt": "ultralytics",
    "applio.txt":      "Applio",
    "gptsovits.txt":   "GPT-SoVITS",
    "audiocraft.txt":  "audiocraft",
}

# 解析 `pkg==1.2.3` / `pkg>=1.0,<2` / `pkg[extra]==1.2` 的头部名字
_REQ_HEAD = re.compile(r"^\s*([A-Za-z0-9_.\-]+)")


def _norm(name: str) -> str:
    """PyPI 规范化:大小写不敏感, _ 和 - 等价。"""
    return name.lower().replace("_", "-")


def parse_lock(path: str) -> dict[str, str]:
    """读 lock 文件,返回 {pkg_normalized: version_spec_raw}。

    version_spec_raw 保留原始约束字符串(如 ==1.2.3, >=1.0,<2),
    没有版本号的行(如裸 `soundfile`)记为空串。注释行/--开头的 pip 选项行跳过。
    """
    out: dict[str, str] = {}
    if not os.path.exists(path):
        return out
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if not s or s.startswith("#") or s.startswith("--"):
                continue
            m = _REQ_HEAD.match(s)
            if not m:
                continue
            name = _norm(m.group(1))
            # 取头之后剩下的部分作为版本约束;extras [...] 段也保留,无伤大雅
            spec = s[m.end():].strip()
            out[name] = spec
    return out


def _extract_pinned_version(spec: str) -> str | None:
    """从 spec 里抓 `==X.Y.Z` 形式的钉死版本; 抓不到返回 None。"""
    m = re.search(r"==\s*([A-Za-z0-9_.+\-]+)", spec or "")
    return m.group(1) if m else None


def static_conflicts(locks_dir: str) -> list[dict]:
    """跨 lock 文件比对,返回 [{package, versions: {tool: spec}, conflict: bool}]。

    conflict 仅当 >=2 个工具用 `==` 钉死了不同版本时为 True;
    一方钉版本另一方放宽(>=, ~=)不算冲突(pip resolver 会自动妥协)。
    """
    per_tool: dict[str, dict[str, str]] = {}
    for fname, tool in LOCK_TO_TOOL.items():
        p = os.path.join(locks_dir, fname)
        per_tool[tool] = parse_lock(p)

    # 收集所有出现过的包
    all_pkgs: set[str] = set()
    for d in per_tool.values():
        all_pkgs.update(d.keys())

    rows: list[dict] = []
    for pkg in sorted(all_pkgs):
        versions = {tool: d[pkg]
                    for tool, d in per_tool.items() if pkg in d}
        if len(versions) < 2:
            continue  # 只出现在一个 lock 里, 不可能冲突
        pinned = {tool: v for tool, spec in versions.items()
                  if (v := _extract_pinned_version(spec))}
        distinct = set(pinned.values())
        conflict = len(distinct) >= 2
        rows.append({
            "package": pkg,
            "versions": versions,   # 原始 spec, UI 直接展示
            "pinned":   pinned,     # 仅钉死的版本
            "conflict": conflict,
        })
    # 冲突的排前面
    rows.sort(key=lambda r: (not r["conflict"], r["package"]))
    return rows


def dynamic_diagnosis(locks_dir: str,
                      installed: dict[str, str]) -> dict[str, dict]:
    """对每个工具的 lock 做实际环境满足度检查。

    Args:
        installed: {pkg_lower: installed_version}, 通常来自 pip list --format=json

    Returns:
        {tool: {
            satisfied: bool,
            total:     int,                   # lock 里要求的总包数
            ok:        int,                   # 当前满足的
            missing:   list[(pkg, want, got)] # 不满足或缺失的; got=None 表示未装
        }}
    """
    installed_norm = {_norm(k): v for k, v in installed.items()}
    out: dict[str, dict] = {}
    for fname, tool in LOCK_TO_TOOL.items():
        p = os.path.join(locks_dir, fname)
        reqs = parse_lock(p)
        missing: list[tuple[str, str, str | None]] = []
        ok = 0
        for pkg, spec in reqs.items():
            got = installed_norm.get(pkg)
            if got is None:
                missing.append((pkg, spec, None))
                continue
            want = _extract_pinned_version(spec)
            if want is None:
                # 没钉死版本,只要装了就算满足(>=, ~= 等约束这里不展开判定,
                # 因为 LaunchAI 的 lock 文件里其实都用 ==,极少 >=)
                ok += 1
                continue
            if _versions_equal(got, want):
                ok += 1
            else:
                missing.append((pkg, spec, got))
        out[tool] = {
            "satisfied": not missing,
            "total":     len(reqs),
            "ok":        ok,
            "missing":   missing,
        }
    return out


def _versions_equal(a: str, b: str) -> bool:
    """容错版本对比: 优先用 packaging.Version 判等(忽略 .post / build 差异);
    解析失败回退到字符串相等。"""
    try:
        return Version(a) == Version(b)
    except InvalidVersion:
        return a == b
