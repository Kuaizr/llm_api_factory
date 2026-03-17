from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class EndpointCreate(BaseModel):
    name: str = Field(..., min_length=1)
    base_url: str = Field(..., min_length=1)
    auth_header_name: str = "Authorization"
    auth_header_prefix: str = "Bearer"
    provider: str = "openai"
    strategy: str = "weighted_round_robin"
    agent_node: str | None = None
    probe_interval_seconds: int | None = Field(default=None, ge=-1, le=86400)
    is_active: bool = True
    # 通用 Provider 扩展字段
    url_path_suffix: str | None = None
    extra_headers: dict[str, str] | None = None
    extra_cookies: str | None = None
    extra_query_params: dict[str, str] | None = None
    oauth_config: dict[str, str] | None = None
    request_body_template: str | None = None


class EndpointUpdate(BaseModel):
    name: str | None = None
    base_url: str | None = None
    auth_header_name: str | None = None
    auth_header_prefix: str | None = None
    provider: str | None = None
    strategy: str | None = None
    agent_node: str | None = None
    probe_interval_seconds: int | None = Field(default=None, ge=-1, le=86400)
    is_active: bool | None = None
    # 通用 Provider 扩展字段
    url_path_suffix: str | None = None
    extra_headers: dict[str, str] | None = None
    extra_cookies: str | None = None
    extra_query_params: dict[str, str] | None = None
    oauth_config: dict[str, str] | None = None
    request_body_template: str | None = None


class EndpointOut(BaseModel):
    id: int
    name: str
    base_url: str
    auth_header_name: str
    auth_header_prefix: str
    provider: str
    strategy: str
    agent_node: str | None
    probe_interval_seconds: int | None
    is_active: bool
    # 通用 Provider 扩展字段
    url_path_suffix: str | None = None
    extra_headers: dict[str, str] | None = None
    extra_cookies: str | None = None
    extra_query_params: dict[str, str] | None = None
    oauth_config: dict[str, str] | None = None
    request_body_template: str | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class APIKeyCreate(BaseModel):
    endpoint_id: int
    key: str = Field(..., min_length=1)
    name: str | None = None
    rule_group: str = "default"
    weight: int = 1
    rpm_limit: int | None = None
    daily_limit: int | None = None
    used_today: int = 0
    total_usage: int = 0
    is_active: bool = True


class EndpointKeyCreate(BaseModel):
    key: str = Field(..., min_length=1)
    name: str | None = None
    rule_group: str = "default"
    weight: int = 1
    rpm_limit: int | None = None
    daily_limit: int | None = None
    used_today: int = 0
    total_usage: int = 0
    is_active: bool = True


class APIKeyUpdate(BaseModel):
    key: str | None = None
    name: str | None = None
    rule_group: str | None = None
    weight: int | None = None
    rpm_limit: int | None = None
    daily_limit: int | None = None
    used_today: int | None = None
    total_usage: int | None = None
    is_active: bool | None = None


class APIKeyOut(BaseModel):
    id: int
    endpoint_id: int
    key: str
    name: str | None
    rule_group: str
    weight: int
    rpm_limit: int | None
    daily_limit: int | None
    used_today: int
    total_usage: int
    is_active: bool
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class EndpointKeyOut(BaseModel):
    id: int
    key_preview: str
    name: str | None
    rule_group: str
    rpm_limit: int | None
    daily_limit: int | None
    used_today: int
    is_active: bool


class EndpointDetailOut(BaseModel):
    id: int
    name: str
    base_url: str
    provider: str
    strategy: str
    is_active: bool
    status: str
    latency: int
    uptime: float
    is_agent_enabled: bool
    agent_node: str | None
    probe_interval_seconds: int | None
    # 通用 Provider 扩展字段
    url_path_suffix: str | None = None
    extra_headers: dict[str, str] | None = None
    extra_cookies: str | None = None
    extra_query_params: dict[str, str] | None = None
    oauth_config: dict[str, str] | None = None
    request_body_template: str | None = None
    model_count: int
    keys: list[EndpointKeyOut]


class RoutingRuleCreate(BaseModel):
    model_pattern: str
    group_name: str = "default"
    priority: int = 10
    strategy: str = "weighted_round_robin"
    is_active: bool = True
    dump_enabled: bool = False
    dump_path: str | None = None
    target_key_ids: list[int]


class RoutingRuleUpdate(BaseModel):
    model_pattern: str | None = None
    group_name: str | None = None
    priority: int | None = None
    strategy: str | None = None
    is_active: bool | None = None
    dump_enabled: bool | None = None
    dump_path: str | None = None
    target_key_ids: list[int] | None = None


class RuleAccessKeyPreviewOut(BaseModel):
    id: int
    name: str | None
    key_preview: str
    is_active: bool
    created_at: datetime


class RoutingRuleOut(BaseModel):
    id: int
    model_pattern: str
    group_name: str
    priority: int
    strategy: str
    is_active: bool
    dump_enabled: bool = False
    dump_path: str | None = None
    target_key_ids: list[int]
    access_keys: list[RuleAccessKeyPreviewOut] = []
    request_count: int = 0
    total_tokens: int = 0
    avg_ttft_ms: int | None = None
    avg_tps: float | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class AuthLoginRequest(BaseModel):
    password: str


class AuthLoginResponse(BaseModel):
    token: str
    role: str
    issued_at: datetime


class AuthMeResponse(BaseModel):
    role: str
    is_admin: bool


class AuthPasswordUpdateRequest(BaseModel):
    current_password: str = Field(..., min_length=1)
    new_password: str = Field(..., min_length=4, max_length=128)


class AuthPasswordUpdateResponse(BaseModel):
    token: str
    updated_at: datetime


class UsageGroupStat(BaseModel):
    group_name: str
    percent: float
    total_tokens: int


class UsageTopKey(BaseModel):
    api_key_id: int
    endpoint_name: str
    key_preview: str
    total_tokens: int


class UsageStatsOut(BaseModel):
    groups: list[UsageGroupStat]
    top_keys: list[UsageTopKey]
    total_tokens_today: int
    generated_at: datetime


class DashboardEndpointOut(BaseModel):
    id: int
    name: str
    base_url: str
    provider: str
    status: str
    latency: int
    uptime: float
    agent_node: str | None


class DashboardAgentOut(BaseModel):
    id: int
    name: str
    region: str | None
    status: str
    last_seen_at: datetime | None
    endpoint_url: str | None


class DashboardStatusOut(BaseModel):
    endpoints: list[DashboardEndpointOut]
    agents: list[DashboardAgentOut]
    generated_at: datetime


class ModelMapCreate(BaseModel):
    endpoint_id: int
    model_alias: str = Field(..., min_length=1)
    real_model: str = Field(..., min_length=1)


class ModelMapUpdate(BaseModel):
    model_alias: str | None = None
    real_model: str | None = None


class ModelMapOut(BaseModel):
    id: int
    endpoint_id: int
    model_alias: str
    real_model: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class EndpointProbeOut(BaseModel):
    provider: str
    probe_status: str
    probe_status_code: int | None
    probe_message: str | None = None
    discovered_models: list[str]
    manual_models: list[ModelMapOut]


class RequestLogOut(BaseModel):
    id: int
    request_id: str
    trace_id: str
    model_alias: str
    endpoint_id: int
    api_key_id: int
    rule_group: str | None
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None
    latency_ms: int
    ttft_ms: int | None
    tps: float | None
    status_code: int
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class OverviewOut(BaseModel):
    endpoints: int
    api_keys: int
    model_maps: int
    request_logs: int
    generated_at: datetime


class HealthStatusOut(BaseModel):
    api_key_id: int
    endpoint_id: int
    endpoint_name: str
    rule_group: str
    is_active: bool
    probe_status: str
    probe_status_code: int | None
    probe_latency_ms: int | None
    probe_checked_at: datetime | None
    probe_real_model: str | None
    circuit_state: str
    circuit_failures: int
    circuit_ttl_seconds: int | None


class MetricsBucketOut(BaseModel):
    bucket_start: datetime
    request_count: int
    rps: float
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    avg_latency_ms: int | None


class HealthProbeBucketOut(BaseModel):
    bucket_start: datetime
    success_count: int
    failure_count: int
    error_count: int
    avg_latency_ms: int | None


class AlertPolicyOut(BaseModel):
    event: str
    enabled: bool
    silence_until: datetime | None
    threshold_ms: int | None


class AlertPolicyUpdate(BaseModel):
    enabled: bool | None = None
    silence_minutes: int | None = Field(default=None, ge=0, le=10080)
    silence_until: datetime | None = None
    threshold_ms: int | None = Field(default=None, ge=0, le=120000)


class TelegramConfigOut(BaseModel):
    configured: bool
    bot_token_masked: str | None = None
    chat_id: str | None = None


class TelegramConfigUpdate(BaseModel):
    bot_token: str | None = None
    chat_id: str | None = None


class TelegramTestOut(BaseModel):
    status: str = "ok"
    detail: str


class AgentBootstrapRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)


class AgentBootstrapOut(BaseModel):
    agent_id: int
    name: str
    token: str
    install_command: str


class AgentHeartbeatRequest(BaseModel):
    name: str = Field(..., min_length=1)
    region: str | None = None
    endpoint_url: str | None = None
    token: str | None = None
    supports_gpt: bool | None = None
    supports_gemini: bool | None = None
    supports_claude: bool | None = None
    probe_latency_ms: int | None = Field(default=None, ge=0, le=120000)


class AgentStatusOut(BaseModel):
    id: int
    name: str
    region: str | None
    endpoint_url: str | None
    supports_gpt: bool | None = None
    supports_gemini: bool | None = None
    supports_claude: bool | None = None
    probe_latency_ms: int | None = None
    probe_checked_at: datetime | None = None
    is_active: bool
    last_seen_at: datetime | None
    status: str


class DeleteResponse(BaseModel):
    status: str = "ok"


class RouteTestRequest(BaseModel):
    model: str = Field(..., min_length=1)
    rule_group: str = "default"


class RouteCandidateOut(BaseModel):
    order: int
    endpoint_id: int
    endpoint_name: str
    api_key_id: int
    weight: int
    real_model: str


class RouteTestResponse(BaseModel):
    model: str
    rule_group: str
    candidates: list[RouteCandidateOut]


class RuleAccessKeyCreate(BaseModel):
    name: str | None = Field(default=None, max_length=128)


class RuleAccessKeyUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=128)
    is_active: bool | None = None


class RuleAccessKeyIssueOut(BaseModel):
    id: int
    rule_id: int
    name: str | None
    key: str
    is_active: bool
    created_at: datetime


class RuleAccessKeyOut(BaseModel):
    id: int
    rule_id: int
    name: str | None
    key_preview: str
    key: str | None = None
    is_active: bool
    created_at: datetime

