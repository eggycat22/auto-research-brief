# 配置

密钥：环境变量优先于网页；网页留空不覆盖已有值。用户名、模型、公网地址等：网页已保存的值优先。

## 环境变量

见仓库根目录 `.env.example`。

| 变量 | 含义 |
|------|------|
| `ACCESS_PASSWORD` | 首次口令。留空则打开网页走设置页；启动后写成哈希存进数据库 |
| `LLM_API_KEY` | OpenAI 兼容接口的 key（DeepSeek / OpenRouter / 中转站均可） |
| `LLM_BASE_URL` | 默认 `https://api.deepseek.com`。中转站一般填到 `…/v1` |
| `LLM_MODEL` | 默认 `deepseek-flash`。填该接口上真实存在的模型名 |
| `GITHUB_USERNAME` | GitHub 用户名，拉公开 Star；也可只在网页填写 |
| `GITHUB_TOKEN` | 可选，提高 GitHub 限额 |
| `WECOM_CORPID` | 企业微信企业 ID |
| `WECOM_SECRET` | 自建应用 Secret |
| `WECOM_AGENTID` | 自建应用 AgentId |
| `WECOM_TOUSER` | 默认 `@all` |
| `PUBLIC_BASE_URL` | 推送里的可点击根地址，如 `http://IP:8787` |
| `IMAGE_API_KEY_FILE` | 可选，仅 `scripts/gen_ui_art.py` 用 |
| `DATABASE_PATH` | 默认 `data/yandu.db` |

## 企业微信

1. 管理后台创建自建应用，记下 AgentId 与 Secret。
2. 应用可见范围包含你自己。
3. 要在微信里收消息，打开该应用的微信插件。
4. 网页设置里填齐三项并点「发送测试」。推送是卡片（标题 + 几行要点 + 打开），不是长 markdown。
5. 未配齐则跳过推送，任务仍算成功。空跑默认不推；可勾选「空跑也通知」。

## 网页里改

- **设置**：模型、搜索密钥、GitHub、企业微信、公网地址；点开文章后的**追问**指令
- **任务**：调度时间、条数/分数等旋钮，以及该任务用到的筛选/写作指令
- **任务 → 补充信源**：RSS 等开放检索源
- **运行**：某次任务的日志与费用

改指令不影响已经写好的文章，只作用于下一次任务和之后的对话。
