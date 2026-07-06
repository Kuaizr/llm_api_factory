from datetime import datetime, timezone
import json
import logging
import re
import time
import uuid
from urllib.parse import quote, urlparse

from fastapi import Depends, HTTPException, Query
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.v1.route_helpers import (
    _build_api_key_out,
    _build_endpoint_detail,
    _build_endpoint_out,
    _build_routing_rule_out,
    _deserialize_rule_config,
    _deserialize_rule_config_detail,
    _ensure_default_rule_group,
    _ensure_rule_group_available,
    _is_default_rule_group,
    _mask_key,
    _merge_masked_oauth_config,
    _normalize_dump_path,
    _normalize_endpoint_access_mode,
    _normalize_api_key_usage,
    _parse_iso_datetime,
    _resolve_endpoint_status,
    _serialize_rule_config,
    _today_utc_date,
)
from app.api.v1.route_models import (
    APIKeyCreate,
    APIKeyDirectTestOut,
    APIKeyDirectTestRequest,
    APIKeyOut,
    APIKeyUpdate,
    AuditLogOut,
    DeleteResponse,
    EndpointCreate,
    EndpointDetailOut,
    EndpointKeyCreate,
    EndpointOut,
    EndpointProbeOut,
    EndpointUpdate,
    FactoryAccessKeyCreate,
    FactoryAccessKeyIssueOut,
    FactoryAccessKeyOut,
    FactoryAccessKeyUpdate,
    ModelMapCreate,
    ModelMapOut,
    ModelMapUpdate,
    RequestAttemptLogOut,
    RequestLogOut,
    RoutingRuleCreate,
    RoutingRuleOut,
    RoutingRuleUpdate,
    RuleAccessKeyCreate,
    RuleAccessKeyIssueOut,
    RuleAccessKeyOut,
    RuleGroupEligibilityCheck,
    RuleGroupEligibilityOut,
)
from app.core.config import get_settings
from app.core.http_client import get_http_client
from app.core.redis import get_redis
from app.core.route_exposure import (
    DEFAULT_EXPOSURE_FORMAT,
    SUPPORTED_EXPOSURE_FORMATS,
    normalize_exposure_format,
)
from app.db.models import (
    APIKey,
    AuditLog,
    Endpoint,
    FactoryAccessKey,
    ModelMap,
    RequestAttemptLog,
    RequestLog,
    RoutingRule,
)
from app.db.session import get_session
from app.services.access_keys import (
    access_key_preview,
    hash_access_key,
    is_hashed_access_key,
)
from app.services.circuit_breaker import CircuitBreaker
from app.services.codex_oauth import resolve_codex_credential
from app.services.codex_usage import read_codex_usage_many
from app.services.health_monitor import HealthProbeResult, HealthProbeStore
from app.services.model_patterns import (
    UnsafeModelPatternError,
    compile_model_pattern,
    validate_model_pattern,
)
from app.services.secrets import (
    decrypt_secret_value,
    encrypt_oauth_config,
    encrypt_secret_value,
)
from app.services.audit import audit_snapshot, record_audit_log
from app.services.agent_transport import (
    AgentRequest,
    AgentResponse,
    AgentUnavailableError,
    get_agent_manager,
)
from app.api.v1.route_proxy_helpers import _build_upstream_headers

SUPPORTED_ENDPOINT_PROVIDERS = {"openai", "anthropic", "gemini", "codex", "custom"}
ANTHROPIC_PROBE_FALLBACK_MODEL = "claude-3-5-haiku-latest"
CUSTOM_ONLY_ENDPOINT_FIELDS = (
    "url_path_suffix",
    "extra_headers",
    "extra_cookies",
    "extra_query_params",
    "oauth_config",
    "request_body_template",
)
logger = logging.getLogger(__name__)


def _parse_audit_json(raw: str | None) -> object | None:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None


def _build_audit_log_out(log: AuditLog) -> AuditLogOut:
    return AuditLogOut(
        id=log.id,
        actor=log.actor,
        action=log.action,
        resource_type=log.resource_type,
        resource_id=log.resource_id,
        resource_name=log.resource_name,
        before=_parse_audit_json(log.before_json),
        after=_parse_audit_json(log.after_json),
        created_at=log.created_at,
    )


def _factory_access_key_preview(item: FactoryAccessKey) -> str:
    stored_preview = str(getattr(item, "key_preview", "") or "").strip()
    if stored_preview:
        return stored_preview
    stored_key = str(getattr(item, "key", "") or "")
    if is_hashed_access_key(stored_key):
        return "********"
    return access_key_preview(stored_key)


def _api_key_resource_name(api_key: APIKey) -> str:
    return api_key.name or f"api_key:{api_key.id}"


def _model_map_resource_name(model_map: ModelMap) -> str:
    return f"{model_map.model_alias}->{model_map.real_model}"


def _validate_rule_model_pattern(pattern: str) -> str:
    try:
        validate_model_pattern(pattern)
    except UnsafeModelPatternError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return pattern


def _validate_rule_dump_path(dump_path: str | None) -> str | None:
    try:
        return _normalize_dump_path(dump_path)
    except HTTPException:
        raise


def _validate_rule_exposure_format(value: object) -> str:
    normalized = normalize_exposure_format(value)
    if normalized == DEFAULT_EXPOSURE_FORMAT and isinstance(value, str):
        raw = value.strip().lower().replace("-", "_")
        if raw and raw not in SUPPORTED_EXPOSURE_FORMATS and raw != "responses":
            raise HTTPException(status_code=400, detail="Invalid exposure_format")
    return normalized


def _normalize_endpoint_provider(raw: object) -> str:
    normalized = str(raw or "").strip().lower()
    if not normalized:
        return "openai"
    if normalized not in SUPPORTED_ENDPOINT_PROVIDERS:
        raise HTTPException(
            status_code=400,
            detail=(
                "Unsupported provider. Allowed values: "
                f"{', '.join(sorted(SUPPORTED_ENDPOINT_PROVIDERS))}"
            ),
        )
    return normalized


def _apply_provider_auth_defaults(data: dict[str, object]) -> None:
    provider = str(data.get("provider") or "").strip().lower()
    header_name = str(data.get("auth_header_name") or "").strip()
    header_prefix = str(data.get("auth_header_prefix") or "").strip()
    if provider == "anthropic" and (
        not header_name or header_name.lower() == "authorization"
    ):
        data["auth_header_name"] = "x-api-key"
        if not header_prefix or header_prefix.lower() == "bearer":
            data["auth_header_prefix"] = ""
    elif provider == "gemini" and (
        not header_name or header_name.lower() == "authorization"
    ):
        data["auth_header_name"] = "x-goog-api-key"
        if not header_prefix or header_prefix.lower() == "bearer":
            data["auth_header_prefix"] = ""


def _clear_custom_only_endpoint_fields(data: dict[str, object], provider: str) -> None:
    if provider == "custom":
        return
    for field in CUSTOM_ONLY_ENDPOINT_FIELDS:
        data[field] = None


def _normalize_url_path_suffix(raw: object) -> str | None:
    if not isinstance(raw, str):
        return None
    trimmed = raw.strip()
    if not trimmed:
        return None
    return trimmed if trimmed.startswith("/") else f"/{trimmed}"


def _build_provider_probe_url(
    endpoint: Endpoint,
    *,
    default_suffix: str,
) -> str:
    custom_suffix = _normalize_url_path_suffix(endpoint.url_path_suffix)
    if custom_suffix:
        return f"{endpoint.base_url.rstrip('/')}{custom_suffix}"

    cleaned = endpoint.base_url.rstrip("/")
    if cleaned.endswith("/v1"):
        cleaned = cleaned[:-3]
    return f"{cleaned}{default_suffix}"


def _build_provider_api_url(endpoint: Endpoint, default_suffix: str) -> str:
    custom_suffix = _normalize_url_path_suffix(endpoint.url_path_suffix)
    if endpoint.provider == "custom" and custom_suffix:
        return f"{endpoint.base_url.rstrip('/')}{custom_suffix}"
    if endpoint.provider == "codex" and default_suffix in {
        "/v1/responses",
        "/v1/responses/compact",
    }:
        suffix = (
            "/backend-api/codex/responses/compact"
            if default_suffix.endswith("/compact")
            else "/backend-api/codex/responses"
        )
        return f"{endpoint.base_url.rstrip('/')}{suffix}"

    cleaned = endpoint.base_url.rstrip("/")
    for version in ("v1", "v1beta", "v1alpha"):
        if default_suffix.startswith(f"/{version}") and cleaned.endswith(f"/{version}"):
            cleaned = cleaned[: -(len(version) + 1)]
            break
    return f"{cleaned}{default_suffix}"


DIRECT_TEST_TEMPLATES = {
    "chat",
    "response",
    "codex",
    "claude",
    "claude-code",
    "gemini",
}
CLAUDE_CODE_BETAS = (
    "claude-code-20250219,"
    "context-1m-2025-08-07,"
    "interleaved-thinking-2025-05-14,"
    "context-management-2025-06-27,"
    "prompt-caching-scope-2026-01-05,"
    "mid-conversation-system-2026-04-07,"
    "effort-2025-11-24"
)


def _endpoint_agent_name(endpoint: Endpoint) -> str | None:
    access_mode = _normalize_endpoint_access_mode(
        getattr(endpoint, "access_mode", None),
        getattr(endpoint, "agent_node", None),
    )
    if access_mode != "via_agent":
        return None
    name = str(getattr(endpoint, "agent_node", "") or "").strip()
    return name or None


async def _send_endpoint_request(
    *,
    endpoint: Endpoint,
    method: str,
    url: str,
    headers: dict[str, str],
    json_payload: dict[str, object] | None,
    client,
    timeout: float | None = None,
) -> tuple[int, dict[str, str], bytes]:
    body = (
        json.dumps(json_payload, ensure_ascii=False).encode("utf-8")
        if json_payload is not None
        else b""
    )
    request_headers = dict(headers)
    if json_payload is not None:
        request_headers.setdefault("Content-Type", "application/json")
    agent_name = _endpoint_agent_name(endpoint)
    if agent_name:
        agent_response = await get_agent_manager().send_request(
            agent_name,
            AgentRequest(
                method=method.upper(),
                url=url,
                headers=request_headers,
                body=body,
                stream=False,
            ),
        )
        if not isinstance(agent_response, AgentResponse):
            raise AgentUnavailableError(f"Agent {agent_name} returned stream response")
        return agent_response.status_code, dict(agent_response.headers), agent_response.body

    if method.upper() == "POST":
        response = await client.post(
            url,
            headers=request_headers,
            content=body,
            timeout=timeout,
        )
    else:
        response = await client.get(url, headers=headers, timeout=timeout)
    try:
        content = await response.aread()
    finally:
        await response.aclose()
    return response.status_code, dict(response.headers), content


def _default_direct_test_template(endpoint: Endpoint) -> str:
    provider = _normalize_endpoint_provider(endpoint.provider)
    if provider == "codex":
        return "codex"
    if provider == "anthropic":
        return "claude-code"
    if provider == "gemini":
        return "gemini"
    return "chat"


def _normalize_direct_test_template(raw: object, endpoint: Endpoint) -> str:
    if not isinstance(raw, str) or not raw.strip():
        return _default_direct_test_template(endpoint)
    normalized = raw.strip().lower()
    if normalized == "responses":
        normalized = "response"
    if normalized in {"claude_code", "claudecode"}:
        normalized = "claude-code"
    if normalized not in DIRECT_TEST_TEMPLATES:
        raise HTTPException(status_code=400, detail="unsupported request_template")
    return normalized


def _build_direct_test_request(
    endpoint: Endpoint, model: str, request_template: str, prompt: str
) -> tuple[str, dict[str, object], dict[str, str]]:
    headers: dict[str, str] = {}
    if request_template in {"response", "codex"}:
        payload: dict[str, object] = {
            "model": model,
            "input": prompt,
            "max_output_tokens": 64,
        }
        if request_template == "codex":
            request_id = uuid.uuid4().hex
            session_id = f"lmf-codex-test-session-{request_id}"
            thread_id = f"lmf-codex-test-thread-{request_id}"
            turn_id = f"lmf-codex-test-turn-{request_id}"
            window_id = f"lmf-codex-test-window-{request_id}"
            installation_id = f"lmf-codex-test-installation-{request_id}"
            turn_metadata = json.dumps(
                {
                    "installation_id": installation_id,
                    "session_id": session_id,
                    "thread_id": thread_id,
                    "turn_id": turn_id,
                    "window_id": window_id,
                    "request_kind": "turn",
                    "source": "llm_api_factory_api_key_test",
                },
                separators=(",", ":"),
            )
            client_metadata = {
                "x-codex-installation-id": installation_id,
                "session_id": session_id,
                "thread_id": thread_id,
                "x-codex-window-id": window_id,
                "x-codex-turn-metadata": turn_metadata,
            }
            payload = {
                "model": model,
                "instructions": "",
                "input": [
                    {
                        "type": "message",
                        "role": "user",
                        "content": [{"type": "input_text", "text": prompt}],
                    }
                ],
                "tools": [],
                "tool_choice": "auto",
                "parallel_tool_calls": False,
                "reasoning": None,
                "store": False,
                "stream": True,
                "include": [],
                "prompt_cache_key": f"lmf-codex-test-{request_id}",
                "client_metadata": client_metadata,
            }
            headers = {
                "User-Agent": "codex-cli/0.0.0 lmf-api-key-test",
                "Originator": "codex_cli_rs",
                "session-id": session_id,
                "thread-id": thread_id,
                "x-client-request-id": thread_id,
                "x-codex-installation-id": installation_id,
                "x-codex-window-id": window_id,
                "x-codex-beta-features": "responses",
                "x-codex-turn-metadata": turn_metadata,
            }
        return _build_provider_api_url(endpoint, "/v1/responses"), payload, headers
    if request_template == "claude":
        return _build_provider_api_url(endpoint, "/v1/messages"), {
            "model": model,
            "max_tokens": 64,
            "messages": [{"role": "user", "content": prompt}],
        }, headers
    if request_template == "claude-code":
        request_id = uuid.uuid4().hex
        session_id = str(uuid.uuid4())
        billing_hash = request_id[:5]
        upstream_model = model.removesuffix("[1m]").strip() or model
        url = f"{_build_provider_api_url(endpoint, '/v1/messages')}?beta=true"
        headers = {
            "Accept": "application/json",
            "User-Agent": "claude-cli/2.1.167 (external, sdk-cli)",
            "X-Claude-Code-Session-Id": session_id,
            "X-Stainless-Arch": "x64",
            "X-Stainless-Lang": "js",
            "X-Stainless-OS": "Linux",
            "X-Stainless-Package-Version": "0.94.0",
            "X-Stainless-Retry-Count": "0",
            "X-Stainless-Runtime": "node",
            "X-Stainless-Runtime-Version": "v24.3.0",
            "X-Stainless-Timeout": "300",
            "anthropic-beta": CLAUDE_CODE_BETAS,
            "anthropic-dangerous-direct-browser-access": "true",
            "anthropic-version": "2023-06-01",
            "x-app": "cli",
        }
        payload = {
            "model": upstream_model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": prompt,
                            "cache_control": {"type": "ephemeral"},
                        }
                    ],
                }
            ],
            "system": [
                {
                    "type": "text",
                    "text": (
                        "x-anthropic-billing-header: "
                        f"cc_version=2.1.167.cb1; cc_entrypoint=sdk-cli; "
                        f"cch={billing_hash};"
                    ),
                },
                {
                    "type": "text",
                    "text": "You are a Claude agent, built on Anthropic's Claude Agent SDK.",
                    "cache_control": {"type": "ephemeral"},
                },
            ],
            "tools": [],
            "metadata": {
                "user_id": json.dumps(
                    {
                        "device_id": "lmf-api-key-test",
                        "account_uuid": "",
                        "session_id": session_id,
                    },
                    separators=(",", ":"),
                )
            },
            "max_tokens": 1024,
            "thinking": {"type": "adaptive"},
            "context_management": {
                "edits": [{"type": "clear_thinking_20251015", "keep": "all"}]
            },
            "output_config": {"effort": "high"},
            "stream": True,
        }
        return url, payload, headers
    if request_template == "gemini":
        encoded_model = quote(model, safe="/")
        return _build_provider_api_url(
            endpoint, f"/v1beta/models/{encoded_model}:generateContent"
        ), {
            "contents": [
                {"role": "user", "parts": [{"text": prompt}]},
            ]
        }, headers

    return _build_provider_api_url(endpoint, "/v1/chat/completions"), {
        "model": model,
        "stream": False,
        "max_tokens": 64,
        "messages": [{"role": "user", "content": prompt}],
    }, headers


def _extract_direct_test_text(payload: object) -> str | None:
    if not isinstance(payload, dict):
        return None

    output_text = payload.get("output_text")
    if isinstance(output_text, str) and output_text.strip():
        return output_text.strip()

    choices = payload.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0]
        if isinstance(first, dict):
            message = first.get("message")
            if isinstance(message, dict):
                content = message.get("content")
                if isinstance(content, str) and content.strip():
                    return content.strip()
            text = first.get("text")
            if isinstance(text, str) and text.strip():
                return text.strip()

    content = payload.get("content")
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        if parts:
            return "\n".join(parts).strip()

    candidates = payload.get("candidates")
    if isinstance(candidates, list) and candidates:
        first = candidates[0]
        if isinstance(first, dict):
            candidate_content = first.get("content")
            if isinstance(candidate_content, dict):
                gemini_parts = candidate_content.get("parts")
                if isinstance(gemini_parts, list):
                    parts = [
                        part.get("text")
                        for part in gemini_parts
                        if isinstance(part, dict) and isinstance(part.get("text"), str)
                    ]
                    if parts:
                        return "\n".join(parts).strip()

    output = payload.get("output")
    if isinstance(output, list):
        parts: list[str] = []
        for item in output:
            if not isinstance(item, dict):
                continue
            item_content = item.get("content")
            if isinstance(item_content, list):
                for part in item_content:
                    if isinstance(part, dict) and isinstance(part.get("text"), str):
                        parts.append(part["text"])
            elif isinstance(item.get("text"), str):
                parts.append(item["text"])
        if parts:
            return "\n".join(parts).strip()

    return None


def _extract_direct_test_sse_text(text: str) -> str | None:
    parts: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("data:"):
            continue
        data = stripped[5:].strip()
        if not data or data == "[DONE]":
            continue
        try:
            payload = json.loads(data)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(payload, dict):
            continue
        delta = payload.get("delta")
        if isinstance(delta, str):
            parts.append(delta)
            continue
        if isinstance(delta, dict):
            text_delta = delta.get("text")
            if isinstance(text_delta, str):
                parts.append(text_delta)
                continue
        output_text = payload.get("output_text")
        if isinstance(output_text, str):
            parts.append(output_text)
            continue
        if payload.get("type") == "response.output_text.delta":
            event_delta = payload.get("delta")
            if isinstance(event_delta, str):
                parts.append(event_delta)
                continue
        choices = payload.get("choices")
        if isinstance(choices, list) and choices:
            choice = choices[0]
            if isinstance(choice, dict):
                choice_delta = choice.get("delta")
                if isinstance(choice_delta, dict):
                    content = choice_delta.get("content")
                    if isinstance(content, str):
                        parts.append(content)
                        continue
        extracted = _extract_direct_test_text(payload)
        if extracted:
            parts.append(extracted)
    if not parts:
        return None
    return "".join(parts).strip() or None


def _direct_test_error_reason(status_code: int, payload: object) -> str | None:
    if status_code >= 400:
        return f"http_{status_code}"
    if not isinstance(payload, dict):
        return None
    error = payload.get("error")
    if error:
        return "error_field"
    errors = payload.get("errors")
    if errors:
        return "errors_field"
    status = str(payload.get("status") or "").strip().lower()
    if status in {"error", "failed", "failure"}:
        return f"status_{status}"
    if payload.get("success") is False or payload.get("ok") is False:
        return "success_false"
    return None


def _pick_probe_model(existing_models: list[ModelMap]) -> str:
    for model in existing_models:
        if model.real_model:
            return model.real_model
        if model.model_alias:
            return model.model_alias
    return ANTHROPIC_PROBE_FALLBACK_MODEL


def _resolve_probe_message(
    *,
    provider: str,
    status: str,
    status_code: int | None,
    discovered_models: list[str],
) -> str | None:
    if status_code in {401, 403}:
        return "API Key 权限问题，请检查上游权限配置。"
    if discovered_models:
        return None
    if status == "error":
        return "探测请求执行失败，请查看后端日志。"
    if status_code is not None and status_code >= 400:
        return f"上游模型探测接口返回 HTTP {status_code}。"
    if provider == "anthropic":
        return None
    if status == "success":
        return "上游模型探测接口返回成功，但没有解析到模型，请在右侧手动新增模型映射。"
    return "上游不支持模型探测接口，请在右侧手动新增模型映射。"


def _dedupe_discovered_models(raw_models: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for model in raw_models:
        normalized = str(model or "").strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(normalized)
    return deduped


def _extract_provider_models(provider: str, payload: object) -> list[str]:
    if not isinstance(payload, dict):
        return []

    if provider == "gemini":
        raw_models = payload.get("models")
        if not isinstance(raw_models, list):
            return []
        names: list[str] = []
        for item in raw_models:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            if name.startswith("models/"):
                name = name.removeprefix("models/")
            if name:
                names.append(name)
        return _dedupe_discovered_models(names)

    payload_models = payload.get("data")
    if not isinstance(payload_models, list):
        return []
    return _dedupe_discovered_models(
        [
            str(item.get("id"))
            for item in payload_models
            if isinstance(item, dict) and item.get("id")
        ]
    )


async def _sync_probe_model_maps(
    *,
    session: AsyncSession,
    endpoint_id: int,
    existing_models: list[ModelMap],
    discovered_models: list[str],
) -> list[ModelMap]:
    normalized_discovered = _dedupe_discovered_models(discovered_models)
    if not normalized_discovered:
        return existing_models

    discovered_set = set(normalized_discovered)
    manual_real_models = {
        (model.real_model or "").strip()
        for model in existing_models
        if not model.probe_managed and (model.real_model or "").strip()
    }

    auto_models_by_real: dict[str, list[ModelMap]] = {}
    auto_models: list[ModelMap] = []
    for model in existing_models:
        if not model.probe_managed:
            continue
        real_model = (model.real_model or "").strip()
        if not real_model:
            continue
        auto_models.append(model)
        auto_models_by_real.setdefault(real_model, []).append(model)

    delete_ids: set[int] = set()

    for real_model in manual_real_models:
        for auto_model in auto_models_by_real.get(real_model, []):
            delete_ids.add(auto_model.id)

    for discovered_model in normalized_discovered:
        if discovered_model in manual_real_models:
            continue
        same_real_models = auto_models_by_real.get(discovered_model, [])
        if same_real_models:
            keeper = same_real_models[0]
            keeper.model_alias = discovered_model
            keeper.real_model = discovered_model
            keeper.probe_managed = True
            for duplicated in same_real_models[1:]:
                delete_ids.add(duplicated.id)
            continue
        session.add(
            ModelMap(
                endpoint_id=endpoint_id,
                model_alias=discovered_model,
                real_model=discovered_model,
                probe_managed=True,
            )
        )

    for auto_model in auto_models:
        real_model = (auto_model.real_model or "").strip()
        if not real_model or real_model in manual_real_models or real_model not in discovered_set:
            delete_ids.add(auto_model.id)

    if delete_ids:
        for model in existing_models:
            if model.id in delete_ids:
                await session.delete(model)

    await session.commit()

    refreshed_result = await session.execute(
        select(ModelMap).where(ModelMap.endpoint_id == endpoint_id).order_by(ModelMap.id)
    )
    return refreshed_result.scalars().all()


def _normalize_rule_group_key(value: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        return "default"
    return "default" if normalized.lower() == "default" else normalized


async def _validate_rule_groups(session: AsyncSession, groups: list[str]) -> list[str]:
    normalized = APIKey.normalize_rule_groups(groups)
    await _ensure_default_rule_group(session)
    non_default_groups = [group for group in normalized if not _is_default_rule_group(group)]
    if not non_default_groups:
        return normalized

    result = await session.execute(select(RoutingRule.group_name))
    existing = {
        _normalize_rule_group_key(item)
        for item in result.scalars().all()
        if isinstance(item, str) and item.strip()
    }
    missing = [group for group in non_default_groups if _normalize_rule_group_key(group) not in existing]
    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"Rule group not found: {', '.join(missing)}",
        )
    return normalized


async def _sync_rule_targets_for_api_key(
    *,
    session: AsyncSession,
    api_key_id: int,
    previous_groups: list[str],
    current_groups: list[str],
) -> None:
    previous_lookup = {_normalize_rule_group_key(group).lower() for group in previous_groups}
    current_lookup = {_normalize_rule_group_key(group).lower() for group in current_groups}
    affected_lookup = previous_lookup | current_lookup
    if not affected_lookup:
        return

    result = await session.execute(select(RoutingRule))
    rules = result.scalars().all()
    for rule in rules:
        group_lookup = _normalize_rule_group_key(rule.group_name).lower()
        if group_lookup not in affected_lookup:
            continue

        target_key_ids, strategy, exposure_format = _deserialize_rule_config_detail(
            rule.target_key_ids_json
        )
        target_key_set = set(target_key_ids)
        should_select = group_lookup in current_lookup
        changed = False
        if should_select and api_key_id not in target_key_set:
            target_key_set.add(api_key_id)
            changed = True
        elif not should_select and api_key_id in target_key_set:
            target_key_set.remove(api_key_id)
            changed = True

        if changed:
            rule.target_key_ids_json = _serialize_rule_config(
                sorted(target_key_set), strategy, exposure_format
            )


async def _sync_api_key_groups_for_rule_targets(
    *,
    session: AsyncSession,
    group_name: str,
    previous_target_key_ids: list[int],
    current_target_key_ids: list[int],
) -> None:
    normalized_group = _normalize_rule_group_key(group_name)
    group_lookup = normalized_group.lower()

    previous_key_ids: set[int] = set()
    for item in previous_target_key_ids:
        try:
            previous_key_ids.add(int(item))
        except (TypeError, ValueError):
            continue

    current_key_ids: set[int] = set()
    for item in current_target_key_ids:
        try:
            current_key_ids.add(int(item))
        except (TypeError, ValueError):
            continue

    affected_key_ids = previous_key_ids | current_key_ids
    if not affected_key_ids:
        return

    result = await session.execute(select(APIKey).where(APIKey.id.in_(affected_key_ids)))
    api_keys = result.scalars().all()

    for api_key in api_keys:
        previous_groups = api_key.rule_groups
        has_group = any(
            _normalize_rule_group_key(group).lower() == group_lookup
            for group in previous_groups
        )
        should_have_group = api_key.id in current_key_ids
        if has_group == should_have_group:
            continue

        if should_have_group:
            next_groups = APIKey.normalize_rule_groups([*previous_groups, normalized_group])
        else:
            next_groups = APIKey.normalize_rule_groups(
                [
                    group
                    for group in previous_groups
                    if _normalize_rule_group_key(group).lower() != group_lookup
                ]
            )

        api_key.assign_rule_groups(next_groups)
        await _sync_rule_targets_for_api_key(
            session=session,
            api_key_id=api_key.id,
            previous_groups=previous_groups,
            current_groups=next_groups,
        )


def _extract_model_aliases(model_maps: list[ModelMap]) -> list[str]:
    return sorted(
        {
            str(item.model_alias or "").strip()
            for item in model_maps
            if str(item.model_alias or "").strip()
        }
    )


async def _resolve_probe_key_for_eligibility(
    *,
    session: AsyncSession,
    endpoint_id: int,
    payload: RuleGroupEligibilityCheck,
) -> str | None:
    if payload.api_key_id is not None:
        api_key = await session.get(APIKey, payload.api_key_id)
        if not api_key:
            raise HTTPException(status_code=404, detail="API key not found")
        if api_key.endpoint_id != endpoint_id:
            raise HTTPException(status_code=400, detail="API key does not belong to endpoint")
        return decrypt_secret_value(api_key.key, settings=get_settings())

    raw_key = str(payload.api_key or "").strip()
    if raw_key:
        return raw_key

    fallback_key = await session.scalar(
        select(APIKey.key)
        .where(APIKey.endpoint_id == endpoint_id, APIKey.is_active.is_(True))
        .order_by(APIKey.id)
    )
    return decrypt_secret_value(fallback_key, settings=get_settings()) if fallback_key else None


async def _probe_endpoint_models_with_key(
    *,
    endpoint: Endpoint,
    api_key_value: str,
) -> tuple[list[str], int | None]:
    provider = _normalize_endpoint_provider(endpoint.provider)
    if provider == "anthropic":
        return [], None

    settings = get_settings()
    client = await get_http_client()
    default_suffix = "/v1beta/models" if provider == "gemini" else "/v1/models"
    url = _build_provider_probe_url(endpoint, default_suffix=default_suffix)
    header_name = endpoint.auth_header_name or "Authorization"
    header_prefix = endpoint.auth_header_prefix
    if header_prefix is None:
        header_prefix = "Bearer"
    headers = (
        {header_name: f"{header_prefix} {api_key_value}"}
        if header_prefix
        else {header_name: api_key_value}
    )

    response = None
    try:
        response = await client.get(
            url,
            headers=headers,
            timeout=settings.health_probe_timeout_seconds,
        )
        status_code = response.status_code
        if status_code >= 400:
            return [], status_code
        payload = response.json()
        discovered_models = _extract_provider_models(provider, payload)
        return discovered_models, status_code
    except Exception:
        return [], None
    finally:
        if response is not None:
            await response.aclose()


async def list_endpoints(session: AsyncSession = Depends(get_session)) -> list[EndpointDetailOut]:
    stmt = (
        select(Endpoint)
        .options(selectinload(Endpoint.api_keys), selectinload(Endpoint.model_maps))
        .order_by(Endpoint.id)
    )
    result = await session.execute(stmt)
    endpoints = result.scalars().unique().all()
    today = _today_utc_date()
    changed = False
    for endpoint in endpoints:
        for key in endpoint.api_keys or []:
            if _normalize_api_key_usage(key, today):
                changed = True
    if changed:
        await session.commit()

    redis = await get_redis()
    probe_store = HealthProbeStore(redis)
    api_key_ids = [
        key.id for endpoint in endpoints for key in (endpoint.api_keys or [])
    ]
    probe_results = await probe_store.read_many(api_key_ids)
    probe_series_map = await probe_store.read_series_many(api_key_ids)
    codex_usage_by_key = await read_codex_usage_many(redis, api_key_ids)

    items: list[EndpointDetailOut] = []
    for endpoint in endpoints:
        series = [
            result
            for key in (endpoint.api_keys or [])
            for result in probe_series_map.get(key.id, [])
        ]
        success_latencies = [
            result.latency_ms
            for result in series
            if result.status == "success" and result.latency_ms is not None
        ]
        ping_latency = (
            int(sum(success_latencies) / len(success_latencies))
            if success_latencies
            else 0
        )
        total = len(series)
        success_count = sum(1 for result in series if result.status == "success")
        uptime = round(success_count / total * 100, 1) if total else 0.0
        items.append(
            _build_endpoint_detail(
                endpoint,
                _resolve_endpoint_status(endpoint, probe_results),
                ping_latency,
                uptime,
                codex_usage_by_key=codex_usage_by_key,
            )
        )
    return items


async def create_endpoint(
    payload: EndpointCreate, session: AsyncSession = Depends(get_session)
) -> EndpointOut:
    import json
    data = payload.model_dump()
    if data.get("agent_node") == "":
        data["agent_node"] = None
    if data.get("access_mode") == "direct" and data.get("agent_node"):
        data["access_mode"] = "via_agent"
    data["access_mode"] = _normalize_endpoint_access_mode(
        data.get("access_mode"), data.get("agent_node")
    )
    if data["access_mode"] == "direct":
        data["agent_node"] = None
    elif not data.get("agent_node"):
        raise HTTPException(
            status_code=400,
            detail="agent_node is required when access_mode is via_agent",
        )
    data["provider"] = _normalize_endpoint_provider(data.get("provider"))
    _apply_provider_auth_defaults(data)
    _clear_custom_only_endpoint_fields(data, str(data["provider"]))
    # 将 dict 字段转为 JSON 字符串存储
    if data.get("extra_headers") is not None:
        data["extra_headers"] = json.dumps(data["extra_headers"])
    if data.get("extra_query_params") is not None:
        data["extra_query_params"] = json.dumps(data["extra_query_params"])
    if data.get("oauth_config") is not None:
        data["oauth_config"] = json.dumps(
            encrypt_oauth_config(data["oauth_config"], settings=get_settings())
        )
    # request_body_template 直接存储字符串，无需序列化
    endpoint = Endpoint(**data)
    session.add(endpoint)
    await session.flush()
    await record_audit_log(
        session,
        action="create",
        resource_type="endpoint",
        resource_id=endpoint.id,
        resource_name=endpoint.name,
        after=endpoint,
    )
    await session.commit()
    await session.refresh(endpoint)
    return _build_endpoint_out(endpoint)


async def update_endpoint(
    endpoint_id: int,
    payload: EndpointUpdate,
    session: AsyncSession = Depends(get_session),
) -> EndpointOut:
    import json
    endpoint = await session.get(Endpoint, endpoint_id)
    if not endpoint:
        raise HTTPException(status_code=404, detail="Endpoint not found")
    before_snapshot = audit_snapshot(endpoint)
    data = payload.model_dump(exclude_unset=True)
    if not data:
        raise HTTPException(status_code=400, detail="No fields to update")
    if data.get("agent_node") == "":
        data["agent_node"] = None
    if "agent_node" in data and "access_mode" not in data:
        data["access_mode"] = "via_agent" if data["agent_node"] else "direct"
    next_access_mode = _normalize_endpoint_access_mode(
        data.get("access_mode", getattr(endpoint, "access_mode", None)),
        data.get("agent_node", getattr(endpoint, "agent_node", None)),
    )
    next_agent_node = data.get("agent_node", getattr(endpoint, "agent_node", None))
    data["access_mode"] = next_access_mode
    if next_access_mode == "direct":
        data["agent_node"] = None
    elif not next_agent_node:
        raise HTTPException(
            status_code=400,
            detail="agent_node is required when access_mode is via_agent",
        )
    if "provider" in data:
        data["provider"] = _normalize_endpoint_provider(data.get("provider"))
        _apply_provider_auth_defaults(data)
    next_provider = str(data.get("provider", endpoint.provider) or "openai").strip().lower()
    _clear_custom_only_endpoint_fields(data, next_provider)
    # 将 dict 字段转为 JSON 字符串存储
    if "extra_headers" in data and data["extra_headers"] is not None:
        data["extra_headers"] = json.dumps(data["extra_headers"])
    if "extra_query_params" in data and data["extra_query_params"] is not None:
        data["extra_query_params"] = json.dumps(data["extra_query_params"])
    if "oauth_config" in data and data["oauth_config"] is not None:
        data["oauth_config"] = _merge_masked_oauth_config(
            endpoint.oauth_config,
            data["oauth_config"],
        )
        data["oauth_config"] = encrypt_oauth_config(
            data["oauth_config"], settings=get_settings()
        )
        data["oauth_config"] = json.dumps(data["oauth_config"])
    # request_body_template 直接存储字符串，无需序列化
    for field, value in data.items():
        setattr(endpoint, field, value)
    await record_audit_log(
        session,
        action="update",
        resource_type="endpoint",
        resource_id=endpoint.id,
        resource_name=endpoint.name,
        before=before_snapshot,
        after=endpoint,
    )
    await session.commit()
    await session.refresh(endpoint)
    return _build_endpoint_out(endpoint)


async def probe_endpoint(
    endpoint_id: int,
    session: AsyncSession = Depends(get_session),
) -> EndpointProbeOut:
    endpoint = await session.get(Endpoint, endpoint_id)
    if not endpoint:
        raise HTTPException(status_code=404, detail="Endpoint not found")
    api_key = await session.scalar(
        select(APIKey)
        .where(APIKey.endpoint_id == endpoint_id, APIKey.is_active.is_(True))
        .order_by(APIKey.id)
    )
    if not api_key:
        raise HTTPException(status_code=400, detail="No active API key for probe")

    existing_stmt = select(ModelMap).where(ModelMap.endpoint_id == endpoint_id).order_by(ModelMap.id)
    existing_result = await session.execute(existing_stmt)
    existing_models = existing_result.scalars().all()
    provider = _normalize_endpoint_provider(endpoint.provider)

    settings = get_settings()
    redis = await get_redis()
    probe_store = HealthProbeStore(
        redis,
        ttl_seconds=settings.health_probe_result_ttl_seconds,
        series_ttl_seconds=settings.health_probe_series_ttl_seconds,
        series_max_entries=settings.health_probe_series_max_entries,
    )
    circuit_breaker = CircuitBreaker(redis, settings=settings)

    client = await get_http_client()
    if provider == "anthropic":
        default_suffix = "/v1/messages"
    elif provider == "gemini":
        default_suffix = "/v1beta/models"
    else:
        default_suffix = "/v1/models"
    url = _build_provider_probe_url(endpoint, default_suffix=default_suffix)
    header_name = endpoint.auth_header_name or "Authorization"
    header_prefix = endpoint.auth_header_prefix
    if header_prefix is None:
        header_prefix = "Bearer"
    decrypted_api_key = decrypt_secret_value(api_key.key, settings=get_settings())
    headers = (
        {header_name: f"{header_prefix} {decrypted_api_key}"}
        if header_prefix
        else {header_name: decrypted_api_key}
    )

    status = "error"
    status_code: int | None = None
    latency_ms: int | None = None
    discovered_models: list[str] = []
    response_body = b""
    started_at = time.perf_counter()
    probe_model = _pick_probe_model(existing_models)
    anthropic_probe_payload = {
        "model": probe_model,
        "max_tokens": 1,
        "messages": [{"role": "user", "content": "ping"}],
    }

    try:
        if provider == "anthropic":
            status_code, _response_headers, response_body = await _send_endpoint_request(
                endpoint=endpoint,
                method="POST",
                url=url,
                headers=headers,
                json_payload=anthropic_probe_payload,
                client=client,
                timeout=settings.health_probe_timeout_seconds,
            )
        else:
            status_code, _response_headers, response_body = await _send_endpoint_request(
                endpoint=endpoint,
                method="GET",
                url=url,
                headers=headers,
                json_payload=None,
                client=client,
                timeout=settings.health_probe_timeout_seconds,
            )
        latency_ms = int((time.perf_counter() - started_at) * 1000)
        if status_code >= 400:
            status = "failure"
        else:
            status = "success"
            if provider not in {"anthropic"}:
                payload = json.loads(response_body.decode("utf-8"))
                discovered_models = _extract_provider_models(provider, payload)
    except Exception:
        latency_ms = int((time.perf_counter() - started_at) * 1000)
        status = "error"
        logger.exception(
            "endpoint_probe_failed endpoint_id=%s provider=%s url=%s",
            endpoint.id,
            provider,
            url,
        )

    if status == "success":
        await circuit_breaker.record_success(api_key.id)
    else:
        await circuit_breaker.record_failure(api_key.id)

    await probe_store.write(
        HealthProbeResult(
            api_key_id=api_key.id,
            endpoint_id=endpoint.id,
            endpoint_name=endpoint.name,
            real_model=None,
            status=status,
            status_code=status_code,
            latency_ms=latency_ms,
            checked_at=datetime.now(timezone.utc),
        )
    )

    if status == "success" and provider != "anthropic" and discovered_models:
        existing_models = await _sync_probe_model_maps(
            session=session,
            endpoint_id=endpoint_id,
            existing_models=existing_models,
            discovered_models=discovered_models,
        )

    probe_message = _resolve_probe_message(
        provider=provider,
        status=status,
        status_code=status_code,
        discovered_models=discovered_models,
    )
    return EndpointProbeOut(
        provider=provider,
        probe_status=status,
        probe_status_code=status_code,
        probe_message=probe_message,
        discovered_models=discovered_models,
        manual_models=[ModelMapOut.model_validate(model) for model in existing_models],
    )


async def delete_endpoint(
    endpoint_id: int, session: AsyncSession = Depends(get_session)
) -> DeleteResponse:
    endpoint = await session.get(Endpoint, endpoint_id)
    if not endpoint:
        raise HTTPException(status_code=404, detail="Endpoint not found")
    before_snapshot = audit_snapshot(endpoint)
    endpoint_name = endpoint.name
    await session.execute(
        delete(RequestAttemptLog).where(RequestAttemptLog.endpoint_id == endpoint_id)
    )
    await session.execute(delete(RequestLog).where(RequestLog.endpoint_id == endpoint_id))
    await session.execute(delete(ModelMap).where(ModelMap.endpoint_id == endpoint_id))
    await session.execute(delete(APIKey).where(APIKey.endpoint_id == endpoint_id))
    await session.delete(endpoint)
    await record_audit_log(
        session,
        action="delete",
        resource_type="endpoint",
        resource_id=endpoint_id,
        resource_name=endpoint_name,
        before=before_snapshot,
    )
    await session.commit()
    return DeleteResponse()


async def create_endpoint_key(
    endpoint_id: int,
    payload: EndpointKeyCreate,
    session: AsyncSession = Depends(get_session),
) -> APIKeyOut:
    endpoint = await session.get(Endpoint, endpoint_id)
    if not endpoint:
        raise HTTPException(status_code=404, detail="Endpoint not found")

    data = payload.model_dump()
    data["key"] = encrypt_secret_value(data["key"], settings=get_settings())
    raw_rule_groups = data.pop("rule_groups", None)
    normalized_groups = await _validate_rule_groups(
        session,
        APIKey.normalize_rule_groups(raw_rule_groups, fallback=data.get("rule_group")),
    )
    data["rule_group"] = next(
        (group for group in normalized_groups if not _is_default_rule_group(group)),
        "default",
    )

    api_key = APIKey(endpoint_id=endpoint_id, **data)
    api_key.assign_rule_groups(normalized_groups)
    session.add(api_key)
    await session.flush()
    await _sync_rule_targets_for_api_key(
        session=session,
        api_key_id=api_key.id,
        previous_groups=[],
        current_groups=normalized_groups,
    )
    await record_audit_log(
        session,
        action="create",
        resource_type="api_key",
        resource_id=api_key.id,
        resource_name=_api_key_resource_name(api_key),
        after=api_key,
    )
    await session.commit()
    await session.refresh(api_key)
    return _build_api_key_out(api_key)


async def list_api_keys(
    endpoint_id: int | None = Query(default=None),
    rule_group: str | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
) -> list[APIKeyOut]:
    stmt = select(APIKey).order_by(APIKey.id)
    if endpoint_id is not None:
        stmt = stmt.where(APIKey.endpoint_id == endpoint_id)

    result = await session.execute(stmt)
    api_keys = result.scalars().all()

    if rule_group is not None:
        target_group = _normalize_rule_group_key(rule_group).lower()
        api_keys = [
            api_key
            for api_key in api_keys
            if api_key.in_rule_group(target_group)
        ]

    today = _today_utc_date()
    changed = False
    for api_key in api_keys:
        if _normalize_api_key_usage(api_key, today):
            changed = True
    if changed:
        await session.commit()
    return [_build_api_key_out(api_key) for api_key in api_keys]


async def check_key_rule_group_eligibility(
    endpoint_id: int,
    payload: RuleGroupEligibilityCheck,
    session: AsyncSession = Depends(get_session),
) -> RuleGroupEligibilityOut:
    endpoint = await session.get(Endpoint, endpoint_id)
    if not endpoint:
        raise HTTPException(status_code=404, detail="Endpoint not found")

    group_name = _normalize_rule_group_key(payload.group_name)
    if _is_default_rule_group(group_name):
        return RuleGroupEligibilityOut(
            group_name="default",
            eligible=True,
            reason=None,
            probed=False,
            required_patterns=[],
            matched_models=[],
        )

    rules_result = await session.execute(
        select(RoutingRule)
        .where(RoutingRule.group_name == group_name, RoutingRule.is_active.is_(True))
        .order_by(RoutingRule.priority.desc(), RoutingRule.id)
    )
    group_rules = rules_result.scalars().all()
    if not group_rules:
        return RuleGroupEligibilityOut(
            group_name=group_name,
            eligible=False,
            reason="分组暂无启用规则，无法校验。",
            probed=False,
            required_patterns=[],
            matched_models=[],
        )

    required_patterns = [rule.model_pattern for rule in group_rules]
    matchers: list[re.Pattern[str]] = []
    for pattern in required_patterns:
        try:
            matchers.append(compile_model_pattern(pattern))
        except UnsafeModelPatternError:
            continue

    if not matchers:
        return RuleGroupEligibilityOut(
            group_name=group_name,
            eligible=False,
            reason="分组规则正则无效，无法校验。",
            probed=False,
            required_patterns=required_patterns,
            matched_models=[],
        )

    maps_result = await session.execute(
        select(ModelMap).where(ModelMap.endpoint_id == endpoint_id).order_by(ModelMap.id)
    )
    model_maps = maps_result.scalars().all()
    probed = False

    if not model_maps:
        probe_key = await _resolve_probe_key_for_eligibility(
            session=session,
            endpoint_id=endpoint_id,
            payload=payload,
        )
        if not probe_key:
            return RuleGroupEligibilityOut(
                group_name=group_name,
                eligible=False,
                reason="缺少可用 API Key，无法执行模型探测。",
                probed=False,
                required_patterns=required_patterns,
                matched_models=[],
            )

        discovered_models, _ = await _probe_endpoint_models_with_key(
            endpoint=endpoint,
            api_key_value=probe_key,
        )
        if discovered_models:
            model_maps = await _sync_probe_model_maps(
                session=session,
                endpoint_id=endpoint_id,
                existing_models=model_maps,
                discovered_models=discovered_models,
            )
            probed = True

    aliases = _extract_model_aliases(model_maps)
    if not aliases:
        return RuleGroupEligibilityOut(
            group_name=group_name,
            eligible=False,
            reason="该端点暂无模型映射，请先完成模型探测后再选择分组。",
            probed=probed,
            required_patterns=required_patterns,
            matched_models=[],
        )

    matched_models = sorted(
        {
            alias
            for alias in aliases
            if any(matcher.match(alias) for matcher in matchers)
        }
    )
    if not matched_models:
        return RuleGroupEligibilityOut(
            group_name=group_name,
            eligible=False,
            reason="该 Key 对应端点模型与分组规则不匹配。",
            probed=probed,
            required_patterns=required_patterns,
            matched_models=[],
        )

    return RuleGroupEligibilityOut(
        group_name=group_name,
        eligible=True,
        reason=None,
        probed=probed,
        required_patterns=required_patterns,
        matched_models=matched_models,
    )


async def create_api_key(
    payload: APIKeyCreate, session: AsyncSession = Depends(get_session)
) -> APIKeyOut:
    endpoint = await session.get(Endpoint, payload.endpoint_id)
    if not endpoint:
        raise HTTPException(status_code=404, detail="Endpoint not found")

    data = payload.model_dump()
    data["key"] = encrypt_secret_value(data["key"], settings=get_settings())
    raw_rule_groups = data.pop("rule_groups", None)
    normalized_groups = await _validate_rule_groups(
        session,
        APIKey.normalize_rule_groups(raw_rule_groups, fallback=data.get("rule_group")),
    )
    data["rule_group"] = next(
        (group for group in normalized_groups if not _is_default_rule_group(group)),
        "default",
    )

    api_key = APIKey(**data)
    api_key.assign_rule_groups(normalized_groups)
    session.add(api_key)
    await session.flush()
    await _sync_rule_targets_for_api_key(
        session=session,
        api_key_id=api_key.id,
        previous_groups=[],
        current_groups=normalized_groups,
    )
    await record_audit_log(
        session,
        action="create",
        resource_type="api_key",
        resource_id=api_key.id,
        resource_name=_api_key_resource_name(api_key),
        after=api_key,
    )
    await session.commit()
    await session.refresh(api_key)
    return _build_api_key_out(api_key)


async def _update_api_key_record(
    *,
    api_key: APIKey,
    payload: APIKeyUpdate,
    session: AsyncSession,
) -> APIKeyOut:
    data = payload.model_dump(exclude_unset=True)
    if not data:
        raise HTTPException(status_code=400, detail="No fields to update")

    before_snapshot = audit_snapshot(api_key)
    previous_groups = api_key.rule_groups
    raw_rule_groups = data.pop("rule_groups", None)
    has_rule_group_update = raw_rule_groups is not None or "rule_group" in data
    if has_rule_group_update:
        fallback_group = data.pop("rule_group", api_key.primary_rule_group)
        normalized_groups = await _validate_rule_groups(
            session,
            APIKey.normalize_rule_groups(raw_rule_groups, fallback=fallback_group),
        )
        api_key.assign_rule_groups(normalized_groups)
    else:
        normalized_groups = previous_groups

    if "key" in data and data["key"] is not None:
        data["key"] = encrypt_secret_value(data["key"], settings=get_settings())

    for field, value in data.items():
        setattr(api_key, field, value)

    if has_rule_group_update:
        await _sync_rule_targets_for_api_key(
            session=session,
            api_key_id=api_key.id,
            previous_groups=previous_groups,
            current_groups=normalized_groups,
        )

    await record_audit_log(
        session,
        action="update",
        resource_type="api_key",
        resource_id=api_key.id,
        resource_name=_api_key_resource_name(api_key),
        before=before_snapshot,
        after=api_key,
    )
    await session.commit()
    await session.refresh(api_key)
    return _build_api_key_out(api_key)


async def update_key(
    api_key_id: int,
    payload: APIKeyUpdate,
    session: AsyncSession = Depends(get_session),
) -> APIKeyOut:
    api_key = await session.get(APIKey, api_key_id)
    if not api_key:
        raise HTTPException(status_code=404, detail="API key not found")
    return await _update_api_key_record(api_key=api_key, payload=payload, session=session)


async def update_api_key(
    api_key_id: int,
    payload: APIKeyUpdate,
    session: AsyncSession = Depends(get_session),
) -> APIKeyOut:
    api_key = await session.get(APIKey, api_key_id)
    if not api_key:
        raise HTTPException(status_code=404, detail="API key not found")
    return await _update_api_key_record(api_key=api_key, payload=payload, session=session)


async def delete_api_key(
    api_key_id: int, session: AsyncSession = Depends(get_session)
) -> DeleteResponse:
    api_key = await session.get(APIKey, api_key_id)
    if not api_key:
        raise HTTPException(status_code=404, detail="API key not found")
    before_snapshot = audit_snapshot(api_key)
    resource_name = _api_key_resource_name(api_key)

    await _sync_rule_targets_for_api_key(
        session=session,
        api_key_id=api_key.id,
        previous_groups=api_key.rule_groups,
        current_groups=[],
    )
    await session.execute(
        delete(RequestAttemptLog).where(RequestAttemptLog.api_key_id == api_key.id)
    )
    await session.execute(delete(RequestLog).where(RequestLog.api_key_id == api_key.id))
    await session.delete(api_key)
    await record_audit_log(
        session,
        action="delete",
        resource_type="api_key",
        resource_id=api_key.id,
        resource_name=resource_name,
        before=before_snapshot,
    )
    await session.commit()
    return DeleteResponse()


async def test_api_key_direct(
    api_key_id: int,
    payload: APIKeyDirectTestRequest,
    session: AsyncSession = Depends(get_session),
) -> APIKeyDirectTestOut:
    result = await session.execute(
        select(APIKey, Endpoint)
        .join(Endpoint, APIKey.endpoint_id == Endpoint.id)
        .where(APIKey.id == api_key_id)
    )
    row = result.first()
    if row is None:
        raise HTTPException(status_code=404, detail="API key not found")
    api_key, endpoint = row

    model = payload.model.strip()
    if not model:
        raise HTTPException(status_code=400, detail="model is required")

    provider = _normalize_endpoint_provider(endpoint.provider)
    request_template = _normalize_direct_test_template(payload.request_template, endpoint)
    prompt = (payload.prompt or "").strip() or "你是什么模型"
    url, request_payload, probe_headers = _build_direct_test_request(
        endpoint, model, request_template, prompt
    )
    client = await get_http_client()
    codex_credential = None
    if provider == "codex":
        codex_credential = await resolve_codex_credential(
            api_key,
            client=client,
            session=session,
        )
    headers = _build_upstream_headers(
        probe_headers,
        endpoint,
        api_key.key,
        request_path=urlparse(url).path,
        payload=request_payload,
        codex_credential=codex_credential,
        is_stream=bool(request_payload.get("stream")),
    )
    start = time.perf_counter()
    status_code = 0
    raw_response: object | None = None
    output_text: str | None = None
    error_reason: str | None = None
    try:
        status_code, _response_headers, content = await _send_endpoint_request(
            endpoint=endpoint,
            method="POST",
            url=url,
            headers=headers,
            json_payload=request_payload,
            client=client,
        )
        text = content.decode("utf-8", errors="replace")
        try:
            raw_response = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            raw_response = text[:4000]
        output_text = _extract_direct_test_text(raw_response)
        if output_text is None and isinstance(raw_response, str):
            output_text = _extract_direct_test_sse_text(raw_response)
        error_reason = _direct_test_error_reason(status_code, raw_response)
    except AgentUnavailableError as exc:
        error_reason = "agent_unavailable"
        raw_response = {"error": str(exc)}
    except Exception as exc:
        error_reason = "request_error"
        raw_response = {"error": str(exc)}
    latency_ms = int((time.perf_counter() - start) * 1000)

    return APIKeyDirectTestOut(
        api_key_id=api_key.id,
        endpoint_id=endpoint.id,
        endpoint_name=endpoint.name,
        provider=provider,
        request_template=request_template,
        model=model,
        prompt=prompt,
        status_code=status_code,
        ok=error_reason is None and 200 <= status_code < 300,
        latency_ms=latency_ms,
        output_text=output_text,
        error_reason=error_reason,
        upstream_url=url,
        raw_response=raw_response,
    )


async def list_rules(session: AsyncSession = Depends(get_session)) -> list[RoutingRuleOut]:
    await _ensure_default_rule_group(session)
    result = await session.execute(
        select(RoutingRule).order_by(RoutingRule.priority.desc(), RoutingRule.id)
    )
    rules = result.scalars().all()

    log_result = await session.execute(
        select(RequestLog, APIKey).join(APIKey, RequestLog.api_key_id == APIKey.id)
    )
    log_rows = log_result.all()
    items: list[RoutingRuleOut] = []
    for rule in rules:
        target_key_ids, strategy, exposure_format = _deserialize_rule_config_detail(
            rule.target_key_ids_json
        )
        try:
            matcher = compile_model_pattern(rule.model_pattern)
        except UnsafeModelPatternError:
            matcher = None
        request_count = 0
        total_tokens = 0
        ttft_sum = 0
        ttft_count = 0
        tps_sum = 0.0
        tps_count = 0
        if matcher:
            for log, api_key in log_rows:
                log_group = (
                    log.rule_group
                    or getattr(api_key, "primary_rule_group", api_key.rule_group)
                    or "default"
                )
                if log_group != rule.group_name:
                    continue
                if not matcher.match(log.model_alias):
                    continue
                request_count += 1
                tokens = log.total_tokens
                if tokens is None:
                    tokens = (log.prompt_tokens or 0) + (log.completion_tokens or 0)
                total_tokens += tokens
                if log.ttft_ms is not None:
                    ttft_sum += log.ttft_ms
                    ttft_count += 1
                if log.tps is not None:
                    tps_sum += float(log.tps)
                    tps_count += 1
        avg_ttft_ms = int(ttft_sum / ttft_count) if ttft_count else None
        avg_tps = round(tps_sum / tps_count, 2) if tps_count else None
        items.append(
            _build_routing_rule_out(
                rule,
                target_key_ids=target_key_ids,
                strategy=strategy,
                exposure_format=exposure_format,
                request_count=request_count,
                total_tokens=total_tokens,
                avg_ttft_ms=avg_ttft_ms,
                avg_tps=avg_tps,
            )
        )
    return items


async def create_rule(
    payload: RoutingRuleCreate, session: AsyncSession = Depends(get_session)
) -> RoutingRuleOut:
    group_name = await _ensure_rule_group_available(session, payload.group_name)
    model_pattern = _validate_rule_model_pattern(payload.model_pattern)
    dump_path = _validate_rule_dump_path(payload.dump_path)
    exposure_format = _validate_rule_exposure_format(payload.exposure_format)
    rule = RoutingRule(
        model_pattern=model_pattern,
        group_name=group_name,
        priority=payload.priority,
        is_active=payload.is_active,
        dump_enabled=payload.dump_enabled,
        dump_path=dump_path,
        target_key_ids_json=_serialize_rule_config(
            payload.target_key_ids, payload.strategy, exposure_format
        ),
    )
    session.add(rule)
    await session.flush()
    await _sync_api_key_groups_for_rule_targets(
        session=session,
        group_name=group_name,
        previous_target_key_ids=[],
        current_target_key_ids=payload.target_key_ids,
    )
    await record_audit_log(
        session,
        action="create",
        resource_type="routing_rule",
        resource_id=rule.id,
        resource_name=rule.group_name,
        after={
            **audit_snapshot(rule),
            "target_key_ids": payload.target_key_ids,
            "strategy": payload.strategy,
            "exposure_format": exposure_format,
        },
    )
    await session.commit()
    await session.refresh(rule)
    target_key_ids, strategy, exposure_format = _deserialize_rule_config_detail(
        rule.target_key_ids_json
    )
    return _build_routing_rule_out(
        rule,
        target_key_ids=target_key_ids,
        strategy=strategy,
        exposure_format=exposure_format,
    )


async def update_rule(
    rule_id: int,
    payload: RoutingRuleUpdate,
    session: AsyncSession = Depends(get_session),
) -> RoutingRuleOut:
    rule = await session.get(RoutingRule, rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="Routing rule not found")
    before_targets, before_strategy, before_exposure_format = _deserialize_rule_config_detail(
        rule.target_key_ids_json
    )
    before_snapshot = {
        **audit_snapshot(rule),
        "target_key_ids": before_targets,
        "strategy": before_strategy,
        "exposure_format": before_exposure_format,
    }
    data = payload.model_dump(exclude_unset=True)
    if not data:
        raise HTTPException(status_code=400, detail="No fields to update")

    previous_group_name = rule.group_name
    current_targets, current_strategy, current_exposure_format = (
        _deserialize_rule_config_detail(rule.target_key_ids_json)
    )
    next_targets = current_targets
    next_strategy = current_strategy
    next_exposure_format = current_exposure_format

    if _is_default_rule_group(rule.group_name):
        if data.get("group_name") is not None and not _is_default_rule_group(
            data["group_name"]
        ):
            raise HTTPException(
                status_code=400,
                detail="Default rule group cannot be renamed",
            )
        if data.get("is_active") is False:
            raise HTTPException(
                status_code=400,
                detail="Default rule group cannot be disabled",
            )

    if "group_name" in data and data["group_name"] != rule.group_name:
        data["group_name"] = await _ensure_rule_group_available(
            session, data["group_name"], exclude_rule_id=rule.id
        )

    has_target_update = "target_key_ids" in data
    has_exposure_update = "exposure_format" in data
    if has_exposure_update:
        next_exposure_format = _validate_rule_exposure_format(
            data.pop("exposure_format")
        )
    if has_target_update or "strategy" in data or has_exposure_update:
        next_targets = data.pop("target_key_ids", current_targets)
        next_strategy = data.pop("strategy", current_strategy)
        rule.target_key_ids_json = _serialize_rule_config(
            next_targets, next_strategy, next_exposure_format
        )

    if "model_pattern" in data:
        data["model_pattern"] = _validate_rule_model_pattern(data["model_pattern"])
    if "dump_path" in data:
        data["dump_path"] = _validate_rule_dump_path(data["dump_path"])

    for field, value in data.items():
        setattr(rule, field, value)

    current_group_name = rule.group_name
    if has_target_update or _normalize_rule_group_key(previous_group_name).lower() != _normalize_rule_group_key(
        current_group_name
    ).lower():
        if _normalize_rule_group_key(previous_group_name).lower() == _normalize_rule_group_key(
            current_group_name
        ).lower():
            await _sync_api_key_groups_for_rule_targets(
                session=session,
                group_name=current_group_name,
                previous_target_key_ids=current_targets,
                current_target_key_ids=next_targets,
            )
        else:
            await _sync_api_key_groups_for_rule_targets(
                session=session,
                group_name=previous_group_name,
                previous_target_key_ids=current_targets,
                current_target_key_ids=[],
            )
            await _sync_api_key_groups_for_rule_targets(
                session=session,
                group_name=current_group_name,
                previous_target_key_ids=[],
                current_target_key_ids=next_targets,
            )

    await record_audit_log(
        session,
        action="update",
        resource_type="routing_rule",
        resource_id=rule.id,
        resource_name=rule.group_name,
        before=before_snapshot,
        after={
            **audit_snapshot(rule),
            "target_key_ids": next_targets,
            "strategy": next_strategy,
            "exposure_format": next_exposure_format,
        },
    )
    await session.commit()
    await session.refresh(rule)
    target_key_ids, strategy, exposure_format = _deserialize_rule_config_detail(
        rule.target_key_ids_json
    )
    return _build_routing_rule_out(
        rule,
        target_key_ids=target_key_ids,
        strategy=strategy,
        exposure_format=exposure_format,
    )


async def delete_rule(
    rule_id: int, session: AsyncSession = Depends(get_session)
) -> DeleteResponse:
    rule = await session.get(RoutingRule, rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="Routing rule not found")
    if _is_default_rule_group(rule.group_name):
        raise HTTPException(
            status_code=400,
            detail="Default rule group cannot be deleted",
        )

    target_key_ids, _ = _deserialize_rule_config(rule.target_key_ids_json)
    before_snapshot = {
        **audit_snapshot(rule),
        "target_key_ids": target_key_ids,
    }
    resource_name = rule.group_name
    await _sync_api_key_groups_for_rule_targets(
        session=session,
        group_name=rule.group_name,
        previous_target_key_ids=target_key_ids,
        current_target_key_ids=[],
    )

    await session.delete(rule)
    await record_audit_log(
        session,
        action="delete",
        resource_type="routing_rule",
        resource_id=rule.id,
        resource_name=resource_name,
        before=before_snapshot,
    )
    await session.commit()
    return DeleteResponse()


# ============ Factory Access Keys (对外访问 Key) ============


def _issue_legacy_rule_access_key() -> str:
    import secrets

    return f"rk-{secrets.token_urlsafe(24)}"


async def create_rule_access_key(
    rule_id: int,
    payload: RuleAccessKeyCreate,
    session: AsyncSession = Depends(get_session),
) -> RuleAccessKeyIssueOut:
    rule = await session.get(RoutingRule, rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="Routing rule not found")

    raw_key = _issue_legacy_rule_access_key()
    item = FactoryAccessKey(
        name=payload.name,
        key=hash_access_key(raw_key),
        key_preview=access_key_preview(raw_key),
        is_active=True,
    )
    item.rule_groups = [rule.group_name]
    session.add(item)
    await session.flush()
    await record_audit_log(
        session,
        action="create",
        resource_type="factory_access_key",
        resource_id=item.id,
        resource_name=item.name,
        after=item,
    )
    await session.commit()
    await session.refresh(item)
    return RuleAccessKeyIssueOut(
        id=item.id,
        rule_id=rule.id,
        name=item.name,
        key_preview=item.key_preview or access_key_preview(raw_key),
        key=raw_key,
        is_active=item.is_active,
        created_at=item.created_at,
    )


async def list_rule_access_keys(
    rule_id: int,
    session: AsyncSession = Depends(get_session),
) -> list[RuleAccessKeyOut]:
    rule = await session.get(RoutingRule, rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="Routing rule not found")

    result = await session.execute(
        select(FactoryAccessKey).order_by(FactoryAccessKey.id.desc())
    )
    group_name = _normalize_rule_group_key(rule.group_name).lower()
    items = [
        item
        for item in result.scalars().all()
        if any(_normalize_rule_group_key(group).lower() == group_name for group in item.rule_groups)
    ]
    return [
        RuleAccessKeyOut(
            id=item.id,
            rule_id=rule.id,
            name=item.name,
            key_preview=_factory_access_key_preview(item),
            key=None,
            is_active=item.is_active,
            created_at=item.created_at,
        )
        for item in items
    ]


async def list_factory_access_keys(
    session: AsyncSession = Depends(get_session),
) -> list[FactoryAccessKeyOut]:
    """列出所有对外访问 Key。"""
    result = await session.execute(
        select(FactoryAccessKey).order_by(FactoryAccessKey.id.desc())
    )
    items = result.scalars().all()
    return [
        FactoryAccessKeyOut(
            id=item.id,
            name=item.name,
            key_preview=_factory_access_key_preview(item),
            key=None,
            rule_groups=item.rule_groups,
            is_active=item.is_active,
            created_at=item.created_at,
        )
        for item in items
    ]


async def create_factory_access_key(
    payload: FactoryAccessKeyCreate,
    session: AsyncSession = Depends(get_session),
) -> FactoryAccessKeyIssueOut:
    """创建对外访问 Key。"""
    import secrets

    raw_key = f"fk-{secrets.token_urlsafe(24)}"
    groups = payload.rule_groups or ["default"]
    item = FactoryAccessKey(
        name=payload.name,
        key=hash_access_key(raw_key),
        key_preview=access_key_preview(raw_key),
        is_active=True,
    )
    item.rule_groups = groups
    session.add(item)
    await session.flush()
    await record_audit_log(
        session,
        action="create",
        resource_type="factory_access_key",
        resource_id=item.id,
        resource_name=item.name,
        after=item,
    )
    await session.commit()
    await session.refresh(item)
    return FactoryAccessKeyIssueOut(
        id=item.id,
        name=item.name,
        key=raw_key,
        rule_groups=item.rule_groups,
        is_active=item.is_active,
        created_at=item.created_at,
    )


async def update_factory_access_key(
    key_id: int,
    payload: FactoryAccessKeyUpdate,
    session: AsyncSession = Depends(get_session),
) -> FactoryAccessKeyOut:
    """更新对外访问 Key。"""
    item = await session.get(FactoryAccessKey, key_id)
    if not item:
        raise HTTPException(status_code=404, detail="Factory access key not found")
    before_snapshot = audit_snapshot(item)
    data = payload.model_dump(exclude_unset=True)
    if not data:
        raise HTTPException(status_code=400, detail="No fields to update")
    if "rule_groups" in data:
        item.rule_groups = data.pop("rule_groups")
    for field, value in data.items():
        setattr(item, field, value)
    await record_audit_log(
        session,
        action="update",
        resource_type="factory_access_key",
        resource_id=item.id,
        resource_name=item.name,
        before=before_snapshot,
        after=item,
    )
    await session.commit()
    await session.refresh(item)
    return FactoryAccessKeyOut(
        id=item.id,
        name=item.name,
        key_preview=_factory_access_key_preview(item),
        key=None,
        rule_groups=item.rule_groups,
        is_active=item.is_active,
        created_at=item.created_at,
    )


async def rotate_factory_access_key(
    key_id: int, session: AsyncSession = Depends(get_session)
) -> FactoryAccessKeyIssueOut:
    """轮换对外访问 Key。"""
    import secrets

    item = await session.get(FactoryAccessKey, key_id)
    if not item:
        raise HTTPException(status_code=404, detail="Factory access key not found")
    before_snapshot = audit_snapshot(item)
    raw_key = f"fk-{secrets.token_urlsafe(24)}"
    item.key = hash_access_key(raw_key)
    item.key_preview = access_key_preview(raw_key)
    await record_audit_log(
        session,
        action="rotate",
        resource_type="factory_access_key",
        resource_id=item.id,
        resource_name=item.name,
        before=before_snapshot,
        after=item,
    )
    await session.commit()
    await session.refresh(item)
    return FactoryAccessKeyIssueOut(
        id=item.id,
        name=item.name,
        key=raw_key,
        rule_groups=item.rule_groups,
        is_active=item.is_active,
        created_at=item.created_at,
    )


async def delete_factory_access_key(
    key_id: int, session: AsyncSession = Depends(get_session)
) -> DeleteResponse:
    """删除对外访问 Key。"""
    item = await session.get(FactoryAccessKey, key_id)
    if not item:
        raise HTTPException(status_code=404, detail="Factory access key not found")
    before_snapshot = audit_snapshot(item)
    resource_name = item.name
    await session.delete(item)
    await record_audit_log(
        session,
        action="delete",
        resource_type="factory_access_key",
        resource_id=item.id,
        resource_name=resource_name,
        before=before_snapshot,
    )
    await session.commit()
    return DeleteResponse()


async def scan_rule_models(
    pattern: str = Query(..., min_length=1),
    session: AsyncSession = Depends(get_session),
) -> list[str]:
    try:
        matcher = compile_model_pattern(pattern)
    except UnsafeModelPatternError as exc:
        raise HTTPException(status_code=400, detail="Invalid model pattern") from exc
    result = await session.execute(
        select(ModelMap.model_alias).distinct().order_by(ModelMap.model_alias)
    )
    aliases = [row[0] for row in result.all()]
    return [alias for alias in aliases if matcher.match(alias)]


async def list_model_maps(
    endpoint_id: int | None = Query(default=None),
    model_alias: str | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
) -> list[ModelMapOut]:
    stmt = select(ModelMap).order_by(ModelMap.id)
    if endpoint_id is not None:
        stmt = stmt.where(ModelMap.endpoint_id == endpoint_id)
    if model_alias is not None:
        stmt = stmt.where(ModelMap.model_alias == model_alias)
    result = await session.execute(stmt)
    return result.scalars().all()


async def create_model_map(
    payload: ModelMapCreate, session: AsyncSession = Depends(get_session)
) -> ModelMapOut:
    model_map = ModelMap(**payload.model_dump(), probe_managed=False)
    session.add(model_map)
    await session.flush()
    await record_audit_log(
        session,
        action="create",
        resource_type="model_map",
        resource_id=model_map.id,
        resource_name=_model_map_resource_name(model_map),
        after=model_map,
    )
    await session.commit()
    await session.refresh(model_map)
    return model_map


async def update_model_map(
    model_map_id: int,
    payload: ModelMapUpdate,
    session: AsyncSession = Depends(get_session),
) -> ModelMapOut:
    model_map = await session.get(ModelMap, model_map_id)
    if not model_map:
        raise HTTPException(status_code=404, detail="Model map not found")
    before_snapshot = audit_snapshot(model_map)
    data = payload.model_dump(exclude_unset=True)
    if not data:
        raise HTTPException(status_code=400, detail="No fields to update")
    for field, value in data.items():
        setattr(model_map, field, value)
    # 用户手动保存后，条目转为手动维护，不再被自动探测覆盖。
    model_map.probe_managed = False
    await record_audit_log(
        session,
        action="update",
        resource_type="model_map",
        resource_id=model_map.id,
        resource_name=_model_map_resource_name(model_map),
        before=before_snapshot,
        after=model_map,
    )
    await session.commit()
    await session.refresh(model_map)
    return model_map


async def delete_model_map(
    model_map_id: int, session: AsyncSession = Depends(get_session)
) -> DeleteResponse:
    model_map = await session.get(ModelMap, model_map_id)
    if not model_map:
        raise HTTPException(status_code=404, detail="Model map not found")
    before_snapshot = audit_snapshot(model_map)
    resource_name = _model_map_resource_name(model_map)
    await session.delete(model_map)
    await record_audit_log(
        session,
        action="delete",
        resource_type="model_map",
        resource_id=model_map.id,
        resource_name=resource_name,
        before=before_snapshot,
    )
    await session.commit()
    return DeleteResponse()


async def list_audit_logs(
    limit: int = Query(default=100, ge=1, le=1000),
    resource_type: str | None = Query(default=None),
    action: str | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
) -> list[AuditLogOut]:
    stmt = select(AuditLog).order_by(AuditLog.id.desc()).limit(limit)
    if resource_type:
        stmt = stmt.where(AuditLog.resource_type == resource_type)
    if action:
        stmt = stmt.where(AuditLog.action == action)
    result = await session.execute(stmt)
    return [_build_audit_log_out(log) for log in result.scalars().all()]


async def list_request_logs(
    limit: int = Query(default=100, ge=1, le=1000),
    model_alias: str | None = Query(default=None),
    endpoint_id: int | None = Query(default=None),
    api_key_id: int | None = Query(default=None),
    status_code: int | None = Query(default=None),
    since: str | None = Query(default=None),
    until: str | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
) -> list[RequestLogOut]:
    stmt = select(RequestLog)
    if model_alias:
        stmt = stmt.where(RequestLog.model_alias == model_alias)
    if endpoint_id is not None:
        stmt = stmt.where(RequestLog.endpoint_id == endpoint_id)
    if api_key_id is not None:
        stmt = stmt.where(RequestLog.api_key_id == api_key_id)
    if status_code is not None:
        stmt = stmt.where(RequestLog.status_code == status_code)
    since_dt = _parse_iso_datetime(since)
    if since_dt:
        stmt = stmt.where(RequestLog.created_at >= since_dt)
    until_dt = _parse_iso_datetime(until)
    if until_dt:
        stmt = stmt.where(RequestLog.created_at <= until_dt)
    stmt = stmt.order_by(RequestLog.created_at.desc()).limit(limit)
    result = await session.execute(stmt)
    return result.scalars().all()


async def list_request_attempt_logs(
    limit: int = Query(default=200, ge=1, le=2000),
    request_id: str | None = Query(default=None),
    trace_id: str | None = Query(default=None),
    model_alias: str | None = Query(default=None),
    endpoint_id: int | None = Query(default=None),
    api_key_id: int | None = Query(default=None),
    outcome: str | None = Query(default=None),
    since: str | None = Query(default=None),
    until: str | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
) -> list[RequestAttemptLogOut]:
    stmt = select(RequestAttemptLog)
    if request_id:
        stmt = stmt.where(RequestAttemptLog.request_id == request_id)
    if trace_id:
        stmt = stmt.where(RequestAttemptLog.trace_id == trace_id)
    if model_alias:
        stmt = stmt.where(RequestAttemptLog.model_alias == model_alias)
    if endpoint_id is not None:
        stmt = stmt.where(RequestAttemptLog.endpoint_id == endpoint_id)
    if api_key_id is not None:
        stmt = stmt.where(RequestAttemptLog.api_key_id == api_key_id)
    if outcome:
        stmt = stmt.where(RequestAttemptLog.outcome == outcome)
    since_dt = _parse_iso_datetime(since)
    if since_dt:
        stmt = stmt.where(RequestAttemptLog.created_at >= since_dt)
    until_dt = _parse_iso_datetime(until)
    if until_dt:
        stmt = stmt.where(RequestAttemptLog.created_at <= until_dt)
    stmt = stmt.order_by(RequestAttemptLog.created_at.desc(), RequestAttemptLog.id.desc()).limit(limit)
    result = await session.execute(stmt)
    return result.scalars().all()
