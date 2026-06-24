import sys
from logger import info, warning, debug, error
from PyQt6.QtGui import QDesktopServices, QTextCursor
from PyQt6.QtCore import QUrl, QThread, pyqtSignal
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QFrame
from PyQt6.QtCore import Qt
from qfluentwidgets import (
    SettingCard, FluentIcon as FIF, ElevatedCardWidget, TextEdit, ComboBox,
    EditableComboBox, LineEdit, PasswordLineEdit, SwitchButton,
    PushButton, PrimaryPushButton, ToolTipFilter, IconWidget, MessageBox, InfoBar,
    StrongBodyLabel, BodyLabel, CaptionLabel, SingleDirectionScrollArea,
)
from workers.pip_worker import PipWorker
from utils.configer import get_field, set_field
import subprocess
import re
CUDA_MAP = {"CPU": "cpu", "CUDA11.8": "118", "CUDA12.4": "124",
            "CUDA12.6": "126", "CUDA13.0": "130", "CUDA13.2": "132"}
MIRROR_MAP = {"阿里云镜像源": "https://mirrors.aliyun.com/pytorch-wheels",
              "清华大学镜像源": "https://pypi.tuna.tsinghua.edu.cn/simple",
              "官方Pytorch源": "https://download.pytorch.org/whl"}


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


class LogTextEdit(TextEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptRichText(True)
        self.setReadOnly(True)

    def append_colored(self, html_text):
        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self.setTextCursor(cursor)

        # 检测并转换 URL 为超链接
        html_text = self._convert_urls_to_links(html_text)

        if "下载进度" in html_text:
            # 进度消息：覆盖当前行
            cursor.movePosition(
                QTextCursor.MoveOperation.StartOfLine, QTextCursor.MoveMode.KeepAnchor)
            cursor.removeSelectedText()
            cursor.insertHtml(html_text)
        else:
            # 普通消息：插入 HTML 并换行
            cursor.insertHtml(html_text + '<br>')

        self.ensureCursorVisible()

    def _convert_urls_to_links(self, text):
        """将文本中的 URL 转换为可点击的超链接"""
        # 匹配 http:// 或 https:// 开头的 URL
        url_pattern = r'(https?://[^\s<>"\'{}|\\^`\[\]]+)'

        def replace_url(match):
            url = match.group(1)
            # 截断过长的 URL 显示
            display_url = url if len(
                url) <= 80 else url[:40] + '...' + url[-30:]
            return f'<a href="{url}" style="color:#4FC3F7; text-decoration:underline;">{display_url}</a>'

        return re.sub(url_pattern, replace_url, text)

    def mousePressEvent(self, event):
        """处理超链接点击"""
        cursor = self.cursorForPosition(event.pos())
        if cursor.charFormat().isAnchor():
            anchor = cursor.charFormat().anchorHref()
            if anchor:
                QDesktopServices.openUrl(QUrl(anchor))
                return
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


class MaterialsAccountCard(ElevatedCardWidget):
    """素材库账户管理卡片 —— 两个平台共用一张卡 内部分两行"""

    PLATFORMS = (
        ("netease",  "网易云音乐", FIF.MUSIC),
        ("bilibili", "哔哩哔哩",  FIF.VIDEO),
    )

    def __init__(self, parent=None):
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 16, 22, 16)
        root.setSpacing(12)

        head = QHBoxLayout()
        head.setSpacing(8)
        head.addWidget(IconWidget(FIF.DOWNLOAD, self))
        head.addWidget(StrongBodyLabel("素材库账户", self))
        head.addStretch()
        root.addLayout(head)

        root.addWidget(CaptionLabel(
            "登录账号后可解锁更高画质 (B 站 1080P+) 和更高命中率 (网易云 VIP / 付费曲目)，"
            "登录凭据仅保存在本机 configs/config.json。", self
        ))

        self._rows: dict[str, dict] = {}
        for key, name, icon in self.PLATFORMS:
            row = self._build_row(key, name, icon)
            root.addLayout(row)

        # 启动时异步刷新一次
        self._refresh()

    def _build_row(self, key: str, name: str, icon) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(10)
        icw = IconWidget(icon, self)
        icw.setFixedSize(20, 20)
        row.addWidget(icw)
        name_lbl = BodyLabel(name, self)
        name_lbl.setMinimumWidth(96)
        row.addWidget(name_lbl)

        status_lbl = CaptionLabel("检查中…", self)
        row.addWidget(status_lbl, 1)

        login_btn = PrimaryPushButton("登录", self, FIF.LINK)
        login_btn.setFixedWidth(96)
        login_btn.clicked.connect(lambda _=False, k=key: self._on_login(k))
        row.addWidget(login_btn)

        logout_btn = PushButton("退出登录", self)
        logout_btn.setFixedWidth(96)
        logout_btn.clicked.connect(lambda _=False, k=key: self._on_logout(k))
        logout_btn.setVisible(False)
        row.addWidget(logout_btn)

        self._rows[key] = {
            "status":  status_lbl,
            "login":   login_btn,
            "logout":  logout_btn,
        }
        return row

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


class LLMConfigCard(ElevatedCardWidget):
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
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 16, 22, 16)
        root.setSpacing(10)

        head = QHBoxLayout()
        head.setSpacing(8)
        head.addWidget(IconWidget(FIF.EDUCATION, self))
        head.addWidget(StrongBodyLabel("LLM 服务配置", self))
        head.addStretch()
        root.addLayout(head)

        root.addWidget(CaptionLabel(
            "用于节点编辑器中的「LLM 提示词」节点(同时也是聊天页的服务配置)。"
            "API Key 仅保存在本机 configs/config.json。", self
        ))

        # 懒导入:避免 settings 模块顶部强拉聊天页(它会顺带 import 一大票
        # qfluentwidgets 与正则、urllib 等;放到这里只有打开设置页才付出代价)
        from widgets.subpage.subpage_llm_chat import PROVIDER_PRESETS, ModelListWorker
        self._presets = PROVIDER_PRESETS
        self._ModelListWorker = ModelListWorker

        # ── 服务商 ──────────────────────────────────────────────────────
        row_provider = QHBoxLayout()
        row_provider.setSpacing(10)
        lbl_p = BodyLabel("服务商", self)
        lbl_p.setMinimumWidth(70)
        row_provider.addWidget(lbl_p)
        self.provider_box = ComboBox(self)
        self.provider_box.addItems(list(self._presets.keys()))
        self.provider_box.currentTextChanged.connect(self._on_provider_changed)
        row_provider.addWidget(self.provider_box, 1)
        root.addLayout(row_provider)

        # ── Base URL ───────────────────────────────────────────────────
        row_url = QHBoxLayout()
        row_url.setSpacing(10)
        lbl_u = BodyLabel("Base URL", self)
        lbl_u.setMinimumWidth(70)
        row_url.addWidget(lbl_u)
        self.url_edit = LineEdit(self)
        self.url_edit.setPlaceholderText("https://api.example.com/v1")
        row_url.addWidget(self.url_edit, 1)
        root.addLayout(row_url)

        # ── API Key ────────────────────────────────────────────────────
        row_key = QHBoxLayout()
        row_key.setSpacing(10)
        lbl_k = BodyLabel("API Key", self)
        lbl_k.setMinimumWidth(70)
        row_key.addWidget(lbl_k)
        self.key_edit = PasswordLineEdit(self)
        self.key_edit.setPlaceholderText("sk-...")
        row_key.addWidget(self.key_edit, 1)
        root.addLayout(row_key)

        # ── 模型 + 拉取按钮 ────────────────────────────────────────────
        row_model = QHBoxLayout()
        row_model.setSpacing(10)
        lbl_m = BodyLabel("模型", self)
        lbl_m.setMinimumWidth(70)
        row_model.addWidget(lbl_m)
        self.model_box = EditableComboBox(self)
        self.model_box.setPlaceholderText("手动填写或点右侧按钮拉取")
        row_model.addWidget(self.model_box, 1)
        self.fetch_btn = PushButton("拉取模型", self, FIF.SYNC)
        self.fetch_btn.setFixedWidth(110)
        self.fetch_btn.clicked.connect(self._on_fetch_models)
        row_model.addWidget(self.fetch_btn)
        root.addLayout(row_model)

        # ── 保存按钮 ───────────────────────────────────────────────────
        row_btn = QHBoxLayout()
        row_btn.addStretch()
        self.save_btn = PrimaryPushButton("保存", self, FIF.SAVE)
        self.save_btn.setFixedWidth(110)
        self.save_btn.clicked.connect(self._on_save)
        row_btn.addWidget(self.save_btn)
        root.addLayout(row_btn)

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


class LazyStartupCard(ElevatedCardWidget):
    """启动行为:懒启动开关。

    开启后,audio / image 分组下的工具页(demucs / whisper / rvc / gptsovits /
    audiocraft / ESRGAN / yolo / iopaint)在启动时只挂占位,首次点开才构造真页;
    home / 模型对话 / 节点编辑器 / 设置 等仍然饿加载。
    改动写入 configs/config.json::app.lazy_startup,下次启动生效。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 16, 22, 16)
        root.setSpacing(10)

        head = QHBoxLayout()
        head.setSpacing(8)
        head.addWidget(IconWidget(FIF.SPEED_HIGH, self))
        head.addWidget(StrongBodyLabel("启动行为", self))
        head.addStretch()
        root.addLayout(head)

        root.addWidget(CaptionLabel(
            "懒启动:开启后,音频 / 图像分组下的八个工具页在启动时只挂占位,"
            "首次点开会显示「正在加载…」并构造真页。"
            "适合只用一两个工具的用户。修改后需重启程序生效。", self
        ))

        row = QHBoxLayout()
        row.setSpacing(10)
        row.addWidget(BodyLabel("懒启动", self), 1)
        self.toggle = SwitchButton(self)
        self.toggle.setChecked(bool(get_field("app.lazy_startup", False)))
        self.toggle.checkedChanged.connect(self._on_toggled)
        row.addWidget(self.toggle)
        root.addLayout(row)

    def _on_toggled(self, checked: bool):
        set_field("app.lazy_startup", bool(checked))
        info(f"[settings] app.lazy_startup → {checked}")
        InfoBar.success(
            "已保存",
            "懒启动设置已更新,下次启动生效。",
            parent=self.window(),
            duration=2500,
        )


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
        outer.addWidget(scroll)
