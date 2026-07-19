# API 入口

下游统一使用 Factory API Key：

```text
Authorization: Bearer fk-...
```

Gemini 入口也兼容：

```text
x-goog-api-key: fk-...
?key=fk-...
```

## OpenAI

支持入口：

```text
GET  /openai/v1/models
POST /openai/v1/chat/completions
POST /openai/v1/completions
POST /openai/v1/embeddings
POST /openai/v1/responses
*    /openai/v1/{path}
```

`/openai/v1/models` 会优先透传上游原生 models 接口，并按 Factory key 可访问规则组过滤模型。

OpenAI Responses 示例：

```bash
curl http://127.0.0.1:8000/openai/v1/responses \
  -H "Authorization: Bearer fk-your-factory-key" \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-5.5","input":"hello"}'
```

OpenAI Chat Completions 示例：

```bash
curl http://127.0.0.1:8000/openai/v1/chat/completions \
  -H "Authorization: Bearer fk-your-factory-key" \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-5.5","messages":[{"role":"user","content":"hello"}]}'
```

Codex 场景会额外保留 Responses 需要的 Codex header，例如 `originator`、`session_id`、`x-codex-turn-metadata` 等。

每类标准入口只会选择协议兼容的 provider。比如 Anthropic Messages 不会在没有 Anthropic/custom 候选时退回 OpenAI endpoint，Codex 候选仅参与 Responses/Codex 路由。

同一个规则可以通过 `exposure_formats` 多选 Chat、Responses、Codex、Anthropic、Claude Code 和 Gemini API 入口；同一规则组的不同规则不能选择重叠的入口。`default` 固定支持全部入口，并动态使用全部启用 Key。只有匹配规则的候选池中存在多个兼容 Key 时，模型不支持、鉴权、额度、限流或常见上游错误才会触发下一个候选；规则只包含一个兼容 Key 时不会发生跨 Key 切换。

## Anthropic

支持入口：

```text
GET  /anthropic/v1/models
POST /anthropic/v1/messages
*    /anthropic/v1/{path}
```

示例：

```bash
curl http://127.0.0.1:8000/anthropic/v1/messages \
  -H "Authorization: Bearer fk-your-factory-key" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "claude",
    "max_tokens": 64,
    "messages": [{"role": "user", "content": "hello"}]
  }'
```

Claude Code 场景建议使用 Claude Code 自身发起请求。控制台的 API key 测试提供 `Claude Code` 模板，用于模拟关键 header 和 body 结构。

Anthropic 入口会根据 `x-claude-code-session-id`、`x-app: cli`、Claude Code beta 或 User-Agent 识别 Claude Code 请求，并选择同组的 `claude_code` 规则；普通 Messages 请求选择 `message` 规则。

## Gemini

支持入口：

```text
GET  /gemini/v1/models
GET  /gemini/v1beta/models
POST /gemini/v1/models/{model}:generateContent
POST /gemini/v1beta/models/{model}:generateContent
POST /gemini/v1/models/{model}:streamGenerateContent
POST /gemini/v1beta/models/{model}:streamGenerateContent
POST /gemini/v1/interactions
POST /gemini/v1beta/interactions
POST /gemini/interactions
*    /gemini/v1/{path}
*    /gemini/v1beta/{path}
```

示例：

```bash
curl "http://127.0.0.1:8000/gemini/v1beta/models/gemini-proxy:generateContent?key=fk-your-factory-key" \
  -H "Content-Type: application/json" \
  -d '{
    "contents": [{"role": "user", "parts": [{"text": "hello"}]}]
  }'
```

Gemini 路由使用 URL path 中的 `{model}`。转发上游时会把 path model 替换成真实模型名，并剥离 Factory key，注入真实上游 key。

## Models 过滤

三类 provider 的模型列表都按 Factory key 可访问规则组取并集过滤。

目标：

- 下游只能看到自己能访问的模型。
- 字段结构尽量贴近原生 provider。
- 不把平台内部 rule group、endpoint、key 信息暴露给下游。
