# auto-research-brief

[English](README.md) · [简体中文](README.zh-CN.md)

A personal daily AI briefing you host yourself: official-source news, GitHub star write-ups, paper close-reads, an idea inbox, and in-article chat. One operator, one machine, one password.

The web UI is named **研读**. The GitHub repo and Docker image are **auto-research-brief**.

This is a fixed workflow, not a free-form agent, and not multi-tenant SaaS. Clone it, run it, keep your data.

## What it is

| Feature | What it does |
|---------|----------------|
| Daily AI digest | Official sources + open search + a second pass for gaps |
| GitHub star explainer | One repo at a time from your stars; new stars and neglected history both count; optional Sunday recap |
| Agentic paper close-read | One paper per run; importance over arXiv recency |
| Inbox | Paste a public URL to extract the text, or just jot an idea |
| In-article chat | Ask the model about the article you are reading |
| Optional WeCom notify | Work WeChat app cards; the web UI works without it |

No sign-up, OAuth, or account table.

## Why there is a password

The password is a **gate on your instance**, not a user system.

If you bind `0.0.0.0` on a public IP (or share a computer), anyone who can open the page can read the library, **chat with your LLM key (you pay)**, and change prompts and settings. The login page stops that. Localhost-only use still benefits from a password if others use the same PC.

First run: leave `ACCESS_PASSWORD` empty and set it in the browser, or put it in `.env`. It is stored as a PBKDF2 hash in SQLite. `change-me` and passwords shorter than 8 characters are rejected.

## LLM APIs

Talks OpenAI-compatible `POST …/chat/completions` with `Authorization: Bearer`.

Works with DeepSeek (`https://api.deepseek.com`, default model `deepseek-flash`), OpenAI, OpenRouter, SiliconFlow, and typical relay endpoints that expose `/v1/chat/completions`.

Set in `.env` or in the web Settings page:

```
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=deepseek-flash
LLM_API_KEY=sk-...
```

Placeholders only:

```
LLM_BASE_URL=https://openrouter.ai/api/v1
LLM_MODEL=anthropic/claude-sonnet-4

LLM_BASE_URL=https://your-relay.example/v1
LLM_MODEL=the-name-shown-on-the-relay
```

If the URL or model name contains `deepseek`, the client also sends DeepSeek `thinking` fields; other providers get a plain chat payload. Cost estimates in the UI still use the DeepSeek Flash price table — treat them as order-of-magnitude if you switch models.

## Run on Windows, macOS, or Linux

Python 3.11+. No Node. This is a venv plus one process, not a double-click installer. Closest to one-shot on a server is Docker Compose.

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

### Docker

```bash
docker build -t auto-research-brief .
docker run -d --name auto-research-brief --restart unless-stopped -p 8787:8787 -v auto-research-brief-data:/data -e TZ=Asia/Shanghai auto-research-brief
```

Open http://127.0.0.1:8787 and finish first-run setup in the browser. Keys can wait until Settings. Compose is the same image:

```bash
docker compose up -d --build
```

See [docs/deploy.md](docs/deploy.md) for a small Linux VPS (swap, port 8787, backups). No domain required: `http://YOUR_PUBLIC_IP:8787`. Put a password on before exposing the port.

## Configuration

Copy `.env.example` to `.env`. Never commit `.env`, `data/`, tokens, or written articles.

| Variable | Purpose |
|----------|---------|
| `ACCESS_PASSWORD` | First-run gate; hashed after bootstrap |
| `LLM_BASE_URL` / `LLM_MODEL` / `LLM_API_KEY` | OpenAI-compatible chat API |
| `GITHUB_USERNAME` | Public star list (token optional) |
| `WECOM_*` | Optional WeCom app notify |
| `PUBLIC_BASE_URL` | Root URL used in notify links |
| `BRAVE_API_KEY` / `TAVILY_API_KEY` | Optional search; otherwise DuckDuckGo HTML |

The Settings page in the web UI can fill the same fields. Env secrets win over empty web fields; usernames and URLs already saved in the database win over env.

More: [docs/configuration.md](docs/configuration.md) · [docs/architecture.md](docs/architecture.md) · [docs/security.md](docs/security.md)

Before you publish a git remote: `python scripts/secret_scan.py`

## Cost

Default path is DeepSeek Flash. Triage turns thinking off; writing and chat turn it on. Off-peak (avoid weekdays 09:00–12:00 and 14:00–18:00 Beijing if you use DeepSeek) is typically well under one yuan RMB per day. Details in [docs/operations.md](docs/operations.md).

## License

MIT. Keep secrets and private reading notes off the public tree.
