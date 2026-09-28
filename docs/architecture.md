# 架构

研读是一个跑在单机上的个人前沿晨报。骨架是 **workflow**，不是自由 agent。点进一篇之后的讨论才是带上限的小 agent。

```
浏览器  →  FastAPI（页面 + /api）
              ├ SQLite（含 GitHub Star 账本）
              ├ APScheduler（三条独立 crontab）
              ├ 采集：官方源巡检 / arXiv / HF Daily / GitHub Star 分页 / RSS
              ├ 检索 agent（search/fetch/github/pdf，有次数上限）
              ├ 官方原图与 PDF 图落盘到 data/media
              └ 可选企业微信应用消息
```

## 三条任务

| 类型 | 默认 cron | 做什么 |
|------|-----------|--------|
| `frontier_digest` | 07:00 | 固定官方源巡检 → 开放检索 → 写一篇日报 |
| `github_star` | 07:10 | 同步公开 Star 账本 → 深入 1 个仓库（原图入库）→ 周日周回顾 |
| `agentic_reading` | 07:20 | 选 1 篇 Agentic 关键工作写成精读 |

旧任务类型 `daily_briefing` 启动时会迁成 `frontier_digest`，并补上后两条（若缺失）。

## 为什么这样拆

- 每天必须交卷，固定步骤才能重试、看日志、控费用。
- 筛选量大，必须关 thinking。
- 对话需要上下文，但不允许无限 tool loop。
- 企业微信只推短导读；完整图文在网页。

## 日报步骤

1. 直接请求固定官方入口（RSS / Releases Atom / 首页 HTML），失败记「尚未完成核验」
2. 开放检索：arXiv、HF Daily、额外 RSS
3. `triage.md` 打分并标注发布状态（传闻/灰度/Preview/Beta/GA/API/权重等）
4. `frontier.md` 写成一篇连贯日报（通常 1–3 个主篇）
5. 先保存文章，再推企业微信

## GitHub Star 账本

- 公开接口 ` /users/{user}/starred `，`per_page=100`，跟 Link 分页
- Accept `application/vnd.github.star+json` 取收藏时间
- 以 `repository_id`（`external_id=repo:{id}`）去重，处理更名
- 同步失败不等于取消收藏，不删账本、不把缺席项标 unstarred
- 选择：你指定的 `prefer_repo` > 近 14 天新收藏 > 队列超过 21 天的历史项（避免饥饿）
- 已完成只在队列状态为 queued/retry 时再写；取消收藏且完整同步成功才移出队列，已有报告保留

## 数据

SQLite 表：`settings`、`tasks`、`sources`、`items`、`messages`、`job_runs`。

条目 `kind`：`paper` / `news` / `github_star` / `agentic` / `inbox` / `digest`。

官方原图：`data/media/`，网页路径 `/media/...`。

## 费用要点

- Prompt 放消息最前面，吃 DeepSeek 前缀缓存。
- 定时默认北京时间 07:00 起错开，避开工作日 9:00–12:00、14:00–18:00 高峰。
- 2C2G：不渲染 JS 官网。动态页过短会标「尚未完成核验」，不能据此写「没有更新」。
