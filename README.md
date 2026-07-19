# 夸克网盘签到助手 · Web 版

夸克网盘每日自动签到工具，原项目 [QuarkcheckIn](https://github.com/Zh-0316/QuarkcheckIn)（PyQt6 桌面 GUI）的 **Web 重构版**。
将签到逻辑迁移为 Web 服务：浏览器管理多账户、手动/批量签到、定时任务、实时日志，并通过 [Server 酱](https://sct.ftqq.com/) 推送通知。

> 签到核心逻辑与抓包方式继承自原项目 [QuarkcheckIn](https://github.com/Zh-0316/QuarkcheckIn)（[@Zh-0316](https://github.com/Zh-0316)）。
> 本仓库（Web 版）：https://github.com/Zhangxxl/quark-checkin-web

## 功能特性

- **多账户管理**：Web 页面添加 / 删除账户，独立签到
- **签到防重复**：每个账户每天只签到一次
- **手动触发**：单个签到 / 一键全部 / 批量选择签到
- **定时任务**：每天指定时间自动签到（默认北京时间 00:30，可在页面调整）
- **Server 酱通知**：每次签到结果推送微信（可在页面修改 SendKey 与通知模式）
- **实时日志**：记录最近三天日志，Web 页面每 5 秒自动刷新（SQLite + 滚动文件）
- **Docker 化**：`docker compose` 一键部署，数据持久化

## 技术栈

- Python 3.12 + Flask（Web 服务与 API）
- SQLite（用户 / 签到记录 / 设置 / 日志）
- APScheduler（进程内定时调度）
- 前端原生 HTML/CSS/JS（Catppuccin Mocha 暗色主题）
- Docker / Docker Compose

## 快速开始（Docker，推荐）

```bash
# 1. 准备环境变量
cp .env.example .env
# 编辑 .env，填入你的 SERVERCHAN_SENDKEY（可选）

# 2. 启动
docker compose up -d --build

# 3. 访问
# 浏览器打开 http://<服务器IP>:5000
```

数据（SQLite 数据库与日志）持久化在名为 `quark-data` 的 Docker 卷中。

### `.env` 说明

| 变量 | 说明 | 默认值 |
| --- | --- | --- |
| `SIGN_HOUR` | 定时签到小时（0-23，北京时间） | `0` |
| `SIGN_MINUTE` | 定时签到分钟（0-59） | `30` |
| `SERVERCHAN_SENDKEY` | Server 酱 SendKey，留空不推送 | 空 |
| `NOTIFY_MODE` | `all` / `fail` / `success` | `all` |
| `PORT` | 服务监听端口 | `5000` |

> 这些值仅在**首次**部署时作为默认值写入数据库；之后在网页「⏰ 定时设置」中修改的值会持久化到数据库，优先于 `.env`。

## 抓包获取签到 URL

### 手机端

1. 安装抓包工具（如 HttpCanary、Stream 等），开始抓包
2. 打开夸克网盘 APP，进入签到页面
3. 在抓包工具中找到 URL：
   ```
   https://drive-m.quark.cn/1/clouddrive/act/growth/reward
   ```
4. 复制该请求的完整 URL（**必须包含 `kps`、`sign`、`vcode` 三个参数**）
5. 回到 Web 页面，点击「➕ 添加用户」，粘贴 URL 即可

> 注意：URL 有效期未知，失效后需重新抓包；多账户需分别抓包。

## 本地运行（无 Docker）

```bash
pip install -r requirements.txt
export SERVERCHAN_SENDKEY="你的SendKey"   # 可选
python app/app.py
# 打开 http://127.0.0.1:5000
```

数据文件默认生成在 `app/` 同目录（`quark.db` 与 `quark_sign.log`），可用 `DATA_DIR` 环境变量指定其他目录。

## 项目结构

```
quark-checkin-web/
├── app/
│   ├── app.py                 # Flask 后端：签到逻辑 / API / 调度 / Server 酱
│   ├── templates/index.html  # 单页 Web UI
│   └── static/                # style.css / app.js
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── .env.example               # 环境变量模板（提交）
├── .env                       # 实际环境变量（不提交，gitignore）
├── LICENSE                    # MIT
└── README.md
```

## 主要 API

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET/POST | `/api/users` | 用户列表 / 添加用户 |
| DELETE | `/api/users/<id>` | 删除用户 |
| POST | `/api/sign/one/<id>` | 单个签到 |
| POST | `/api/sign/all` | 全部签到 |
| POST | `/api/sign/selected` | 批量选择签到 |
| GET/POST | `/api/schedule` | 读取 / 设置定时 |
| GET/POST | `/api/notify_mode` | 读取 / 设置通知模式 |
| GET/POST | `/api/serverchan` | 读取 / 设置 SendKey |
| POST | `/api/serverchan/test` | 测试推送 |
| GET | `/api/logs?days=3` | 最近三天日志 |
| GET | `/api/status` | 服务状态 |

## 说明与免责

- 本工具仅用于学习与交流，请遵守相关平台规则，勿滥用。
- 签到接口来自夸克移动端，参数有效期未知，失效后需重新抓包。
- 签到请求关闭了 TLS 证书校验（与原项目一致），仅用于请求夸克官方接口，以保证签到稳定性。

## 许可证

[MIT](LICENSE) — 衍生自原项目，保留原作者致谢。
