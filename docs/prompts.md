# Prompt 约定

产品行为主要由 `prompts/` 下的 Markdown 决定。网页改的是覆盖稿，存在 SQLite，不改仓库文件。

**任务页**编辑每条任务时，只出现这条流水线会用到的指令。**设置 → 追问**只改点开文章后的讨论人设。`frontier.md` / `explain.md` 已不再接入。

| 任务 | 指令 | thinking |
|------|------|----------|
| 前沿快讯 | `triage` 筛选 → `news` 快讯 | 筛选关；快讯关 |
| GitHub Star 精讲 | `github_star` 精讲；周日可能再跑 `weekly`；写稿前 `research` | 精讲/周回顾开；检索关 |
| Agentic 精读 | `agentic_pick` 选题 → `agentic` 精读；写稿前 `research` | 选题关；精读开 |
| （阅读，非任务） | `chat` 追问 | 默认开，设置里可关 |

同类任务共用同一份指令。`research` 由 Star 精讲和 Agentic 精读共用。

## 规则

- 用中文写，面向工程阅读，不要营销腔。
- 只根据给定材料写。没有的数字、实验、身份不要编。未运行不得声称实测。
- 材料放在用户消息里，用 `<<<SOURCE>>>` 包裹。
- 原图只引用已保存的 `/media/...`，不要生成替代图。
- 改默认稿时同步改 `CHANGELOG.md` 一句说明。
- 不要把个人 API key、公司内网地址写进 prompt。
