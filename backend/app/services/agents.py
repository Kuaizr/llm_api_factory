from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import hmac
import secrets
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Agent


@dataclass(frozen=True)
class AgentStatus:
    id: int
    name: str
    region: str | None
    network_group: str | None
    labels: list[str]
    endpoint_url: str | None
    supports_gpt: bool | None
    supports_gemini: bool | None
    supports_claude: bool | None
    probe_latency_ms: int | None
    probe_checked_at: datetime | None
    is_draining: bool
    is_active: bool
    last_seen_at: datetime | None
    status: str


def _normalize_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def issue_agent_token() -> str:
    return secrets.token_urlsafe(32)


def hash_agent_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def verify_agent_token(token: str, token_hash: str) -> bool:
    return hmac.compare_digest(hash_agent_token(token), token_hash)


def build_agent_statuses(
    agents: Iterable[Agent],
    now: datetime,
    timeout_seconds: int,
) -> list[AgentStatus]:
    normalized_now = _normalize_datetime(now)
    statuses: list[AgentStatus] = []
    for agent in agents:
        last_seen = agent.last_seen_at
        status = "offline"
        if agent.is_active and last_seen is not None:
            normalized_last_seen = _normalize_datetime(last_seen)
            delta = (normalized_now - normalized_last_seen).total_seconds()
            if delta <= timeout_seconds:
                status = "draining" if getattr(agent, "is_draining", False) else "online"
        statuses.append(
            AgentStatus(
                id=agent.id,
                name=agent.name,
                region=agent.region,
                network_group=getattr(agent, "network_group", None),
                labels=getattr(agent, "labels", []),
                endpoint_url=agent.endpoint_url,
                supports_gpt=getattr(agent, "supports_gpt", None),
                supports_gemini=getattr(agent, "supports_gemini", None),
                supports_claude=getattr(agent, "supports_claude", None),
                probe_latency_ms=getattr(agent, "probe_latency_ms", None),
                probe_checked_at=getattr(agent, "probe_checked_at", None),
                is_draining=bool(getattr(agent, "is_draining", False)),
                is_active=agent.is_active,
                last_seen_at=agent.last_seen_at,
                status=status,
            )
        )
    return statuses


async def get_agent_by_name(session: AsyncSession, name: str) -> Agent | None:
    result = await session.execute(select(Agent).where(Agent.name == name))
    return result.scalars().first()


async def list_agents(session: AsyncSession) -> list[Agent]:
    result = await session.execute(select(Agent).order_by(Agent.id))
    return result.scalars().all()


async def upsert_agent(
    session: AsyncSession,
    name: str,
    region: str | None,
    endpoint_url: str | None,
    network_group: str | None = None,
    labels: list[str] | None = None,
    now: datetime | None = None,
    touch: bool = True,
    auth_token_hash: str | None = None,
    supports_gpt: bool | None = None,
    supports_gemini: bool | None = None,
    supports_claude: bool | None = None,
    probe_latency_ms: int | None = None,
    probe_checked_at: datetime | None = None,
) -> Agent:
    current_time = now or datetime.now(timezone.utc)
    if probe_checked_at is None and any(
        value is not None
        for value in (supports_gpt, supports_gemini, supports_claude, probe_latency_ms)
    ):
        probe_checked_at = current_time

    agent = await get_agent_by_name(session, name)
    if agent is None:
        agent_data = {
            "name": name,
            "region": region,
            "endpoint_url": endpoint_url,
            "auth_token_hash": auth_token_hash,
            "supports_gpt": supports_gpt,
            "supports_gemini": supports_gemini,
            "supports_claude": supports_claude,
            "probe_latency_ms": probe_latency_ms,
            "probe_checked_at": probe_checked_at,
            "is_draining": False,
            "is_active": True,
            "last_seen_at": current_time if touch else None,
        }
        if network_group is not None:
            agent_data["network_group"] = network_group
        agent = Agent(**agent_data)
        if labels is not None:
            agent.labels = labels
        session.add(agent)
        await session.commit()
        await session.refresh(agent)
        return agent

    if region is not None:
        agent.region = region
    if network_group is not None:
        agent.network_group = network_group
    if labels is not None:
        agent.labels = labels
    if endpoint_url is not None:
        agent.endpoint_url = endpoint_url
    if auth_token_hash is not None:
        agent.auth_token_hash = auth_token_hash
    if supports_gpt is not None:
        agent.supports_gpt = supports_gpt
    if supports_gemini is not None:
        agent.supports_gemini = supports_gemini
    if supports_claude is not None:
        agent.supports_claude = supports_claude
    if probe_latency_ms is not None:
        agent.probe_latency_ms = probe_latency_ms
    if probe_checked_at is not None:
        agent.probe_checked_at = probe_checked_at
    if touch:
        agent.last_seen_at = current_time
    await session.commit()
    await session.refresh(agent)
    return agent
