"""包管理 / 依赖诊断二级页面。

挂在设置页里(QStackedWidget 的 index 1),提供三件事:
  1. 工具管理   —— 每个 LaunchAI 工具的安装态 + 卸载(彻底删除克隆文件)/ 去安装
  2. 依赖冲突分析 —— 跨工具锁版冲突 + 每工具满足度(纯解析,见 utils/conflict_analyzer)
  3. 全部已安装包 —— pip list 全量 + 逐包卸载 + 搜索过滤

卸载统一走 workers.pip_worker.UninstallWorker(信号形状与 PipWorker 一致),
底部复用 subpage_setting_page.LogTextEdit 做流式终端。
"""
import os

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QStackedWidget, QFrame, QSizePolicy,
    QTreeWidgetItem, QHeaderView,
)
from qfluentwidgets import (
    Pivot, CardWidget, ElevatedCardWidget, SmoothScrollArea, SearchLineEdit,
    PushButton, PrimaryPushButton, TransparentToolButton, IconWidget,
    FluentIcon as FIF, TitleLabel, StrongBodyLabel, BodyLabel, CaptionLabel,
    InfoBar, InfoBarPosition, MessageBox, TreeWidget, RoundMenu, Action,
)

from logger import info, warning, error
from utils.configer import get_field, set_field
from workers.pip_worker import (
    PipWorker, UninstallWorker, LOCKS_ROOT, GIT_PROJECTS_ROOT, fork_map,
)
from utils import conflict_analyzer as ca
from widgets.subpage.subpage_setting_page import LogTextEdit


# 工具元数据。kind:
#   "pip"     —— 用 PipWorker.is_package_installed 检测;卸载 pip uninstall + 删克隆目录
#   "flag"    —— 仓库式安装,读 configs/config.json::installed.<pkg>(克隆中断也不误判)
#   "builtin" —— Real-ESRGAN 二进制随包内置,恒为已安装;只允许清理可选源码残留
# fork 命中 fork_map 时,克隆目录 = _git_projects/<pkg>_<fork>,卸载时一并 rmtree。
# win_attr = app.py 里 Window 上对应 SwitchPage 的属性名(大小写不统一,显式列出)。
TOOLS = [
    # pkg,            page_name,    win_attr,              display,             kind
    ("demucs",        "demucs",     "demucsinterface",     "音频分离 - Demucs",       "pip"),
    ("openai-whisper", "whisper",   "whisperInterface",    "语音识别 - Whisper",      "pip"),
    ("ultralytics",   "yolo",       "yoloInterface",       "目标检测 - YOLO",         "pip"),
    ("Applio",        "rvc",        "rvcInterface",        "AI 变声 - RVC",          "flag"),
    ("GPT-SoVITS",    "gptsovits",  "gptsovitsInterface",  "语音合成 - GPT-SoVITS",   "flag"),
    ("audiocraft",    "audiocraft", "audiocraftInterface", "音乐生成 - Audiocraft",   "pip"),
    ("Real-ESRGAN",   "ESRGAN",     "ESRGANinterface",     "图像超分 - Real-ESRGAN",  "builtin"),
    ("iopaint",       "iopaint",    "iopaintInterface",    "图像修复 - IOPaint",      "pip"),
]


def _clone_dir(pkg: str) -> str | None:
    """工具的克隆源码目录 _git_projects/<pkg>_<fork>;不在 fork_map 则无。"""
    fork = fork_map.get(pkg)
    if not fork:
        return None
    return os.path.join(GIT_PROJECTS_ROOT, f"{pkg}_{fork}")


class _ScanWorker(QThread):
    """后台拉 pip list + 跑依赖诊断,避免阻塞 UI(pip list 可能数秒)。"""
    done = pyqtSignal(object)   # dict | None

    def run(self):
        try:
            installed_list = PipWorker.list_installed_packages()
            installed_map = {d["name"]: d["version"] for d in installed_list}
            edges = PipWorker.list_dependency_edges()
            static = ca.static_conflicts(LOCKS_ROOT)
            dyn = ca.dynamic_diagnosis(LOCKS_ROOT, installed_map)
            self.done.emit({
                "installed": installed_list,   # [{name, version}]
                "edges": edges,                # {norm_name: [norm_dep, ...]}
                "static": static,
                "dynamic": dyn,
            })
        except Exception as e:
            error(f"[pkgmgr] 扫描失败: {e}")
            self.done.emit(None)


class PackageManagerWidget(QWidget):
    """设置页内的包管理二级页。on_back 由设置页传入(切回 index 0)。"""

    def __init__(self, on_back=None, parent=None):
        super().__init__(parent=parent)
        self.setObjectName("packageManagerInterface")
        self._on_back = on_back
        self._worker = None        # UninstallWorker
        self._scan = None          # _ScanWorker
        self._scan_cache = None    # 最近一次扫描结果,工具管理/全部包共用

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 18, 24, 16)
        root.setSpacing(12)

        # ── 顶栏:返回 + 标题 + 刷新 ────────────────────────────────
        head = QHBoxLayout()
        head.setSpacing(8)
        self.back_btn = TransparentToolButton(FIF.RETURN, self)
        self.back_btn.setToolTip("返回设置")
        self.back_btn.clicked.connect(self._handle_back)
        head.addWidget(self.back_btn)
        head.addWidget(TitleLabel("包管理与依赖诊断", self))
        head.addStretch()
        self.refresh_btn = PushButton("刷新", self, FIF.SYNC)
        self.refresh_btn.clicked.connect(self.refresh)
        head.addWidget(self.refresh_btn)
        root.addLayout(head)

        # ── Pivot + 三视图 ──────────────────────────────────────────
        self.pivot = Pivot(self)
        self.stacked = QStackedWidget(self)

        self.tools_view = self._build_scroll_host()
        self.conflict_view = self._build_scroll_host()
        self.all_view = self._build_all_packages_view()

        self._add_pivot_item("tools", "工具管理", self.tools_view)
        self._add_pivot_item("conflict", "依赖冲突分析", self.conflict_view)
        self._add_pivot_item("all", "全部已安装包", self.all_view)
        self.stacked.setCurrentWidget(self.tools_view)
        self.pivot.setCurrentItem("tools")

        root.addWidget(self.pivot)
        root.addWidget(self.stacked, 1)

        # ── 底部终端(默认隐藏,卸载时显示)──────────────────────────
        # 整块包在 terminal_box 里:顶部一条标题栏带「折叠 / 关闭」按钮,
        # 否则日志一旦弹出就赖在界面上下不去(用户反馈)。
        self.terminal_box = QWidget(self)
        box_lay = QVBoxLayout(self.terminal_box)
        box_lay.setContentsMargins(0, 0, 0, 0)
        box_lay.setSpacing(0)

        bar = QHBoxLayout()
        bar.setContentsMargins(8, 2, 4, 2)
        bar.setSpacing(4)
        bar.addWidget(CaptionLabel("终端日志", self.terminal_box))
        bar.addStretch()
        self.term_collapse_btn = TransparentToolButton(FIF.CHEVRON_DOWN_MED, self.terminal_box)
        self.term_collapse_btn.setToolTip("折叠 / 展开日志")
        self.term_collapse_btn.clicked.connect(self._toggle_terminal_collapsed)
        bar.addWidget(self.term_collapse_btn)
        self.term_close_btn = TransparentToolButton(FIF.CLOSE, self.terminal_box)
        self.term_close_btn.setToolTip("隐藏日志")
        self.term_close_btn.clicked.connect(self._hide_terminal)
        bar.addWidget(self.term_close_btn)
        box_lay.addLayout(bar)

        self.terminal = LogTextEdit(self.terminal_box)
        self.terminal.setMaximumHeight(170)
        self.terminal.setStyleSheet("""
            TextEdit {
                background-color: #1e1e1e;
                color: #d4d4d4;
                font-family: Consolas, monospace;
                font-size: 11px;
                border-radius: 4px;
            }
        """)
        box_lay.addWidget(self.terminal)

        self.terminal_box.setVisible(False)
        self._term_collapsed = False
        root.addWidget(self.terminal_box)

        # 占位:首次 refresh() 前给个提示
        self._render_tools_placeholder("点击右上角「刷新」加载工具状态")
        self._render_conflict_placeholder()

    # ── Pivot / 滚动容器脚手架 ──────────────────────────────────────
    def _add_pivot_item(self, route: str, text: str, widget: QWidget):
        self.stacked.addWidget(widget)
        self.pivot.addItem(
            routeKey=route, text=text,
            onClick=lambda *_a, w=widget: self.stacked.setCurrentWidget(w),
        )

    def _build_scroll_host(self) -> SmoothScrollArea:
        """一个竖向滚动容器,内部 content 的布局存到 .body_layout 方便重建。"""
        scroll = SmoothScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("QScrollArea{background:transparent;border:none}")
        scroll.viewport().setStyleSheet("background:transparent")
        content = QWidget(scroll)
        content.setStyleSheet("background:transparent")
        lay = QVBoxLayout(content)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(10)
        scroll.setWidget(content)
        scroll.body_layout = lay
        return scroll

    @staticmethod
    def _clear_layout(lay):
        while lay.count():
            item = lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

    # ── 全部已安装包视图(依赖树)──────────────────────────────────
    def _build_all_packages_view(self) -> QWidget:
        host = QWidget(self)
        v = QVBoxLayout(host)
        v.setContentsMargins(4, 4, 4, 4)
        v.setSpacing(8)

        top = QHBoxLayout()
        top.setSpacing(6)
        self.all_search = SearchLineEdit(host)
        self.all_search.setPlaceholderText("筛选包名…")
        self.all_search.textChanged.connect(self._on_all_search)
        top.addWidget(self.all_search, 1)
        self.expand_btn = TransparentToolButton(FIF.CHEVRON_DOWN_MED, host)
        self.expand_btn.setToolTip("展开全部")
        self.expand_btn.clicked.connect(lambda: self.all_tree.expandAll())
        top.addWidget(self.expand_btn)
        self.collapse_btn = TransparentToolButton(FIF.CHEVRON_RIGHT_MED, host)
        self.collapse_btn.setToolTip("折叠全部")
        self.collapse_btn.clicked.connect(lambda: self.all_tree.collapseAll())
        top.addWidget(self.collapse_btn)
        v.addLayout(top)

        self.all_count_lbl = CaptionLabel("", host)
        self.all_count_lbl.setStyleSheet("color:rgba(128,128,128,180);")
        v.addWidget(self.all_count_lbl)

        # 顶层 = 不被任何已安装包依赖的包;子节点 = 它的(已安装)依赖,逐层递进。
        # 右键单个节点可卸载。共享依赖会在多个父节点下重复出现,环用 ↻ 标注后不再展开。
        self.all_tree = TreeWidget(host)
        self.all_tree.setColumnCount(2)
        self.all_tree.setHeaderLabels(["包 / 依赖", "版本"])
        self.all_tree.header().setStretchLastSection(False)
        self.all_tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.all_tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.all_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.all_tree.customContextMenuRequested.connect(self._on_tree_menu)
        v.addWidget(self.all_tree, 1)
        return host

    # ── 渲染:占位 ──────────────────────────────────────────────────
    def _render_tools_placeholder(self, text: str):
        lay = self.tools_view.body_layout
        self._clear_layout(lay)
        lbl = BodyLabel(text, self.tools_view)
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(lbl)
        lay.addStretch()

    def _render_conflict_placeholder(self):
        lay = self.conflict_view.body_layout
        self._clear_layout(lay)
        card = ElevatedCardWidget(self.conflict_view)
        cl = QVBoxLayout(card)
        cl.setContentsMargins(22, 16, 22, 16)
        cl.setSpacing(8)
        cl.addWidget(StrongBodyLabel("依赖冲突分析", card))
        cl.addWidget(CaptionLabel(
            "多个工具共用同一个 venv,锁版依赖里同名包被钉到不同版本时会静默冲突。"
            "点下方按钮扫描当前环境并比对 locks/ 下各工具的锁版依赖。", card))
        btn = PrimaryPushButton("开始分析", card, FIF.SEARCH)
        btn.clicked.connect(self.refresh)
        cl.addWidget(btn, 0, Qt.AlignmentFlag.AlignLeft)
        lay.addWidget(card)
        lay.addStretch()

    # ── 刷新:起后台扫描 ────────────────────────────────────────────
    def refresh(self):
        if self._scan is not None and self._scan.isRunning():
            return
        self._render_tools_placeholder("正在加载…")
        self.all_count_lbl.setText("正在加载…")
        self.refresh_btn.setEnabled(False)
        self._scan = _ScanWorker(self)
        self._scan.done.connect(self._on_scan_done)
        self._scan.start()

    def _on_scan_done(self, data):
        self.refresh_btn.setEnabled(True)
        if data is None:
            self._render_tools_placeholder("加载失败,无法获取包列表(pip list 异常)")
            self.all_count_lbl.setText("加载失败")
            return
        self._scan_cache = data
        self._render_tools(data)
        self._render_conflict(data)
        self._render_all_packages(data["installed"], data.get("edges", {}))

    # ── 渲染:工具管理 ──────────────────────────────────────────────
    def _is_tool_installed(self, pkg: str, kind: str, installed_names: set) -> bool:
        if kind == "builtin":
            return True
        if kind == "flag":
            # 仓库式工具(Applio / GPT-SoVITS):优先看 installed flag;
            # 但「项目部署安装」时 flag 可能从没被写过(配置被重置 / 旧版本装的),
            # 此时回退到克隆目录是否存在 —— 目录在就是真装了。
            if bool(get_field(f"installed.{pkg}", False)):
                return True
            clone = _clone_dir(pkg)
            return bool(clone and os.path.isdir(clone))
        return ca._norm(pkg) in installed_names

    def _render_tools(self, data):
        installed_names = {ca._norm(d["name"]) for d in data["installed"]}
        lay = self.tools_view.body_layout
        self._clear_layout(lay)

        for pkg, page_name, win_attr, display, kind in TOOLS:
            installed = self._is_tool_installed(pkg, kind, installed_names)
            card = CardWidget(self.tools_view)
            row = QHBoxLayout(card)
            row.setContentsMargins(16, 12, 16, 12)
            row.setSpacing(12)

            name_box = QVBoxLayout()
            name_box.setSpacing(2)
            name_box.addWidget(StrongBodyLabel(display, card))
            sub = pkg + ("  · 内置" if kind == "builtin" else "")
            cap = CaptionLabel(sub, card)
            cap.setStyleSheet("color:rgba(128,128,128,180);")
            name_box.addWidget(cap)
            row.addLayout(name_box)
            row.addStretch()

            # 状态徽章
            if kind == "builtin":
                badge = CaptionLabel("● 内置", card)
                badge.setStyleSheet("color:#4FC3F7;font-weight:bold;")
            elif installed:
                badge = CaptionLabel("● 已安装", card)
                badge.setStyleSheet("color:#4CAF50;font-weight:bold;")
            else:
                badge = CaptionLabel("○ 未安装", card)
                badge.setStyleSheet("color:rgba(128,128,128,180);")
            row.addWidget(badge)

            # 操作按钮
            if kind == "builtin":
                clone = _clone_dir(pkg)
                if clone and os.path.isdir(clone):
                    btn = PushButton("清理源码残留", card)
                    btn.clicked.connect(
                        lambda _=False, p=pkg, d=display, c=clone:
                        self._confirm_purge_only(p, d, c))
                    row.addWidget(btn)
                else:
                    hint = CaptionLabel("不可卸载", card)
                    hint.setStyleSheet("color:rgba(128,128,128,140);")
                    row.addWidget(hint)
            elif installed:
                btn = PushButton("卸载", card)
                btn.setStyleSheet(
                    "PushButton{color:#F44336;} PushButton:hover{color:#F44336;}")
                btn.clicked.connect(
                    lambda _=False, p=pkg, d=display, k=kind, w=win_attr:
                    self._confirm_uninstall_tool(p, d, k, w))
                row.addWidget(btn)
            else:
                btn = PrimaryPushButton("去安装", card)
                btn.clicked.connect(
                    lambda _=False, pn=page_name: self._goto_install(pn))
                row.addWidget(btn)

            lay.addWidget(card)
        lay.addStretch()

    def _goto_install(self, page_name: str):
        win = self.window()
        nav = getattr(win, "navigate_to", None)
        if callable(nav):
            nav(page_name)
        else:
            warning(f"[pkgmgr] 主窗口没有 navigate_to,无法跳转 {page_name}")

    # ── 渲染:依赖冲突分析 ──────────────────────────────────────────
    def _render_conflict(self, data):
        lay = self.conflict_view.body_layout
        self._clear_layout(lay)

        static = data["static"]
        conflicts = [r for r in static if r["conflict"]]

        # ① 跨工具版本冲突
        card1 = ElevatedCardWidget(self.conflict_view)
        c1 = QVBoxLayout(card1)
        c1.setContentsMargins(22, 16, 22, 16)
        c1.setSpacing(8)
        head = QHBoxLayout()
        head.addWidget(StrongBodyLabel("跨工具版本冲突", card1))
        head.addStretch()
        if conflicts:
            tag = CaptionLabel(f"⚠ {len(conflicts)} 处冲突", card1)
            tag.setStyleSheet("color:#F44336;font-weight:bold;")
        else:
            tag = CaptionLabel("✓ 未发现钉死版本冲突", card1)
            tag.setStyleSheet("color:#4CAF50;font-weight:bold;")
        head.addWidget(tag)
        c1.addLayout(head)
        c1.addWidget(CaptionLabel(
            "仅当 ≥2 个工具用 == 钉死了不同版本才算冲突;一方放宽约束不计。", card1))

        if conflicts:
            for r in conflicts:
                line = self._conflict_row(card1, r)
                c1.addWidget(line)
        lay.addWidget(card1)

        # ② 每工具满足度
        card2 = ElevatedCardWidget(self.conflict_view)
        c2 = QVBoxLayout(card2)
        c2.setContentsMargins(22, 16, 22, 16)
        c2.setSpacing(8)
        c2.addWidget(StrongBodyLabel("每工具锁版满足度", card2))
        c2.addWidget(CaptionLabel(
            "对照 locks/<tool>.txt 检查当前 venv 是否满足该工具的锁版依赖。", card2))

        dyn = data["dynamic"]
        for tool, d in sorted(dyn.items()):
            if d["total"] == 0:
                continue  # 没有 lock 文件的工具跳过
            sat = d["satisfied"]
            r = QHBoxLayout()
            r.setSpacing(8)
            mark = CaptionLabel("✓" if sat else "✗", card2)
            mark.setStyleSheet(
                f"color:{'#4CAF50' if sat else '#F44336'};font-weight:bold;")
            r.addWidget(mark)
            r.addWidget(BodyLabel(tool, card2))
            r.addStretch()
            r.addWidget(CaptionLabel(f"{d['ok']}/{d['total']} 满足", card2))
            wrap = QWidget(card2)
            wrap.setLayout(r)
            c2.addWidget(wrap)
            # 缺失/不符的包(最多列 6 个,避免刷屏)
            if d["missing"]:
                miss = d["missing"][:6]
                parts = []
                for name, want, got in miss:
                    parts.append(f"{name}: 需 {want or '任意'} / "
                                 f"{'未装' if got is None else got}")
                more = "" if len(d["missing"]) <= 6 else f" …(+{len(d['missing'])-6})"
                ml = CaptionLabel("    " + "; ".join(parts) + more, card2)
                ml.setStyleSheet("color:rgba(244,67,54,200);")
                ml.setWordWrap(True)
                c2.addWidget(ml)
        lay.addWidget(card2)
        lay.addStretch()

    def _conflict_row(self, parent, r) -> QWidget:
        w = QWidget(parent)
        v = QVBoxLayout(w)
        v.setContentsMargins(8, 6, 8, 6)
        v.setSpacing(2)
        w.setStyleSheet("background:rgba(244,67,54,28);border-radius:6px;")
        title = StrongBodyLabel(r["package"], w)
        title.setStyleSheet("color:#F44336;")
        v.addWidget(title)
        for tool, spec in r["versions"].items():
            v.addWidget(CaptionLabel(f"    {tool}: {spec or '(无版本约束)'}", w))
        return w

    # ── 渲染:全部已安装包(依赖树)──────────────────────────────────
    def _render_all_packages(self, installed, edges):
        self._all_packages = sorted(installed, key=lambda d: d["name"].lower())
        # 规范化名 -> (显示名, 版本)
        self._info_map = {
            ca._norm(d["name"]): (d["name"], d["version"]) for d in installed
        }
        self._edges = edges or {}

        # 顶层:不出现在任何包依赖列表里的包(没人依赖它 → 它是入口)
        depended = set()
        for deps in self._edges.values():
            depended.update(deps)
        all_norm = set(self._info_map.keys())
        roots = sorted(all_norm - depended)

        self.all_tree.clear()
        placed = set()
        for norm in roots:
            self._add_tree_node(None, norm, frozenset())
            placed.add(norm)
        # 纯环 / 互相依赖且无外部入口的包不会被 roots 覆盖,补成顶层避免丢失
        for norm in sorted(all_norm - set(roots)):
            if not self._node_reachable(norm, roots):
                self._add_tree_node(None, norm, frozenset())

        self._on_all_search(self.all_search.text())

    def _node_reachable(self, target, roots) -> bool:
        """target 是否能从任一 root 沿依赖边到达(判断是否已在树里出现过)。"""
        stack = list(roots)
        seen = set()
        while stack:
            n = stack.pop()
            if n == target:
                return True
            if n in seen:
                continue
            seen.add(n)
            stack.extend(self._edges.get(n, []))
        return False

    def _add_tree_node(self, parent_item, norm, path):
        disp, ver = self._info_map.get(norm, (norm, ""))
        item = QTreeWidgetItem([disp, ver])
        item.setData(0, Qt.ItemDataRole.UserRole, disp)
        if parent_item is None:
            self.all_tree.addTopLevelItem(item)
        else:
            parent_item.addChild(item)
        if norm in path:            # 检测到环:标注后不再深入
            item.setText(0, f"{disp}  ↻")
            return
        deps = sorted(self._edges.get(norm, []))
        if deps:
            child_path = path | {norm}
            for dep in deps:
                self._add_tree_node(item, dep, child_path)

    def _on_all_search(self, text: str):
        if not hasattr(self, "_all_packages"):
            return
        kw = (text or "").strip().lower()
        root = self.all_tree.invisibleRootItem()
        for i in range(root.childCount()):
            self._filter_tree_item(root.child(i), kw)

        total = len(self._all_packages)
        if kw:
            shown = sum(1 for d in self._all_packages if kw in d["name"].lower())
            self.all_count_lbl.setText(f"共 {total} 个包,匹配 {shown} 个")
        else:
            self.all_count_lbl.setText(f"共 {total} 个包")

    def _filter_tree_item(self, item, kw) -> bool:
        """递归过滤:自身或任一子孙命中关键字则显示;命中时展开。返回是否可见。"""
        self_match = (not kw) or (kw in item.text(0).lower())
        child_match = False
        for i in range(item.childCount()):
            if self._filter_tree_item(item.child(i), kw):
                child_match = True
        visible = self_match or child_match
        item.setHidden(not visible)
        if kw and child_match:
            item.setExpanded(True)
        return visible

    def _on_tree_menu(self, pos):
        item = self.all_tree.itemAt(pos)
        if item is None:
            return
        name = item.data(0, Qt.ItemDataRole.UserRole)
        if not name:
            return
        menu = RoundMenu(parent=self.all_tree)
        act = Action(FIF.DELETE, f"卸载 {name}")
        act.triggered.connect(lambda _=False, n=name: self._confirm_uninstall_one(n))
        menu.addAction(act)
        menu.exec(self.all_tree.viewport().mapToGlobal(pos))

    # ── 卸载流程 ────────────────────────────────────────────────────
    def _busy(self) -> bool:
        if self._worker is not None and self._worker.isRunning():
            InfoBar.warning("请稍候", "已有卸载任务在运行",
                            parent=self.window(), duration=2500,
                            position=InfoBarPosition.TOP)
            return True
        return False

    def _confirm_uninstall_one(self, name: str):
        if self._busy():
            return
        box = MessageBox("确认卸载", f"将从 venv 中卸载包:\n\n{name}\n\n"
                         "若有工具依赖它,可能导致该工具不可用。", self.window())
        box.yesButton.setText("卸载")
        box.cancelButton.setText("取消")
        if not box.exec():
            return
        self._start_uninstall([name], purge_dirs=None,
                              clear_flag=None, win_attr=None)

    def _confirm_uninstall_tool(self, pkg, display, kind, win_attr):
        if self._busy():
            return
        clone = _clone_dir(pkg)
        extra = f"\n\n并删除源码目录:\n{clone}" if clone and os.path.isdir(clone) else ""
        box = MessageBox(
            "确认卸载工具",
            f"将卸载「{display}」:\n\n"
            f"· pip 卸载 {pkg}{extra}\n· 清除已安装标记\n\n此操作不可撤销,确定继续?",
            self.window())
        box.yesButton.setText("彻底卸载")
        box.cancelButton.setText("取消")
        if not box.exec():
            return
        purge = [clone] if clone and os.path.isdir(clone) else None
        self._start_uninstall([pkg], purge_dirs=purge,
                              clear_flag=pkg, win_attr=win_attr)

    def _confirm_purge_only(self, pkg, display, clone):
        """Real-ESRGAN 等内置工具:只删可选的源码残留,不动 pip / 内置二进制。"""
        if self._busy():
            return
        box = MessageBox(
            "清理源码残留",
            f"「{display}」为内置工具,二进制随程序分发、不可卸载。\n\n"
            f"仅删除源码安装时克隆的目录:\n{clone}\n\n确定继续?",
            self.window())
        box.yesButton.setText("删除")
        box.cancelButton.setText("取消")
        if not box.exec():
            return
        self._start_uninstall([], purge_dirs=[clone],
                              clear_flag=None, win_attr=None)

    def _start_uninstall(self, packages, purge_dirs, clear_flag, win_attr):
        self._show_terminal()
        self.terminal.clear()
        self._pending = {"clear_flag": clear_flag, "win_attr": win_attr}
        self._worker = UninstallWorker(packages, purge_dirs=purge_dirs, parent=self)
        self._worker.output_signal.connect(self.terminal.append_colored)
        self._worker.finished_signal.connect(self._on_uninstall_finished)
        self._worker.start()

    def _on_uninstall_finished(self, success, message):
        pending = getattr(self, "_pending", {}) or {}
        # 释放 worker 引用(线程已结束)
        w = self._worker
        if w is not None:
            if w.isRunning():
                w.wait(3000)
            self._worker = None

        if success:
            # 清 flag(仓库式工具:UninstallWorker 仅在 pip rc==0 时清,这里兜底保证清掉)
            flag = pending.get("clear_flag")
            if flag:
                set_field(f"installed.{flag}", False)
            # 把对应工具页翻回「未安装」,无需重启
            self._flip_tool_page(pending.get("win_attr"))
            InfoBar.success("完成", message, parent=self.window(),
                            duration=4000, position=InfoBarPosition.TOP)
        else:
            InfoBar.error("未完成", message, parent=self.window(),
                          duration=-1, position=InfoBarPosition.TOP)
        # 重新扫描刷新列表与状态
        self.refresh()

    def _flip_tool_page(self, win_attr):
        if not win_attr:
            return
        win = self.window()
        iface = getattr(win, win_attr, None)
        if iface is None:
            return
        try:
            # SwitchPage 有 switch_page;LazySwitchPage 未构造真页时 __getattr__ 会抛,
            # try 兜底(下次点开会按安装态重新判定)
            iface.switch_page(0)
        except Exception as e:
            info(f"[pkgmgr] 翻回未安装页跳过 ({win_attr}): {e}")

    # ── 终端折叠 / 显隐 ─────────────────────────────────────────────
    def _show_terminal(self):
        """展开终端(卸载开始时调用),顺带把折叠态复位。"""
        self._term_collapsed = False
        self.terminal.setVisible(True)
        self.term_collapse_btn.setIcon(FIF.CHEVRON_DOWN_MED)
        self.terminal_box.setVisible(True)

    def _hide_terminal(self):
        """整块隐藏日志面板。"""
        self.terminal_box.setVisible(False)

    def _toggle_terminal_collapsed(self):
        """只折叠日志正文,保留标题栏,方便随时再展开。"""
        self._term_collapsed = not self._term_collapsed
        self.terminal.setVisible(not self._term_collapsed)
        self.term_collapse_btn.setIcon(
            FIF.CHEVRON_RIGHT_MED if self._term_collapsed else FIF.CHEVRON_DOWN_MED)

    def _handle_back(self):
        if callable(self._on_back):
            self._on_back()
