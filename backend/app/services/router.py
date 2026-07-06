from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from typing import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.providers import normalize_provider_filters, normalize_provider_name
from app.core.route_exposure import (
    DEFAULT_EXPOSURE_FORMAT,
    exposure_format_matches,
    normalize_exposure_format,
)
from app.db.models import APIKey, Agent, Endpoint, ModelMap, RoutingRule
from app.core.timezone import app_today
from app.services.agent_transport import get_agent_manager
from app.services.circuit_breaker import CircuitBreaker
from app.services.model_patterns import model_pattern_matches


@dataclass(frozen=True)
class AgentRouteState:
    is_active: bool
    is_draining: bool


@dataclass(frozen=True)
class RouteCandidate:
    api_key: APIKey
    endpoint: Endpoint
    real_model: str

    @property
    def execution_mode(self) -> str:
        access_mode = str(getattr(self.endpoint, "access_mode", "") or "").strip()
        if access_mode == "via_agent":
            return "via_agent"
        if not access_mode and getattr(self.endpoint, "agent_node", None):
            return "via_agent"
        return "direct"

    @property
    def agent_name(self) -> str | None:
        if self.execution_mode != "via_agent":
            return None
        name = getattr(self.endpoint, "agent_node", None)
        if not name:
            return None
        trimmed = str(name).strip()
        return trimmed or None


DEFAULT_RULE_STRATEGY = "weighted_round_robin"
SEQUENTIAL_STATE_TTL_SECONDS = 86400
WRR_STATE_MAX_POOLS = 1024
RPM_STATE_TTL_SECONDS = 120

_wrr_state: OrderedDict[str, dict[int, int]] = OrderedDict()


class ModelRouter:
    def __init__(self, circuit_breaker: CircuitBreaker) -> None:
        self.circuit_breaker = circuit_breaker
        self._last_sequential_state_key: str | None = None

    async def get_candidates(
        self,
        session: AsyncSession,
        model_alias: str,
        rule_group: str,
        *,
        provider_filters: str | Sequence[str] | set[str] | None = None,
        provider_filter_fallback_to_any: bool = False,
        allow_unmapped_fallback: bool = False,
        allow_default_rule_fallback: bool = True,
        exposure_format: str = DEFAULT_EXPOSURE_FORMAT,
    ) -> tuple[list[RouteCandidate], str]:
        target_key_ids, strategy = await self._select_rule_targets(
            session, model_alias, rule_group, exposure_format=exposure_format
        )
        effective_group = rule_group
        if (
            not target_key_ids
            and rule_group != "default"
            and allow_default_rule_fallback
        ):
            fallback_targets, fallback_strategy = await self._select_rule_targets(
                session,
                model_alias,
                "default",
                exposure_format=exposure_format,
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
        rows = result.all()
        all_candidates = [
            RouteCandidate(
                api_key=api_key, endpoint=endpoint, real_model=model_map.real_model
            )
            for api_key, endpoint, model_map in rows
        ]
        candidates = await self._filter_available_candidates(
            session, all_candidates, effective_group, target_key_ids=target_key_ids
        )
        candidates = self._filter_provider_candidates(
            candidates,
            provider_filters,
            fallback_to_any=provider_filter_fallback_to_any,
        )

        if not candidates and allow_unmapped_fallback:
            fallback_candidates = await self._load_unmapped_candidates(
                session, model_alias, effective_group
            )
            candidates = self._filter_provider_candidates(
                fallback_candidates,
                provider_filters,
                fallback_to_any=provider_filter_fallback_to_any,
            )

        ordered = await self.order_candidates(
            candidates,
            strategy,
            model_alias=model_alias,
            effective_group=effective_group,
            provider_filters=provider_filters,
            target_key_ids=target_key_ids,
        )
        return ordered, effective_group

    async def order_candidates(
        self,
        candidates: Sequence[RouteCandidate],
        strategy: str,
        *,
        model_alias: str,
        effective_group: str,
        provider_filters: str | Sequence[str] | set[str] | None = None,
        target_key_ids: list[int] | None = None,
    ) -> list[RouteCandidate]:
        context_key = f"{model_alias}:{effective_group}:{strategy}"
        normalized = strategy or DEFAULT_RULE_STRATEGY
        if normalized != "sequential":
            self._last_sequential_state_key = None
            return self._order_candidates(
                candidates, normalized, context_key, target_key_ids
            )

        state_key = self._sequential_state_key(
            model_alias=model_alias,
            effective_group=effective_group,
            provider_filters=provider_filters,
            target_key_ids=target_key_ids,
        )
        self._last_sequential_state_key = state_key
        active_key_id = await self._get_sequential_active_key_id(state_key)
        return self._order_candidates(
            candidates,
            normalized,
            context_key,
            target_key_ids,
            active_key_id=active_key_id,
        )

    async def record_candidate_success(self, candidate: RouteCandidate) -> None:
        if not self._last_sequential_state_key:
            return
        await self.circuit_breaker.redis.set(
            self._last_sequential_state_key,
            str(candidate.api_key.id),
            ex=SEQUENTIAL_STATE_TTL_SECONDS,
        )

    async def get_sequential_active_key_id(
        self,
        *,
        model_alias: str,
        effective_group: str,
        provider_filters: str | Sequence[str] | set[str] | None = None,
        target_key_ids: list[int] | None = None,
    ) -> int | None:
        state_key = self._sequential_state_key(
            model_alias=model_alias,
            effective_group=effective_group,
            provider_filters=provider_filters,
            target_key_ids=target_key_ids,
        )
        return await self._get_sequential_active_key_id(state_key)

    async def _load_unmapped_candidates(
        self,
        session: AsyncSession,
        model_alias: str,
        effective_group: str,
    ) -> list[RouteCandidate]:
        fallback_stmt = (
            select(APIKey, Endpoint)
            .join(Endpoint, APIKey.endpoint_id == Endpoint.id)
            .where(
                APIKey.is_active.is_(True),
                Endpoint.is_active.is_(True),
            )
            .order_by(APIKey.id)
        )
        result = await session.execute(fallback_stmt)
        candidates = [
            RouteCandidate(api_key=api_key, endpoint=endpoint, real_model=model_alias)
            for api_key, endpoint in result.all()
        ]
        return await self._filter_available_candidates(
            session, candidates, effective_group, target_key_ids=[]
        )

    async def _filter_available_candidates(
        self,
        session: AsyncSession,
        candidates: Sequence[RouteCandidate],
        effective_group: str,
        *,
        target_key_ids: list[int],
    ) -> list[RouteCandidate]:
        via_agent_names = {candidate.agent_name for candidate in candidates if candidate.agent_name}
        agent_state = await self._load_agent_route_state(session, via_agent_names)
        agent_manager = get_agent_manager()

        eligible: list[RouteCandidate] = []
        for candidate in candidates:
            api_key = candidate.api_key
            if not target_key_ids:
                if hasattr(api_key, "in_rule_group"):
                    if not api_key.in_rule_group(effective_group):
                        continue
                elif getattr(api_key, "rule_group", "default") != effective_group:
                    continue
            if not self._passes_key_limits(api_key):
                continue
            eligible.append(candidate)

        circuit_availability = await self.circuit_breaker.are_available(
            [candidate.api_key.id for candidate in eligible]
        )

        circuit_available: list[RouteCandidate] = []
        for candidate in eligible:
            api_key = candidate.api_key
            if not circuit_availability.get(api_key.id, True):
                continue
            circuit_available.append(candidate)

        rpm_counts = await self._load_rpm_counts(circuit_available)

        available: list[RouteCandidate] = []
        for candidate in circuit_available:
            api_key = candidate.api_key
            if not self._passes_rpm_limit(api_key, rpm_counts.get(api_key.id, 0)):
                continue
            if candidate.execution_mode == "via_agent":
                agent_name = candidate.agent_name
                if not agent_name:
                    continue
                state = agent_state.get(agent_name)
                if state is None or not state.is_active or state.is_draining:
                    continue
                if agent_manager.get(agent_name) is None:
                    continue
            available.append(candidate)
        return available

    @staticmethod
    def _passes_key_limits(api_key: APIKey) -> bool:
        today = app_today()
        used_today = getattr(api_key, "used_today", 0) or 0
        if getattr(api_key, "used_today_date", None) != today:
            used_today = 0
        daily_limit = getattr(api_key, "daily_limit", None)
        return daily_limit is None or used_today < daily_limit

    @staticmethod
    def _rpm_limit(api_key: APIKey) -> int | None:
        raw_limit = getattr(api_key, "rpm_limit", None)
        if raw_limit is None:
            return None
        try:
            return max(0, int(raw_limit))
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _rpm_state_key(api_key_id: int, now: datetime | None = None) -> str:
        timestamp = now or datetime.now(timezone.utc)
        window = timestamp.astimezone(timezone.utc).strftime("%Y%m%d%H%M")
        return f"rate:rpm:{api_key_id}:{window}"

    @classmethod
    def _passes_rpm_limit(cls, api_key: APIKey, current_count: int) -> bool:
        rpm_limit = cls._rpm_limit(api_key)
        return rpm_limit is None or current_count < rpm_limit

    async def _load_rpm_counts(
        self, candidates: Sequence[RouteCandidate]
    ) -> dict[int, int]:
        ids_and_keys = [
            (candidate.api_key.id, self._rpm_state_key(candidate.api_key.id))
            for candidate in candidates
            if self._rpm_limit(candidate.api_key) is not None
        ]
        if not ids_and_keys:
            return {}
        values = await self.circuit_breaker.redis.mget([key for _, key in ids_and_keys])
        counts: dict[int, int] = {}
        for (api_key_id, _), value in zip(ids_and_keys, values, strict=False):
            try:
                counts[api_key_id] = int(value or 0)
            except (TypeError, ValueError):
                counts[api_key_id] = 0
        return counts

    async def reserve_candidate_attempt(self, candidate: RouteCandidate) -> bool:
        rpm_limit = self._rpm_limit(candidate.api_key)
        if rpm_limit is None:
            return True
        key = self._rpm_state_key(candidate.api_key.id)
        count = await self.circuit_breaker.redis.incr(key)
        if count == 1:
            await self.circuit_breaker.redis.expire(key, RPM_STATE_TTL_SECONDS)
        return count <= rpm_limit

    @staticmethod
    def _filter_provider_candidates(
        candidates: Sequence[RouteCandidate],
        provider_filters: str | Sequence[str] | set[str] | None,
        *,
        fallback_to_any: bool,
    ) -> list[RouteCandidate]:
        filters = normalize_provider_filters(provider_filters)
        if not filters:
            return list(candidates)
        filtered = [
            candidate
            for candidate in candidates
            if normalize_provider_name(getattr(candidate.endpoint, "provider", None))
            in filters
        ]
        if filtered or not fallback_to_any:
            return filtered
        return list(candidates)

    async def _is_key_available(self, api_key: APIKey) -> bool:
        if not self._passes_key_limits(api_key):
            return False
        return await self.circuit_breaker.is_available(api_key.id)

    async def _load_agent_route_state(
        self, session: AsyncSession, agent_names: set[str | None]
    ) -> dict[str, AgentRouteState]:
        names = {str(name).strip() for name in agent_names if str(name or "").strip()}
        if not names:
            return {}
        try:
            result = await session.execute(select(Agent).where(Agent.name.in_(names)))
        except (AttributeError, AssertionError):
            return {}
        return {
            agent.name: AgentRouteState(
                is_active=bool(agent.is_active),
                is_draining=bool(getattr(agent, "is_draining", False)),
            )
            for agent in result.scalars().all()
        }

    @staticmethod
    def _candidate_weight(candidate: RouteCandidate) -> int:
        return max(getattr(candidate.api_key, "weight", 1), 1)

    async def _select_rule_targets(
        self,
        session: AsyncSession,
        model_alias: str,
        rule_group: str,
        *,
        exposure_format: str = DEFAULT_EXPOSURE_FORMAT,
    ) -> tuple[list[int], str]:
        result = await session.execute(
            select(RoutingRule)
            .where(RoutingRule.group_name == rule_group, RoutingRule.is_active.is_(True))
            .order_by(RoutingRule.priority.desc(), RoutingRule.id)
        )
        rules = result.scalars().all()
        for rule in rules:
            if not model_pattern_matches(rule.model_pattern, model_alias):
                continue
            _, _, rule_exposure_format = self._parse_rule_config_detail(
                rule.target_key_ids_json
            )
            if exposure_format_matches(rule_exposure_format, exposure_format):
                return self._parse_rule_config(rule.target_key_ids_json)
        return [], DEFAULT_RULE_STRATEGY

    @staticmethod
    def _parse_rule_config_detail(raw: str) -> tuple[list[int], str, str]:
        if not raw:
            return [], DEFAULT_RULE_STRATEGY, DEFAULT_EXPOSURE_FORMAT
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return [], DEFAULT_RULE_STRATEGY, DEFAULT_EXPOSURE_FORMAT
        if isinstance(data, list):
            return (
                ModelRouter._parse_key_ids(data),
                DEFAULT_RULE_STRATEGY,
                DEFAULT_EXPOSURE_FORMAT,
            )
        if isinstance(data, dict):
            target_key_ids = ModelRouter._parse_key_ids(
                data.get("target_key_ids", [])
            )
            strategy = data.get("strategy") or DEFAULT_RULE_STRATEGY
            if not isinstance(strategy, str):
                strategy = str(strategy)
            exposure_format = normalize_exposure_format(data.get("exposure_format"))
            return target_key_ids, strategy, exposure_format
        return [], DEFAULT_RULE_STRATEGY, DEFAULT_EXPOSURE_FORMAT

    @staticmethod
    def _parse_rule_config(raw: str) -> tuple[list[int], str]:
        target_key_ids, strategy, _ = ModelRouter._parse_rule_config_detail(raw)
        return target_key_ids, strategy

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
    def _sequential_state_key(
        *,
        model_alias: str,
        effective_group: str,
        provider_filters: str | Sequence[str] | set[str] | None,
        target_key_ids: list[int] | None,
    ) -> str:
        normalized_filters = sorted(normalize_provider_filters(provider_filters) or [])
        payload = {
            "model_alias": model_alias,
            "effective_group": effective_group,
            "provider_filters": normalized_filters,
            "target_key_ids": target_key_ids or [],
        }
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        return f"route:sequential:active:{digest}"

    async def _get_sequential_active_key_id(self, state_key: str) -> int | None:
        raw = await self.circuit_breaker.redis.get(state_key)
        if raw is None:
            return None
        try:
            return int(raw)
        except (TypeError, ValueError):
            return None

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
            while len(_wrr_state) > WRR_STATE_MAX_POOLS:
                _wrr_state.popitem(last=False)
        else:
            _wrr_state.move_to_end(pool_key)

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
        active_key_id: int | None = None,
    ) -> list[RouteCandidate]:
        if not candidates:
            return []
        normalized = strategy or DEFAULT_RULE_STRATEGY
        if normalized == "sequential":
            if target_key_ids:
                key_order = {key_id: index for index, key_id in enumerate(target_key_ids)}
                ordered = sorted(
                    candidates,
                    key=lambda candidate: key_order.get(candidate.api_key.id, len(target_key_ids)),
                )
            else:
                ordered = sorted(candidates, key=lambda candidate: candidate.api_key.id)
            if active_key_id is None:
                return ordered
            active_index = next(
                (
                    index
                    for index, candidate in enumerate(ordered)
                    if candidate.api_key.id == active_key_id
                ),
                None,
            )
            if active_index is None:
                return ordered
            return [*ordered[active_index:], *ordered[:active_index]]
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
