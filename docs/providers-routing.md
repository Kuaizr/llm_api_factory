# Provider 与路由语义

LLM API Factory 把 provider 分成两层：标准 provider 和 custom provider。

## 标准 provider

标准 provider 包括：

- `openai`
- `anthropic`
- `gemini`

标准链路目标是最小侵入：

- 不主动删除未知 body 字段
- 不主动注入 `rule_group`
- 不应用 request body template
- 不使用 custom-only header/query/cookie/path suffix
- OpenAI / Anthropic 只在必要时替换 body 里的 `model`
- Gemini 只在必要时替换 URL path 里的 model
- 注入上游鉴权和 trace header
- 响应体尽量原样返回

如果下游传了上游不接受的字段，上游报错会透传给下游。Factory 不替下游猜测“哪些字段应该删除”。

## Codex OAuth provider

`codex` 是与标准 `openai` API Key 渠道隔离的实验 provider，只参与 Responses/Codex 兼容入口。它接收 Auth JSON，按 Key 维护访问凭证和 5 小时/1 周用量窗口；详细行为见 [Codex OAuth Provider](codex-provider.md)。

当 Codex 端点绑定 Agent 时，模型探测、Key 测试、Token 刷新和真实推理均从该 Agent 发出；Agent 不可用时不会回退主服务直连。

## Custom provider

`custom` 是强定制适配器，适合私有协议、魔改兼容接口和特殊 runtime。

custom 支持：

- `url_path_suffix`
- `extra_headers`
- `extra_cookies`
- `extra_query_params`
- `request_body_template`

这些能力不会在标准 provider 生效。前端会隐藏，后端也会清理标准 provider 上的 custom-only 配置。

## Factory key 与规则组

下游请求只需要一个 Factory API Key：

```text
Authorization: Bearer fk-...
```

Factory key 绑定它允许访问的规则组。路由时有三个概念：

- `requested_group`：下游声明的组，可能来自 body/header，也可能为空。
- `allowed_groups`：Factory key 允许访问的组。
- `effective_group`：最终真正生效的组。

系统只信 `effective_group`。下游不能通过传 header 或 body 字段越权访问未绑定规则组。

## 顺序主备策略

`sequential` 的语义是主备，不是每次都从第一个候选死试到底。

行为：

- 成功使用某个候选后，后续优先继续使用它。
- 对 `429/500/502/503/504`，同一候选最多本地重试 3 次。
- 对 `401/403`，不做同候选重试，直接尝试 fallback。
- 失败达到阈值后进入熔断，TTL 内跳过。
- 成功请求或成功探测会关闭该 key 的熔断。

这样可以减少坏 key 拖慢请求，也更容易命中 provider 侧缓存。

## Weighted round robin

`weighted_round_robin` 用于按权重分摊请求。当前适合单进程部署；多 worker 场景需要把运行态统一迁到 Redis 后再扩展。

## Route explain

控制台和 CLI 都可以查看路由解释，用来确认：

- 请求模型匹配了哪个规则组
- Factory key 允许访问哪些组
- 哪些候选被过滤
- 最终候选为什么可用或不可用
- 是否需要 Agent，Agent 是否在线
