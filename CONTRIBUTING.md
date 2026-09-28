# Contributing

研读是单用户自托管。补功能时保持这个边界：不要做成账号系统或多租户。

- 不要提交 `.env`、`data/`、密钥、私人文章。
- 发 PR 前跑 `python scripts/secret_scan.py`。
- 口令只存哈希；密钥不要写进日志。
- 默认任务继续走固定 workflow，不要改成无限 agent。
