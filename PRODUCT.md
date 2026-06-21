# Product

## Register

product

## Users

中国大陆地区的 AI 爱好者 / 创作者，Windows 桌面端用户。多数有一定技术基础（用过 Python、了解 pip、知道什么是 CUDA 与显卡驱动），但不愿意——也不应该——每接触一个新模型就重走一遍"配 venv → 选 torch+CUDA 版本 → 翻墙拉 GitHub → 修依赖冲突"的流程。

他们想要的：在一个窗口里安装、启动、对接 Demucs / Whisper / Real-ESRGAN / YOLO / RVC / GPT-SoVITS / Audiocraft / IOPaint 等社区开源模型；保留每个工具原本的能力与命名（不被换成"魔法术语"），同时让网络、依赖、版本对齐这些"环境地狱"问题彻底消失。

## Product Purpose

LaunchAI（中文名"奇点"）是一个 PyQt6 桌面启动器，把分散在 GitHub 各处的开源 AI 模型集中到统一的 Fluent 风格 UI 之下：
- 每个工具有独立的"未安装 → 安装中 → 可用"状态机（`SwitchPage`），首次进入时显示安装引导与日志，安装完成自动切到功能页。
- 安装层（`PipWorker`）针对中国大陆网络做了路由优化：PyPI 走可选镜像、`torch` 可直拼阿里云轮子 URL、Git 克隆走 `dulwich` + 镜像 HEAD 探活、Real-ESRGAN / Demucs 等已知坑位有硬编码补丁。
- 一个实验性的节点编辑器（`node/`）允许把不同工具串成数据流。

成功的样子：用户从下载启动器到跑通第一个模型推理，全程不需要打开终端、不需要手动 `pip install`、不需要读上游 README 的 troubleshooting 段落。

## Brand Personality

三个词：**实用 · 硬核 · 个人化**（practical · hardcore · author-driven）。

- **实用**：不卖未来感，卖"它真的能跑"。每一处界面都先回答"用户在这一步要做什么"，再考虑装饰。
- **硬核**：术语不被稀释。CUDA、torch、fork、镜像、wheel、setup.py develop——该出现的名词就出现，因为目标用户认得它们。
- **个人化**：这是一个单作者项目（`@Estrella` / `13ee.icu`），公告里直接挂"请勿用于盈利性用途 / 唯一发布地点在 GitHub"。这种态度本身是产品的一部分，不要被磨平成无声的企业语气。

视觉与情感锚点：自承"绘世风格启动器"（见 `widgets/home_page.py` 顶部 docstring）——参考的是 SD 绘世启动器在中文 AI 圈那种"一个人做、能用、有自己脾气"的味道，而不是 SaaS 登陆页的精致空洞。

## Anti-references

明确不要的样子：

- **商业化 SaaS 风格的 AI 工具登陆页**：渐变 CTA、首屏价值主张三栏、客户 logo 墙。LaunchAI 不卖东西，公告里反向强调"如果你为它付了费请去投诉商家"。
- **"傻瓜化"的小白包装**：把 `pip`、`venv`、`CUDA` 改名成"魔法环境"、"加速核心"之类的术语糖衣。目标用户已经知道这些词的真实意思，重命名只会让排错更难。
- **散漫无主张的工具聚合站**：单纯把一堆 GitHub 项目摆在一起、没有作者视角、没有取舍。LaunchAI 的"取舍"（哪个 fork、哪条镜像、哪种安装顺序）就是它存在的理由。
- **过度装饰的首页**：横幅已经有动效 + 光晕，足够了。不要再往主页堆磨砂玻璃卡片、巨型渐变标题、3D icon 这类与"用户来这里启动工具"无关的东西。

## Design Principles

四条战略原则，约束所有未来的 UI / 交互决策。这些不是视觉规则（颜色、字号、圆角），而是"在功能取舍发生时优先服从谁"的指南。

1. **消灭环境地狱（Eliminate environment hell）**——产品价值的来源是"它替你装好了"。所有 `PipWorker` 里的硬编码补丁（basicsr 的 torchvision 改名、demucs 的 requirements 过滤、Real-ESRGAN 的安装顺序）都是这一条的具体执行。后续新增工具时，宁可在 worker 里多写一段 fixup，也不要把锅甩给用户的 README。
2. **信任用户的技术常识（Trust the user's technical literacy）**——UI 文案、日志、报错都用真实术语。`torch+cu121` 就写 `torch+cu121`，不要包装成"AI 加速引擎 v12.1"。错误信息暴露原始 stack 比"出错了请重试"更受目标用户欢迎。
3. **尊重中国大陆的真实网络（Honor the actual Chinese-mainland network）**——镜像列表、本地 wheel 缓存、`dulwich` 而非系统 `git`、HEAD 探活后按延迟重排——这些不是性能优化，是"能不能装得上"的底线。任何新增的下载/克隆动作都必须走这套路由，不要回退到默认 PyPI / GitHub 直连。
4. **保留作者声音（Keep the author's voice visible）**——主页公告、关于页、版本号附近这些地方允许、并且应该带有第一人称视角。不要为了"显得专业"把"@Estrella"、"13ee.icu"、对盈利性使用的态度这些痕迹擦掉；它们是这个项目与企业版 AI 工具最关键的区别。

## Accessibility & Inclusion

目标等级：**WCAG 2.1 AA 文本对比度** + **`prefers-reduced-motion` 等价处理**。语言保持中文为主，暂不投入 i18n 脚手架。

具体需要兑现 / 后续要补齐的点：

- **对比度**：当前多处正文使用 `rgba(200,200,200,210)` / `rgba(140,140,140,200)` 等淡灰色字（见 `widgets/home_page.py:329, 367, 388`），叠加 Fluent 深色背景后接近 4.5:1 的临界。改造方向是把"次级文本"的色值从透明度灰统一到一个具名 token（如 `--text-secondary`），并保证它对默认深色背景达到 ≥4.5:1；标题与图标可放宽到 ≥3:1。
- **动效**：`BannerWidget` 的 30 ms 定时器 + 光晕动画是常驻装饰，未来需要在系统侧检测到"减少动效"偏好时退化为静态背景（仅显示当前底图，停止 `_anim_timer`）。Qt 没有原生的 `prefers-reduced-motion`，但可以读 Windows 注册表 `Control Panel\Desktop\UserPreferencesMask` 中的动画位，或在设置页加一个显式开关并默认从系统拿值。
- **语言**：UI、日志、错误信息保持中文。代码注释、文件名、Python 标识符保持英文/拼音混排现状。暂不引入 `gettext` 等 i18n 框架——如果未来有海外贡献者需求再处理，PRODUCT.md 会被相应更新。
- **键盘可达**：节点编辑器 `Shift+A` 已有快捷键先例。新增主要交互时优先考虑能否走键盘（Tab 顺序、Enter 触发主按钮），但不强求每个二级操作都有快捷键。
