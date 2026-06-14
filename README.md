# Telegram Bot Desktop — 手动登录交互版

基于 PyQt6 的 Windows 桌面应用，用于管理 Telegram Bot 登录流程。

## 功能

- **Bot 设置**: 配置 Bot Token、API ID/Hash、代理、输出目录
- **回复配置**: 自定义所有 Bot 回复文案（验证码提示、二级密码、错误提示等）
- **登录成功**: 配置登录成功后的操作（自动回复、群转发等）
- **手动登录**: GUI 内手动输入手机号进行 Telegram 登录
- **实时日志**: 底部面板显示运行日志
- **卡密激活**: 启动时验证卡密，支持 HWID 绑定
- **远程登录**: Telethon 登录在服务端执行，客户端仅做 HTTP 调用

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

## 卡密系统

- 启动时检查卡密状态，未激活则弹出激活窗口
- 支持 HWID 绑定（MAC + CPU + 磁盘序列号）
- 支持天卡/月卡/永久卡格式: `TG-XXXXXXXX-30D`
- 远程验证服务器地址通过加密字符串存储
