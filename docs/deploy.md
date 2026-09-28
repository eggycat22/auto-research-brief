# 部署

开源名 **auto-research-brief**，网页仍叫研读。单进程 Python + SQLite。本机可用 venv，服务器用 Docker。

无域名也可以：防火墙放行 `8787`，浏览器打开 `http://公网IP:8787`。**公网监听前必须设访问口令**，否则别人能读文库、用你的 LLM 密钥追问（费用记在你账上）。

## 本机（Windows / macOS / Linux）

Python 3.11+。步骤见仓库根目录 README。项目若在 OneDrive 下，SQLite 会写到 `%LOCALAPPDATA%\yandu\`，避免同步锁。其他系统默认 `./data/yandu.db`。

## Docker 镜像

密钥不要打进镜像。空卷启动后走网页首次设置。

```bash
docker build -t auto-research-brief .
docker run -d --name auto-research-brief --restart unless-stopped \
  -p 8787:8787 \
  -v auto-research-brief-data:/data \
  -e TZ=Asia/Shanghai \
  auto-research-brief
```

或 Compose（`.env` 可没有）：

```bash
docker compose up -d --build
```

SQLite 在卷里是 `/data/yandu.db`。备份：

```bash
docker cp auto-research-brief:/data/yandu.db ./backup-yandu-$(date +%F).db
```

2G 云主机先加 1–2G swap。升级：重新 `docker build` 再 `docker compose up -d`。

## 注意

- DeepSeek 工作日北京时间 9:00–12:00、14:00–18:00 高峰价翻倍。默认任务尽量避开这段。
- 不要在同一台 2G 机器上再跑 Ollama、Chrome、Postgres。
- 公网暴露时用安全组限制来源 IP。
- **不要把 SQLite 放进网盘同步目录。** Docker 用命名卷，不要绑 OneDrive 路径。
