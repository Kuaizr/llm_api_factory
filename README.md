# LLM API Factory

## 背景
LLM API Factory 是一个面向个人或小团队的 LLM API 聚合分发与监控服务：统一管理多家模型 API Key，按模型名进行路由与降级，并提供健康探针、熔断与可视化控制台。核心理念是“模型名是一等公民”，请求只需指定模型，系统会自动选择可用端点。

## 主要能力
- OpenAI / Anthropic 标准入口透传（支持流式），并记录请求用量与耗时。
- 按模型 + 规则组路由，熔断不可用 Key，并支持多 Key 负载策略。
- 通用 Provider 扩展：自定义 URL 后缀、额外 Header/Cookie/Query、请求体模板变量替换。
- OAuth Client Credentials：自动取 Token、Redis 缓存、401 自动刷新重试。
- 健康探针与趋势可视化，告警策略配置（Telegram）。
- 管理控制台：资产管理、路由测试、日志导出与筛选。
- 可选 Agent 节点（用于跨境代理，支持请求代理加速）。

## 快速开始

### 前置依赖

- Python 由 uv 管理，项目默认使用 `backend/.python-version` 中的 Python 版本。
- Node.js / npm 用于安装和构建前端。

安装 uv：

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

如果 uv 安装在 `~/.local/bin/uv` 但当前 shell 找不到它，可以先刷新 PATH：

```bash
source "$HOME/.local/bin/env"
```

安装前端依赖：

```bash
cd frontend
npm ci
cd ..
```

### 推荐：单入口启动

单入口模式会先构建前端，然后由后端托管 `frontend/dist`，只需要访问一个端口：

```bash
bash scripts/start_all.sh --rebuild-frontend
```

默认访问地址：`http://127.0.0.1:8000`

默认管理员密码：`admin`

自定义端口和管理员密码：

```bash
bash scripts/start_all.sh --port 9000 --admin-token "your-admin-token" --rebuild-frontend
```

如果前端已经构建过，可以跳过构建：

```bash
bash scripts/start_all.sh --skip-build
```

停止服务：

```bash
kill "$(cat scripts/pids/app-8000.pid)"
```

日志位置：

```bash
scripts/logs/app-8000.log
```

### 后端开发

```bash
cd backend
uv sync
```

建议配置环境变量（可选）：

```bash
export LLM_DATABASE_URL="postgresql+asyncpg://postgres:postgres@localhost:5432/llm_api_factory"
export LLM_REDIS_URL="redis://localhost:6379/0"
export LLM_MASTER_AUTH_TOKEN="your-admin-token"
```

启动服务：

```bash
uv run uvicorn app.main:app --reload --port 8000
```

默认未配置 `LLM_DATABASE_URL` 时使用本地 SQLite 数据库；Redis 相关能力需要本地 Redis 或外部 Redis。

### 前端开发

```bash
cd frontend
```

安装依赖：

```bash
npm ci
```

可选环境变量：

```bash
export VITE_API_BASE="http://localhost:8000"
export VITE_ADMIN_TOKEN="your-admin-token"
```

启动前端：

```bash
npm run dev -- --port 5173
```

开发模式下前端地址为 `http://127.0.0.1:5173`，API 请求转发到 `VITE_API_BASE`。

## 测试

后端测试：

```bash
cd backend
uv run pytest -q
```

前端测试：

```bash
cd frontend
npm test -- --run
```

## CLI 控制面

CLI 面向自动化脚本和 agent 运维，默认读取：

```bash
export LLM_FACTORY_URL="http://127.0.0.1:8000"
export LLM_FACTORY_TOKEN="admin"
```

也可以每次显式传入：

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

### Rule Group

```bash
uv run llm-factory rule-group list
uv run llm-factory rule-group create vps-only '^gpt-vps$' --key-ids 3 --strategy sequential
uv run llm-factory rule-group update 2 --model-pattern '^gpt-.*$'
uv run llm-factory rule-group bind 2 --key-ids 3,4 --strategy weighted_round_robin
```

## 可选：Agent 节点

Agent 节点用于跨境代理加速，通过 WebSocket 与后端保持连接，支持请求转发。

### 部署方式

#### 方式一：通过管理控制台（推荐）

如果 Agent 要部署在远程 VPS，先用公网域名、反向代理或 SSH tunnel 暴露本机控制面，并在启动主程序时配置公网地址和安装脚本地址：

```bash
bash scripts/start_all.sh \
  --port 8000 \
  --agent-public-base-url https://factory.example.com \
  --agent-install-script-url https://raw.githubusercontent.com/Kuaizr/llm_api_factory/main/scripts/agent_install.sh \
  --agent-install-repo-url https://github.com/Kuaizr/llm_api_factory.git \
  --agent-install-repo-ref main
```

1. 登录管理控制台（管理员权限）
2. 进入「Agent 节点」页签
3. 点击「部署新节点」按钮
4. 输入节点名称，点击生成部署命令
5. 复制生成的命令，在目标服务器上执行

#### 方式二：手动运行

```bash
# 克隆仓库
git clone https://github.com/your-repo/llm-api-factory.git
cd llm-api-factory

# 准备后端依赖
cd backend
uv sync

# 运行 Agent
export LLM_AGENT_WS_URL="ws://localhost:8000/agent/ws"
export LLM_AGENT_HEARTBEAT_URL="http://localhost:8000/agent/heartbeat"
export LLM_AGENT_NAME="edge-hk"
export LLM_AGENT_AUTH_TOKEN="your-token-from-console"
export LLM_AGENT_REGION="HK"
uv run python -m app.services.agent_client
```

#### 方式三：使用安装脚本

可从 GitHub raw 直接安装：

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

### 常用命令行参数

| 参数 | 说明 |
|------|------|
| `--ws-url` | WebSocket 连接地址 (必需) |
| `--heartbeat-url` | 心跳上报地址 (必需) |
| `--agent-name` | 节点名称 (必需) |
| `--agent-token` | 认证 Token (必需) |
| `--agent-region` | 区域标识 (如 HK/SG/US) |
| `--agent-network-group` | 网络分组 |
| `--agent-labels` | 逗号分隔标签 |
| `--agent-endpoint-url` | 出口公网地址 (用于延迟探测) |
| `--repo` | Agent 代码仓库地址 |
| `--repo-ref` | Agent 代码分支、tag 或 commit |
| `--no-systemd` | 不注册 systemd，用 nohup 后台运行 |

### Agent 功能

- **心跳检测**：Agent 定期向后端发送心跳，维持在线状态
- **能力探测**：Agent 启动时自动探测支持的模型类型
- **请求代理**：后端将请求转发给 Agent，Agent 转发到目标 LLM 服务
- **Token 管理**：每个 Agent 拥有独立 Token，支持重新生成（仅限未部署节点）

## 接口示例

OpenAI 标准入口（推荐）：

```
GET  /openai/v1/models
POST /openai/v1/chat/completions
POST /openai/v1/completions
POST /openai/v1/embeddings
POST /openai/v1/responses
```

Anthropic 标准入口：

```
POST /anthropic/v1/messages
```

> 说明：旧的 `/v1/*` 兼容入口已移除，请统一迁移到 `/openai/v1/*` 或 `/anthropic/v1/*`。
