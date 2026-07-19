# 安全模型

LLM API Factory 是个人和小团队控制面，不是多租户公共 SaaS。默认安全目标是保护管理员控制面、上游密钥和 Agent 执行边界。

## 凭据类型

| 凭据 | 用途 |
| --- | --- |
| Admin password/session token | 登录控制台和调用 `/admin/*` |
| Factory API Key | 下游调用 `/openai/*`、`/anthropic/*`、`/gemini/*` |
| Upstream API Key | Factory 调用真实 provider |
| Agent token | Agent 连接主服务 |
| Data encryption key | 加密数据库中的敏感字段 |

## Factory key

Factory key 只在创建或轮换时返回完整值。之后只显示预览。

数据库保存：

- `sha256:` 哈希
- 预览值
- 绑定规则组
- 启用状态和限制

不会保存完整明文。

## 上游密钥加密

上游 API key 和 OAuth client secret 会以 `enc:v1:` 前缀加密保存。

密钥来源优先级：

1. `LLM_DATA_ENCRYPTION_KEY`
2. `LLM_MASTER_AUTH_TOKEN`

生产环境必须显式配置 `LLM_DATA_ENCRYPTION_KEY`，并在备份和迁移时一起保管。

## 标准链路 header 策略

标准 provider 不做 body 字段删改，但 header 不是完全无条件透传。

原因：

- header 可能包含 Cookie、内网信息、浏览器来源等敏感内容。
- 上游第三方 provider 不应该收到下游所有隐私 header。
- 某些 CLI 场景需要特定 header，因此采用有边界的 allowlist。

Codex Responses 场景会保留 Codex 需要的请求 header，例如 `originator`、`session_id`、`x-codex-turn-metadata` 等。响应中的账号套餐、5 小时/1 周额度和 Cookie header 不会透传给下游。

Codex Auth JSON 在服务端只保留访问 token、刷新 token、账号 ID 和过期时间，其他导出字段会在加密入库前丢弃。公开仪表盘不会返回 endpoint 或 Agent 的真实上游地址。

## Agent 安全边界

Agent 是可信远程节点。主服务被攻破或 Agent token 泄露时，Agent 可能变成代理跳板。

生产建议：

- 不要使用 `LLM_AGENT_ALLOWED_TARGETS=*`
- 每个 Agent 只允许必要上游域名或内网 CIDR
- 不要把 Agent 部署在高敏内网
- Agent token 定期轮换
- 删除不再使用的 Agent
- 远端 systemd 服务确认已停止

Agent 端会校验：

- scheme 必须是 `http` 或 `https`
- 目标 host/port 必须命中 allowlist
- 私网、localhost、非 global IP 需要显式允许
- hostname 解析结果必须符合规则

## Debug header

普通下游只返回：

- `x-request-id`
- `x-trace-id`

内部 debug header 只应在管理员排查时使用，例如：

- `x-endpoint-id`
- `x-api-key-id`
- `x-real-model`
- `x-agent-node`

## 实验性账号态 Provider

Codex OAuth provider 作为隔离的实验 provider 提供，不会混入标准 OpenAI API key provider。管理员应确认其账号权限、服务条款和风控风险，并使用独立的 Factory key 控制访问范围。

其他订阅账号 OAuth 转 API provider 当前不内置，包括：

- Claude.ai OAuth 账号态转 API
- Gemini 账号态转 API

它们不是标准 API key provider，涉及更强协议模拟、token refresh、账号风控和服务条款风险。未来如果支持，也应继续作为隔离的实验 provider。
