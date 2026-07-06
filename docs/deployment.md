# 部署与配置

## 本地长期运行

推荐先构建前端，再由后端托管：

```bash
bash scripts/start_all.sh --rebuild-frontend --admin-token "change-me"
```

后台运行时：

```bash
tail -f scripts/logs/app-8000.log
kill "$(cat scripts/pids/app-8000.pid)"
```

## systemd

示例 user service：

```ini
[Unit]
Description=LLM API Factory
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/path/to/llm_api_factory/backend
Environment=LLM_MASTER_AUTH_TOKEN=change-me
Environment=LLM_DATA_ENCRYPTION_KEY=replace-with-a-stable-random-secret
Environment=LLM_APP_TIMEZONE=Asia/Shanghai
Environment=LLM_CORS_ALLOW_ORIGINS=*
ExecStart=/home/you/.local/bin/uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
```

启用：

```bash
systemctl --user daemon-reload
systemctl --user enable --now llm-api-factory.service
```

## 代理环境

如果 shell 可以访问上游，但 systemd 服务不行，通常是代理环境没有传入 service。

```bash
mkdir -p ~/.config/systemd/user/llm-api-factory.service.d
```

```ini
# ~/.config/systemd/user/llm-api-factory.service.d/proxy.conf
[Service]
Environment="HTTP_PROXY=http://127.0.0.1:7897"
Environment="HTTPS_PROXY=http://127.0.0.1:7897"
Environment="http_proxy=http://127.0.0.1:7897"
Environment="https_proxy=http://127.0.0.1:7897"
Environment="NO_PROXY=127.*,localhost,<local>"
Environment="no_proxy=127.*,localhost,<local>"
```

重启：

```bash
systemctl --user daemon-reload
systemctl --user restart llm-api-factory.service
```

## PostgreSQL

SQLite 可以先用，生产高并发建议 PostgreSQL：

```bash
export LLM_DATABASE_URL="postgresql+asyncpg://llm:password@localhost:5432/llm_factory"
export LLM_PG_POOL_SIZE=10
export LLM_PG_MAX_OVERFLOW=5
```

`scripts/start_all.sh` 检测到 PostgreSQL URL 时，会用 `pg_isready` 做启动前可达性检查。

## Redis

Redis 用于：

- 健康探测结果
- 熔断状态
- 时间序列统计
- sequential 当前候选状态

配置：

```bash
export LLM_REDIS_URL="redis://localhost:6379/0"
```

没有 Redis 时会退回内存实现，服务可以运行，但重启后运行态会丢失。

## 关键配置

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `LLM_MASTER_AUTH_TOKEN` | `None` | 管理员密码 |
| `LLM_DATA_ENCRYPTION_KEY` | `None` | 数据库敏感字段加密密钥 |
| `LLM_APP_TIMEZONE` | `Asia/Shanghai` | 控制台统计和日期窗口时区 |
| `LLM_DATABASE_URL` | SQLite | 数据库地址 |
| `LLM_REDIS_URL` | `redis://localhost:6379/0` | Redis 地址 |
| `LLM_HTTP_TIMEOUT_SECONDS` | `60` | 直连上游超时 |
| `LLM_CIRCUIT_BREAKER_FAILURES` | `3` | 熔断失败阈值 |
| `LLM_CIRCUIT_BREAKER_TTL_SECONDS` | `3600` | 熔断 TTL |
| `LLM_AGENT_ALLOWED_TARGETS` | `*` | Agent 默认目标 allowlist |
| `LLM_AGENT_REQUEST_TIMEOUT_SECONDS` | `60` | Agent 请求启动超时 |
| `LLM_AGENT_STREAM_IDLE_TIMEOUT_SECONDS` | `300` | Agent 流式空闲超时 |
| `LLM_PROXY_DUMP_ROOT` | `backend/proxy_dumps` | dump 文件目录 |

生产环境至少设置 `LLM_MASTER_AUTH_TOKEN` 和 `LLM_DATA_ENCRYPTION_KEY`。

