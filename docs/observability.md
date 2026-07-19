# 观测、日志与缓存命中

LLM API Factory 默认记录请求元数据。完整 prompt/response dump 是可选能力。

## Request log

每次请求会记录：

- request id / trace id
- 模型别名和真实模型
- endpoint / api key
- 状态码
- latency
- prompt tokens
- completion tokens
- total tokens
- cached tokens
- cache hit
- agent node
- rule group

这些元数据不依赖 dump 开启。dump 只控制是否把完整请求/响应内容写到文件。

## Dump index

`dump_index` 是请求内容 dump 的索引表，也会记录 token、cache、stream 状态等字段。

dump 文件路径按机器和日期分层：

```text
{dump_root}/{hostname}/{YYYY-MM-DD}/{real_model}/{request_id}.json
```

流式请求会记录：

- `stream_complete=true`：流正常结束
- `stream_complete=false`：客户端或上游中途断开，或者流内出现明确的失败事件

## Cache hit

cache hit 来自 provider 返回的 usage 字段。

常见字段：

- OpenAI: `usage.prompt_tokens_details.cached_tokens`
- Anthropic: `usage.cache_read_input_tokens`
- Gemini: `usageMetadata.cachedContentTokenCount`

只要 cached tokens 大于 0，就记为 HIT。

如果上游没有返回 usage 或 cached token 字段，Factory 不会猜测命中情况，会显示 MISS 或未知。

## 流式 token

OpenAI Chat Completions 流式请求会自动注入：

```json
{"stream_options":{"include_usage":true}}
```

这样 OpenAI 在流末尾返回 usage，便于统计 token。

Responses API 流式、Anthropic 流式、Gemini 流式会从各自事件结构中旁路解析 usage，不改变响应内容。Codex provider 始终使用 SSE；只有流正常结束后才给候选 Key 记成功，`response.failed` 或 `error` 事件会记为失败。

## 最近请求日志

控制台最近请求日志用于排查当前流量。建议关注：

- status 是否成功
- total/input/output/cached tokens
- latency
- cache HIT/MISS
- trace id
- endpoint 和 api key

trace id 可以继续在请求尝试日志里定位每一次 fallback 尝试。

## Route explain

当请求没有走到预期 key 时，先看 route explain：

- Factory key 是否绑定了目标规则组
- 模型名是否匹配规则
- 候选 key 是否启用
- endpoint 是否启用
- Agent 是否在线
- key 是否处于熔断
- provider 是否匹配入口协议
