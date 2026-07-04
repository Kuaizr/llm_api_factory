from fastapi import Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.route_helpers import _resolve_allowed_rule_groups_from_token
from app.core.providers import normalize_provider_filters, normalize_provider_name
from app.db.models import Endpoint, ModelMap, RoutingRule
from app.services.model_patterns import model_pattern_matches


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
