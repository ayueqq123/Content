# Telegram Bot Desktop — 手动登录交互版

基于 PyQt6 的 Windows 桌面应用，用于管理 Telegram Bot 登录流程。

## 功能

- **Bot 设置**: 配置 Bot Token、API ID/Hash、代理、输出目录
- **回复配置**: 自定义所有 Bot 回复文案（验证码提示、二级密码、错误提示等）
- **登录成功**: 配置登录成功后的操作（自动回复、群转发等）
- **手动登录**: GUI 内手动输入手机号进行 Telegram 登录
- **实时日志**: 底部面板显示运行日志
- **多 Bot 手动回复**: 支持多个 Bot Token 同时运行；手动回复按「所选 Bot → 会话所属 Bot → 第一个 Bot」路由，并可在聊天面板下拉选择回复 Bot（HTML 解析失败时自动回退纯文本重发）
- **卡密激活**: 启动时验证卡密，支持 HWID 绑定
- **远程登录**: Telethon 登录在服务端执行，客户端仅做 HTTP 调用
- **Session 有效性检测**: 登录成功后按间隔轮询服务端校验账号是否仍在线，仅在 Telegram 明确未授权时才提示失效（详见「Session 有效性检测」）

## 技术栈

- **PyQt6** — GUI 框架
- **aiogram 3.x** — Telegram Bot API
- **httpx** — 远程登录 HTTP 客户端
- **qasync** — Qt + asyncio 事件循环桥接
- **Nuitka** — 编译为原生 .exe（防反编译）

## 安全防护

本项目内置三层防逆向措施：

1. **Nuitka 原生编译** — Python → C → 机器码，无 `.pyc` 可提取
2. **核心逻辑服务端化** — Telethon 登录引擎运行在服务器，客户端不含登录核心代码
3. **敏感字符串加密** — URL、密钥等通过 `core/crypto.py` 运行时解密，静态分析无法直接提取

## 快速开始

### 1. 安装客户端依赖

```bash
pip install -r requirements.txt
```

### 2. 启动

```bash
python main.py
```

### 3. 配置

启动后在 GUI 中填写:
- Bot Token（从 @BotFather 获取）
- API ID / API Hash（从 https://my.telegram.org 获取）
- 代理（可选）: 支持 `socks5://host:port` 或 `IP:端口:用户名:密码`

### 4. 打包为 .exe（Nuitka — 推荐）

```bash
pip install nuitka

# 调试版（带控制台）
build.bat

# 发行版（无控制台窗口）
build.bat --release
```

> **注意**: 不再推荐使用 PyInstaller，因为 `.pyc` 字节码容易被反编译。
> Nuitka 将 Python 编译为 C 再编译为原生二进制，逆向难度大幅提升。

## 项目结构

```
Content/
├── main.py                    # 入口
├── build.bat                  # Nuitka 构建脚本
├── requirements.txt           # 客户端依赖
├── config.json                # 本地配置（运行时生成）
├── core/
│   ├── crypto.py              # 字符串加密/解密工具
│   ├── config_manager.py      # JSON 配置读写
│   ├── login_engine.py        # 远程登录 HTTP 客户端
│   ├── session_manager.py     # Session 文件管理
│   └── bot_runner.py          # Bot 启动/停止/轮询
├── bot/
│   ├── handlers.py            # Bot 命令处理
│   └── keyboards.py           # 键盘布局
├── ui/
│   ├── main_window.py         # 主窗口（Tab + 日志 + 控制栏）
│   ├── bot_config_tab.py      # Bot 设置 Tab
│   ├── reply_config_tab.py    # 回复配置 Tab
│   ├── success_config_tab.py  # 登录成功配置 Tab
│   ├── activate_dialog.py     # 卡密激活弹窗
│   └── styles.py              # QSS 样式
├── license/
│   ├── hwid.py                # 硬件指纹
│   └── activator.py           # 卡密验证/激活（字符串已加密）
├── license_server/            # 服务端（部署到 VPS）
│   ├── server.py              # FastAPI 服务 + 远程登录 API
│   ├── database.py            # SQLite 卡密存储
│   ├── login_service.py       # 服务端 Telethon 登录引擎
│   ├── requirements.txt       # 服务端依赖
│   ├── static/index.html      # 管理后台 Web UI
│   └── sessions/              # 服务端 Session 文件目录
├── tools/
│   ├── check_proxies.py       # 代理到 Telegram DC 连通性检测
│   └── session_to_tdata.py    # .session 转 tdata zip 工具
└── sessions/                  # 客户端 Session 文件目录
```

## 架构说明

```
┌─────────────────────────┐         ┌───────────────────────────┐
│     客户端 (.exe)        │  HTTP   │     服务端 (VPS)           │
│                         │ ──────> │                           │
│  PyQt6 GUI              │         │  FastAPI                  │
│  aiogram Bot            │         │  ├─ 卡密验证 API           │
│  login_engine (HTTP)    │         │  ├─ 远程登录 API           │
│  license activator      │         │  │  └─ Telethon            │
│  crypto (字符串解密)     │         │  └─ 管理后台 Web UI        │
└─────────────────────────┘         └───────────────────────────┘
```

- 客户端**不包含** Telethon，所有登录逻辑在服务端执行
- 即使客户端被逆向，也只能看到 HTTP 调用接口，看不到登录核心代码
- 卡密验证同时在客户端和服务端双重校验

## Session 有效性检测

登录成功后，客户端可周期性调用服务端 `/api/login/check_session` 确认账号是否仍在线。**校验完全在服务端进行**（Telethon 在服务端），客户端只发 HTTP。

### 只有「确认未授权」才判失效

服务端用 `get_me()` 探测并**按异常类型**判定，避免把瞬时抖动误报成失效：

| 结果 | 判定 |
|------|------|
| `get_me()` 正常返回 | ✅ Session 有效 |
| `UnauthorizedError` / `AuthKeyDuplicatedError` | ❌ Session 已失效（**唯一**判失效的情况） |
| `FloodWaitError` / 其他 RPC 错误 / 连接失败 | ⚠️ 不确定（`inconclusive`），不报失效、不计失败 |

> 注意：不要用 `is_user_authorized()`——它会把任何 RPCError（限流、临时服务端错误）内部吞掉并统一返回 `False`，导致好号被误报失效。

### 出口 IP / 代理一致性 + 多级回退

Session 的 auth_key 是登录时在服务端用「登录那次的 proxy」建立的。校验时若从不同出口 IP 连接，可能被 Telegram 拒绝（误报失效）。为此服务端：

1. 登录时把该 session 使用的 proxy **持久化**到 session 文件旁的 `<base>.proxy`；
2. 校验时按 **请求 proxy → 登录 proxy → 直连** 依次尝试，某个连不上就自动降级到下一个；
3. 全部（含直连）都连不上时只返回「不确定」，**绝不误报失效**。

这样即使代理临时失效、或客户端本地改用了 Clash 等代理，校验也不会把好号判死。

### 服务器 IPv4 到 Telegram 被封时

若服务器到 Telegram DC 的 IPv4 被封（直连全部超时、日志出现「连接超时预算 22s 用尽」），但 IPv6 可用，在服务端设置 `LICENSE_TELEGRAM_USE_IPV6=1` 让**直连**走 IPv6（代理连接仍走 IPv4）。详见 `license_server/README.md`。

### 相关配置（登录成功配置 Tab）

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `session_check_enabled` | 关 | 是否启用周期性检测 |
| `post_login_check_delay` | `300` 秒 | 每隔多少秒检测一次（值越小对 Telegram 探测越频繁，越易触发限流） |
| `session_check_fail_threshold` | `3` | 连续「确认失效」达到此次数才提示，防瞬时问题误报 |
| `session_invalid_notify_group` | 关 | 失效时是否发送通知到群 |

## 加密新字符串

如需加密新的敏感字符串嵌入代码中：

```python
from core.crypto import encrypt, decrypt

blob = encrypt("my_secret_value")
print(blob)           # → base64 字符串
print(decrypt(blob))  # → my_secret_value
```

然后在代码中使用 `_d("base64_blob")` 替代明文。

## 代理格式

支持以下格式:
- `socks5://host:port`
- `socks5://user:pass@host:port`
- `http://host:port`
- `IP:端口:用户名:密码`（自动识别为 SOCKS5）

### 代理池 / 代理文件

代理可通过三种来源提供（优先级由上到下合并去重）：

1. **代理文件**（推荐）：在「Bot 设置 → 代理文件」选择一个 txt 文件，每行一个代理，空行与 `#` 开头的行忽略；
2. **代理池文本框**：直接在框内每行粘贴一个代理（与代理文件合并去重）；
3. **单个代理**：以上都为空时使用「代理设置」里的单个代理，仍为空则直连。

> 代理池不会整体上传到服务端。客户端在本地保存整个代理池，登录时按账号 **轮流分配**（round-robin）一个代理，并把该账号分配到的**单个**代理随每次登录 / 校验请求发给服务端。同一账号后续的 session 校验会复用它登录时那个代理（出口 IP 保持一致，避免被 Telegram 判为异常）。因此「上传一次代理文件」只是让客户端读到整个池，服务端每次收到的仍是对应账号的那一个代理，而不是整份代理池。

### 代理失效自动退回直连

- **登录 / session 校验** 时，如果分配到的代理连不上，服务端会自动改用**直连**完成本次请求（登录阶段：代理 → 直连；校验阶段：请求代理 → 登录代理 → 直连），不会因为代理死掉而登录失败或误报 session 失效。
- 退回直连会让该账号的出口 IP 变成**服务器的直连 IP**，请知悉（相当于该账号临时不走代理）。
- 在「登录成功设置」里勾选 **代理失效（自动退回直连）时发送通知到群** 后，一旦发生退回直连，会向「登录成功上传到群」配置的群发送一条代理失效通知（含手机号、失效代理、时间）。

## Session 转 tdata 工具

登录时服务端会自动把 session 转成 tdata 下发；转换失败时客户端只拿到 `.session` 文件。补齐转换有两种方式：

- **GUI 按钮**：主界面「Session 转 tdata」——选择一个或多个 `.session` 文件，上传到服务端 `/api/login/convert_tdata`（需有效卡密）转换，tdata zip 自动保存到输出目录。
- **本地工具** `tools/session_to_tdata.py`（依赖 `telethon` + `opentele`）：

```bash
pip install telethon opentele
python -m tools.session_to_tdata sessions/13800000000.session
python -m tools.session_to_tdata sessions/ --proxy socks5://user:pass@host:port   # 批量转换
```

转换需要连一次 Telegram（`UseCurrentSession`，不换号不挤号），输出 `<手机号>_<时间戳>_tdata.zip`。

## 卡密系统

- 启动时检查卡密状态，未激活则弹出激活窗口
- 支持 HWID 绑定（MAC + CPU + 磁盘序列号）
- 支持天卡/月卡/永久卡格式: `TG-XXXXXXXX-30D`
- 远程验证服务器地址通过加密字符串存储
