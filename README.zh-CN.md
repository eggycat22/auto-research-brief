# 研读（auto-research-brief）

[English](README.md) · [简体中文](README.zh-CN.md)

给一个人用的自托管每日简报：官方源技术日报、GitHub Star 精讲、论文精读、灵感箱，以及对着文章追问。一台机器、一个操作者、一个口令。

网页名称是 **研读**。开源仓库和 Docker 镜像名是 **auto-research-brief**。

骨架是固定流程，不是自由 agent，也不是多租户 SaaS。自己的机器、自己的数据。

## 能做什么

| 能力 | 说明 |
|------|------|
| 前沿日报 | 官方源巡检 + 开放检索 + 复核缺口 |
| Star 精讲 | 每次讲一个你 Star 过的仓库；新收藏和长期没写过的都会纳入；周日可补周回顾 |
| 论文精读 | 每次一篇；重要性优先于刚挂上 arXiv |
| 灵感箱 | 贴公开链接抽出正文，或只记想法 |
| 追问 | 对着当前文章问模型 |
| 企业微信（可选） | 应用消息卡片；不配也能用网页 |

没有注册、没有 OAuth、没有账号表。克隆下来跑即可。

## 口令是干什么的

口令是 **这台实例的门禁**，不是用户系统。

如果你把服务绑在公网 IP 的 `0.0.0.0`（或和别人共用一台电脑），能打开网页的人就能读文库、**用你的大模型密钥聊天（钱算你的）**，还能改提示词和设置。登录页就是拦这个的。只在本机用，若电脑上还有别人，也建议设口令。

第一次运行：把 `ACCESS_PASSWORD` 留空，在浏览器里设置；或者写进 `.env`。口令以 PBKDF2 哈希存在 SQLite 里。`change-me` 和短于 8 位的口令会被拒绝。

## 大模型接口

走 OpenAI 兼容的 `POST …/chat/completions`，请求头是 `Authorization: Bearer`。

可用 DeepSeek（`https://api.deepseek.com`，默认模型 `deepseek-flash`），以及 OpenAI、OpenRouter、硅基流动，还有提供 `/v1/chat/completions` 的中转站。

在 `.env` 或网页「设置」里填写：

```
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=deepseek-flash
LLM_API_KEY=sk-...
```

下面只是占位示例：

```
LLM_BASE_URL=https://openrouter.ai/api/v1
LLM_MODEL=anthropic/claude-sonnet-4

LLM_BASE_URL=https://your-relay.example/v1
LLM_MODEL=the-name-shown-on-the-relay
```

地址或模型名里带 `deepseek` 时，客户端会额外发送 DeepSeek 的 `thinking` 字段；其他供应商走普通对话请求。界面里的费用估算仍按 DeepSeek Flash 价目，换模型后只当数量级参考。

## 在 Windows、macOS、Linux 上运行

需要 Python 3.11+，不需要 Node。这是虚拟环境加一个进程，不是双击安装包。服务器上最省事的是 Docker Compose。

### Windows（PowerShell）

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r backend/requirements.txt
Copy-Item .env.example .env
# 编辑 .env：至少填 LLM_API_KEY，或把 ACCESS_PASSWORD 留空，用浏览器设置页
cd backend
python run.py
```

打开 http://127.0.0.1:8787

项目如果放在 OneDrive 下，SQLite 会写到 `%LOCALAPPDATA%\yandu\`，避免同步锁库。

### macOS / Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
cp .env.example .env
# 编辑 .env
cd backend
python run.py
```

打开 http://127.0.0.1:8787

### Docker

```bash
docker build -t auto-research-brief .
docker run -d --name auto-research-brief --restart unless-stopped -p 8787:8787 -v auto-research-brief-data:/data -e TZ=Asia/Shanghai auto-research-brief
```

打开 http://127.0.0.1:8787，在浏览器里完成首次设置。密钥可以稍后在「设置」里填。Compose 用的是同一镜像：

```bash
docker compose up -d --build
```

小体积 Linux 云主机（交换分区、8787 端口、备份）见 [docs/deploy.md](docs/deploy.md)。不一定要域名：`http://你的公网IP:8787`。端口对外开放之前先设口令。

## 配置

把 `.env.example` 复制为 `.env`。不要提交 `.env`、`data/`、令牌或写好的文章。

| 变量 | 用途 |
|------|------|
| `ACCESS_PASSWORD` | 首次门禁；引导完成后只存哈希 |
| `LLM_BASE_URL` / `LLM_MODEL` / `LLM_API_KEY` | OpenAI 兼容对话接口 |
| `GITHUB_USERNAME` | 公开 Star 列表（令牌可选） |
| `WECOM_*` | 可选的企业微信应用通知 |
| `PUBLIC_BASE_URL` | 通知卡片里的站点根地址 |
| `BRAVE_API_KEY` / `TAVILY_API_KEY` | 可选检索；不填则用 DuckDuckGo 网页 |

网页「设置」可以填同样的项。环境变量里的密钥优先于网页里的空值；已经写进数据库的用户名和网址优先于环境变量。

更多：[docs/configuration.md](docs/configuration.md) · [docs/architecture.md](docs/architecture.md) · [docs/security.md](docs/security.md)

对外公开 git 远程之前先跑：`python scripts/secret_scan.py`

## 费用

默认走 DeepSeek Flash。筛选关闭思考；写稿和追问打开思考。避开工作日北京时间 9:00–12:00、14:00–18:00 的高峰，一天通常几角人民币。细节见 [docs/operations.md](docs/operations.md)。

## 许可证

MIT。密钥和私人阅读笔记不要放进公开仓库。
