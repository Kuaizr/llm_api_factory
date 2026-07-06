# Codex OAuth Provider

`codex` provider is separate from the standard `openai` provider. It targets the
Codex backend API and is intended for Codex CLI compatible `/v1/responses`
traffic.

## Credential

Store a JSON object as the endpoint API key value:

```json
{
  "access_token": "mock-access-token",
  "refresh_token": "mock-refresh-token",
  "account_id": "chatgpt-account-id",
  "expires_at": 1790000000
}
```

The gateway never reads `~/.codex/auth.json`. Admins must explicitly provide the
credential JSON they want to manage. Tests use inline mock credentials only.

## Refresh

When `expires_at` is near expiry, LMF refreshes with:

- token URL: `LLM_CODEX_OAUTH_TOKEN_URL`
- client id: `LLM_CODEX_OAUTH_CLIENT_ID`

The refreshed credential is written back to the stored API key when possible.

## Upstream Shape

Codex requests are sent to:

- `/backend-api/codex/responses`
- `/backend-api/codex/responses/compact`

Required headers are injected:

- `Authorization: Bearer <access_token>`
- `chatgpt-account-id: <account_id>`
- `OpenAI-Beta: responses=experimental`
- `originator: codex_cli_rs`

Codex provider request bodies are minimally shaped for the Codex backend:

- default `instructions` to `""`
- force `store` to `false`
- remove `max_output_tokens`
- remove `temperature`

## Rule Exposure

Routing rules now have `exposure_format`:

- `any`
- `chat`
- `response`
- `codex`
- `message`
- `claude_code`
- `gemini`

Legacy rules are treated as `any`. Codex CLI-like `/openai/v1/responses` traffic
selects `codex`; ordinary Responses API traffic selects `response`.
