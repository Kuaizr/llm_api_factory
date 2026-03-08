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
        ModelRouter._order_candidates(candidates)[0].api_key.id for _ in range(6)
    ]
    assert order == [1, 2, 1, 1, 2, 1]


def test_weighted_round_robin_zero_weight_defaults_to_one() -> None:
    router_module._wrr_state.clear()
    candidates = build_candidates([0, 0])
    order = [
        ModelRouter._order_candidates(candidates)[0].api_key.id for _ in range(4)
    ]
    assert order == [1, 2, 1, 2]
