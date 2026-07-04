from dataclasses import dataclass

import pytest

from app.core.config import Settings
from app.core.redis import MemoryRedis
from app.services.circuit_breaker import CircuitBreaker
from app.services import router as router_module
from app.services.router import ModelRouter, RouteCandidate


class CircuitBreakerStub:
    def __init__(self) -> None:
        self.redis = MemoryRedis()

    async def is_available(self, _api_key_id: int) -> bool:
        return True

    async def are_available(self, api_key_ids: list[int]) -> dict[int, bool]:
        return {api_key_id: True for api_key_id in api_key_ids}


class CountingRedis(MemoryRedis):
    def __init__(self) -> None:
        super().__init__()
        self.get_count = 0
        self.mget_count = 0

    async def get(self, key: str) -> str | None:
        self.get_count += 1
        return await super().get(key)

    async def mget(self, keys: list[str]) -> list[str | None]:
        self.mget_count += 1
        return await super().mget(keys)


@dataclass
class APIKeyStub:
    id: int
    weight: int


@dataclass
class EndpointStub:
    id: int
    agent_node: str | None = None
    access_mode: str = "direct"


def build_candidates(weights: list[int]) -> list[RouteCandidate]:
    candidates = []
    for index, weight in enumerate(weights, start=1):
        api_key = APIKeyStub(id=index, weight=weight)
        endpoint = EndpointStub(id=index)
        candidates.append(
            RouteCandidate(api_key=api_key, endpoint=endpoint, real_model="model")
        )
    return candidates


def test_weighted_round_robin_sequence() -> None:
    router_module._wrr_state.clear()
    candidates = build_candidates([2, 1])
    order = [
        ModelRouter._order_candidates(candidates, "weighted_round_robin", "ctx")[0].api_key.id
        for _ in range(6)
    ]
    assert order == [1, 2, 1, 1, 2, 1]


def test_weighted_round_robin_zero_weight_defaults_to_one() -> None:
    router_module._wrr_state.clear()
    candidates = build_candidates([0, 0])
    order = [
        ModelRouter._order_candidates(candidates, "weighted_round_robin", "ctx")[0].api_key.id
        for _ in range(4)
    ]
    assert order == [1, 2, 1, 2]


def test_sequential_strategy_respects_target_key_order() -> None:
    candidates = build_candidates([1, 1, 1])
    target_key_ids = [3, 1, 2]
    ordered = ModelRouter._order_candidates(
        candidates, strategy="sequential", context="", target_key_ids=target_key_ids
    )
    assert [candidate.api_key.id for candidate in ordered] == [3, 1, 2]


def test_sequential_strategy_falls_back_to_id_sort_without_target_key_ids() -> None:
    candidates = build_candidates([1, 1, 1])
    ordered = ModelRouter._order_candidates(
        candidates, strategy="sequential", context="", target_key_ids=None
    )
    assert [candidate.api_key.id for candidate in ordered] == [1, 2, 3]


def test_sequential_strategy_places_unknown_keys_last() -> None:
    candidates = build_candidates([1, 1, 1, 1])
    target_key_ids = [4, 2]
    ordered = ModelRouter._order_candidates(
        candidates, strategy="sequential", context="", target_key_ids=target_key_ids
    )
    key_ids = [candidate.api_key.id for candidate in ordered]
    assert key_ids[:2] == [4, 2]
    assert sorted(key_ids[2:]) == [1, 3]


def test_sequential_strategy_rotates_from_active_key() -> None:
    candidates = build_candidates([1, 1, 1])
    ordered = ModelRouter._order_candidates(
        candidates,
        strategy="sequential",
        context="",
        target_key_ids=[1, 2, 3],
        active_key_id=2,
    )
    assert [candidate.api_key.id for candidate in ordered] == [2, 3, 1]


@pytest.mark.asyncio
async def test_sequential_strategy_persists_success_as_active_key() -> None:
    router = ModelRouter(CircuitBreakerStub())
    candidates = build_candidates([1, 1, 1])
    target_key_ids = [1, 2, 3]

    first_order = await router.order_candidates(
        candidates,
        strategy="sequential",
        model_alias="gpt-5",
        effective_group="codex",
        target_key_ids=target_key_ids,
    )
    assert [candidate.api_key.id for candidate in first_order] == [1, 2, 3]

    await router.record_candidate_success(candidates[1])

    second_order = await router.order_candidates(
        candidates,
        strategy="sequential",
        model_alias="gpt-5",
        effective_group="codex",
        target_key_ids=target_key_ids,
    )
    assert [candidate.api_key.id for candidate in second_order] == [2, 3, 1]


@pytest.mark.asyncio
async def test_filter_available_candidates_batches_circuit_lookup() -> None:
    redis = CountingRedis()
    await redis.set("circuit:2:state", "open")
    router = ModelRouter(CircuitBreaker(redis, settings=Settings()))
    candidates = build_candidates([1, 1, 1])

    available = await router._filter_available_candidates(
        session=None,
        candidates=candidates,
        effective_group="default",
        target_key_ids=[1, 2, 3],
    )

    assert [candidate.api_key.id for candidate in available] == [1, 3]
    assert redis.mget_count == 1
    assert redis.get_count == 0


def test_route_candidate_reports_direct_execution_by_default() -> None:
    candidate = RouteCandidate(
        api_key=APIKeyStub(id=1, weight=1),
        endpoint=EndpointStub(id=1),
        real_model="model",
    )

    assert candidate.execution_mode == "direct"
    assert candidate.agent_name is None


def test_route_candidate_reports_via_agent_execution() -> None:
    candidate = RouteCandidate(
        api_key=APIKeyStub(id=1, weight=1),
        endpoint=EndpointStub(id=1, access_mode="via_agent", agent_node="edge-hk"),
        real_model="model",
    )

    assert candidate.execution_mode == "via_agent"
    assert candidate.agent_name == "edge-hk"


def test_route_candidate_keeps_legacy_agent_node_compatibility() -> None:
    @dataclass
    class LegacyEndpointStub:
        id: int
        agent_node: str | None = None

    candidate = RouteCandidate(
        api_key=APIKeyStub(id=1, weight=1),
        endpoint=LegacyEndpointStub(id=1, agent_node="edge-sg"),
        real_model="model",
    )

    assert candidate.execution_mode == "via_agent"
    assert candidate.agent_name == "edge-sg"
