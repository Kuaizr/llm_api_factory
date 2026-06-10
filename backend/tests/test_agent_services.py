from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import pytest

from app.services import agents as agents_module


@dataclass
class AgentStub:
    name: str
    region: str | None
    endpoint_url: str | None
    network_group: str | None = None
    labels: list[str] | None = None
    supports_gpt: bool | None = None
    supports_gemini: bool | None = None
    supports_claude: bool | None = None
    probe_latency_ms: int | None = None
    probe_checked_at: datetime | None = None
    auth_token_hash: str | None = None
    id: int = 0
    is_draining: bool = False
    is_active: bool = True
    last_seen_at: datetime | None = None


class FakeSession:
    def __init__(self) -> None:
        self.added: list[AgentStub] = []
        self.commits = 0
        self.refreshed: list[AgentStub] = []

    def add(self, agent: AgentStub) -> None:
        self.added.append(agent)

    async def commit(self) -> None:
        self.commits += 1

    async def refresh(self, agent: AgentStub) -> None:
        self.refreshed.append(agent)


@pytest.mark.asyncio
async def test_upsert_agent_creates_new(monkeypatch: pytest.MonkeyPatch) -> None:
    session = FakeSession()
    now = datetime(2024, 1, 1, tzinfo=timezone.utc)

    async def fake_get_agent_by_name(_session, _name):
        return None

    monkeypatch.setattr(agents_module, "get_agent_by_name", fake_get_agent_by_name)
    monkeypatch.setattr(agents_module, "Agent", AgentStub)

    agent = await agents_module.upsert_agent(
        session=session,
        name="edge-hk",
        region="hk",
        endpoint_url="https://edge.example.com",
        now=now,
    )

    assert agent.name == "edge-hk"
    assert agent.region == "hk"
    assert agent.network_group is None
    assert agent.endpoint_url == "https://edge.example.com"
    assert agent.last_seen_at == now
    assert session.added
    assert session.commits == 1


@pytest.mark.asyncio
async def test_upsert_agent_updates_existing(monkeypatch: pytest.MonkeyPatch) -> None:
    session = FakeSession()
    existing = AgentStub(
        id=1,
        name="edge-sg",
        region="sg",
        endpoint_url="https://old.example.com",
        last_seen_at=None,
    )
    now = datetime(2024, 1, 2, tzinfo=timezone.utc)

    async def fake_get_agent_by_name(_session, _name):
        return existing

    monkeypatch.setattr(agents_module, "get_agent_by_name", fake_get_agent_by_name)

    agent = await agents_module.upsert_agent(
        session=session,
        name="edge-sg",
        region="sgp",
        endpoint_url=None,
        now=now,
    )

    assert agent is existing
    assert agent.region == "sgp"
    assert agent.endpoint_url == "https://old.example.com"
    assert agent.last_seen_at == now


def test_build_agent_statuses() -> None:
    now = datetime(2024, 1, 2, tzinfo=timezone.utc)
    agents = [
        AgentStub(
            id=1,
            name="edge-hk",
            region="hk",
            network_group="egress-asia",
            labels=["hk", "fast"],
            endpoint_url=None,
            last_seen_at=now - timedelta(seconds=30),
        ),
        AgentStub(
            id=2,
            name="edge-us",
            region="us",
            endpoint_url=None,
            last_seen_at=now - timedelta(seconds=300),
        ),
        AgentStub(
            id=3,
            name="edge-eu",
            region="eu",
            endpoint_url=None,
            last_seen_at=None,
        ),
    ]

    statuses = agents_module.build_agent_statuses(agents, now, timeout_seconds=120)

    assert statuses[0].status == "online"
    assert statuses[0].network_group == "egress-asia"
    assert statuses[0].labels == ["hk", "fast"]
    assert statuses[1].status == "offline"
    assert statuses[2].status == "offline"


def test_build_agent_statuses_marks_draining() -> None:
    now = datetime(2024, 1, 1, tzinfo=timezone.utc)
    agent = AgentStub(
        id=1,
        name="edge-drain",
        region="us",
        endpoint_url=None,
        is_active=True,
        is_draining=True,
        last_seen_at=now,
    )

    statuses = agents_module.build_agent_statuses([agent], now, timeout_seconds=60)

    assert statuses[0].is_active is True
    assert statuses[0].is_draining is True
    assert statuses[0].status == "draining"


@pytest.mark.asyncio
async def test_upsert_agent_updates_network_group_and_labels(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = FakeSession()
    existing = AgentStub(
        id=1,
        name="edge-hk",
        region="hk",
        network_group=None,
        labels=[],
        endpoint_url=None,
    )

    async def fake_get_agent_by_name(_session, _name):
        return existing

    monkeypatch.setattr(agents_module, "get_agent_by_name", fake_get_agent_by_name)

    agent = await agents_module.upsert_agent(
        session=session,
        name="edge-hk",
        region=None,
        network_group="egress-asia",
        labels=["hk", "fast"],
        endpoint_url=None,
    )

    assert agent is existing
    assert agent.network_group == "egress-asia"
    assert agent.labels == ["hk", "fast"]
