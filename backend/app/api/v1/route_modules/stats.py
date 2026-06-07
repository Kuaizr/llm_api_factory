from fastapi import APIRouter, Depends

from app.api.v1.route_helpers import _require_master_auth
from app.api.v1.route_models import (
    DashboardStatusOut,
    MetricsBucketOut,
    RouteExplainResponse,
    OverviewOut,
    RouteTestResponse,
    UsageStatsOut,
)
from app.api.v1.route_modules.stats_handlers import (
    admin_metrics_timeseries,
    admin_overview,
    admin_usage_stats,
    public_dashboard,
    route_explain,
    route_test,
)

router = APIRouter()
_admin_dependencies = [Depends(_require_master_auth)]

router.add_api_route(
    "/v1/status/dashboard",
    public_dashboard,
    methods=["GET"],
    response_model=DashboardStatusOut,
)
router.add_api_route(
    "/admin/overview",
    admin_overview,
    methods=["GET"],
    response_model=OverviewOut,
    dependencies=_admin_dependencies,
)
router.add_api_route(
    "/admin/stats/usage",
    admin_usage_stats,
    methods=["GET"],
    response_model=UsageStatsOut,
    dependencies=_admin_dependencies,
)
router.add_api_route(
    "/admin/metrics/timeseries",
    admin_metrics_timeseries,
    methods=["GET"],
    response_model=list[MetricsBucketOut],
    dependencies=_admin_dependencies,
)
router.add_api_route(
    "/admin/route-test",
    route_test,
    methods=["POST"],
    response_model=RouteTestResponse,
    dependencies=_admin_dependencies,
)
router.add_api_route(
    "/admin/route-explain",
    route_explain,
    methods=["POST"],
    response_model=RouteExplainResponse,
    dependencies=_admin_dependencies,
)
