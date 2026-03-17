from dataclasses import dataclass
from datetime import datetime, timezone
import json
import re
from typing import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import APIKey, Endpoint, ModelMap, RoutingRule
from app.services.circuit_breaker import CircuitBreaker


@dataclass(frozen=True)
class RouteCandidate:
    api_key: APIKey
    endpoint: Endpoint
    real_model: str


DEFAULT_RULE_STRATEGY = "weighted_round_robin"

_wrr_state: dict[str, dict[int, int]] = {}


class ModelRouter:
    def __init__(self, circuit_breaker: CircuitBreaker) -> None:
        self.circuit_breaker = circuit_breaker

    async def get_candidates(
        self, session: AsyncSession, model_alias: str, rule_group: str
    ) -> tuple[list[RouteCandidate], str]:
        target_key_ids, strategy = await self._select_rule_targets(
            session, model_alias, rule_group
        )
        effective_group = rule_group
        if not target_key_ids and rule_group != "default":
            fallback_targets, fallback_strategy = await self._select_rule_targets(
                session, model_alias, "default"
            )
            target_key_ids = fallback_targets
            strategy = fallback_strategy
            effective_group = "default"
        stmt = (
            select(APIKey, Endpoint, ModelMap)
            .join(Endpoint, APIKey.endpoint_id == Endpoint.id)
            .join(ModelMap, ModelMap.endpoint_id == Endpoint.id)
            .where(
                ModelMap.model_alias == model_alias,
                APIKey.is_active.is_(True),
                Endpoint.is_active.is_(True),
            )
            .order_by(APIKey.id)
        )
        if target_key_ids:
            stmt = stmt.where(APIKey.id.in_(target_key_ids))

        result = await session.execute(stmt)
        candidates: list[RouteCandidate] = []
        for api_key, endpoint, model_map in result.all():
            if not target_key_ids:
                if hasattr(api_key, "in_rule_group"):
                    if not api_key.in_rule_group(effective_group):
                        continue
                elif getattr(api_key, "rule_group", "default") != effective_group:
                    continue
            if not await self._is_key_available(api_key):
                continue
            candidates.append(
                RouteCandidate(
                    api_key=api_key, endpoint=endpoint, real_model=model_map.real_model
                )
            )
        context_key = f"{model_alias}:{effective_group}:{strategy}"
        ordered = self._order_candidates(
            candidates, strategy, context_key, target_key_ids
        )
        return ordered, effective_group

    async def _is_key_available(self, api_key: APIKey) -> bool:
        today = datetime.now(timezone.utc).date()
        used_today = api_key.used_today or 0
        if api_key.used_today_date != today:
            used_today = 0
        if api_key.daily_limit is not None and used_today >= api_key.daily_limit:
            return False
        return await self.circuit_breaker.is_available(api_key.id)

    @staticmethod
    def _candidate_weight(candidate: RouteCandidate) -> int:
        return max(getattr(candidate.api_key, "weight", 1), 1)

    async def _select_rule_targets(
        self, session: AsyncSession, model_alias: str, rule_group: str
    ) -> tuple[list[int], str]:
        result = await session.execute(
            select(RoutingRule)
            .where(RoutingRule.group_name == rule_group, RoutingRule.is_active.is_(True))
            .order_by(RoutingRule.priority.desc(), RoutingRule.id)
        )
        rules = result.scalars().all()
        for rule in rules:
            try:
                if re.match(rule.model_pattern, model_alias):
                    return self._parse_rule_config(rule.target_key_ids_json)
            except re.error:
                continue
        return [], DEFAULT_RULE_STRATEGY

    @staticmethod
    def _parse_rule_config(raw: str) -> tuple[list[int], str]:
        if not raw:
            return [], DEFAULT_RULE_STRATEGY
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return [], DEFAULT_RULE_STRATEGY
        if isinstance(data, list):
            return ModelRouter._parse_key_ids(data), DEFAULT_RULE_STRATEGY
        if isinstance(data, dict):
            target_key_ids = ModelRouter._parse_key_ids(
                data.get("target_key_ids", [])
            )
            strategy = data.get("strategy") or DEFAULT_RULE_STRATEGY
            if not isinstance(strategy, str):
                strategy = str(strategy)
            return target_key_ids, strategy
        return [], DEFAULT_RULE_STRATEGY

    @staticmethod
    def _parse_key_ids(data: object) -> list[int]:
        if not isinstance(data, list):
            return []
        parsed: list[int] = []
        for item in data:
            if isinstance(item, int):
                parsed.append(item)
            elif isinstance(item, str) and item.isdigit():
                parsed.append(int(item))
        return parsed

    @staticmethod
    def _pool_key(candidates: Sequence[RouteCandidate], context: str) -> str:
        parts = [
            f"{candidate.api_key.id}:{ModelRouter._candidate_weight(candidate)}"
            for candidate in sorted(candidates, key=lambda item: item.api_key.id)
        ]
        base = "|".join(parts)
        return f"{context}|{base}" if context else base

    @staticmethod
    def _select_wrr_candidate(
        candidates: Sequence[RouteCandidate], context: str
    ) -> RouteCandidate:
        ordered = sorted(candidates, key=lambda item: item.api_key.id)
        weights = {item.api_key.id: ModelRouter._candidate_weight(item) for item in ordered}
        pool_key = ModelRouter._pool_key(ordered, context)
        state = _wrr_state.get(pool_key)
        if state is None or set(state.keys()) != set(weights.keys()):
            state = {candidate_id: 0 for candidate_id in weights}
            _wrr_state[pool_key] = state

        total_weight = sum(weights.values())
        selected: RouteCandidate | None = None
        selected_current: int | None = None
        for candidate in ordered:
            candidate_id = candidate.api_key.id
            state[candidate_id] += weights[candidate_id]
            current = state[candidate_id]
            if selected is None or current > selected_current:
                selected = candidate
                selected_current = current

        if selected is None:
            return ordered[0]
        state[selected.api_key.id] -= total_weight
        return selected

    @staticmethod
    def _order_candidates(
        candidates: Sequence[RouteCandidate],
        strategy: str = DEFAULT_RULE_STRATEGY,
        context: str = "",
        target_key_ids: list[int] | None = None,
    ) -> list[RouteCandidate]:
        if not candidates:
            return []
        normalized = strategy or DEFAULT_RULE_STRATEGY
        if normalized == "sequential":
            if target_key_ids:
                key_order = {key_id: index for index, key_id in enumerate(target_key_ids)}
                return sorted(
                    candidates,
                    key=lambda candidate: key_order.get(candidate.api_key.id, len(target_key_ids)),
                )
            return sorted(candidates, key=lambda candidate: candidate.api_key.id)
        selected = ModelRouter._select_wrr_candidate(candidates, context)
        remaining = [
            candidate
            for candidate in candidates
            if candidate.api_key.id != selected.api_key.id
        ]
        remaining.sort(
            key=lambda candidate: (
                ModelRouter._candidate_weight(candidate),
                candidate.api_key.id,
            ),
            reverse=True,
        )
        return [selected, *remaining]
