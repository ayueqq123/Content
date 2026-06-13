# Telegram Bot Desktop — 手动登录交互版

基于 PyQt6 的 Windows 桌面应用，用于管理 Telegram Bot 登录流程。

## 功能

- **Bot 设置**: 配置 Bot Token、API ID/Hash、代理、输出目录
- **回复配置**: 自定义所有 Bot 回复文案（验证码提示、二级密码、错误提示等）
- **登录成功**: 配置登录成功后的操作（自动回复、群转发等）
- **手动登录**: GUI 内手动输入手机号进行 Telegram 登录
- **实时日志**: 底部面板显示运行日志
- **卡密激活**: 启动时验证卡密，支持 HWID 绑定

## 技术栈

- **PyQt6** — GUI 框架
- **aiogram 3.x** — Telegram Bot API
- **Telethon** — Telegram 客户端登录
- **qasync** — Qt + asyncio 事件循环桥接

## 快速开始

### 1. 安装依赖

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

### 4. 打包为 .exe（Windows）

```bash
pyinstaller --onefile --windowed --name "TGBot" main.py
```

## 项目结构

```
Content/
├── main.py                    # 入口
├── requirements.txt
├── config.json                # 本地配置（运行时生成）
├── core/
│   ├── config_manager.py      # JSON 配置读写
│   ├── login_engine.py        # Telethon 登录引擎
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
│   └── activator.py           # 卡密验证/激活
└── sessions/                  # Session 文件目录
```

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
- 可对接远程验证服务器（修改 `license/activator.py` 中的 `LICENSE_SERVER_URL`）
