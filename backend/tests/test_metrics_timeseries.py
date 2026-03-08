from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app.api.v1 import routes as routes_module


@dataclass
class RequestLogStub:
    created_at: datetime
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None
    latency_ms: int


def test_build_metric_buckets_aggregates_logs() -> None:
    start = datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc)
    end = start + timedelta(hours=2)
    logs = [
        RequestLogStub(
            created_at=start + timedelta(minutes=10),
            prompt_tokens=10,
            completion_tokens=5,
            total_tokens=15,
            latency_ms=120,
        ),
        RequestLogStub(
            created_at=start + timedelta(minutes=50),
            prompt_tokens=20,
            completion_tokens=10,
            total_tokens=30,
            latency_ms=200,
        ),
        RequestLogStub(
            created_at=start + timedelta(hours=1, minutes=5),
            prompt_tokens=5,
            completion_tokens=5,
            total_tokens=10,
            latency_ms=80,
        ),
    ]

    buckets = routes_module.build_metric_buckets(logs, start, end, bucket_minutes=60)

    assert len(buckets) == 3
    assert buckets[0].request_count == 2
    assert buckets[0].total_tokens == 45
    assert buckets[0].avg_latency_ms == 160
    assert buckets[1].request_count == 1
    assert buckets[1].prompt_tokens == 5
    assert buckets[2].request_count == 0
    assert buckets[2].total_tokens == 0
