# LLM API Factory

LLM API Factory 是一个面向个人或小团队的 LLM 控制面。它对外提供统一 API 入口，对内管理 endpoint、API key、规则组、健康探测、熔断和 agent 节点，让 agent、CLI 工具和自定义脚本只需要面向一组逻辑模型名工作。

项目的核心方向是：标准协议最小侵入代理 + 策略路由 + 执行位置控制。

## 项目定位

LLM API Factory 负责：

- 统一 OpenAI / Anthropic / Gemini 风格入口
- 逻辑模型名、模型映射和规则组路由
- endpoint / API key / factory key / agent 管理
- 健康探测、熔断、候选筛选和 fallback
- 请求日志、用量统计、route test / route explain
- 面向自动化脚本和 agent 运维的 CLI 控制面

它不负责：

- 做公共运营平台、多租户套餐、注册登录和复杂计费
- 重复实现 CLIProxyAPI 的 OAuth、订阅账号和协议翻译能力
- 自动部署或管理 GPU、本地模型、vLLM、llama.cpp
- 把所有 provider 兼容细节都写进主服务

CLIProxyAPI 或其他特殊 runtime 可以作为普通 upstream endpoint 接入；如果它已经能被主节点直接访问，不需要强制走 agent。

## 主要能力

- 标准 provider：OpenAI、Anthropic、Gemini
- 自定义 provider：自定义 path、header、cookie、query、request body template
- OpenAI 风格入口：models、chat completions、completions、embeddings、responses
- Anthropic 风格入口：messages
- Gemini 风格入口：generateContent、streamGenerateContent
- 标准链路流式与非流式透传
- 按模型名和规则组选择候选 endpoint / key
- sequential、weighted round robin 等策略
- 熔断不可用 key，并在探测或成功请求后恢复
- Agent 节点通过 WebSocket 回连，支持远程代理请求
- 管理控制台和 CLI 两套控制面

## Provider 语义

### 标准 Provider

`openai`、`anthropic`、`gemini` 走最小侵入代理路线：

- 下游 body 想传什么就传什么
- 不主动删除未知字段
- 不主动做 request body template
- 不使用 endpoint 的 custom header/query/cookie/path suffix
- OpenAI / Anthropic 只在需要时替换 `model`
- Gemini 只在需要时替换 URL path 里的 model
- 注入必要鉴权头和 trace header
- 响应尽量原始返回，日志和 usage 统计在旁路解析

这意味着标准 provider 尽量兼容上游协议未来新增字段。下游如果传了上游不接受的字段，上游报错会原样暴露给下游。

### Custom Provider

`custom` provider 是强定制适配器，适合接入私有协议、魔改兼容接口或外部 runtime：

- `url_path_suffix`
- `extra_headers`
- `extra_cookies`
- `extra_query_params`
- `request_body_template`

这些能力只对 `custom` 生效。前端在选择标准 provider 时会隐藏这些字段，后端也会清空标准 provider 上的 custom-only 配置。

## 路由和规则组

下游请求通常只需要：

- 统一 base URL
- 一个 factory access key
- body 里的逻辑模型名

规则组由平台发放的 factory access key 控制。也就是说，调用方 key 绑定了它能访问的规则组，路由时会得到一个最终生效组。

关键概念：

- `requested_group`：下游声明的组，可能来自 body/header，也可能为空
- `allowed_groups`：factory access key 允许访问的组
- `effective_group`：平台最终采用的组

下游声明的组不会被当成可信授权来源。系统只信 `effective_group`，不会因为下游传了某个组就越权。

顺序策略目前按主备语义工作：

- 对 `429/500/502/503/504`，同一候选最多本地重试 3 次
- 对 `401/403`，不做同一候选重试，直接记录失败并尝试 fallback
- 多次失败的 key 会进入熔断，在 TTL 内跳过
- 成功请求或成功探测会关闭对应 key 的熔断状态

## 快速开始

### 依赖

- Python 由 `uv` 管理，版本见 [backend/.python-version](backend/.python-version)
- Node.js / npm 用于前端安装和构建

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

### 单入口启动

推荐使用单入口模式：先构建前端，再由后端托管 `frontend/dist`。

```bash
bash scripts/start_all.sh --rebuild-frontend
```

默认访问地址：

```text
http://127.0.0.1:8000
```

默认管理员密码：

```text
admin
```

自定义端口和管理员密码：

```bash
bash scripts/start_all.sh --port 9000 --admin-token "your-admin-token" --rebuild-frontend
```

如果前端已经构建过：

```bash
bash scripts/start_all.sh --skip-build
```

停止后台进程：

```bash
kill "$(cat scripts/pids/app-8000.pid)"
```

日志位置：

```text
scripts/logs/app-8000.log
```

### 后端开发

```bash
cd backend
uv sync
uv run uvicorn app.main:app --reload --port 8000
```

常用环境变量：

```bash
export LLM_MASTER_AUTH_TOKEN="your-admin-token"
export LLM_DATA_ENCRYPTION_KEY="long-random-secret-for-db-secrets"
export LLM_DATABASE_URL="sqlite+aiosqlite:///./llm_api_factory.db"
export LLM_SQLITE_BUSY_TIMEOUT_MS="5000"
export LLM_SQLITE_JOURNAL_MODE="WAL"
export LLM_REDIS_URL="redis://localhost:6379/0"
export LLM_AGENT_STREAM_IDLE_TIMEOUT_SECONDS="300"
```

默认数据库是 SQLite：

```text
backend/llm_api_factory.db
```

SQLite 连接默认启用外键约束、`busy_timeout=5000` 和 `journal_mode=WAL`，用于降低请求日志写入时的锁等待失败概率。高并发生产环境仍建议迁移到 PostgreSQL。

上游 provider API key 和 OAuth `client_secret` 会以 `enc:v1:` 前缀加密后写入数据库。加密密钥优先使用 `LLM_DATA_ENCRYPTION_KEY`，未配置时回退到 `LLM_MASTER_AUTH_TOKEN`。生产环境建议显式配置 `LLM_DATA_ENCRYPTION_KEY`，并保持重启前后一致。

Factory/rule access key 只会在创建或轮换响应中返回完整值；数据库保存 `sha256:` 哈希和预览值，不保存完整明文。

Redis 用于健康探测结果、熔断状态和时间序列数据。开发环境没有 Redis 时会退回内存实现，服务可用，但重启后这些运行态数据会丢失。

### 前端开发

```bash
cd frontend
npm ci
npm run dev -- --port 5173
```

可选环境变量：

```bash
export VITE_API_BASE="http://localhost:8000"
```

前端不支持也不应配置构建时 admin token。管理台通过 `/auth/login` 获取服务端签发的 admin session token。

开发模式前端地址：

```text
http://127.0.0.1:5173
```

## systemd 运行

本机长期运行可以使用 user systemd service，例如：

```ini
[Unit]
Description=LLM API Factory
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/path/to/llm_api_factory/backend
Environment=LLM_MASTER_AUTH_TOKEN=admin
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

如果 shell 里可以访问某些上游，但 systemd 服务不行，通常是代理环境没有传给服务。可以加 drop-in：

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

然后重启：

```bash
systemctl --user daemon-reload
systemctl --user restart llm-api-factory.service
```

## 控制面鉴权

控制面有两类 key：

- Admin token：登录后台和调用 `/admin/*`
- Factory access key：下游工具调用 `/openai/*`、`/anthropic/*`、`/gemini/*`

下游请求示例：

```bash
curl http://127.0.0.1:8000/openai/v1/responses \
  -H "Authorization: Bearer fk-your-factory-key" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-5.5",
    "input": "Reply with exactly OK."
  }'
```

通常不需要下游额外传规则组 header。规则组由 factory access key 绑定。

## CLI 控制面

CLI 默认读取：

```bash
export LLM_FACTORY_URL="http://127.0.0.1:8000"
export LLM_FACTORY_TOKEN="admin"
```

也可以显式传入：

```bash
cd backend
uv run llm-factory --base-url http://127.0.0.1:8000 --token admin --help
```

### Upstream

```bash
uv run llm-factory upstream list

uv run llm-factory upstream add openai-vps https://api.openai.com/v1 \
  --provider openai \
  --access-mode via_agent \
  --agent-node edge-vps \
  --key sk-xxx \
  --rule-group vps-only \
  --model gpt-vps=gpt-4.1

uv run llm-factory upstream update 1 --name openai-vps-2
uv run llm-factory upstream disable 1
uv run llm-factory upstream test 1
```

### Route

```bash
uv run llm-factory route test gpt-vps --rule-group vps-only
uv run llm-factory --output json route explain gpt-vps --rule-group vps-only
```

### Worker

```bash
uv run llm-factory worker list
uv run llm-factory worker label 1 --labels openai,restricted --network-group vps-us
uv run llm-factory worker drain 1
uv run llm-factory worker enable 1
uv run llm-factory worker disable 1
```

Worker 状态语义：

- `enable`：可接新请求
- `drain`：节点在线，但不分配新请求
- `disable`：管理上禁用，不参与路由

### Rule Group

```bash
uv run llm-factory rule-group list
uv run llm-factory rule-group create vps-only '^gpt-vps$' --key-ids 3 --strategy sequential
uv run llm-factory rule-group update 2 --model-pattern '^gpt-.*$'
uv run llm-factory rule-group bind 2 --key-ids 3,4 --strategy weighted_round_robin
```

## Agent 节点

Agent 节点用于表达请求执行位置。典型场景：

- 上游只能从特定 VPS 网络访问
- 主节点不能直接访问远程私网 endpoint
- 需要让某些请求从指定 region、network group 或标签节点发出

Agent 不是 provider，也不是模型 runtime。它只是远程转发节点。

### 管理台部署

如果 Agent 要部署在远程 VPS，先让远程机器能访问主服务控制面，并在启动主程序时配置公网地址和安装脚本地址：

```bash
bash scripts/start_all.sh \
  --port 8000 \
  --agent-public-base-url https://factory.example.com \
  --agent-install-script-url https://raw.githubusercontent.com/Kuaizr/llm_api_factory/main/scripts/agent_install.sh \
  --agent-install-repo-url https://github.com/Kuaizr/llm_api_factory.git \
  --agent-install-repo-ref main
```

然后：

1. 登录管理控制台
2. 进入 Agent 页面
3. 创建新代理
4. 生成部署命令
5. 在远程 VPS 执行该命令

### 安装脚本

可以直接从 GitHub raw 拉取安装脚本：

```bash
curl -fsSL https://raw.githubusercontent.com/Kuaizr/llm_api_factory/main/scripts/agent_install.sh | bash -s -- \
  --ws-url wss://factory.example.com/agent/ws \
  --heartbeat-url https://factory.example.com/agent/heartbeat \
  --agent-name "edge-hk" \
  --agent-token "your-token" \
  --agent-region "HK" \
  --repo https://github.com/Kuaizr/llm_api_factory.git \
  --repo-ref main
```

常用参数：

| 参数 | 说明 |
|------|------|
| `--ws-url` | WebSocket 连接地址，必需 |
| `--heartbeat-url` | 心跳上报地址，必需 |
| `--agent-name` | 节点名称，必需 |
| `--agent-token` | 认证 token，必需 |
| `--agent-region` | 区域标识，如 HK、SG、US |
| `--agent-network-group` | 网络分组 |
| `--agent-labels` | 逗号分隔标签 |
| `--agent-endpoint-url` | 出口公网地址，用于延迟探测 |
| `--allowed-targets` | 允许 Agent 代理访问的 host、host:port、CIDR 或 `*` |
| `--repo` | Agent 代码仓库地址 |
| `--repo-ref` | Agent 代码分支、tag 或 commit |
| `--no-systemd` | 不注册 systemd，用 nohup 后台运行 |

Agent 会对 hostname 目标做 DNS 解析，解析结果必须全部是公网地址；需要访问内网 mock/API 服务时，请显式配置对应 IP、IP:port 或 CIDR。

### 手动运行

```bash
git clone https://github.com/Kuaizr/llm_api_factory.git
cd llm_api_factory/backend
uv sync

export LLM_AGENT_WS_URL="ws://localhost:8000/agent/ws"
export LLM_AGENT_HEARTBEAT_URL="http://localhost:8000/agent/heartbeat"
export LLM_AGENT_NAME="edge-hk"
export LLM_AGENT_AUTH_TOKEN="your-token-from-console"
export LLM_AGENT_REGION="HK"
export LLM_AGENT_ALLOWED_TARGETS="api.openai.com,api.anthropic.com,generativelanguage.googleapis.com"

uv run python -m app.services.agent_client
```

## API 入口

OpenAI 风格：

```text
GET  /openai/v1/models
POST /openai/v1/chat/completions
POST /openai/v1/completions
POST /openai/v1/embeddings
POST /openai/v1/responses
```

Anthropic 风格：

```text
POST /anthropic/v1/messages
```

Gemini 风格：

```text
POST /gemini/v1beta/models/{model}:generateContent
POST /gemini/v1beta/models/{model}:streamGenerateContent
```

旧的 `/v1/*` 兼容入口已移除，请使用 `/openai/v1/*`、`/anthropic/v1/*` 或 `/gemini/v1beta/*`。

## 测试

后端：

```bash
cd backend
uv run pytest -q
```

前端：

```bash
cd frontend
npm test -- --run
```

## 开发原则

- 标准 provider 保持最小侵入，不做无必要 body/response 重组
- 强定制能力放在 `custom` provider，不混入标准链路
- 路由语义优先清晰：逻辑模型名、规则组、候选 key、执行位置要可解释
- Agent 的核心价值是执行位置控制，不是环境托管或 provider 实现
- 新功能优先服务个人/小团队自动化，不扩张成公共运营平台
