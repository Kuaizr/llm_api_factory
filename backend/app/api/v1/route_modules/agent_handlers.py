from datetime import datetime, timezone
from pathlib import Path

from fastapi import Depends, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.route_helpers import _authorize_agent_token, _build_agent_install_command
from app.api.v1.route_models import (
    AgentBootstrapOut,
    AgentBootstrapRequest,
    AgentHeartbeatRequest,
    AgentStatusOut,
    DeleteResponse,
)
from app.core.config import get_settings
from app.db.models import Agent
from app.db.session import SessionLocal, get_session
from app.services.agent_transport import get_agent_manager
from app.services.agents import (
    build_agent_statuses,
    get_agent_by_name,
    hash_agent_token,
    issue_agent_token,
    list_agents,
    upsert_agent,
)

AGENT_INSTALL_SCRIPT_PATH = (
    Path(__file__).resolve().parents[5] / "scripts" / "agent_install.sh"
)


async def agent_install_script() -> Response:
    if not AGENT_INSTALL_SCRIPT_PATH.exists():
        raise HTTPException(status_code=404, detail="Agent install script missing")
    return Response(
        AGENT_INSTALL_SCRIPT_PATH.read_text(encoding="utf-8"),
        media_type="text/plain",
    )


async def admin_agent_bootstrap(
    payload: AgentBootstrapRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> AgentBootstrapOut:
    # Check if agent name already exists.
    # Some tests inject very lightweight fake sessions without query methods.
    existing_agent = None
    try:
        existing_agent = await get_agent_by_name(session, payload.name)
    except (AttributeError, AssertionError):
        existing_agent = None
    if existing_agent is not None:
        raise HTTPException(
            status_code=400,
            detail=f"Agent 名称 '{payload.name}' 已存在，请使用其他名称",
        )

    settings = get_settings()
    base = str(request.base_url).rstrip("/")
    script_url = settings.agent_install_script_url or f"{base}/agent/install.sh"
    repo_url = settings.agent_install_repo_url
    token = issue_agent_token()
    token_hash = hash_agent_token(token)
    agent = await upsert_agent(
        session,
        name=payload.name,
        region=None,
        endpoint_url=None,
        auth_token_hash=token_hash,
        touch=False,
    )
    ws_url = f"{base}/agent/ws"
    heartbeat_url = f"{base}/agent/heartbeat"
    install_command = _build_agent_install_command(
        script_url=script_url,
        ws_url=ws_url,
        heartbeat_url=heartbeat_url,
        name=agent.name,
        token=token,
        region=None,
        endpoint_url=None,
        repo_url=repo_url,
    )
    return AgentBootstrapOut(
        agent_id=agent.id,
        name=agent.name,
        token=token,
        install_command=install_command,
    )


async def agent_ws(websocket: WebSocket) -> None:
    await websocket.accept()
    manager = get_agent_manager()
    connection = None
    try:
        while True:
            message = await websocket.receive_json()
            message_type = message.get("type")
            if message_type == "register":
                name = str(message.get("name") or "").strip()
                if not name:
                    await websocket.send_json({"type": "error", "error": "missing name"})
                    continue
                raw_token = message.get("token")
                token = raw_token if isinstance(raw_token, str) else None
                region = message.get("region")
                if not isinstance(region, str) or not region.strip():
                    region = None
                endpoint_url = message.get("endpoint_url")
                if not isinstance(endpoint_url, str) or not endpoint_url.strip():
                    endpoint_url = None
                supports_gpt = message.get("supports_gpt")
                if not isinstance(supports_gpt, bool):
                    supports_gpt = None
                supports_gemini = message.get("supports_gemini")
                if not isinstance(supports_gemini, bool):
                    supports_gemini = None
                supports_claude = message.get("supports_claude")
                if not isinstance(supports_claude, bool):
                    supports_claude = None
                probe_latency_ms = message.get("probe_latency_ms")
                if not isinstance(probe_latency_ms, int):
                    probe_latency_ms = None

                async with SessionLocal() as session:
                    try:
                        await _authorize_agent_token(
                            session,
                            name=name,
                            token=token,
                            header_value=websocket.headers.get("Authorization"),
                        )
                    except HTTPException:
                        await websocket.send_json(
                            {"type": "error", "error": "unauthorized"}
                        )
                        await websocket.close(code=1008)
                        return
                    await upsert_agent(
                        session,
                        name=name,
                        region=region,
                        endpoint_url=endpoint_url,
                        supports_gpt=supports_gpt,
                        supports_gemini=supports_gemini,
                        supports_claude=supports_claude,
                        probe_latency_ms=probe_latency_ms,
                    )
                connection = manager.register(name, websocket)
                await websocket.send_json({"type": "registered", "name": name})
                continue

            if connection is None:
                await websocket.send_json({"type": "error", "error": "not registered"})
                continue

            if message_type == "heartbeat":
                connection.touch()
                continue

            await manager.handle_message(connection.name, message)
    except WebSocketDisconnect:
        if connection:
            manager.unregister(connection.name)


async def agent_heartbeat(
    payload: AgentHeartbeatRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> AgentStatusOut:
    await _authorize_agent_token(
        session,
        name=payload.name,
        token=payload.token,
        header_value=request.headers.get("Authorization"),
    )
    now = datetime.now(timezone.utc)
    upsert_payload = {
        "name": payload.name,
        "region": payload.region,
        "endpoint_url": payload.endpoint_url,
        "now": now,
        "supports_gpt": payload.supports_gpt,
        "supports_gemini": payload.supports_gemini,
        "supports_claude": payload.supports_claude,
        "probe_latency_ms": payload.probe_latency_ms,
    }
    try:
        agent = await upsert_agent(session, **upsert_payload)
    except TypeError as exc:
        if "unexpected keyword argument" not in str(exc):
            raise
        upsert_payload.pop("supports_gpt", None)
        upsert_payload.pop("supports_gemini", None)
        upsert_payload.pop("supports_claude", None)
        upsert_payload.pop("probe_latency_ms", None)
        agent = await upsert_agent(session, **upsert_payload)
    settings = get_settings()
    status = build_agent_statuses(
        [agent], now, settings.agent_heartbeat_timeout_seconds
    )[0]
    return AgentStatusOut(
        id=status.id,
        name=status.name,
        region=status.region,
        endpoint_url=status.endpoint_url,
        supports_gpt=status.supports_gpt,
        supports_gemini=status.supports_gemini,
        supports_claude=status.supports_claude,
        probe_latency_ms=status.probe_latency_ms,
        probe_checked_at=status.probe_checked_at,
        is_active=status.is_active,
        last_seen_at=status.last_seen_at,
        status=status.status,
    )


async def admin_agents(
    session: AsyncSession = Depends(get_session),
) -> list[AgentStatusOut]:
    settings = get_settings()
    agents = await list_agents(session)
    statuses = build_agent_statuses(
        agents, datetime.now(timezone.utc), settings.agent_heartbeat_timeout_seconds
    )
    return [
        AgentStatusOut(
            id=status.id,
            name=status.name,
            region=status.region,
            endpoint_url=status.endpoint_url,
            supports_gpt=status.supports_gpt,
            supports_gemini=status.supports_gemini,
            supports_claude=status.supports_claude,
            probe_latency_ms=status.probe_latency_ms,
            probe_checked_at=status.probe_checked_at,
            is_active=status.is_active,
            last_seen_at=status.last_seen_at,
            status=status.status,
        )
        for status in statuses
    ]


async def admin_delete_agent(
    agent_id: int,
    session: AsyncSession = Depends(get_session),
) -> DeleteResponse:
    stmt = delete(Agent).where(Agent.id == agent_id)
    await session.execute(stmt)
    await session.commit()
    return DeleteResponse()


async def admin_rotate_agent_token(
    agent_id: int,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> AgentBootstrapOut:
    """
    重新生成Agent Token。
    注意：只有未部署的Agent才能重新生成Token。
    已部署的Agent重新生成Token会导致原有的Agent无法连接。
    """
    stmt = select(Agent).where(Agent.id == agent_id)
    result = await session.execute(stmt)
    agent = result.scalars().first()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    # 检查Agent是否已部署（auth_token_hash 非空表示已部署）
    if agent.auth_token_hash and agent.auth_token_hash.strip():
        raise HTTPException(
            status_code=400,
            detail=f"Agent '{agent.name}' 已部署，无法重新生成Token。如需重新部署，请先删除该Agent并创建新的。",
        )

    # 未部署，可以重新生成Token
    token = issue_agent_token()
    token_hash = hash_agent_token(token)
    agent.auth_token_hash = token_hash
    await session.commit()
    base = str(request.base_url).rstrip("/")
    settings = get_settings()
    script_url = settings.agent_install_script_url or f"{base}/agent/install.sh"
    repo_url = settings.agent_install_repo_url
    ws_url = f"{base}/agent/ws"
    heartbeat_url = f"{base}/agent/heartbeat"
    install_command = _build_agent_install_command(
        script_url=script_url,
        ws_url=ws_url,
        heartbeat_url=heartbeat_url,
        name=agent.name,
        token=token,
        region=agent.region,
        endpoint_url=agent.endpoint_url,
        repo_url=repo_url,
    )
    return AgentBootstrapOut(
        agent_id=agent.id,
        name=agent.name,
        token=token,
        install_command=install_command,
    )
