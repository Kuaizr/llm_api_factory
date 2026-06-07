from dataclasses import dataclass

from app.services import router as router_module
from app.services.router import ModelRouter, RouteCandidate


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
