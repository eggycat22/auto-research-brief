# Changelog

## 0.3.5 — 2026-09-28

- Open-source name is auto-research-brief (UI stays 研读). Docker image builds with /data volume, TZ, and /health.

## 0.3.4 — 2026-09-28

- Tasks can be paused and resumed from the task bar (schedule only; 立即运行 still works).

## 0.3.3 — 2026-09-28

- WeCom: textcard instead of markdown, mute empty-day pings, settings checklist and test send.

## 0.3.2 — 2026-09-28

- In-article chat composer contrast (card + filled send).
- LLM client: DeepSeek `thinking` only when the base URL or model looks like DeepSeek; other OpenAI-compatible relays get a plain payload.
- README / deploy: Windows, macOS, Linux, Docker; password purpose; no secrets in docs.

## 0.3.1 — 2026-09-28

- Inbox fetches public URLs (arXiv / GitHub / HTML / PDF) into the reading pane so chat has the actual text.

## 0.3.0 — 2026-09-27

- Self-hosted setup: first-run page, hashed access password, login rate limit, Secure cookie on HTTPS.
- Inbox only saves notes; no separate “write now” action.
- Chat token cost rolls into today’s spend.

## 0.2.2 — 2026-09-21

- Task prompts live on each task's edit form; Settings only keeps API keys and in-article 追问.

## 0.2.1 — 2026-09-20

- Bounded research/chat agent with search (web/github/huggingface/arxiv/s2/library), fetch, GitHub file, and PDF tools.
- Frontier open search is bilingual and multi-backend; Star/Agentic deep-read through the agent before writing.

## 0.2.0 — 2026-09-20

- Notify channel is WeCom app messaging (corpid / secret / agentid). Server酱 has been removed.
- Split into three scheduled workflows: frontier digest, GitHub Star ledger, Agentic reading.
- Prompts rewritten from the original ChatGPT task specs: official-source patrol, evidence rules, original figures, anti-starvation Star queue, Sunday weekly review.

## 0.1.0 — 2026-09-20

- First usable version: daily briefing workflow, GitHub star write-up, idea inbox, in-article chat, web task CRUD.
- Default model path is DeepSeek `deepseek-flash` (OpenAI-compatible). Triage runs with thinking off; writing and chat use thinking.
- Single Python process, SQLite, no Node runtime. Docker image is Python-only for 2C2G machines.
