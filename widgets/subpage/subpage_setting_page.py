import sys
from logger import info, warning, debug, error
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtCore import QUrl, QThread, pyqtSignal
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QFrame, QStackedWidget, QLabel, QApplication
from PyQt6.QtCore import Qt
from qfluentwidgets import (
    SettingCard, FluentIcon as FIF, ElevatedCardWidget, TextEdit, ComboBox,
    EditableComboBox, LineEdit, PasswordLineEdit, SwitchButton,
    PushButton, PrimaryPushButton, ToolTipFilter, IconWidget, MessageBox, InfoBar,
    StrongBodyLabel, BodyLabel, CaptionLabel, SingleDirectionScrollArea,
    ExpandGroupSettingCard, PrimaryToolButton, ToolButton,
)
from widgets.log_text_edit import LogTextEdit
from workers.pip_worker import PipWorker
from utils.configer import get_field, set_field
import subprocess
CUDA_MAP = {"CPU": "cpu", "CUDA11.8": "118", "CUDA12.4": "124",
            "CUDA12.6": "126", "CUDA13.0": "130", "CUDA13.2": "132"}
MIRROR_MAP = {"阿里云镜像源": "https://mirrors.aliyun.com/pytorch-wheels",
              "清华大学镜像源": "https://pypi.tuna.tsinghua.edu.cn/simple",
              "官方Pytorch源": "https://download.pytorch.org/whl"}


def apply_app_font(root: QWidget):
    """把 qfluentwidgets 卡片里写死成 Segoe UI 的标题/内容标签字体族换成 app 全局字体。

    app.py 启动时 ``app.setFont`` 把全局字体设成 JetBrains Maple Mono,但 qfluentwidgets
    的 SettingCard / ExpandGroupSettingCard 会通过卡片 QSS 的 ``font:`` 简写给子 QLabel 设
    Segoe UI,优先级高于 app.setFont,导致卡片标题字体和应用其它部分不一致。这里在 label
    级用 inline stylesheet 只覆写 font-family(inline 优先级最高),保留各自原有字号/颜色。
    日志等显式 monospace 的标签跳过;LogTextEdit 不是 QLabel 不受影响。
    """
    app = QApplication.instance()
    if app is None:
        return
    family = app.font().family()
    if not family:
        return
    for lbl in root.findChildren(QLabel):
        ss = lbl.styleSheet()
        low = ss.lower()
        if "monospace" in low or "consolas" in low:
            continue
        f = lbl.font()
        px, pt = f.pixelSize(), f.pointSize()
        if px > 0:
            size_rule = f"font-size:{px}px;"
        elif pt > 0:
            size_rule = f"font-size:{pt}pt;"
        else:
            size_rule = ""
        prefix = (ss + ";") if ss and not ss.rstrip().endswith(";") else ss
        lbl.setStyleSheet(f"{prefix}font-family:'{family}';{size_rule}")


class HelpIcon(IconWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setIcon(FIF.HELP)
        self.setFixedSize(16, 16)
        self.setToolTip("点击查看详细教程")

    def mousePressEvent(self, event):
        QDesktopServices.openUrl(
            QUrl("https://blog.csdn.net/taotao_guiwang/article/details/156749455"))
        super().mousePressEvent(event)


class InstallPyTorchCard(ElevatedCardWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.reinstall = False
        # self.setMaximumHeight(180)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 16, 22, 16)
        layout.setSpacing(20)

        python_version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
        self.python_card = SettingCard(
            FIF.CODE,
            "Python 版本",
            python_version,
            parent=self
        )
        layout.addWidget(self.python_card)

        torch_version = self._get_pip_packages().get("torch", "未安装")
        Button_Text = "安装"
        if torch_version != "未安装":
            Button_Text = "更改版本"
            self.reinstall = True
        self.torch_card = SettingCard(
            FIF.CLOUD,
            "PyTorch 版本",
            torch_version,
            parent=self
        )

        btn_row = QHBoxLayout()

        self.comboBox = ComboBox()
        ver_list = ["CPU", "CUDA11.8", "CUDA12.4",
                    "CUDA12.6",  "CUDA13.0", "CUDA13.2"]
        self.comboBox.addItems(ver_list)
        self.comboBox.setPlaceholderText("选择需要的版本")

        self.combo_help_icon = HelpIcon(self)
        self.combo_help_icon.setFixedSize(16, 16)
        self.combo_help_icon.setToolTip(
            "我应该选择什么版本?\n10-30系列可用万金油CUDA11.8 其中10系必须使用11.8\n40系列推荐CUDA12.4+\n50系必须使用CUDA12.4+\n详细参考文章:https://blog.csdn.net/taotao_guiwang/article/details/156749455 \n (点击图标跳转至文章)")
        self.combo_help_icon.installEventFilter(
            ToolTipFilter(self.combo_help_icon))
        self.combo_help_icon.mousePressEvent = lambda event: QDesktopServices.openUrl(
            QUrl("https://blog.csdn.net/taotao_guiwang/article/details/156749455"))

        self.mirror_comboBox = ComboBox()
        self.mirror_list = ["阿里云镜像源",  "官方Pytorch源",]
        self.mirror_comboBox.addItems(self.mirror_list)

        self.install_btn = PushButton(Button_Text)
        self.install_btn.clicked.connect(self.on_install_clicked)
        btn_row.addWidget(self.comboBox)
        btn_row.addWidget(self.combo_help_icon)
        btn_row.addWidget(self.mirror_comboBox)
        btn_row.addWidget(self.install_btn)

        self.terminal_widget = QWidget(self)
        self.terminal_widget.setVisible(False)
        terminal_layout = QVBoxLayout(self.terminal_widget)
        terminal_layout.setContentsMargins(0, 10, 0, 0)
        self.terminal_text = LogTextEdit()
        self.terminal_text.setAcceptRichText(True)
        self.terminal_text.setReadOnly(True)
        self.terminal_text.setMaximumHeight(200)
        self.terminal_text.setStyleSheet("""
            TextEdit {
                background-color: #1e1e1e;
                color: #d4d4d4;
                font-family: Consolas, monospace;
                font-size: 11px;
                border-radius: 4px;
            }
        """)
        terminal_layout.addWidget(self.terminal_text)

        layout.addWidget(self.torch_card)
        layout.addLayout(btn_row)
        layout.addWidget(self.terminal_widget)

    @staticmethod
    def _get_pip_packages():
        """获取 Python 包信息"""
        targets = {
            "torch", "torchvision", "torchaudio"
        }
        result = {}
        try:
            output = subprocess.check_output(
                [sys.executable, "-m", "pip", "list", "--format=freeze"],
                timeout=10, stderr=subprocess.DEVNULL
            ).decode("utf-8", errors="ignore")
            for line in output.splitlines():
                if "==" in line:
                    name, ver = line.split("==", 1)
                    if name.lower() in targets:
                        result[name.lower()] = ver
        except Exception:
            pass
        return result

    def on_install_clicked(self,):
        selected_text = self.comboBox.currentText()
        selected_mirror = self.mirror_comboBox.currentText()

        cuda_ver = CUDA_MAP.get(selected_text, "cpu")

        msg_box = MessageBox(
            "确认安装",
            f"CUDA 版本: {cuda_ver}\n镜像源: {selected_mirror}\n\n是否继续安装？",
            self.window()
        )
        msg_box.yesButton.setText("继续")
        msg_box.cancelButton.setText("取消")

        if msg_box.exec():
            self.start_install(cuda_ver, selected_mirror)

    def start_install(self, cuda_ver: int, mirror: str):
        """开始安装 PyTorch"""
        info(f"开始安装 - CUDA: {cuda_ver}, 镜像源: {mirror}")
        # 防止重复点击启动多个 worker(原 bug:worker 是局部变量,二次点击会再启一个)
        existing = getattr(self, 'worker', None)
        if existing is not None and existing.isRunning():
            warning("已有安装任务在运行,忽略本次启动请求")
            return
        base_mirror_url = MIRROR_MAP.get(mirror, None)
        mirror_url = f"{base_mirror_url}/cu{cuda_ver}"
        packages = ["torch", "torchvision", "torchaudio"]
        if int(cuda_ver) == 132:
            packages.remove("torchaudio")
        # 必须挂到 self 上,否则函数返回后局部 worker 被回收,
        # 触发 "QThread: Destroyed while thread is still running"
        self.worker = PipWorker(packages, mirror_url,
                                is_torch=cuda_ver, force=self.reinstall)
        self.worker.output_signal.connect(self.terminal_text.append_colored)
        self.worker.finished_signal.connect(self.on_install_finished)

        self.worker.start()
        # 显示终端并改变按钮
        self.terminal_widget.setVisible(True)
        self.install_btn.setText("取消")
        try:
            self.install_btn.clicked.disconnect()
        except TypeError:
            pass
        self.install_btn.clicked.connect(self.cancel_install)

    def cancel_install(self):
        """取消安装:让 PipWorker 自己 kill 子进程并发 finished_signal,UI 在 on_install_finished 复位"""
        worker = getattr(self, 'worker', None)
        if worker is not None and worker.isRunning():
            # PipWorker.cancel() 会终止当前 subprocess 并最终发 finished_signal("已取消安装")
            worker.cancel()
            self.install_btn.setEnabled(False)
            self.install_btn.setText("正在终止...")
        else:
            # 没有运行中的安装,直接复位 UI
            self.on_install_finished(False, "用户取消安装")

    def on_install_finished(self, success, message):
        """安装完成"""

        self.install_btn.setText("安装")
        try:
            self.install_btn.clicked.disconnect()
        except TypeError:
            pass
        self.install_btn.clicked.connect(self.on_install_clicked)
        self.install_btn.setEnabled(True)

        # 释放 worker 引用:此时线程已结束(finished_signal 在 run() 返回前发出),
        # QThread 析构安全
        worker = getattr(self, 'worker', None)
        if worker is not None:
            if worker.isRunning():
                worker.wait(3000)
            self.worker = None

        if success:
            InfoBar.success("安装成功", message,
                            parent=self.window(), duration=-1)
        else:
            InfoBar.error("安装失败", message, parent=self.window(), duration=-1)

    def append_html(self, html_text):
        """追加 HTML 格式的日志"""
        cursor = self.terminal_text.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        self.terminal_text.setTextCursor(cursor)
        self.terminal_text.insertHtml(html_text)
        self.terminal_text.ensureCursorVisible()


class _UserInfoWorker(QThread):
    """异步拉两个平台的用户信息 避免阻塞 settings 页面渲染"""
    netease = pyqtSignal(object)   # dict | None
    bilibili = pyqtSignal(object)

    def run(self):
        try:
            from utils._netease_weapi import get_user_info as _ne
            self.netease.emit(_ne())
        except Exception:
            self.netease.emit(None)
        try:
            from utils.material_fetcher import get_bilibili_user_info as _bi
            self.bilibili.emit(_bi())
        except Exception:
            self.bilibili.emit(None)


class MaterialsAccountCard(ExpandGroupSettingCard):
    """素材库账户管理卡片 —— 两个平台共用一张卡 内部分两行"""

    PLATFORMS = (
        ("netease",  "网易云音乐", FIF.MUSIC),
        ("bilibili", "哔哩哔哩",  FIF.VIDEO),
    )

    def __init__(self, parent=None):
        super().__init__(FIF.DOWNLOAD, "素材库账户",
                         "登录账号后可解锁更高画质 (B 站 1080P+) 和更高命中率 (网易云 VIP / 付费曲目)，"
                         "登录凭据仅保存在本机 configs/config.json。",
                         parent)

        # 调整内部布局
        self.viewLayout.setContentsMargins(0, 0, 0, 0)
        self.viewLayout.setSpacing(0)

        self._rows: dict[str, dict] = {}
        for key, name, icon in self.PLATFORMS:
            item = self._build_item(key, name, icon)
            self.addGroupWidget(item)

        # 启动时异步刷新一次
        self._refresh()

    def _build_item(self, key: str, name: str, icon) -> QWidget:
        """构建单个平台的登录控件行"""
        item = QWidget(self)
        layout = QHBoxLayout(item)
        layout.setContentsMargins(20, 12, 20, 12)
        layout.setSpacing(10)

        icw = IconWidget(icon, self)
        icw.setFixedSize(20, 20)
        layout.addWidget(icw)

        name_lbl = BodyLabel(name, self)
        name_lbl.setMinimumWidth(96)
        layout.addWidget(name_lbl)

        status_lbl = CaptionLabel("检查中…", self)
        layout.addWidget(status_lbl, 1)

        login_btn = PrimaryPushButton("登录", self, FIF.LINK)
        login_btn.setFixedWidth(96)
        login_btn.clicked.connect(lambda _=False, k=key: self._on_login(k))
        layout.addWidget(login_btn)

        logout_btn = PushButton("退出登录", self)
        logout_btn.setFixedWidth(96)
        logout_btn.clicked.connect(lambda _=False, k=key: self._on_logout(k))
        logout_btn.setVisible(False)
        layout.addWidget(logout_btn)

        self._rows[key] = {
            "status":  status_lbl,
            "login":   login_btn,
            "logout":  logout_btn,
        }
        return item

    # ── 状态刷新 ───────────────────────────────────────────────────

    def _refresh(self):
        # 没 cookie 时直接显示未登录 不发请求
        for key, _name, _ic in self.PLATFORMS:
            has_cookie = bool(
                (get_field(f"materials.{key}_cookie", "") or "").strip())
            r = self._rows[key]
            if not has_cookie:
                r["status"].setText("未登录")
                r["login"].setVisible(True)
                r["logout"].setVisible(False)
            else:
                r["status"].setText("已登录 —— 正在查询账号信息…")
                r["login"].setVisible(False)
                r["logout"].setVisible(True)

        # 异步拉用户信息
        self._worker = _UserInfoWorker(self)
        self._worker.netease.connect(lambda d: self._fill("netease", d))
        self._worker.bilibili.connect(lambda d: self._fill("bilibili", d))
        self._worker.start()

    def _fill(self, key: str, data):
        r = self._rows[key]
        if data is None:
            # 有 cookie 但拉不到信息 = cookie 失效
            has_cookie = bool(
                (get_field(f"materials.{key}_cookie", "") or "").strip())
            if has_cookie:
                r["status"].setText("⚠️ 登录已失效 请重新登录")
                r["login"].setVisible(True)
                r["login"].setText("重新登录")
                r["logout"].setVisible(True)
            return
        if key == "netease":
            name = data.get("nickname", "")
            uid = data.get("userId")
            vip = "  · VIP" if data.get("vipType", 0) else ""
            r["status"].setText(f"已登录: {name} (uid {uid}){vip}")
        else:
            name = data.get("uname", "")
            mid = data.get("mid")
            vip = "  · 大会员" if data.get("vip") else ""
            r["status"].setText(f"已登录: {name} (mid {mid}){vip}")
        r["login"].setVisible(False)
        r["logout"].setVisible(True)

    # ── 按钮 ───────────────────────────────────────────────────────

    def _on_login(self, key: str):
        from widgets.dialog_qr_login import QRLoginDialog
        dlg = QRLoginDialog(key, self.window())
        if dlg.exec():
            self._refresh()

    def _on_logout(self, key: str):
        name = {"netease": "网易云音乐", "bilibili": "哔哩哔哩"}[key]
        box = MessageBox("确认退出登录",
                         f"将清除本机保存的 {name} 登录凭据。\n"
                         f"已下载的素材不受影响 但下次需要 VIP / 大会员内容时需要重新登录。",
                         self.window())
        box.yesButton.setText("退出登录")
        box.cancelButton.setText("取消")
        if not box.exec():
            return
        set_field(f"materials.{key}_cookie", "")
        info(f"[materials] 退出 {key} 登录")
        InfoBar.success("已退出登录", name,
                        parent=self.window(), duration=2500)
        self._refresh()


class LLMConfigCard(ExpandGroupSettingCard):
    """LLM 服务配置卡片 —— 给节点编辑器里的「LLM 提示词」节点用。

    与内置聊天页(subpage_llm_chat)共享同一份配置 ``configs/config.json::llm_chat``;
    设置页这里改完聊天页打开时也即时生效,反之亦然。节点 executor
    (node_worker.LLMPromptExec)只读 ``llm_chat.{base_url, model, api_key}``,
    所以这里写回这三个字段就够节点直接用。
    """

    # provider/model 拉取在子线程跑;Qt 警告 "QObject::startTimer: Timers
    # cannot be started from another thread" 通常是 worker parent 串错,这里
    # 不传 parent 用 self 持引用即可。
    def __init__(self, parent=None):
        super().__init__(FIF.EDUCATION, "LLM 服务配置",
                         "用于节点编辑器中的「LLM 提示词」节点(同时也是聊天页的服务配置)。"
                         "API Key 仅保存在本机 configs/config.json。",
                         parent)

        # 调整内部布局
        self.viewLayout.setContentsMargins(0, 0, 0, 0)
        self.viewLayout.setSpacing(0)

        # 懒导入:避免 settings 模块顶部强拉聊天页(它会顺带 import 一大票
        # qfluentwidgets 与正则、urllib 等;放到这里只有打开设置页才付出代价)
        from widgets.subpage.subpage_llm_chat import PROVIDER_PRESETS, ModelListWorker
        self._presets = PROVIDER_PRESETS
        self._ModelListWorker = ModelListWorker

        # ── 服务商 ──────────────────────────────────────────────────────
        self.provider_box = ComboBox(self)
        self.provider_box.addItems(list(self._presets.keys()))
        self.provider_box.currentTextChanged.connect(self._on_provider_changed)
        self.provider_box.setFixedWidth(220)

        # ── Base URL ───────────────────────────────────────────────────
        self.url_edit = LineEdit(self)
        self.url_edit.setPlaceholderText("https://api.example.com/v1")
        self.url_edit.setFixedWidth(280)

        # ── API Key ────────────────────────────────────────────────────
        self.key_edit = PasswordLineEdit(self)
        self.key_edit.setPlaceholderText("sk-...")
        self.key_edit.setFixedWidth(280)

        # ── 模型 + 拉取按钮 ────────────────────────────────────────────
        model_widget = QWidget(self)
        model_layout = QHBoxLayout(model_widget)
        model_layout.setContentsMargins(20, 12, 20, 12)
        model_layout.setSpacing(8)
        model_layout.addWidget(BodyLabel("模型", self))
        self.model_box = EditableComboBox(self)
        self.model_box.setPlaceholderText("手动填写或点右侧按钮拉取")
        model_layout.addWidget(self.model_box, 1)
        self.fetch_btn = PushButton("拉取模型", self, FIF.SYNC)
        self.fetch_btn.setFixedWidth(110)
        self.fetch_btn.clicked.connect(self._on_fetch_models)
        model_layout.addWidget(self.fetch_btn)

        # ── 保存按钮 ───────────────────────────────────────────────────
        save_widget = QWidget(self)
        save_layout = QHBoxLayout(save_widget)
        save_layout.setContentsMargins(20, 12, 20, 12)
        save_layout.addStretch()
        self.save_btn = PrimaryPushButton("保存", self, FIF.SAVE)
        self.save_btn.setFixedWidth(110)
        self.save_btn.clicked.connect(self._on_save)
        save_layout.addWidget(self.save_btn)

        # 添加各组到手风琴卡中
        self.addGroup(FIF.ROBOT, "服务商", "选择预设服务商自动填充", self.provider_box)
        self.addGroup(FIF.LINK, "Base URL", "API 接口地址", self.url_edit)
        self.addGroup(FIF.VPN, "API Key", "密钥仅保存在本机", self.key_edit)
        self.addGroupWidget(model_widget)
        self.addGroupWidget(save_widget)

        self._model_worker = None
        self._load_from_config()

    # ── 加载 / 保存 ──────────────────────────────────────────────────────
    def _load_from_config(self):
        provider = (get_field("llm_chat.provider", "") or "").strip()
        base_url = (get_field("llm_chat.base_url", "") or "").strip()
        api_key = (get_field("llm_chat.api_key", "") or "").strip()
        model = (get_field("llm_chat.model", "") or "").strip()
        cached_models = get_field("llm_chat.models", []) or []

        # provider 失配(老配置 / 用户改过 preset 名)时落到「自定义」,避免下拉
        # 显示一个 placeholder 让用户以为没选
        if provider not in self._presets:
            provider = "自定义"
        # blockSignals 避免触发 _on_provider_changed 覆盖刚加载的 base_url
        self.provider_box.blockSignals(True)
        self.provider_box.setCurrentText(provider)
        self.provider_box.blockSignals(False)

        self.url_edit.setText(base_url)
        self.key_edit.setText(api_key)

        # model 下拉:优先用缓存的远端列表 + 当前 model 顶到首位
        items: list[str] = []
        if isinstance(cached_models, list):
            items = [str(m) for m in cached_models if m]
        if model and model not in items:
            items.insert(0, model)
        self.model_box.clear()
        if items:
            self.model_box.addItems(items)
        self.model_box.setCurrentText(model)

    def _on_provider_changed(self, name: str):
        # 切换预设时,如果 url/key 为空就自动填默认值;非空保留用户已有的(避免
        # 误点下拉把已配置的 base_url 抹了)
        url_default, model_default = self._presets.get(name, ("", ""))
        if url_default and not self.url_edit.text().strip():
            self.url_edit.setText(url_default)
        if model_default:
            # 仅当当前模型框是空 或 模型还不在已知列表里时,顺手填一个默认
            cur = self.model_box.currentText().strip()
            if not cur:
                if model_default not in [self.model_box.itemText(i)
                                         for i in range(self.model_box.count())]:
                    self.model_box.addItem(model_default)
                self.model_box.setCurrentText(model_default)

    def _on_save(self):
        provider = self.provider_box.currentText().strip()
        base_url = self.url_edit.text().strip()
        api_key = self.key_edit.text().strip()
        model = self.model_box.currentText().strip()

        if not base_url:
            InfoBar.warning("保存失败", "Base URL 不能为空",
                            parent=self.window(), duration=3000)
            return
        if not model:
            InfoBar.warning("保存失败", "请选择或填写模型名",
                            parent=self.window(), duration=3000)
            return

        set_field("llm_chat.provider", provider)
        set_field("llm_chat.base_url", base_url)
        set_field("llm_chat.api_key", api_key)
        set_field("llm_chat.model", model)
        info(f"[settings] LLM 配置已保存 provider={provider} model={model}")
        InfoBar.success("已保存", "LLM 配置已写入 configs/config.json",
                        parent=self.window(), duration=2500)

    # ── 拉取模型 ─────────────────────────────────────────────────────────
    def _on_fetch_models(self):
        if self._model_worker is not None and self._model_worker.isRunning():
            return  # 防抖:正在拉就忽略
        base_url = self.url_edit.text().strip()
        if not base_url:
            InfoBar.warning("无法拉取", "请先填写 Base URL",
                            parent=self.window(), duration=3000)
            return

        api_key = self.key_edit.text().strip()
        self.fetch_btn.setEnabled(False)
        self.fetch_btn.setText("拉取中…")

        self._model_worker = self._ModelListWorker(api_key, base_url, self)
        self._model_worker.finished_list.connect(self._on_fetch_ok)
        self._model_worker.error.connect(self._on_fetch_err)
        self._model_worker.finished.connect(self._reset_fetch_btn)
        self._model_worker.start()

    def _reset_fetch_btn(self):
        self.fetch_btn.setEnabled(True)
        self.fetch_btn.setText("拉取模型")

    def _on_fetch_ok(self, ids: list):
        cur = self.model_box.currentText().strip()
        self.model_box.clear()
        self.model_box.addItems(ids)
        # 拉取后保留用户原先选中的 model 作为当前值(它可能还在列表里;不在
        # 列表也允许用户继续用,EditableComboBox 允许任意输入)
        if cur:
            self.model_box.setCurrentText(cur)
        # 同时缓存到 config,下次打开设置页直接显示
        set_field("llm_chat.models", ids)
        InfoBar.success("拉取成功",
                        f"获取到 {len(ids)} 个模型",
                        parent=self.window(), duration=2500)

    def _on_fetch_err(self, msg: str):
        InfoBar.error("拉取失败", msg[:200],
                      parent=self.window(), duration=5000)


class LazyStartupCard(ExpandGroupSettingCard):
    """启动行为:懒启动开关。

    开启后,audio / image 分组下的工具页(demucs / whisper / rvc / gptsovits /
    audiocraft / ESRGAN / yolo / iopaint)在启动时只挂占位,首次点开才构造真页;
    home / 模型对话 / 节点编辑器 / 设置 等仍然饿加载。
    改动写入 configs/config.json::app.lazy_startup,下次启动生效。
    """

    def __init__(self, parent=None):
        super().__init__(FIF.SPEED_HIGH, "启动行为",
                         "懒启动:开启后,音频 / 图像分组下工具启动时会快很多，但首次点开某工具时会有短暂卡顿",
                         parent)

        # 调整内部布局
        self.viewLayout.setContentsMargins(0, 0, 0, 0)
        self.viewLayout.setSpacing(0)

        self.toggle = SwitchButton(self)
        self.toggle.setOnText("开")
        self.toggle.setOffText("关")
        self.toggle.setChecked(bool(get_field("app.lazy_startup", False)))
        self.toggle.checkedChanged.connect(self._on_toggled)

        self.addGroup(FIF.POWER_BUTTON, "懒启动",
                     "延迟加载音频/图像工具页，大幅提升启动速度", self.toggle)

    def _on_toggled(self, checked: bool):
        set_field("app.lazy_startup", bool(checked))
        info(f"[settings] app.lazy_startup → {checked}")
        InfoBar.success(
            "已保存",
            "懒启动设置已更新,下次启动生效。",
            parent=self.window(),
            duration=2500,
        )



class PackageManagerCard(ExpandGroupSettingCard):
    """包管理与依赖诊断卡片"""

    def __init__(self, on_open, parent=None):
        super().__init__(FIF.APPLICATION, "包管理与依赖诊断",
                         "查看 venv 里安装的全部包并逐个卸载、对各工具执行安装/卸载,"
                         "以及诊断多个工具共用同一环境时的锁版依赖冲突。",
                         parent)

        # 调整内部布局
        self.viewLayout.setContentsMargins(0, 0, 0, 0)
        self.viewLayout.setSpacing(0)

        self.open_btn = PrimaryPushButton("打开包管理器", self, FIF.RIGHT_ARROW)
        self.open_btn.clicked.connect(on_open)

        self.addGroup(FIF.COMMAND_PROMPT, "包管理器",
                     "查看 / 卸载已安装包，诊断依赖冲突", self.open_btn)


class ApiServerCard(ExpandGroupSettingCard):
    """HTTP API 服务卡片 —— 把 8 个工具开放成 FastAPI 接口。

    开关写 ``configs/config.json::api_server`` 并即时启停 server.api_server.ApiServerManager。
    强制 Bearer API Key;可监听 0.0.0.0 暴露给局域网(给红字风险提示)。
    """

    def __init__(self, parent=None):
        super().__init__(FIF.CLOUD, "API 服务（HTTP）",
                         "启用后会在本机起一个 HTTP 服务，把 8 个工具开放成 API（请求体参考 LLM 调用："
                         "顶层 model 选工具、parameters 带参数、stream 流式）。强制使用 Bearer API Key。",
                         parent)

        # 调整内部布局
        self.viewLayout.setContentsMargins(0, 0, 0, 0)
        self.viewLayout.setSpacing(0)

        # ── 监听地址 ───────────────────────────────────────────────────
        self.host_box = ComboBox(self)
        self.host_box.addItems(["127.0.0.1（仅本机）", "0.0.0.0（局域网可访问）"])
        self.host_box.currentIndexChanged.connect(self._on_host_changed)
        self.host_box.setFixedWidth(180)

        # ── 端口 ───────────────────────────────────────────────────────
        self.port_edit = LineEdit(self)
        self.port_edit.setPlaceholderText("8765")
        self.port_edit.setFixedWidth(135)

        # ── API Key ────────────────────────────────────────────────────
        key_widget = QWidget(self)
        key_layout = QHBoxLayout(key_widget)
        key_layout.setContentsMargins(20, 12, 20, 12)
        key_layout.setSpacing(8)
        key_layout.addWidget(BodyLabel("API Key", self))
        self.key_edit = PasswordLineEdit(self)
        self.key_edit.setPlaceholderText("必填 —— 点右侧生成")
        key_layout.addWidget(self.key_edit, 1)
        self.gen_btn = PushButton("生成", self, FIF.SYNC)
        self.gen_btn.setFixedWidth(90)
        self.gen_btn.clicked.connect(self._on_generate_key)
        key_layout.addWidget(self.gen_btn)

        # ── 局域网风险提示(仅 0.0.0.0 时显示) ─────────────────────────
        warn_widget = QWidget(self)
        warn_layout = QHBoxLayout(warn_widget)
        warn_layout.setContentsMargins(20, 8, 20, 8)
        self.warn_lbl = CaptionLabel(
            "⚠ 监听 0.0.0.0 会把工具暴露给同网段设备，请务必使用强随机 Key。", self)
        self.warn_lbl.setStyleSheet("color:#F44336;")
        warn_layout.addWidget(self.warn_lbl)
        warn_widget.setVisible(False)
        self._warn_widget = warn_widget

        # ── 状态 + 开关 + 复制示例 ─────────────────────────────────────
        ctl_widget = QWidget(self)
        ctl_layout = QHBoxLayout(ctl_widget)
        ctl_layout.setContentsMargins(20, 12, 20, 12)
        ctl_layout.setSpacing(10)
        self.status_lbl = CaptionLabel("已停止", self)
        self.status_lbl.setStyleSheet("color:rgba(128,128,128,180);")
        ctl_layout.addWidget(self.status_lbl, 1)
        self.copy_btn = PushButton("复制示例请求", self,
                                   getattr(FIF, "COPY", None) or FIF.LINK)
        self.copy_btn.clicked.connect(self._on_copy_example)
        ctl_layout.addWidget(self.copy_btn)
        self.toggle = SwitchButton(self)
        self.toggle.checkedChanged.connect(self._on_toggled)
        ctl_layout.addWidget(self.toggle)

        # 添加各组到手风琴卡中
        self.addGroup(FIF.GLOBE, "监听地址", "本机或局域网可访问", self.host_box)
        self.addGroup(FIF.CONNECT, "端口", "HTTP 服务监听端口", self.port_edit)
        self.addGroupWidget(key_widget)
        self.addGroupWidget(warn_widget)
        self.addGroupWidget(ctl_widget)

        self._load_from_config()

    # ── 配置加载 / 状态 ──────────────────────────────────────────────────
    def _host_value(self) -> str:
        return "0.0.0.0" if self.host_box.currentIndex() == 1 else "127.0.0.1"

    def _port_value(self) -> int:
        try:
            return int((self.port_edit.text() or "8765").strip())
        except ValueError:
            return 8765

    def _load_from_config(self):
        host = (get_field("api_server.host", "127.0.0.1") or "127.0.0.1").strip()
        port = get_field("api_server.port", 8765) or 8765
        key = (get_field("api_server.api_key", "") or "").strip()
        self.host_box.setCurrentIndex(1 if host == "0.0.0.0" else 0)
        self.warn_lbl.setVisible(host == "0.0.0.0")
        self.port_edit.setText(str(port))
        self.key_edit.setText(key)
        # 与实际运行态对齐(可能是 app.py 自启动起来的)
        try:
            from server.api_server import ApiServerManager
            running = ApiServerManager.instance().is_running()
        except Exception:
            running = False
        self.toggle.blockSignals(True)
        self.toggle.setChecked(running)
        self.toggle.blockSignals(False)
        self._refresh_status(running)

    def _refresh_status(self, running: bool):
        if running:
            try:
                from server.api_server import ApiServerManager
                base = ApiServerManager.instance().base_url
            except Exception:
                base = ""
            host = self._host_value()
            shown = base if host != "0.0.0.0" else f"http://0.0.0.0:{self._port_value()}"
            self.status_lbl.setText(f"运行中 · {shown}")
            self.status_lbl.setStyleSheet("color:#4CAF50;")
        else:
            self.status_lbl.setText("已停止")
            self.status_lbl.setStyleSheet("color:rgba(128,128,128,180);")

    # ── 交互 ─────────────────────────────────────────────────────────────
    def _on_host_changed(self, _idx: int):
        self._warn_widget.setVisible(self._host_value() == "0.0.0.0")

    def _on_generate_key(self):
        import secrets
        self.key_edit.setText(secrets.token_hex(24))

    def _on_copy_example(self):
        from PyQt6.QtWidgets import QApplication
        host = "127.0.0.1" if self._host_value() == "0.0.0.0" else self._host_value()
        key = (self.key_edit.text() or "<API_KEY>").strip()
        example = (
            f'curl -X POST http://{host}:{self._port_value()}/v1/invoke '
            f'-H "Authorization: Bearer {key}" '
            f'-H "Content-Type: application/json" '
            f'-d \'{{"model":"whisper","input":"C:/a.wav",'
            f'"parameters":{{"model":"small","device":"cpu"}}}}\''
        )
        QApplication.clipboard().setText(example)
        InfoBar.success("已复制", "示例 curl 已复制到剪贴板",
                        parent=self.window(), duration=2500)

    def _on_toggled(self, checked: bool):
        from utils.configer import set_field
        if checked:
            key = (self.key_edit.text() or "").strip()
            if not key:
                InfoBar.warning("无法启动", "请先生成或填写 API Key",
                                parent=self.window(), duration=3000)
                self.toggle.blockSignals(True)
                self.toggle.setChecked(False)
                self.toggle.blockSignals(False)
                return
            host, port = self._host_value(), self._port_value()
            # 落盘(server 启动时也会读 api_server.api_key 做鉴权)
            set_field("api_server.host", host)
            set_field("api_server.port", port)
            set_field("api_server.api_key", key)
            try:
                from server.api_server import ApiServerManager
                ApiServerManager.instance().start(host, port)
            except Exception as e:
                error(f"[settings] API 服务启动失败: {e}")
                InfoBar.error("启动失败", str(e)[:200],
                              parent=self.window(), duration=-1)
                self.toggle.blockSignals(True)
                self.toggle.setChecked(False)
                self.toggle.blockSignals(False)
                self._refresh_status(False)
                return
            set_field("api_server.enabled", True)
            self._refresh_status(True)
            InfoBar.success("已启动", f"API 服务运行在 {self._host_value()}:{port}",
                            parent=self.window(), duration=3000)
        else:
            try:
                from server.api_server import ApiServerManager
                ApiServerManager.instance().stop()
            except Exception as e:
                warning(f"[settings] API 服务停止异常: {e}")
            set_field("api_server.enabled", False)
            self._refresh_status(False)


class SettingsWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self.setObjectName("settingsInterface")

        # 外层布局只放一个滚动容器, 不要再直接放卡片。
        # InstallPyTorchCard 开始装 torch 时 terminal_widget 从 0 撑到 200+ px,
        # 没有滚动容器时整页超出 FluentWindow 的可视高度,Qt 会把下方 MaterialsAccountCard
        # 挤到 install 卡上方,视觉上就是"飞上来重叠"。套 SingleDirectionScrollArea
        # 后超出部分变滚动条,布局不会再溢出。
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = SingleDirectionScrollArea(
            self, orient=Qt.Orientation.Vertical)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("QScrollArea{background:transparent;border:none}")
        scroll.viewport().setStyleSheet("background:transparent")

        content = QWidget(scroll)
        content.setStyleSheet("background:transparent")
        layout = QVBoxLayout(content)
        layout.setSpacing(12)
        layout.setContentsMargins(30, 30, 30, 30)

        self.pkg_card = PackageManagerCard(self._open_package_manager, content)
        layout.addWidget(self.pkg_card)

        self.api_card = ApiServerCard(content)
        layout.addWidget(self.api_card)

        self.lazy_card = LazyStartupCard(content)
        layout.addWidget(self.lazy_card)

        self.materials_card = MaterialsAccountCard(content)
        layout.addWidget(self.materials_card)

        self.llm_card = LLMConfigCard(content)
        layout.addWidget(self.llm_card)

        self.installer = InstallPyTorchCard(content)
        layout.addWidget(self.installer)

        layout.addStretch()

        scroll.setWidget(content)

        # 把卡片标题/内容标签的字体统一成 app.py 设置的全局字体
        # (qfluentwidgets 默认给它们写死了 Segoe UI)
        apply_app_font(content)

        # 内层 QStackedWidget:index 0 = 设置主页(上面的滚动区),
        # index 1 = 包管理二级页(懒构造,首次点「打开包管理器」才建)。
        self._stack = QStackedWidget(self)
        self._stack.addWidget(scroll)
        self._pkg_page = None
        outer.addWidget(self._stack)

    def _open_package_manager(self):
        """切到包管理二级页;首次打开时才构造它(连带拉起 pip_worker 等)。"""
        if self._pkg_page is None:
            # 懒导入避免与 subpage_package_manager 的循环引用
            from widgets.subpage.subpage_package_manager import PackageManagerWidget
            self._pkg_page = PackageManagerWidget(
                on_back=lambda: self._stack.setCurrentIndex(0), parent=self)
            self._stack.addWidget(self._pkg_page)
            apply_app_font(self._pkg_page)
        self._stack.setCurrentWidget(self._pkg_page)
        self._pkg_page.refresh()
