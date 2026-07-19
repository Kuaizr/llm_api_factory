from typing import AsyncGenerator
import asyncio
import json
import re
import time
from urllib.parse import parse_qsl, urlencode

from fastapi import Request

from app.api.v1.route_helpers import _dump_proxy_record
from app.core.config import get_settings
from app.db.models import RoutingRule
from app.services.background_tasks import safe_create_task
from app.services.billing import RequestMetrics, extract_usage, write_request_log
from app.services.codex_oauth import CodexCredential, apply_codex_auth_headers
from app.services.endpoint_transport import endpoint_agent_name, send_endpoint_request
from app.services.router import RouteCandidate
from app.services.secrets import decrypt_oauth_config, decrypt_secret_value

OAUTH_CACHE_PREFIX = "oauth:endpoint"
DEFAULT_OAUTH_EXPIRES_IN_SECONDS = 3600
DEFAULT_OAUTH_REFRESH_LEEWAY_SECONDS = 120
DEFAULT_OAUTH_LOCK_TTL_SECONDS = 10
OAUTH_LOCK_WAIT_STEP_SECONDS = 0.1
OAUTH_LOCK_WAIT_ROUNDS = 20
STANDARD_PROVIDER_NAMES = {"openai", "anthropic", "gemini", "codex"}

# 请求体模板变量替换的正则模式
# 先匹配被双引号包裹的占位符，避免字符串转义问题
QUOTED_TEMPLATE_VARIABLE_PATTERN = re.compile(r'"\{\{(\w+)}}"')
TEMPLATE_VARIABLE_PATTERN = re.compile(r"\{\{(\w+)}}")
PASSTHROUGH_HEADER_ALLOWLIST = {
    "accept",
    "anthropic-beta",
    "anthropic-dangerous-direct-browser-access",
    "anthropic-version",
    "content-type",
    "idempotency-key",
    "openai-beta",
    "openai-organization",
    "openai-project",
    "user-agent",
    "x-goog-api-client",
    "x-goog-fieldmask",
    "x-goog-request-params",
    "x-goog-user-project",
    "x-app",
    "x-claude-code-session-id",
}
PASSTHROUGH_HEADER_PREFIXES = ("x-stainless-",)
CODEX_PASSTHROUGH_HEADER_ALLOWLIST = {
    "originator",
    "session-id",
    "session_id",
    "thread-id",
    "x-client-request-id",
    "x-codex-beta-features",
    "x-codex-installation-id",
    "x-codex-parent-thread-id",
    "x-codex-turn-state",
    "x-codex-turn-metadata",
    "x-codex-window-id",
    "x-openai-subagent",
    "x-responsesapi-include-timing-metrics",
}


def _is_custom_provider(endpoint: object | None) -> bool:
    if endpoint is None:
        return False
    provider = str(getattr(endpoint, "provider", "") or "").strip().lower()
    return provider not in STANDARD_PROVIDER_NAMES


def _json_encode_template_value(value: object) -> str:
    return json.dumps(value, ensure_ascii=False)


def _render_request_body_template(
    template: str,
    variables: dict[str, object],
) -> str:
    """渲染请求体模板，替换 {{variable}} 占位符。"""

    def quoted_replacer(match: re.Match[str]) -> str:
        var_name = match.group(1)
        if var_name not in variables:
            return match.group(0)
        return _json_encode_template_value(variables[var_name])

    rendered = QUOTED_TEMPLATE_VARIABLE_PATTERN.sub(quoted_replacer, template)

    def replacer(match: re.Match[str]) -> str:
        var_name = match.group(1)
        if var_name not in variables:
            return match.group(0)
        return _json_encode_template_value(variables[var_name])

    return TEMPLATE_VARIABLE_PATTERN.sub(replacer, rendered)


def _extract_template_variables(payload: dict[str, object]) -> dict[str, object]:
    """从请求体中提取模板变量值。

    支持：
    - {{model}} -> payload.get("model")
    - {{prompt}} -> 从 messages 或 prompt 字段提取
    - 其他字段直接从 payload 获取
    """
    variables: dict[str, object] = dict(payload)

    # 提取 prompt 变量（从 messages 或 prompt 字段）
    if "messages" in payload and isinstance(payload["messages"], list):
        messages = payload["messages"]
        if messages and isinstance(messages[-1], dict):
            last_message = messages[-1]
            content = last_message.get("content")
            if isinstance(content, str):
                variables["prompt"] = content
            elif isinstance(content, list):
                # 多模态消息，提取第一个文本内容
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "text":
                        text = part.get("text")
                        if isinstance(text, str):
                            variables["prompt"] = text
                            break
    elif "prompt" in payload:
        variables["prompt"] = payload["prompt"]

    return variables


def _apply_request_body_template(
    endpoint: object,
    payload: dict[str, object],
    real_model: str,
) -> dict[str, object] | None:
    """如果 endpoint 配置了请求体模板，则使用模板渲染新请求体。

    Args:
        endpoint: Endpoint 对象
        payload: 原始请求体
        real_model: 实际模型名（已重写）

    Returns:
        - None: 不使用模板，保持原始 payload
        - dict: 使用模板渲染后的新 payload
    """
    if not _is_custom_provider(endpoint):
        return None

    template = getattr(endpoint, "request_body_template", None)
    if not template or not isinstance(template, str) or not template.strip():
        return None

    variables = _extract_template_variables(payload)
    variables["model"] = real_model

    try:
        rendered = _render_request_body_template(template, variables)
        parsed = json.loads(rendered)
        if isinstance(parsed, dict):
            return parsed
        return None
    except (json.JSONDecodeError, TypeError):
        # 模板渲染结果不是合法 JSON，忽略模板
        return None


def _is_openai_responses_path(request_path: str | None) -> bool:
    normalized = str(request_path or "").rstrip("/")
    return (
        normalized.endswith("/v1/responses")
        or normalized.endswith("/v1/responses/compact")
        or normalized.endswith("/backend-api/codex/responses")
        or normalized.endswith("/backend-api/codex/responses/compact")
    )


def _looks_like_codex_request(
    incoming_headers: dict,
    payload: dict[str, object] | None,
) -> bool:
    lowered_headers = {str(key).lower(): value for key, value in incoming_headers.items()}
    if any(key in lowered_headers for key in CODEX_PASSTHROUGH_HEADER_ALLOWLIST):
        return True
    user_agent = str(lowered_headers.get("user-agent") or "").lower()
    if "codex" in user_agent:
        return True
    if payload and (
        "prompt_cache_key" in payload or "client_metadata" in payload
    ):
        return True
    return False


def _build_upstream_headers(
    incoming_headers: dict,
    endpoint: object,
    api_key: str,
    *,
    request_path: str | None = None,
    payload: dict[str, object] | None = None,
    codex_credential: CodexCredential | None = None,
    is_stream: bool = False,
) -> dict:
    headers = {}
    provider = str(getattr(endpoint, "provider", "") or "").strip().lower()
    allow_codex_headers = (
        provider in {"openai", "codex"}
        and _is_openai_responses_path(request_path)
        and _looks_like_codex_request(incoming_headers, payload)
    )
    skip_headers = {
        "host",
        "content-length",
        "authorization",
        "x-api-key",
        "x-goog-api-key",
    }
    for key, value in incoming_headers.items():
        lowered_key = key.lower()
        if lowered_key in skip_headers:
            continue
        is_allowed = (
            lowered_key in PASSTHROUGH_HEADER_ALLOWLIST
            or any(
                lowered_key.startswith(prefix) for prefix in PASSTHROUGH_HEADER_PREFIXES
            )
            or (
                allow_codex_headers
                and lowered_key in CODEX_PASSTHROUGH_HEADER_ALLOWLIST
            )
        )
        if not is_allowed:
            continue
        headers[key] = value

    if provider == "codex":
        if codex_credential is None:
            raise RuntimeError("Codex credential is required for codex provider")
        def set_header(name: str, value: str, *, preserve_existing: bool = False) -> None:
            matching = [key for key in headers if key.lower() == name.lower()]
            resolved_value = value
            if preserve_existing and matching:
                resolved_value = str(headers[matching[0]])
            for key in matching:
                del headers[key]
            headers[name] = resolved_value

        headers = apply_codex_auth_headers(headers, codex_credential)
        set_header("Content-Type", "application/json")
        set_header("Accept", "text/event-stream" if is_stream else "application/json")
        set_header("OpenAI-Beta", "responses=experimental", preserve_existing=True)
        set_header("originator", "codex_cli_rs", preserve_existing=True)
        return headers

    resolved_api_key = decrypt_secret_value(api_key, settings=get_settings())

    header_name = getattr(endpoint, "auth_header_name", "Authorization") or "Authorization"
    header_prefix = getattr(endpoint, "auth_header_prefix", "Bearer") or ""
    if header_prefix:
        headers[header_name] = f"{header_prefix} {resolved_api_key}"
    else:
        headers[header_name] = resolved_api_key

    if _is_custom_provider(endpoint):
        # 处理扩展字段：extra_headers
        extra_headers_json = getattr(endpoint, "extra_headers", None)
        if extra_headers_json:
            try:
                extra_headers = json.loads(extra_headers_json)
                if isinstance(extra_headers, dict):
                    for key, value in extra_headers.items():
                        headers[key] = str(value)
            except (json.JSONDecodeError, TypeError):
                pass

        # 处理扩展字段：extra_cookies
        extra_cookies = getattr(endpoint, "extra_cookies", None)
        if extra_cookies:
            existing_cookie = headers.get("Cookie", "")
            if existing_cookie:
                headers["Cookie"] = f"{existing_cookie}; {extra_cookies}"
            else:
                headers["Cookie"] = extra_cookies

    return headers


def _get_agent_name(endpoint: object) -> str | None:
    return endpoint_agent_name(endpoint)


def _strip_duplicate_version_segment(base: str, path: str) -> str:
    for version in ("v1", "v1beta", "v1alpha"):
        prefix = f"/{version}"
        if base.endswith(prefix) and (path == prefix or path.startswith(f"{prefix}/")):
            stripped = path[len(prefix) :]
            return stripped or "/"
    return path


def _upstream_query_string(request: Request, endpoint: object | None) -> str:
    query_parts = list(parse_qsl(request.url.query, keep_blank_values=True))
    if not query_parts:
        return ""
    provider = str(getattr(endpoint, "provider", "") or "").strip().lower()
    if provider == "gemini":
        query_parts = [(key, value) for key, value in query_parts if key != "key"]
    return urlencode(query_parts)


def _build_target_url(
    base_url: str,
    request: Request,
    path_prefix: str | None = None,
    endpoint: object | None = None,
    path_override: str | None = None,
) -> str:
    base = base_url.rstrip("/")
    path = path_override if path_override is not None else request.url.path
    provider = str(getattr(endpoint, "provider", "") or "").strip().lower()
    if provider == "codex":
        if str(path).rstrip("/").endswith("/v1/responses/compact"):
            path = "/backend-api/codex/responses/compact"
        else:
            path = "/backend-api/codex/responses"
        url = f"{base}{path}"
        upstream_query = _upstream_query_string(request, endpoint)
        return f"{url}?{upstream_query}" if upstream_query else url
    if path_prefix and path.startswith(path_prefix):
        path = path[len(path_prefix) :]
        if not path.startswith("/"):
            path = f"/{path}"

    # 如果 endpoint 配置了自定义 url_path_suffix，则使用它替代默认路径
    url_path_suffix = None
    if _is_custom_provider(endpoint):
        url_path_suffix = getattr(endpoint, "url_path_suffix", None)

    if url_path_suffix:
        # 使用自定义后缀路径
        path = url_path_suffix if url_path_suffix.startswith("/") else f"/{url_path_suffix}"
    else:
        # 默认路径处理逻辑：避免 base_url 已带 /v1 或 /v1beta 时重复拼接版本段。
        path = _strip_duplicate_version_segment(base, path)

    url = f"{base}{path}"

    # 处理扩展字段：extra_query_params
    if _is_custom_provider(endpoint):
        extra_query_params_json = getattr(endpoint, "extra_query_params", None)
        if extra_query_params_json:
            try:
                extra_query_params = json.loads(extra_query_params_json)
                if isinstance(extra_query_params, dict) and extra_query_params:
                    query_parts = list(
                        parse_qsl(
                            _upstream_query_string(request, endpoint),
                            keep_blank_values=True,
                        )
                    )
                    for key, value in extra_query_params.items():
                        query_parts.append((str(key), str(value)))
                    new_query = urlencode(query_parts)
                    url = f"{url}?{new_query}" if new_query else url
            except (json.JSONDecodeError, TypeError):
                pass

    upstream_query = _upstream_query_string(request, endpoint)
    if upstream_query and "?" not in url:
        url = f"{url}?{upstream_query}"
    return url


def _coerce_positive_int(value: object, default: int) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value if value > 0 else default
    if isinstance(value, float):
        parsed = int(value)
        return parsed if parsed > 0 else default
    if isinstance(value, str):
        trimmed = value.strip()
        if trimmed.isdigit():
            parsed = int(trimmed)
            return parsed if parsed > 0 else default
    return default


def _parse_json_object(raw: object) -> dict[str, object] | None:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return None
        if isinstance(parsed, dict):
            return parsed
    return None


def _extract_oauth_config(endpoint: object) -> dict[str, object] | None:
    config = _parse_json_object(getattr(endpoint, "oauth_config", None))
    config = decrypt_oauth_config(config, settings=get_settings())
    if not config:
        return None

    token_url = str(config.get("token_url") or "").strip()
    client_id = str(config.get("client_id") or "").strip()
    client_secret = str(config.get("client_secret") or "").strip()
    if not token_url or not client_id or not client_secret:
        return None

    normalized = dict(config)
    normalized["token_url"] = token_url
    normalized["client_id"] = client_id
    normalized["client_secret"] = client_secret
    return normalized


def _oauth_cache_key(endpoint: object) -> str:
    endpoint_id = getattr(endpoint, "id", "unknown")
    return f"{OAUTH_CACHE_PREFIX}:{endpoint_id}:token"


def _oauth_lock_key(endpoint: object) -> str:
    endpoint_id = getattr(endpoint, "id", "unknown")
    return f"{OAUTH_CACHE_PREFIX}:{endpoint_id}:lock"


def _decode_cached_oauth_token(raw: str) -> tuple[str, int] | None:
    parsed = _parse_json_object(raw)
    if not parsed:
        return None

    access_token = parsed.get("access_token")
    expires_at = parsed.get("expires_at")
    if not isinstance(access_token, str) or not access_token.strip():
        return None
    if not isinstance(expires_at, int):
        return None
    return access_token.strip(), expires_at


def _is_oauth_token_expiring(expires_at: int, refresh_leeway_seconds: int) -> bool:
    return expires_at - int(time.time()) <= refresh_leeway_seconds


async def _read_cached_oauth_token(
    redis: object,
    cache_key: str,
    refresh_leeway_seconds: int,
) -> str | None:
    cached_value = await redis.get(cache_key)
    if not cached_value:
        return None
    if not isinstance(cached_value, str):
        return None

    decoded = _decode_cached_oauth_token(cached_value)
    if not decoded:
        return None
    access_token, expires_at = decoded
    if _is_oauth_token_expiring(expires_at, refresh_leeway_seconds):
        return None
    return access_token


def _build_oauth_request_form(config: dict[str, object]) -> dict[str, str]:
    form = {
        "grant_type": str(config.get("grant_type") or "client_credentials"),
        "client_id": str(config["client_id"]),
        "client_secret": str(config["client_secret"]),
    }

    scope = config.get("scope")
    if scope is not None and str(scope).strip():
        form["scope"] = str(scope).strip()

    audience = config.get("audience")
    if audience is not None and str(audience).strip():
        form["audience"] = str(audience).strip()

    resource = config.get("resource")
    if resource is not None and str(resource).strip():
        form["resource"] = str(resource).strip()

    return form


async def _fetch_oauth_token(
    http_client: object,
    config: dict[str, object],
    endpoint: object,
) -> tuple[str, int]:
    token_url = str(config["token_url"])
    form = _build_oauth_request_form(config)
    response = await send_endpoint_request(
        endpoint=endpoint,
        method="POST",
        url=token_url,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        body=urlencode(form).encode("utf-8"),
        client=http_client,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"OAuth token request failed with status {response.status_code}")
    try:
        payload = json.loads(response.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("OAuth token response must be valid JSON") from exc

    if not isinstance(payload, dict):
        raise RuntimeError("OAuth token response must be a JSON object")

    access_token = payload.get("access_token")
    if not isinstance(access_token, str) or not access_token.strip():
        raise RuntimeError("OAuth token response missing access_token")

    expires_at_raw = payload.get("expires_at")
    if isinstance(expires_at_raw, (int, float)):
        expires_at = int(expires_at_raw)
    else:
        expires_in_default = _coerce_positive_int(
            config.get("default_expires_in_seconds"),
            DEFAULT_OAUTH_EXPIRES_IN_SECONDS,
        )
        expires_in = _coerce_positive_int(payload.get("expires_in"), expires_in_default)
        expires_at = int(time.time()) + expires_in

    return access_token.strip(), expires_at


async def _write_cached_oauth_token(
    redis: object,
    cache_key: str,
    access_token: str,
    expires_at: int,
) -> None:
    ttl_seconds = max(1, expires_at - int(time.time()))
    payload = json.dumps({"access_token": access_token, "expires_at": expires_at})
    await redis.set(cache_key, payload, ex=ttl_seconds)


async def _wait_for_oauth_token(
    redis: object,
    cache_key: str,
    refresh_leeway_seconds: int,
) -> str | None:
    for _ in range(OAUTH_LOCK_WAIT_ROUNDS):
        cached_token = await _read_cached_oauth_token(
            redis, cache_key, refresh_leeway_seconds
        )
        if cached_token:
            return cached_token
        await asyncio.sleep(OAUTH_LOCK_WAIT_STEP_SECONDS)
    return None


async def _resolve_oauth_access_token(
    endpoint: object,
    redis: object,
    http_client: object,
    *,
    force_refresh: bool = False,
) -> str | None:
    config = _extract_oauth_config(endpoint)
    if not config:
        return None

    refresh_leeway_seconds = _coerce_positive_int(
        config.get("refresh_leeway_seconds"),
        DEFAULT_OAUTH_REFRESH_LEEWAY_SECONDS,
    )
    lock_ttl_seconds = _coerce_positive_int(
        config.get("lock_ttl_seconds"),
        DEFAULT_OAUTH_LOCK_TTL_SECONDS,
    )

    cache_key = _oauth_cache_key(endpoint)
    lock_key = _oauth_lock_key(endpoint)

    if not force_refresh:
        cached_token = await _read_cached_oauth_token(
            redis, cache_key, refresh_leeway_seconds
        )
        if cached_token:
            return cached_token

    has_lock = await redis.set(lock_key, "1", ex=lock_ttl_seconds, nx=True)
    if not has_lock:
        cached_token = await _wait_for_oauth_token(
            redis, cache_key, refresh_leeway_seconds
        )
        if cached_token:
            return cached_token
        has_lock = await redis.set(lock_key, "1", ex=lock_ttl_seconds, nx=True)
        if not has_lock:
            raise RuntimeError("OAuth token refresh lock timeout")

    try:
        if not force_refresh:
            cached_token = await _read_cached_oauth_token(
                redis, cache_key, refresh_leeway_seconds
            )
            if cached_token:
                return cached_token

        access_token, expires_at = await _fetch_oauth_token(
            http_client,
            config,
            endpoint,
        )
        await _write_cached_oauth_token(redis, cache_key, access_token, expires_at)
        return access_token
    finally:
        await redis.delete(lock_key)


async def _apply_oauth_access_token(
    headers: dict[str, str],
    endpoint: object,
    redis: object,
    http_client: object,
    *,
    force_refresh: bool = False,
) -> tuple[dict[str, str], bool]:
    access_token = await _resolve_oauth_access_token(
        endpoint,
        redis,
        http_client,
        force_refresh=force_refresh,
    )
    if not access_token:
        return headers, False

    header_name = getattr(endpoint, "auth_header_name", "Authorization") or "Authorization"
    header_prefix = getattr(endpoint, "auth_header_prefix", "Bearer") or ""
    if header_prefix:
        headers[header_name] = f"{header_prefix} {access_token}"
    else:
        headers[header_name] = access_token
    return headers, True


def _filter_response_headers(headers: dict) -> dict:
    excluded = {
        "content-length",
        "content-encoding",
        "transfer-encoding",
        "connection",
        "content-type",
        "set-cookie",
    }
    sensitive_prefixes = (
        "x-codex-primary-",
        "x-codex-secondary-",
        "x-codex-credits-",
    )
    sensitive_exact = {"x-codex-plan-type"}
    return {
        key: value
        for key, value in headers.items()
        if key.lower() not in excluded
        and key.lower() not in sensitive_exact
        and not key.lower().startswith(sensitive_prefixes)
    }


def _build_debug_headers(
    request_id: str,
    trace_id: str,
    candidate: RouteCandidate,
    model_alias: str,
    *,
    include_internal: bool = False,
) -> dict:
    headers = {
        "x-request-id": request_id,
        "x-trace-id": trace_id,
    }
    if not include_internal:
        return headers

    headers.update(
        {
            "x-endpoint-id": str(candidate.endpoint.id),
            "x-endpoint-name": candidate.endpoint.name,
            "x-api-key-id": str(candidate.api_key.id),
            "x-model-alias": model_alias,
            "x-real-model": candidate.real_model,
            "x-execution-mode": candidate.execution_mode,
        }
    )
    if candidate.agent_name:
        headers["x-agent-node"] = candidate.agent_name
    return headers


def _merge_headers(base: dict, extra: dict) -> dict:
    merged = dict(base)
    merged.update(extra)
    sanitized: dict[str, str] = {}
    for key, value in merged.items():
        try:
            key_text = str(key)
            value_text = str(value)
            key_text.encode("latin-1")
            value_text.encode("latin-1")
        except UnicodeEncodeError:
            continue
        sanitized[key_text] = value_text
    return sanitized


async def _stream_response(
    response,
    request_id: str,
    trace_id: str,
    model_alias: str,
    real_model: str,
    endpoint_id: int,
    api_key_id: int,
    requested_rule_group: str | None,
    rule_group: str,
    exposure_format: str,
    status_code: int,
    latency_ms: int,
    request_start: float,
    dump_rule: RoutingRule | None = None,
    dump_endpoint_name: str | None = None,
    dump_request_body: bytes | None = None,
    dump_session_id: str | None = None,
    dump_request_path: str | None = None,
    execution_mode: str = "direct",
    agent_node: str | None = None,
    upstream_url: str | None = None,
    circuit_breaker=None,
    router_service=None,
    route_candidate: RouteCandidate | None = None,
) -> AsyncGenerator[bytes, None]:
    buffer = ""
    usage_payload = None
    first_data_at: float | None = None
    chunks: list[bytes] = []
    stream_complete = False
    stream_failed = False
    try:
        async for chunk in response.aiter_bytes():
            if chunk:
                if dump_rule is not None:
                    chunks.append(chunk)
                buffer, usage_payload, data_seen, chunk_failed = _inspect_stream_chunk(
                    buffer, usage_payload, chunk
                )
                stream_failed = stream_failed or chunk_failed
                if data_seen and first_data_at is None:
                    first_data_at = time.perf_counter()
            yield chunk
        stream_complete = not stream_failed
    except (asyncio.CancelledError, GeneratorExit):
        stream_complete = False
        raise
    except Exception:
        stream_complete = False
        stream_failed = True
        raise
    finally:
        stream_end = time.perf_counter()
        await response.aclose()
        if circuit_breaker is not None and route_candidate is not None:
            if stream_failed:
                await circuit_breaker.record_failure(route_candidate.api_key.id)
            elif stream_complete:
                await circuit_breaker.record_success(route_candidate.api_key.id)
                if router_service is not None:
                    await router_service.record_candidate_success(route_candidate)
        ttft_ms = (
            int((first_data_at - request_start) * 1000)
            if first_data_at is not None
            else None
        )
        prompt_tokens, completion_tokens, total_tokens, cached_tokens = extract_usage(
            usage_payload
        )
        tps = _calculate_tps(first_data_at, stream_end, completion_tokens)
        resolved_latency_ms = ttft_ms if ttft_ms is not None else latency_ms
        metrics = RequestMetrics(
            request_id=request_id,
            trace_id=trace_id,
            model_alias=model_alias,
            endpoint_id=endpoint_id,
            api_key_id=api_key_id,
            requested_rule_group=requested_rule_group,
            rule_group=rule_group,
            exposure_format=exposure_format,
            status_code=status_code,
            latency_ms=resolved_latency_ms,
            ttft_ms=ttft_ms,
            tps=tps,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            cached_tokens=cached_tokens,
            execution_mode=execution_mode,
            agent_node=agent_node,
            upstream_url=upstream_url,
        )
        safe_create_task(write_request_log(metrics))
        if dump_rule is not None and dump_endpoint_name:
            safe_create_task(
                _dump_proxy_record(
                    dump_rule,
                    request_id,
                    trace_id,
                    dump_endpoint_name,
                    model_alias,
                    dump_request_body or b"",
                    b"".join(chunks),
                    status_code,
                    endpoint_id=endpoint_id,
                    real_model=real_model,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    total_tokens=total_tokens,
                    cached_tokens=cached_tokens,
                    latency_ms=resolved_latency_ms,
                    is_stream=True,
                    stream_complete=stream_complete,
                    session_id=dump_session_id,
                    request_path=dump_request_path,
                )
            )


def _calculate_tps(
    first_data_at: float | None, stream_end: float, completion_tokens: int | None
) -> float | None:
    if first_data_at is None or completion_tokens is None:
        return None
    if completion_tokens <= 0:
        return None
    duration = stream_end - first_data_at
    if duration <= 0:
        return None
    return completion_tokens / duration


def _inspect_stream_chunk(
    buffer: str, usage_payload: dict | None, chunk: bytes
) -> tuple[str, dict | None, bool, bool]:
    try:
        text = chunk.decode("utf-8")
    except UnicodeDecodeError:
        return buffer, usage_payload, False, False

    buffer += text
    lines = buffer.split("\n")
    buffer = lines.pop()
    data_seen = False
    stream_failed = False
    for line in lines:
        line = line.strip()
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if not data or data == "[DONE]":
            continue
        data_seen = True
        try:
            payload = json.loads(data)
        except json.JSONDecodeError:
            continue
        payload_type = str(payload.get("type") or "").strip().lower()
        if payload_type in {"error", "response.failed"} or isinstance(
            payload.get("error"), dict
        ):
            stream_failed = True
        metadata = payload.get("metadata")
        if "usage" in payload or "usageMetadata" in payload or "total_usage" in payload:
            usage_payload = payload
        elif isinstance(metadata, dict) and (
            "total_usage" in metadata or "usage" in metadata
        ):
            usage_payload = payload
        elif payload.get("type") == "response.completed":
            response_payload = payload.get("response")
            if isinstance(response_payload, dict) and "usage" in response_payload:
                usage_payload = response_payload
        else:
            choices = payload.get("choices")
            if isinstance(choices, list) and any(
                isinstance(choice, dict) and isinstance(choice.get("usage"), dict)
                for choice in choices
            ):
                usage_payload = payload
    return buffer, usage_payload, data_seen, stream_failed
