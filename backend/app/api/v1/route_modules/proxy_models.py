import json
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.route_helpers import _resolve_allowed_rule_groups_from_token
from app.api.v1.route_proxy_helpers import (
    _apply_oauth_access_token,
    _build_target_url,
    _build_upstream_headers,
)
from app.core.http_client import get_http_client
from app.core.providers import normalize_provider_filters, normalize_provider_name
from app.core.redis import get_redis
from app.db.models import Endpoint, ModelMap, RoutingRule
from app.services.agent_transport import AgentRequest, AgentUnavailableError, get_agent_manager
from app.services.circuit_breaker import CircuitBreaker
from app.services.model_patterns import model_pattern_matches
from app.services.notifications import get_notifier
from app.services.router import ModelRouter, RouteCandidate


def build_models_response(model_aliases: list[str]) -> dict[str, object]:
    models = [
        {"id": model_alias, "object": "model", "owned_by": "proxy"}
        for model_alias in model_aliases
    ]
    return {"object": "list", "data": models}


def build_gemini_models_response(model_aliases: list[str]) -> dict[str, object]:
    models = [
        {
            "name": f"models/{model_alias}",
            "version": model_alias,
            "displayName": model_alias,
            "supportedGenerationMethods": [
                "generateContent",
                "streamGenerateContent",
                "countTokens",
            ],
        }
        for model_alias in model_aliases
    ]
    return {"models": models}


def _model_match_tokens(value: object, *, provider: str) -> set[str]:
    if not isinstance(value, str) or not value.strip():
        return set()
    cleaned = value.strip()
    tokens = {cleaned, cleaned.lower()}
    if provider == "gemini":
        if cleaned.startswith("models/"):
            without_prefix = cleaned.removeprefix("models/")
            tokens.update({without_prefix, without_prefix.lower()})
        else:
            tokens.update({f"models/{cleaned}", f"models/{cleaned.lower()}"})
    return tokens


def _allowed_model_tokens(
    model_aliases: set[str],
    candidates: list[RouteCandidate],
    *,
    provider: str,
) -> set[str]:
    tokens: set[str] = set()
    for model_name in model_aliases:
        tokens.update(_model_match_tokens(model_name, provider=provider))
    for candidate in candidates:
        tokens.update(_model_match_tokens(candidate.real_model, provider=provider))
    return tokens


def _model_item_tokens(item: object, *, provider: str) -> set[str]:
    if not isinstance(item, dict):
        return set()
    tokens: set[str] = set()
    if provider == "gemini":
        tokens.update(_model_match_tokens(item.get("name"), provider=provider))
        tokens.update(_model_match_tokens(item.get("id"), provider=provider))
        return tokens
    tokens.update(_model_match_tokens(item.get("id"), provider=provider))
    tokens.update(_model_match_tokens(item.get("name"), provider=provider))
    return tokens


def _models_payload_key(provider: str, payload: dict[str, object]) -> str | None:
    if provider == "gemini" and isinstance(payload.get("models"), list):
        return "models"
    if isinstance(payload.get("data"), list):
        return "data"
    if isinstance(payload.get("models"), list):
        return "models"
    return None


def _filter_upstream_models_payload(
    provider: str,
    payload: dict[str, object],
    allowed_tokens: set[str],
) -> dict[str, object] | None:
    list_key = _models_payload_key(provider, payload)
    if list_key is None:
        return None

    filtered_items: list[object] = []
    for item in payload.get(list_key, []):
        if _model_item_tokens(item, provider=provider) & allowed_tokens:
            filtered_items.append(item)

    filtered = dict(payload)
    filtered[list_key] = filtered_items
    return filtered


def _merge_models_payloads(
    provider: str,
    payloads: list[dict[str, object]],
) -> dict[str, object] | None:
    if not payloads:
        return None

    merged = dict(payloads[0])
    list_key = _models_payload_key(provider, merged)
    if list_key is None:
        return None

    seen: set[str] = set()
    items: list[object] = []
    for payload in payloads:
        payload_key = _models_payload_key(provider, payload)
        if payload_key is None:
            continue
        for item in payload.get(payload_key, []):
            item_tokens = sorted(_model_item_tokens(item, provider=provider))
            identity = item_tokens[0] if item_tokens else repr(item)
            if identity in seen:
                continue
            seen.add(identity)
            items.append(item)

    merged[list_key] = items
    if len(payloads) > 1:
        for pagination_key in (
            "nextPageToken",
            "next_page_token",
            "first_id",
            "last_id",
            "has_more",
        ):
            merged.pop(pagination_key, None)
    return merged


def _append_query_param(url: str, key: str, value: str) -> str:
    parts = urlsplit(url)
    query = [
        (item_key, item_value)
        for item_key, item_value in parse_qsl(parts.query)
        if item_key != key
    ]
    query.append((key, value))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


async def _fetch_single_models_payload(
    *,
    candidate: RouteCandidate,
    method: str,
    url: str,
    headers: dict[str, str],
    client: object,
) -> dict[str, object] | None:
    agent_name = candidate.agent_name
    if agent_name:
        try:
            agent_response = await get_agent_manager().send_request(
                agent_name,
                AgentRequest(
                    method=method,
                    url=url,
                    headers=headers,
                    body=b"",
                    stream=False,
                ),
            )
        except AgentUnavailableError:
            return None
        status_code = agent_response.status_code
        content = agent_response.body
    else:
        try:
            response = await client.request(method, url, headers=headers)
        except Exception:
            return None
        try:
            status_code = response.status_code
            content = response.content
        finally:
            await response.aclose()

    if status_code >= 400:
        return None
    try:
        payload = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def _next_models_page_url(provider: str, current_url: str, payload: dict[str, object]) -> str | None:
    if provider == "gemini":
        token = payload.get("nextPageToken")
        if isinstance(token, str) and token.strip():
            return _append_query_param(current_url, "pageToken", token.strip())
        return None

    has_more = payload.get("has_more")
    last_id = payload.get("last_id")
    if has_more is True and isinstance(last_id, str) and last_id.strip():
        return _append_query_param(current_url, "after", last_id.strip())
    return None


async def _fetch_models_payloads_for_candidate(
    request: Request,
    candidate: RouteCandidate,
    *,
    provider: str,
    path_prefix: str,
    redis: object,
    client: object,
) -> list[dict[str, object]]:
    headers = _build_upstream_headers(
        request.headers,
        candidate.endpoint,
        candidate.api_key.key,
    )
    headers, _ = await _apply_oauth_access_token(
        headers,
        candidate.endpoint,
        redis,
        client,
    )
    url = _build_target_url(
        candidate.endpoint.base_url,
        request,
        path_prefix=path_prefix,
        endpoint=candidate.endpoint,
    )

    payloads: list[dict[str, object]] = []
    next_url: str | None = url
    visited_urls: set[str] = set()
    for _ in range(20):
        if not next_url or next_url in visited_urls:
            break
        visited_urls.add(next_url)
        payload = await _fetch_single_models_payload(
            candidate=candidate,
            method="GET",
            url=next_url,
            headers=headers,
            client=client,
        )
        if payload is None:
            break
        payloads.append(payload)
        next_url = _next_models_page_url(provider, next_url, payload)
    return payloads


async def _matching_rule_groups_by_model_alias(
    session: AsyncSession,
    model_aliases: set[str],
    allowed_groups: list[str],
) -> dict[str, list[str]]:
    matched: dict[str, list[str]] = {model_alias: [] for model_alias in model_aliases}
    if any(group.lower() == "default" for group in allowed_groups):
        for model_alias in model_aliases:
            matched[model_alias].append("default")

    non_default_groups = [group for group in allowed_groups if group.lower() != "default"]
    if not non_default_groups:
        return matched

    result = await session.execute(
        select(RoutingRule)
        .where(
            RoutingRule.is_active.is_(True),
            RoutingRule.group_name.in_(non_default_groups),
        )
        .order_by(RoutingRule.priority.desc(), RoutingRule.id)
    )
    rules = result.scalars().all()
    for model_alias in model_aliases:
        seen = {group.lower() for group in matched[model_alias]}
        for group in non_default_groups:
            group_key = group.lower()
            if group_key in seen:
                continue
            if any(
                rule.group_name.lower() == group_key
                and model_pattern_matches(rule.model_pattern, model_alias)
                for rule in rules
            ):
                matched[model_alias].append(group)
                seen.add(group_key)
    return matched


async def _collect_accessible_model_candidates(
    request: Request,
    session: AsyncSession,
    *,
    provider_filter: str | tuple[str, ...] | None,
    provider_filter_fallback_to_any: bool,
) -> tuple[set[str], list[RouteCandidate]]:
    allowed_groups = await _resolve_allowed_rule_groups_from_token(session, request)
    model_aliases = set(
        await list_accessible_model_aliases(
            request,
            session,
            provider_filter=provider_filter,
            provider_filter_fallback_to_any=provider_filter_fallback_to_any,
        )
    )
    if not model_aliases:
        return set(), []

    redis = await get_redis()
    router_service = ModelRouter(CircuitBreaker(redis, notifier=get_notifier()))
    candidates_by_key: dict[tuple[int, str], RouteCandidate] = {}
    matching_groups = await _matching_rule_groups_by_model_alias(
        session,
        model_aliases,
        allowed_groups,
    )
    for model_alias in sorted(model_aliases):
        for group in matching_groups.get(model_alias, []):
            candidates, _ = await router_service.get_candidates(
                session,
                model_alias,
                group,
                provider_filters=provider_filter,
                provider_filter_fallback_to_any=provider_filter_fallback_to_any,
                allow_unmapped_fallback=False,
            )
            for candidate in candidates:
                identity = (candidate.api_key.id, candidate.real_model)
                candidates_by_key.setdefault(identity, candidate)
    return model_aliases, list(candidates_by_key.values())


async def list_upstream_models_filtered(
    request: Request,
    session: AsyncSession,
    *,
    provider: str,
    path_prefix: str,
    provider_filter: str | tuple[str, ...] | None = None,
    provider_filter_fallback_to_any: bool = False,
) -> dict[str, object] | None:
    normalized_provider = normalize_provider_name(provider)
    model_aliases, candidates = await _collect_accessible_model_candidates(
        request,
        session,
        provider_filter=provider_filter,
        provider_filter_fallback_to_any=provider_filter_fallback_to_any,
    )
    if not candidates:
        return None

    allowed_tokens = _allowed_model_tokens(
        model_aliases,
        candidates,
        provider=normalized_provider,
    )
    if not allowed_tokens:
        return None

    redis = await get_redis()
    client = await get_http_client()
    filtered_payloads: list[dict[str, object]] = []
    queried_keys: set[int] = set()
    for candidate in candidates:
        if candidate.api_key.id in queried_keys:
            continue
        queried_keys.add(candidate.api_key.id)
        try:
            payloads = await _fetch_models_payloads_for_candidate(
                request,
                candidate,
                provider=normalized_provider,
                path_prefix=path_prefix,
                redis=redis,
                client=client,
            )
        except Exception:
            continue
        for payload in payloads:
            filtered = _filter_upstream_models_payload(
                normalized_provider,
                payload,
                allowed_tokens,
            )
            if filtered is not None:
                filtered_payloads.append(filtered)

    return _merge_models_payloads(normalized_provider, filtered_payloads)


async def list_accessible_model_aliases(
    request: Request,
    session: AsyncSession,
    *,
    provider_filter: str | tuple[str, ...] | None = None,
    provider_filter_fallback_to_any: bool = False,
) -> list[str]:
    allowed_groups = await _resolve_allowed_rule_groups_from_token(session, request)
    provider_filters = normalize_provider_filters(provider_filter)

    result = await session.execute(
        select(ModelMap.model_alias, Endpoint.provider)
        .join(Endpoint, ModelMap.endpoint_id == Endpoint.id)
        .where(Endpoint.is_active.is_(True))
        .distinct()
    )
    rows = result.all()
    normalized_rows = [
        (str(model_alias), normalize_provider_name(provider))
        for model_alias, provider in rows
        if model_alias
    ]

    filtered_rows = (
        [row for row in normalized_rows if row[1] in provider_filters]
        if provider_filters
        else normalized_rows
    )
    if provider_filters and not filtered_rows and provider_filter_fallback_to_any:
        filtered_rows = normalized_rows

    model_aliases = sorted({model_alias for model_alias, _ in filtered_rows})
    if not model_aliases:
        return []

    if any(group.lower() == "default" for group in allowed_groups):
        return model_aliases

    non_default_groups = [group for group in allowed_groups if group.lower() != "default"]
    if not non_default_groups:
        return []

    rule_result = await session.execute(
        select(RoutingRule)
        .where(
            RoutingRule.is_active.is_(True),
            RoutingRule.group_name.in_(non_default_groups),
        )
        .order_by(RoutingRule.priority.desc(), RoutingRule.id)
    )
    rules_by_group: dict[str, list[RoutingRule]] = {}
    for rule in rule_result.scalars().all():
        rules_by_group.setdefault(rule.group_name.lower(), []).append(rule)

    allowed_aliases: set[str] = set()
    for model_alias in model_aliases:
        for group in non_default_groups:
            if any(
                model_pattern_matches(rule.model_pattern, model_alias)
                for rule in rules_by_group.get(group.lower(), [])
            ):
                allowed_aliases.add(model_alias)
                break

    return sorted(allowed_aliases)


async def list_models(
    request: Request,
    session: AsyncSession,
    *,
    provider_filter: str | tuple[str, ...] | None = None,
    provider_filter_fallback_to_any: bool = False,
) -> dict[str, object]:
    model_aliases = await list_accessible_model_aliases(
        request,
        session,
        provider_filter=provider_filter,
        provider_filter_fallback_to_any=provider_filter_fallback_to_any,
    )
    return build_models_response(model_aliases)
