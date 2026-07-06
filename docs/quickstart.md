# 快速开始

这份文档用于从零启动一个本地 LLM API Factory。

## 依赖

- Linux / WSL2 / macOS
- Python 由 `uv` 管理
- Node.js 与 npm
- 可选：Redis、PostgreSQL

安装 `uv`：

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
source "$HOME/.local/bin/env"
```

安装前端依赖：

```bash
cd frontend
npm ci
cd ..
```

## 单入口启动

推荐使用单入口模式，由后端托管前端构建产物：

```bash
bash scripts/start_all.sh --rebuild-frontend --admin-token "change-me"
```

访问：

```text
http://127.0.0.1:8000
```

日志：

```text
scripts/logs/app-8000.log
```

PID：

```text
scripts/pids/app-8000.pid
```

停止：

```bash
kill "$(cat scripts/pids/app-8000.pid)"
```

## 第一次配置

1. 登录控制台。
2. 在 API 端点页面新增 endpoint。
3. 为 endpoint 添加上游 API key。
4. 探测模型，或手动添加模型映射。
5. 创建规则组，选择模型匹配规则和候选 key。
6. 创建 Factory API Key，并绑定规则组。
7. 下游使用 Factory API Key 调用 `/openai/*`、`/anthropic/*` 或 `/gemini/*`。

## 最小调用

```bash
curl http://127.0.0.1:8000/openai/v1/responses \
  -H "Authorization: Bearer fk-your-factory-key" \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-5.5","input":"hello"}'
```

## 默认数据库

默认使用 SQLite：

```text
backend/llm_api_factory.db
```

SQLite 默认启用：

- foreign key
- `busy_timeout=5000`
- `journal_mode=WAL`

个人和低并发单机可以先用 SQLite。请求日志写入量上来后，建议改用 PostgreSQL。

## 常用环境变量

```bash
export LLM_MASTER_AUTH_TOKEN="change-me"
export LLM_DATA_ENCRYPTION_KEY="a-stable-long-random-secret"
export LLM_APP_TIMEZONE="Asia/Shanghai"
export LLM_DATABASE_URL="sqlite+aiosqlite:///./llm_api_factory.db"
export LLM_REDIS_URL="redis://localhost:6379/0"
```

`LLM_DATA_ENCRYPTION_KEY` 必须稳定保存。上游 API key 和 OAuth client secret 会用它加密后落库。

