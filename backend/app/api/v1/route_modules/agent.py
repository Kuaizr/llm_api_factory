from fastapi import APIRouter, Depends

from app.api.v1.route_helpers import _require_master_auth
from app.api.v1.route_models import AgentBootstrapOut, AgentStatusOut, DeleteResponse
from app.api.v1.route_modules.agent_handlers import (
    admin_agent_bootstrap,
    admin_agents,
    admin_delete_agent,
    admin_rotate_agent_token,
    agent_heartbeat,
    agent_install_script,
    agent_ws,
)

router = APIRouter()
_admin_dependencies = [Depends(_require_master_auth)]

router.add_api_route(
    "/agent/install.sh",
    agent_install_script,
    methods=["GET"],
)
router.add_api_route(
    "/admin/agents/bootstrap",
    admin_agent_bootstrap,
    methods=["POST"],
    response_model=AgentBootstrapOut,
    dependencies=_admin_dependencies,
)
router.add_api_websocket_route(
    "/agent/ws",
    agent_ws,
)
router.add_api_route(
    "/agent/heartbeat",
    agent_heartbeat,
    methods=["POST"],
    response_model=AgentStatusOut,
)
router.add_api_route(
    "/admin/agents",
    admin_agents,
    methods=["GET"],
    response_model=list[AgentStatusOut],
    dependencies=_admin_dependencies,
)
router.add_api_route(
    "/admin/agents/{agent_id}",
    admin_delete_agent,
    methods=["DELETE"],
    response_model=DeleteResponse,
    dependencies=_admin_dependencies,
)
router.add_api_route(
    "/admin/agents/{agent_id}/rotate-token",
    admin_rotate_agent_token,
    methods=["POST"],
    response_model=AgentBootstrapOut,
    dependencies=_admin_dependencies,
)
