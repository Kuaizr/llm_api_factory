# LLM API Factory

## 背景
LLM API Factory 是一个面向个人或小团队的 LLM API 聚合分发与监控服务：统一管理多家模型 API Key，按模型名进行路由与降级，并提供健康探针、熔断与可视化控制台。核心理念是“模型名是一等公民”，请求只需指定模型，系统会自动选择可用端点。

## 主要能力
- OpenAI 兼容透明代理（支持流式），并记录请求用量与耗时。
- 按模型 + 规则组路由，熔断不可用 Key。
- 健康探针与趋势可视化，告警策略配置（Telegram）。
- 管理控制台：资产管理、路由测试、日志导出与筛选。
- 可选 Agent 节点（用于跨境代理，支持请求代理加速）。

## 快速开始

### 1. 后端
进入后端目录：

```
cd backend
```

安装依赖：

```
pip install -e .
```

建议配置环境变量（可选）：

```
export LLM_DATABASE_URL="postgresql+asyncpg://postgres:postgres@localhost:5432/llm_api_factory"
export LLM_REDIS_URL="redis://localhost:6379/0"
export LLM_MASTER_AUTH_TOKEN="your-admin-token"
```

启动服务：

```
uvicorn app.main:app --reload --port 8000
```

### 2. 前端
进入前端目录：

```
cd frontend
```

安装依赖：

```
npm install
```

可选环境变量：

```
export VITE_API_BASE="http://localhost:8000"
export VITE_ADMIN_TOKEN="your-admin-token"
```

启动前端：

```
npm run dev -- --port 5173
```

## 测试

后端测试：

```
cd backend
pytest -q
```

前端测试：

```
cd frontend
npm test -- --run
```

## 可选：Agent 节点

Agent 节点用于跨境代理加速，通过 WebSocket 与后端保持连接，支持请求转发。

### 部署方式

#### 方式一：通过管理控制台（推荐）

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

# 安装依赖
pip install httpx websockets

# 运行 Agent
python scripts/llm-api-factory-agent.py \
  --ws-url ws://localhost:8000/agent/ws \
  --heartbeat-url http://localhost:8000/agent/heartbeat \
  --name "edge-hk" \
  --token "your-token-from-console" \
  --region "HK"
```

#### 方式三：使用安装脚本（未来支持）

未来可从 GitHub 直接安装：

```bash
curl -fsSL https://raw.githubusercontent.com/your-repo/llm-api-factory/main/scripts/agent_install.sh | bash -s -- \
  --ws-url ws://localhost:8000/agent/ws \
  --heartbeat-url http://localhost:8000/agent/heartbeat \
  --agent-name "edge-hk" \
  --agent-token "your-token" \
  --agent-region "HK"
```

### 常用命令行参数

| 参数 | 说明 |
|------|------|
| `--ws-url` | WebSocket 连接地址 (必需) |
| `--heartbeat-url` | 心跳上报地址 (必需) |
| `--name` | 节点名称 (必需) |
| `--token` | 认证 Token (必需) |
| `--region` | 区域标识 (如 HK/SG/US) |
| `--endpoint-url` | 出口公网地址 (用于延迟探测) |

### Agent 功能

- **心跳检测**：Agent 定期向后端发送心跳，维持在线状态
- **能力探测**：Agent 启动时自动探测支持的模型类型
- **请求代理**：后端将请求转发给 Agent，Agent 转发到目标 LLM 服务
- **Token 管理**：每个 Agent 拥有独立 Token，支持重新生成（仅限未部署节点）

## 接口示例

模型列表：

```
GET /v1/models
```

聊天代理：

```
POST /v1/chat/completions
```

文本补全：

```
POST /v1/completions
```

向量嵌入：

```
POST /v1/embeddings
```
