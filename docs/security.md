# 安全

这是单用户自托管工具，不是多租户 SaaS。开源仓库里不得出现真实密钥。不需要注册、OAuth 或用户表。

## 口令

- 未设置（空、`change-me`、短于 8 位）时，网页只开放首次设置页。口令用来挡住能打开这台实例的人，不是多用户账号。
- 首次设置或 `.env` 里的 `ACCESS_PASSWORD` 会写成 PBKDF2 哈希存进 SQLite，之后以数据库为准。
- 登录 15 分钟内同一 IP 失败 8 次会暂时拒绝。
- HTTPS 下 session cookie 带 `Secure`。
- 设置页可以改口令。

## 不要提交

- `.env`
- `data/`（数据库、运行日志、官方原图、session.secret）
- `private/`、`*.key`、交接包 zip
- 企业微信 Secret、GitHub token、LLM key、生图 key
- 已经写成的私人文章和对话

`.gitignore` 已覆盖上述路径。发布前运行：

```bash
python scripts/secret_scan.py
```

并再搜一遍 `sk-`、`Bearer`、`ww` 开头的 corp secret。

## 运行时

- 公网 IP 暴露时，安全组尽量只放行你的 IP。
- 密钥可在网页设置里保存，明文存在 SQLite（口令除外，口令只存哈希）。文件权限靠机器本身；不要把 `data/` 同步到公开网盘。
- 日志只记状态码、模型名、token 计数、错误类型，不记 Authorization、完整 key、签名 URL。
- 对话里的「抓取链接」只允许 http(s)，拒绝内网和 metadata 地址。
- 每日任务默认不自动重试付费生图；本项目的每日流水线本来就不调用生图。

## 推送

企业微信应用 Secret 等同于「能以该应用发消息」。泄漏后应立即在企业微信管理后台重置 Secret。企业微信是可选通知通道，不配也能用网页。推送是短卡片，不含文章全文。
