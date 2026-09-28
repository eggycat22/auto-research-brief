# 研读 — auto-research-brief

The **website UI stays 研读**. The open-source name is **auto-research-brief**: a personal daily AI briefing with GitHub star write-ups, paper close-reads, an idea inbox, and in-article chat. Self-hosted. Chinese UI. One operator, one machine, one password.

GitHub repo / Docker image: `auto-research-brief`. About: `Personal auto research brief: daily AI news, GitHub stars, paper close-reads, idea inbox. Self-hosted. Chinese UI.`

研读是给**一个人**用的阅读工具：每天巡检官方源写成中文技术日报，从你的 GitHub Star 里挑一个做原图精讲，再精读一篇 Agentic 领域工作。贴链接进灵感箱就能抽出正文追问。自己的机器、自己的数据。骨架是固定 workflow，不是自由 agent，也不是多租户 SaaS。

## What it is

| 能力 | 说明 |
|------|------|
| Daily AI digest / 前沿日报 | 官方源巡检 + 开放检索 + 复核缺口 |
| GitHub stars explainer / Star 精讲 | 按仓库记账；新收藏与历史饥饿兼顾；周日可补周回顾 |
| Agentic paper close-read / 论文精读 | 每次一篇，重要性优先于 arXiv 新鲜度 |
| Inbox / 灵感箱 | 贴公开链接抽出正文，或只记想法 |
| In-article chat / 追问 | 对着当前文章问模型 |
| Optional WeCom notify | 企业微信应用消息；不配也能用网页 |

**Not** a multi-user product. No sign-up, OAuth, or account table. Clone it, run it, keep your data.

## Access password — what it is for

The password is a **gate on your instance**, not a user system.

If you bind `0.0.0.0` on a public IP (or share a computer), anyone who can open the page can: read the library, **chat with your LLM key (you pay)**, and change prompts/settings. The login page stops that. Localhost-only use still benefits from a password if others use the same PC.

First run: leave `ACCESS_PASSWORD` empty and set it in the browser, or put it in `.env`. It is stored as a PBKDF2 hash in SQLite. `change-me` and passwords shorter than 8 characters are rejected.

## LLM APIs (not DeepSeek-only)

Talks **OpenAI-compatible** `POST …/chat/completions` with `Authorization: Bearer`.

Works with:

- DeepSeek (`https://api.deepseek.com`, default model `deepseek-flash`)
- OpenAI, OpenRouter, SiliconFlow, and typical **中转站 / relay** endpoints that expose `/v1/chat/completions`

Set in `.env` or **设置**:

```
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=deepseek-flash
LLM_API_KEY=sk-...
```

Examples (placeholders only):

```
LLM_BASE_URL=https://openrouter.ai/api/v1
LLM_MODEL=anthropic/claude-sonnet-4

LLM_BASE_URL=https://your-relay.example/v1
LLM_MODEL=the-name-shown-on-the-relay
```

If the URL or model name contains `deepseek`, the client also sends DeepSeek `thinking` fields; other providers get a plain chat payload. Cost estimates in the UI still use the DeepSeek Flash price table — treat them as order-of-magnitude if you switch models.

## Run on Windows, macOS, or Linux

Python 3.11+. No Node. This is a **venv + one process** app, not a double-click installer. Closest to one-shot on a server is Docker Compose.

### Windows (PowerShell)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r backend/requirements.txt
Copy-Item .env.example .env
# edit .env — at least LLM_API_KEY, or leave ACCESS_PASSWORD empty for the setup page
cd backend
python run.py
```

Open http://127.0.0.1:8787

If the project lives under OneDrive, SQLite is stored in `%LOCALAPPDATA%\yandu\` so the DB is not file-locked by sync.

### macOS / Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
cp .env.example .env
# edit .env
cd backend
python run.py
```

Open http://127.0.0.1:8787

### Docker (any OS with Docker)

```bash
docker build -t auto-research-brief .
docker run -d --name auto-research-brief --restart unless-stopped -p 8787:8787 -v auto-research-brief-data:/data -e TZ=Asia/Shanghai auto-research-brief
```

Open http://127.0.0.1:8787 and finish first-run setup in the browser. Keys can wait until 设置. Compose is the same image:

```bash
docker compose up -d --build
```

See [docs/deploy.md](docs/deploy.md) for a small Linux VPS (swap, port 8787, backups). No domain required: `http://YOUR_PUBLIC_IP:8787`. Put a password on before exposing the port.

## Configuration

Copy `.env.example` → `.env`. **Never commit `.env`, `data/`, tokens, or written articles.**

| Variable | Purpose |
|----------|---------|
| `ACCESS_PASSWORD` | First-run gate; hashed after bootstrap |
| `LLM_BASE_URL` / `LLM_MODEL` / `LLM_API_KEY` | OpenAI-compatible chat API |
| `GITHUB_USERNAME` | Public star list (token optional) |
| `WECOM_*` | Optional WeCom app notify |
| `PUBLIC_BASE_URL` | Root URL used in notify links |
| `BRAVE_API_KEY` / `TAVILY_API_KEY` | Optional search; else DuckDuckGo HTML |

Web **设置** can fill the same fields. Env secrets win over empty web fields; usernames/URLs already saved in the DB win over env.

More: [docs/configuration.md](docs/configuration.md) · [docs/architecture.md](docs/architecture.md) · [docs/security.md](docs/security.md)

Before you publish a git remote: `python scripts/secret_scan.py`

## Cost

Default path is DeepSeek Flash. Triage turns thinking off; writing and chat turn it on. Off-peak (avoid weekdays 09:00–12:00 and 14:00–18:00 Beijing if you use DeepSeek) is typically a few 角 RMB per day. Details in [docs/operations.md](docs/operations.md).

## License

MIT. Keep secrets and private reading notes off the public tree.
