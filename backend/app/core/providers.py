from __future__ import annotations

from collections.abc import Sequence


def normalize_provider_name(value: object) -> str:
    if not isinstance(value, str):
        return "openai"
    normalized = value.strip().lower()
    return normalized or "openai"


def normalize_provider_filters(
    provider_filters: str | Sequence[str] | set[str] | None,
) -> set[str] | None:
    if provider_filters is None:
        return None
    if isinstance(provider_filters, str):
        return {normalize_provider_name(provider_filters)}
    filters = {
        normalize_provider_name(provider)
        for provider in provider_filters
        if isinstance(provider, str) and provider.strip()
    }
    return filters or None
