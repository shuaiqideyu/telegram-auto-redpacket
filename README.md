# Telegram 自动抢红包 v0.2 学习版

多账号 Telegram 红包自动监听、识别与领取系统，附带 Web 管理控制台。

检测到群组 / 频道中的红包后，按类型自动分流到对应领取模块；支持多账号并发、策略过滤、群组级开关与战绩统计。当前版本 **v0.2 学习版**。

---

## 能做什么

| 能力 | 说明 |
|---|---|
| 关键词领取 | 识别带「领取」等关键词的 Callback 按钮，一步点击到账 |
| 窗口验证码 | 群消息里的算式验证码（含花式数字 / Custom Emoji），解出后点对应选项 |
| 私信验证码 | 群内跳转 Bot，在私聊里出题再解题领取 |
| 网页验证 | 打开 WebView 验证页，用视觉模型识别图片验证码后提交（如 OKPay / KKPay） |
| 福利来 | 通过 hCaptcha 打码 + HTTP 接口领取 |

控制台还可管理：

- **账号**：手机号登录 / 扫码登录 / Session 导入，启停监听与秒包
- **秒包群组**：扫描会话、按群开关、置顶
- **屏蔽**：按群、用户、机器人屏蔽，或屏蔽私信红包
- **策略过滤**：关键词黑名单、币种白/黑名单、金额下限、条件预检
- **总览与记录**：今日战绩、近七日趋势、钱包汇总、逐条领取记录
- **热更新**：改模块开关或系统配置后即时生效，无需重启账号进程

---

## 技术栈

| 层 | 技术 |
|---|---|
| 后端 | Python 3 · FastAPI · Telethon · SQLAlchemy（async）· PostgreSQL · Redis |
| 前端 | Vue 3 · TypeScript · Vite · Element Plus · ECharts |
| 可选依赖 | Playwright / CloakBrowser（网页验证码）、兼容 OpenAI 协议的视觉模型、2captcha（福利来 hCaptcha） |

---

## 环境要求

开始前请准备：

1. **Python 3.10+**、**Node.js 18+**
2. **PostgreSQL 14+**（存账号、配置、记录）
3. **Redis 6+**（跨账号答案共享、去重与缓存）
4. Telegram 应用凭据：到 [my.telegram.org](https://my.telegram.org) 申请 `api_id` / `api_hash`
5.（可选）通知 Bot Token：用 [@BotFather](https://t.me/BotFather) 创建，用于把领取结果私聊推送给账号本人
6.（可选）视觉模型 API Key：网页验证码识别用
7.（可选）2captcha API Key：福利来模块打码用

---

## 快速开始（本地开发）

### 1. 克隆仓库

```bash
git clone https://github.com/shuaiqideyu/telegram-auto-redpacket.git
cd telegram-auto-redpacket
```

### 2. 安装 Python 依赖

推荐使用虚拟环境：

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

若要使用「网页验证」模块，再安装浏览器内核：

```bash
playwright install chromium
```

### 3. 安装前端依赖

```bash
cd frontend
npm install
cd ..
```

### 4. 准备数据库与 Redis

在 PostgreSQL 中创建数据库与用户（名称可自定，与下面连接串一致即可），例如：

```sql
CREATE USER hongbao WITH PASSWORD 'changeme';
CREATE DATABASE hongbao OWNER hongbao;
```

确认本机 Redis 已启动（默认示例使用 DB `10`）。

### 5. 填写配置

```bash
cp .env.example .env
```

用编辑器打开 `.env`，按实际环境修改。常用项说明：

| 变量 | 必填 | 说明 |
|---|---|---|
| `API_ID` | 是 | Telegram 应用 ID |
| `API_HASH` | 是 | Telegram 应用 Hash |
| `DATABASE_URL` | 是 | 形如 `postgresql+asyncpg://用户:密码@主机:5432/库名` |
| `REDIS_URL` | 建议 | 默认 `redis://127.0.0.1:6379/10` |
| `HOST` / `PORT` | 否 | 后端监听地址，默认 `127.0.0.1:8000` |
| `AUTOSTART_ACCOUNTS` | 否 | `true` 时启动后自动恢复已启用账号；本地可保持 `false` |
| `NOTIFY_BOT_TOKEN` | 否 | 领取结果通知 Bot |
| `VISION_API_KEY` | 否 | 网页验证码视觉识别 |
| `VISION_BASE_URL` / `VISION_MODEL` / `VISION_MODELS` | 否 | 视觉 API 地址与模型；可配多模型并发 |
| `BROWSER_BACKEND` | 否 | `playwright`（默认）或 `cloak` |
| `HEADLESS` | 否 | 浏览器是否无头，默认 `true` |
| `SESSION_ENCRYPT_KEY` | 生产建议 | 64 位 hex，用于加密存储账号 Session；开发可留空。生成：`python3 -c "import secrets; print(secrets.token_hex(32))"` |
| `MAX_ATTEMPTS` | 否 | 单个红包最多尝试轮数，默认 `4`（首次播种进数据库，之后以控制台为准） |

`.env` 中的 AI / 通知等项主要用于**首次启动时写入数据库**；之后请以 Web 控制台「系统配置」为准，保存后会热更新到运行中的账号。

### 6. 启动

开发时需要**两个进程**：

```bash
# 终端 1：后端 API（默认 http://127.0.0.1:8000）
python3 main.py

# 终端 2：前端开发服务器（http://localhost:5173，已代理 /api → 后端）
cd frontend && npm run dev
```

浏览器打开 **http://localhost:5173**。

### 7. 第一次使用控制台

1. 进入 **账号管理**，用手机号、扫码或 Session 导入登录 Telegram 账号  
2. 开启该账号的「监听」与「秒包」  
3. 进入 **红包模块**，按需要打开对应模块；网页验证 / 福利来请先在 **系统配置** 填好 API Key  
4. 进入 **秒包管理**，扫描群组并确认要抢的群已开启  
5. 需要过滤低价值或指定币种时，在 **系统配置** 里设置领取策略  
6. 领取结果可在 **秒包记录** / **总览** 查看；若配置了通知 Bot，账号本人还会收到私聊推送  

---

## 生产部署

生产环境一般只跑**一个后端进程**：先构建前端静态资源，由 FastAPI 同端口托管。

### 1. 构建前端

```bash
cd frontend
npm ci
npm run build
cd ..
```

产物目录：`frontend/dist/`。

### 2. 配置与启动

1. 服务器上同样配置 `.env`（`DATABASE_URL`、`REDIS_URL`、`API_ID` / `API_HASH` 等）  
2. 建议 `HOST=0.0.0.0`、`AUTOSTART_ACCOUNTS=true`，并填写 `SESSION_ENCRYPT_KEY`  
3. 使用项目虚拟环境启动：

```bash
.venv/bin/python main.py
```

或：

```bash
.venv/bin/uvicorn backend.app:app --host 0.0.0.0 --port 8000
```

可用 systemd、Supervisor、宝塔「Python 项目」等托管；工作目录为仓库根目录。进程启停请在面板或本机自行操作。

### 3. Nginx 反代（可选）

```nginx
server {
    listen 443 ssl;
    server_name your-domain.example.com;

    # ssl_certificate     /path/to/fullchain.pem;
    # ssl_certificate_key /path/to/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 120s;
    }
}
```

部署后访问 `https://your-domain.example.com` 即可打开控制台。

### 4. 升级与回滚

```bash
git fetch --tags
git checkout v0.2          # 或其它目标版本
source .venv/bin/activate
pip install -r requirements.txt
cd frontend && npm ci && npm run build && cd ..
# 然后在进程管理器中重启本服务
```

回滚时切回上一 tag，重新构建前端并重启即可。

---

## 目录说明

```
.
├── main.py                 # 后端入口（uvicorn）
├── requirements.txt        # Python 依赖
├── .env.example            # 环境变量模板
├── core/                   # 识别、领取、通知、缓存、视觉
│   └── claimers/           # 各红包类型领取模块
├── backend/                # FastAPI、账号调度、路由
├── frontend/               # Web 控制台源码
└── tools/                  # 调试辅助脚本（可选）
```

控制台路由：

| 路径 | 页面 |
|---|---|
| `/dashboard` | 总览 |
| `/accounts` | 账号管理 |
| `/modules` | 红包模块 |
| `/groups` | 秒包管理 |
| `/blocklist` | 屏蔽管理 |
| `/records` | 秒包记录 |
| `/settings` | 系统配置 |

---

## 常见问题

**启动报数据库连接失败**  
检查 PostgreSQL 是否运行、`DATABASE_URL` 用户密码库名是否正确，以及本机是否允许该用户连接。

**Redis 报错或功能异常**  
确认 Redis 已启动，且 `REDIS_URL` 的 DB 编号、密码与实例一致。

**登录账号提示 Session 失效**  
在控制台重新登录或重新导入 Session。

**网页验证码不识别**  
在系统配置中填写可用的视觉 API Key / Base URL / 模型；确认已执行 `playwright install chromium`。

**福利来领不到**  
在模块或系统配置中填写 2captcha Key，并确认打码账户余额充足。

**改了配置不生效**  
多数设置保存后会热更新。若刚改的是 `.env` 里的部署级项（如数据库地址、端口），需要重启后端进程。

**开发时前端改完没有变化**  
请使用 `npm run dev`，不要每次用 `npm run build` 代替开发服务器。

---

## License

[MIT](./LICENSE)
