# 卡密验证服务端

基于 FastAPI 的远程卡密验证服务，用于 Telegram Bot 桌面版的授权管理。

## 功能

- **卡密激活**: 客户端发送卡密 + 机器码(HWID)，服务端校验并绑定
- **心跳校验**: 客户端启动时验证卡密是否仍有效
- **远程登录**: 服务端运行 Telethon，客户端通过 HTTP API 发起登录（防逆向核心）
- **管理接口**: 生成卡密、查看列表、封禁/解封、统计
- **管理后台 Web UI**: 浏览器访问 `/`，密码登录后图形化管理卡密
- **SQLite 存储**: 轻量无依赖，适合单机部署

## 管理后台 Web UI

部署后浏览器访问服务根路径（例如 `https://你的域名/license/`）即可打开管理后台。

登录密码按以下优先级解析：
1. `DAOHAO_DB_PATH` 指向的 daohao sqlite 库中的 `web_password`（复用 daohao 后台密码）
2. `LICENSE_ADMIN_PASSWORD` 环境变量
3. `LICENSE_ADMIN_KEY` 环境变量

后台功能：统计面板、生成卡密（可批量）、卡密列表（筛选/分页）、封禁/解封/删除。
登录成功后返回一个会话 token（Bearer），管理 API 同时兼容 `X-Admin-Key` 请求头（供脚本调用）。

## 部署到 VPS

### 1. 上传文件

将 `license_server/` 目录上传到你的 VPS：

```bash
scp -r license_server/ root@your-vps:/opt/license_server/
```

### 2. 安装依赖

```bash
cd /opt/license_server
pip install -r requirements.txt
```

### 3. 设置管理密钥并启动

```bash
export LICENSE_ADMIN_KEY="your-secret-admin-key-here"
uvicorn license_server.server:app --host 0.0.0.0 --port 8900
```

或者使用 systemd 服务（推荐）：

```ini
# /etc/systemd/system/license-server.service
[Unit]
Description=TG Bot License Server
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt
Environment=LICENSE_ADMIN_KEY=your-secret-admin-key-here
ExecStart=/usr/bin/python3 -m uvicorn license_server.server:app --host 0.0.0.0 --port 8900
Restart=always

[Install]
WantedBy=multi-user.target
```

```bash
systemctl enable --now license-server
```

### 4. 配置客户端

在 `license/activator.py` 中设置：

```python
LICENSE_SERVER_URL = "http://your-vps-ip:8900"
```

## API 接口

### 公开接口

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/activate` | 激活卡密 `{key, hwid}` |
| POST | `/api/check` | 心跳校验 `{key, hwid}` |
| GET  | `/api/ping` | 健康检查 |

### 远程登录接口（需有效卡密）

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/login/send_code` | 发送验证码 `{phone, api_id, api_hash, proxy?, device_model?, license_key, hwid}` |
| POST | `/api/login/verify_code` | 验证码登录 `{session_id, phone, code}` |
| POST | `/api/login/verify_2fa` | 两步验证 `{session_id, phone, password}` |
| POST | `/api/login/download_session` | 下载 session 文件 `{session_id, phone}` |
| POST | `/api/login/cancel` | 取消登录会话 `{session_id, phone}` |

### 管理接口（需要 `X-Admin-Key` 请求头）

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/admin/generate` | 生成卡密 `{count, days, prefix, note}` |
| GET  | `/api/admin/list` | 查看卡密列表 `?status=&page=&page_size=` |
| POST | `/api/admin/ban` | 封禁卡密 `{key}` |
| POST | `/api/admin/unban` | 解封卡密 `{key}` |
| POST | `/api/admin/delete` | 删除卡密 `{key}` |
| GET  | `/api/admin/stats` | 统计数据 |

### 使用示例（curl）

```bash
# 生成 10 张 30 天卡密
curl -X POST http://localhost:8900/api/admin/generate \
  -H "X-Admin-Key: your-secret-admin-key-here" \
  -H "Content-Type: application/json" \
  -d '{"count": 10, "days": 30}'

# 查看所有卡密
curl http://localhost:8900/api/admin/list \
  -H "X-Admin-Key: your-secret-admin-key-here"

# 封禁某张卡密
curl -X POST http://localhost:8900/api/admin/ban \
  -H "X-Admin-Key: your-secret-admin-key-here" \
  -H "Content-Type: application/json" \
  -d '{"key": "TG-ABCD1234-30D"}'

# 查看统计
curl http://localhost:8900/api/admin/stats \
  -H "X-Admin-Key: your-secret-admin-key-here"
```

## 卡密状态

| 状态 | 说明 |
|------|------|
| `unused` | 已生成，未使用 |
| `used` | 已激活，绑定了 HWID |
| `banned` | 已封禁 |

## 安全建议

- 生产环境建议使用 Nginx 反代 + HTTPS
- `LICENSE_ADMIN_KEY` 使用强密码
- 定期备份 `licenses.db`
