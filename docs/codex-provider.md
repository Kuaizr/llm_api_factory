# Codex OAuth Provider

`codex` provider is separate from the standard `openai` provider. It targets the
Codex backend API and is intended for Codex CLI compatible `/v1/responses`
traffic.

## Credential

Import a JSON object as an endpoint API key value. Both a flat object and the
Codex export shape with these fields under `tokens` are accepted:

```json
{
  "access_token": "mock-access-token",
  "refresh_token": "mock-refresh-token",
  "account_id": "chatgpt-account-id",
  "expires_at": 1790000000
}
```

Only `access_token`, `refresh_token`, `account_id`, and `expires_at` are retained.
Fields such as email, ID token, profile data, and other export metadata are
discarded before the credential is encrypted and stored.

If `account_id` or `expires_at` is omitted, LMF derives it from the access-token
JWT when the corresponding claim is present.

## Refresh

When `expires_at` is near expiry, LMF refreshes with:

- token URL: `LLM_CODEX_OAUTH_TOKEN_URL`
- client id: `LLM_CODEX_OAUTH_CLIENT_ID`

Refresh happens shortly before expiry and is also attempted once after an
upstream HTTP 401. The refreshed credential is committed through an independent
database session before the request continues. A per-key local/Redis lock keeps
concurrent workers from refreshing the same credential repeatedly.

A credential without `refresh_token` remains unchanged while its access token is
valid. Once it expires or the upstream rejects it, an administrator must import a
new credential.

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
- force `stream` to `true`
- remove `max_output_tokens`
- remove `temperature`

The downstream response is therefore SSE even when the incoming payload contains
`"stream": false`. A terminal `response.failed`/`error` event is recorded as a
failed route attempt for circuit-breaker health rather than as a success.

Quota headers used internally for the 5-hour and 1-week usage windows are not
forwarded to downstream clients. Usage is stored per API key and displayed on
that key's management card.

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
