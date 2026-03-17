from fastapi import APIRouter, Depends

from app.api.v1.route_helpers import _require_master_auth
from app.api.v1.route_models import (
    AlertPolicyOut,
    HealthProbeBucketOut,
    HealthStatusOut,
    TelegramConfigOut,
    TelegramTestOut,
)
from app.api.v1.route_modules.health_handlers import (
    admin_alert_policies,
    admin_alert_policy_update,
    admin_health_probe_timeseries,
    admin_health_status,
    admin_telegram_config,
    admin_telegram_config_update,
    admin_telegram_test,
)

router = APIRouter()
_admin_dependencies = [Depends(_require_master_auth)]

router.add_api_route(
    "/admin/alerts",
    admin_alert_policies,
    methods=["GET"],
    response_model=list[AlertPolicyOut],
    dependencies=_admin_dependencies,
)
router.add_api_route(
    "/admin/alerts/{event}",
    admin_alert_policy_update,
    methods=["PUT"],
    response_model=AlertPolicyOut,
    dependencies=_admin_dependencies,
)
router.add_api_route(
    "/admin/telegram/config",
    admin_telegram_config,
    methods=["GET"],
    response_model=TelegramConfigOut,
    dependencies=_admin_dependencies,
)
router.add_api_route(
    "/admin/telegram/config",
    admin_telegram_config_update,
    methods=["PATCH"],
    response_model=TelegramConfigOut,
    dependencies=_admin_dependencies,
)
router.add_api_route(
    "/admin/telegram/test",
    admin_telegram_test,
    methods=["POST"],
    response_model=TelegramTestOut,
    dependencies=_admin_dependencies,
)
router.add_api_route(
    "/admin/health-status/timeseries",
    admin_health_probe_timeseries,
    methods=["GET"],
    response_model=list[HealthProbeBucketOut],
    dependencies=_admin_dependencies,
)
router.add_api_route(
    "/admin/health-status",
    admin_health_status,
    methods=["GET"],
    response_model=list[HealthStatusOut],
    dependencies=_admin_dependencies,
)
