import type {
  AgentBootstrapResult,
  AgentNode,
  ApiKeyDirectTestResult,
  ApiKey,
  Endpoint,
  EndpointProbeResult,
  EndpointStatus,
  HealthStatus,
  MetricsBucket,
  ModelMap,
  RuleGroupEligibilityResult,
  RoutingRule,
  DumpSearchItem,
  DumpSearchResult,
  StatsDistributionItem,
  StatsKpiValue,
  StatsLatencyBucket,
  StatsOverview,
  StatsTimeseriesBucket,
  StatsTopKey,
  TelegramConfig,
  UsageGroup,
  UsageStats,
  UsageTopKey,
} from "./shared";

export type DashboardOverview = {
  endpoints: number;
  api_keys: number;
  model_maps: number;
  request_logs: number;
  generated_at: string;
};

export type DashboardHealthStatus = {
  api_key_id: number;
  endpoint_id: number;
  endpoint_name: string;
  rule_group: string;
  is_active: boolean;
  probe_status: string;
  probe_status_code: number | null;
  probe_latency_ms: number | null;
  probe_checked_at: string | null;
  probe_real_model: string | null;
  circuit_state: string;
  circuit_failures: number;
  circuit_ttl_seconds: number | null;
};

export type DashboardHealthProbeBucket = {
  bucket_start: string;
  success_count: number;
  failure_count: number;
  error_count: number;
  avg_latency_ms: number | null;
};

export type DashboardAlertPolicy = {
  event: string;
  enabled: boolean;
  silence_until: string | null;
  threshold_ms: number | null;
};

type PublicEndpoint = Omit<
  Endpoint,
  "keys" | "model_count" | "is_agent_enabled" | "strategy" | "is_active"
>;

export type DashboardStatusPayload = {
  endpoints: PublicEndpoint[];
  agents: AgentNode[];
};

const endpointStatuses = new Set<EndpointStatus>(["online", "degraded", "offline"]);

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

const isString = (value: unknown): value is string => typeof value === "string";
const isNumber = (value: unknown): value is number =>
  typeof value === "number" && Number.isFinite(value);
const isBoolean = (value: unknown): value is boolean => typeof value === "boolean";
const isNullableString = (value: unknown): value is string | null =>
  value === null || isString(value);
const isNullableNumber = (value: unknown): value is number | null =>
  value === null || isNumber(value);
const isNullableBoolean = (value: unknown): value is boolean | null =>
  value === null || isBoolean(value);

const parseStringArray = (value: unknown): string[] | null => {
  if (!Array.isArray(value) || value.some((item) => !isString(item))) {
    return null;
  }
  return value;
};

export const parseStringList = parseStringArray;

const parseNumberArray = (value: unknown): number[] | null => {
  if (!Array.isArray(value) || value.some((item) => !isNumber(item))) {
    return null;
  }
  return value;
};

const parseStringRecord = (value: unknown): Record<string, string> | null => {
  if (value === null || value === undefined) {
    return null;
  }
  if (!isRecord(value)) {
    return null;
  }
  const entries = Object.entries(value);
  if (entries.some(([, item]) => !isString(item))) {
    return null;
  }
  return Object.fromEntries(entries) as Record<string, string>;
};

const parseArray = <T>(
  value: unknown,
  parser: (item: unknown) => T | null
): T[] | null => {
  if (!Array.isArray(value)) {
    return null;
  }
  const parsed: T[] = [];
  for (const item of value) {
    const next = parser(item);
    if (next === null) {
      return null;
    }
    parsed.push(next);
  }
  return parsed;
};

const parseEndpointStatus = (value: unknown): EndpointStatus | null =>
  isString(value) && endpointStatuses.has(value as EndpointStatus)
    ? (value as EndpointStatus)
    : null;

const parseApiKey = (value: unknown): ApiKey | null => {
  if (!isRecord(value)) {
    return null;
  }
  const id = value.id;
  const keyPreview = value.key_preview;
  const rpmLimit = value.rpm_limit;
  const dailyLimit = value.daily_limit;
  const usedToday = value.used_today;
  const isActive = value.is_active;
  if (
    !isNumber(id) ||
    !isString(keyPreview) ||
    !isNullableNumber(rpmLimit) ||
    !isNullableNumber(dailyLimit) ||
    !isNumber(usedToday) ||
    !isBoolean(isActive)
  ) {
    return null;
  }
  const ruleGroups =
    value.rule_groups === undefined ? undefined : parseStringArray(value.rule_groups);
  if (value.rule_groups !== undefined && ruleGroups === null) {
    return null;
  }
  return {
    id,
    key_preview: keyPreview,
    rule_group: isString(value.rule_group) ? value.rule_group : undefined,
    rule_groups: ruleGroups ?? undefined,
    rpm_limit: rpmLimit,
    daily_limit: dailyLimit,
    used_today: usedToday,
    is_active: isActive,
    name: isNullableString(value.name) ? value.name : undefined,
    codex_usage: isRecord(value.codex_usage) ? value.codex_usage : undefined,
  };
};

const parseEndpoint = (value: unknown, requireAdminFields: boolean): Endpoint | null => {
  if (!isRecord(value)) {
    return null;
  }
  const id = value.id;
  const name = value.name;
  const baseUrl = value.base_url;
  const provider = value.provider;
  const status = parseEndpointStatus(value.status);
  const latency = value.latency;
  const uptime = value.uptime;
  const agentNode = value.agent_node;
  const probeInterval =
    value.probe_interval_seconds === undefined ? null : value.probe_interval_seconds;
  if (
    !isNumber(id) ||
    !isString(name) ||
    !isString(baseUrl) ||
    !isString(provider) ||
    status === null ||
    !isNumber(latency) ||
    !isNumber(uptime) ||
    !isNullableString(agentNode) ||
    !isNullableNumber(probeInterval)
  ) {
    return null;
  }
  const keys = value.keys === undefined ? [] : parseArray(value.keys, parseApiKey);
  if (keys === null) {
    return null;
  }
  if (requireAdminFields && value.model_count !== undefined && !isNumber(value.model_count)) {
    return null;
  }
  const accessMode =
    value.access_mode === "direct" || value.access_mode === "via_agent"
      ? value.access_mode
      : undefined;
  return {
    id,
    name,
    base_url: baseUrl,
    auth_header_name: isString(value.auth_header_name) ? value.auth_header_name : undefined,
    auth_header_prefix: isString(value.auth_header_prefix) ? value.auth_header_prefix : undefined,
    provider,
    access_mode: accessMode,
    is_active: isBoolean(value.is_active) ? value.is_active : status !== "offline",
    status,
    latency,
    uptime,
    is_agent_enabled: isBoolean(value.is_agent_enabled)
      ? value.is_agent_enabled
      : accessMode === "via_agent" || Boolean(agentNode),
    agent_node: agentNode,
    probe_interval_seconds: probeInterval,
    model_count: isNumber(value.model_count) ? value.model_count : 0,
    keys,
    strategy: isString(value.strategy) ? value.strategy : "weighted_round_robin",
    url_path_suffix: isNullableString(value.url_path_suffix) ? value.url_path_suffix : null,
    extra_headers: parseStringRecord(value.extra_headers),
    extra_cookies: isNullableString(value.extra_cookies) ? value.extra_cookies : null,
    extra_query_params: parseStringRecord(value.extra_query_params),
    oauth_config: parseStringRecord(value.oauth_config),
    request_body_template: isNullableString(value.request_body_template)
      ? value.request_body_template
      : null,
  };
};

export const parseEndpointList = (value: unknown): Endpoint[] | null =>
  parseArray(value, (item) => parseEndpoint(item, true));

export const parseAgent = (value: unknown): AgentNode | null => {
  if (!isRecord(value)) {
    return null;
  }
  const id = value.id;
  const name = value.name;
  const status = value.status;
  const lastSeen = value.last_seen_at;
  const endpointUrl = value.endpoint_url;
  if (
    !isNumber(id) ||
    !isString(name) ||
    !isString(status) ||
    !isNullableString(lastSeen) ||
    !isNullableString(endpointUrl)
  ) {
    return null;
  }
  const labels = value.labels === undefined ? undefined : parseStringArray(value.labels);
  if (value.labels !== undefined && labels === null) {
    return null;
  }
  return {
    id,
    name,
    region: isNullableString(value.region) ? value.region : null,
    network_group: isNullableString(value.network_group) ? value.network_group : null,
    labels: labels ?? undefined,
    status,
    last_seen_at: lastSeen,
    endpoint_url: endpointUrl,
    supports_gpt: typeof value.supports_gpt === "boolean" ? value.supports_gpt : null,
    supports_gemini: typeof value.supports_gemini === "boolean" ? value.supports_gemini : null,
    supports_claude: typeof value.supports_claude === "boolean" ? value.supports_claude : null,
    probe_latency_ms: isNullableNumber(value.probe_latency_ms) ? value.probe_latency_ms : null,
    probe_checked_at: isNullableString(value.probe_checked_at) ? value.probe_checked_at : null,
    is_draining: isBoolean(value.is_draining) ? value.is_draining : false,
    is_active: isBoolean(value.is_active) ? value.is_active : true,
  };
};

export const parseAgentList = (value: unknown): AgentNode[] | null =>
  parseArray(value, parseAgent);

export const parseDashboardStatus = (value: unknown): DashboardStatusPayload | null => {
  if (!isRecord(value)) {
    return null;
  }
  const endpoints = parseArray(value.endpoints, (item) => parseEndpoint(item, false));
  const agents = parseAgentList(value.agents);
  if (endpoints === null || agents === null) {
    return null;
  }
  return { endpoints, agents };
};

export const parseRoutingRule = (value: unknown): RoutingRule | null => {
  if (!isRecord(value)) {
    return null;
  }
  const targetKeyIds = parseNumberArray(value.target_key_ids);
  const parsedExposureFormats = parseStringArray(value.exposure_formats);
  if (
    !isNumber(value.id) ||
    !isString(value.model_pattern) ||
    !isString(value.group_name) ||
    parsedExposureFormats === null ||
    parsedExposureFormats.length === 0 ||
    targetKeyIds === null ||
    !isNumber(value.priority) ||
    !isBoolean(value.is_active)
  ) {
    return null;
  }
  return {
    id: value.id,
    model_pattern: value.model_pattern,
    group_name: value.group_name,
    exposure_formats: parsedExposureFormats,
    target_key_ids: targetKeyIds,
    priority: value.priority,
    strategy: isString(value.strategy) ? value.strategy : "weighted_round_robin",
    is_active: value.is_active,
    dump_enabled: isBoolean(value.dump_enabled) ? value.dump_enabled : false,
    dump_path: isNullableString(value.dump_path) ? value.dump_path : null,
    request_count: isNumber(value.request_count) ? value.request_count : 0,
    total_tokens: isNumber(value.total_tokens) ? value.total_tokens : 0,
    avg_ttft_ms: isNullableNumber(value.avg_ttft_ms) ? value.avg_ttft_ms : null,
    avg_tps: isNullableNumber(value.avg_tps) ? value.avg_tps : null,
  };
};

export const parseRoutingRuleList = (value: unknown): RoutingRule[] | null =>
  parseArray(value, parseRoutingRule);

const parseUsageGroup = (value: unknown): UsageGroup | null => {
  if (
    !isRecord(value) ||
    !isString(value.group_name) ||
    !isNumber(value.percent) ||
    !isNumber(value.total_tokens)
  ) {
    return null;
  }
  return {
    group_name: value.group_name,
    percent: value.percent,
    total_tokens: value.total_tokens,
  };
};

const parseUsageTopKey = (value: unknown): UsageTopKey | null => {
  if (
    !isRecord(value) ||
    !isNumber(value.api_key_id) ||
    !isString(value.endpoint_name) ||
    !isString(value.key_preview) ||
    !isNumber(value.total_tokens)
  ) {
    return null;
  }
  return {
    api_key_id: value.api_key_id,
    endpoint_name: value.endpoint_name,
    key_preview: value.key_preview,
    total_tokens: value.total_tokens,
    request_count: isNumber(value.request_count) ? value.request_count : 0,
    cache_hit_rate: isNullableNumber(value.cache_hit_rate) ? value.cache_hit_rate : null,
    avg_latency_ms: isNullableNumber(value.avg_latency_ms) ? value.avg_latency_ms : null,
  };
};

export const parseModelMap = (value: unknown): ModelMap | null => {
  if (
    !isRecord(value) ||
    !isNumber(value.id) ||
    !isNumber(value.endpoint_id) ||
    !isString(value.model_alias) ||
    !isString(value.real_model) ||
    !isString(value.created_at)
  ) {
    return null;
  }
  return {
    id: value.id,
    endpoint_id: value.endpoint_id,
    model_alias: value.model_alias,
    real_model: value.real_model,
    probe_managed: isBoolean(value.probe_managed) ? value.probe_managed : false,
    created_at: value.created_at,
  };
};

export const parseModelMapList = (value: unknown): ModelMap[] | null =>
  parseArray(value, parseModelMap);

export const parseEndpointProbeResult = (
  value: unknown
): EndpointProbeResult | null => {
  if (!isRecord(value)) {
    return null;
  }
  const discoveredModels = parseStringArray(value.discovered_models);
  const manualModels = parseModelMapList(value.manual_models);
  const probeStatus = value.probe_status;
  if (
    !isString(value.provider) ||
    !isString(probeStatus) ||
    !["success", "failure", "error"].includes(probeStatus) ||
    !isNullableNumber(value.probe_status_code) ||
    !isNullableString(value.probe_message) ||
    discoveredModels === null ||
    manualModels === null
  ) {
    return null;
  }
  return {
    provider: value.provider,
    probe_status: probeStatus as EndpointProbeResult["probe_status"],
    probe_status_code: value.probe_status_code,
    probe_message: value.probe_message,
    discovered_models: discoveredModels,
    manual_models: manualModels,
  };
};

export const parseAgentBootstrapResult = (
  value: unknown
): AgentBootstrapResult | null => {
  if (
    !isRecord(value) ||
    !isNumber(value.agent_id) ||
    !isString(value.name) ||
    !isString(value.token) ||
    !isString(value.install_command)
  ) {
    return null;
  }
  return {
    agent_id: value.agent_id,
    name: value.name,
    token: value.token,
    install_command: value.install_command,
  };
};

export const parseRuleGroupEligibilityResult = (
  value: unknown
): RuleGroupEligibilityResult | null => {
  if (!isRecord(value)) {
    return null;
  }
  const requiredPatterns = parseStringArray(value.required_patterns);
  const matchedModels = parseStringArray(value.matched_models);
  if (
    !isString(value.group_name) ||
    !isBoolean(value.eligible) ||
    !isNullableString(value.reason) ||
    !isBoolean(value.probed) ||
    requiredPatterns === null ||
    matchedModels === null
  ) {
    return null;
  }
  return {
    group_name: value.group_name,
    eligible: value.eligible,
    reason: value.reason,
    probed: value.probed,
    required_patterns: requiredPatterns,
    matched_models: matchedModels,
  };
};

export const parseApiKeyDirectTestResult = (
  value: unknown
): ApiKeyDirectTestResult | null => {
  if (
    !isRecord(value) ||
    !isNumber(value.api_key_id) ||
    !isNumber(value.endpoint_id) ||
    !isString(value.endpoint_name) ||
    !isString(value.provider) ||
    !isString(value.request_template) ||
    !isString(value.model) ||
    !isString(value.prompt) ||
    !isNumber(value.status_code) ||
    !isBoolean(value.ok) ||
    !isNumber(value.latency_ms) ||
    !isNullableString(value.output_text) ||
    !isNullableString(value.error_reason) ||
    !isString(value.upstream_url)
  ) {
    return null;
  }
  return {
    api_key_id: value.api_key_id,
    endpoint_id: value.endpoint_id,
    endpoint_name: value.endpoint_name,
    provider: value.provider,
    request_template: value.request_template,
    model: value.model,
    prompt: value.prompt,
    status_code: value.status_code,
    ok: value.ok,
    latency_ms: value.latency_ms,
    output_text: value.output_text,
    error_reason: value.error_reason,
    upstream_url: value.upstream_url,
    raw_response: value.raw_response,
  };
};

export const parseUsageStats = (value: unknown): UsageStats | null => {
  if (!isRecord(value)) {
    return null;
  }
  const groups = parseArray(value.groups, parseUsageGroup);
  const topKeys = parseArray(value.top_keys, parseUsageTopKey);
  if (groups === null || topKeys === null || !isString(value.generated_at)) {
    return null;
  }
  return {
    groups,
    top_keys: topKeys,
    total_tokens_today: isNumber(value.total_tokens_today) ? value.total_tokens_today : 0,
    generated_at: value.generated_at,
  };
};

const parseMetricsBucket = (value: unknown): MetricsBucket | null => {
  if (
    !isRecord(value) ||
    !isString(value.bucket_start) ||
    !isNumber(value.request_count) ||
    !isNumber(value.rps) ||
    !isNumber(value.prompt_tokens) ||
    !isNumber(value.completion_tokens) ||
    !isNumber(value.total_tokens) ||
    !isNullableNumber(value.avg_latency_ms)
  ) {
    return null;
  }
  return {
    bucket_start: value.bucket_start,
    request_count: value.request_count,
    rps: value.rps,
    prompt_tokens: value.prompt_tokens,
    completion_tokens: value.completion_tokens,
    total_tokens: value.total_tokens,
    avg_latency_ms: value.avg_latency_ms,
  };
};

export const parseMetricsBucketList = (value: unknown): MetricsBucket[] | null =>
  parseArray(value, parseMetricsBucket);

const parseStatsKpiValue = (value: unknown): StatsKpiValue | null => {
  if (
    !isRecord(value) ||
    !isNumber(value.value) ||
    !isNumber(value.previous_value) ||
    !isNullableNumber(value.change_percent)
  ) {
    return null;
  }
  return {
    value: value.value,
    previous_value: value.previous_value,
    change_percent: value.change_percent,
  };
};

export const parseStatsOverview = (value: unknown): StatsOverview | null => {
  if (!isRecord(value)) {
    return null;
  }
  const totalRequests = parseStatsKpiValue(value.total_requests);
  const totalTokens = parseStatsKpiValue(value.total_tokens);
  const cacheHitRate = parseStatsKpiValue(value.cache_hit_rate);
  const avgLatency = parseStatsKpiValue(value.avg_latency_ms);
  if (
    !totalRequests ||
    !totalTokens ||
    !cacheHitRate ||
    !avgLatency ||
    !isNumber(value.prompt_tokens) ||
    !isNumber(value.completion_tokens) ||
    !isNumber(value.cached_tokens) ||
    !isNullableNumber(value.p95_latency_ms) ||
    !isString(value.generated_at)
  ) {
    return null;
  }
  return {
    total_requests: totalRequests,
    total_tokens: totalTokens,
    cache_hit_rate: cacheHitRate,
    avg_latency_ms: avgLatency,
    prompt_tokens: value.prompt_tokens,
    completion_tokens: value.completion_tokens,
    cached_tokens: value.cached_tokens,
    p95_latency_ms: value.p95_latency_ms,
    generated_at: value.generated_at,
  };
};

const parseStatsTimeseriesBucket = (
  value: unknown
): StatsTimeseriesBucket | null => {
  if (
    !isRecord(value) ||
    !isString(value.bucket_start) ||
    !isNumber(value.request_count) ||
    !isNumber(value.prompt_tokens) ||
    !isNumber(value.completion_tokens) ||
    !isNumber(value.total_tokens) ||
    !isNumber(value.cached_tokens) ||
    !isNumber(value.cache_hits) ||
    !isNumber(value.cache_hit_rate) ||
    !isNullableNumber(value.avg_latency_ms)
  ) {
    return null;
  }
  return {
    bucket_start: value.bucket_start,
    request_count: value.request_count,
    prompt_tokens: value.prompt_tokens,
    completion_tokens: value.completion_tokens,
    total_tokens: value.total_tokens,
    cached_tokens: value.cached_tokens,
    cache_hits: value.cache_hits,
    cache_hit_rate: value.cache_hit_rate,
    avg_latency_ms: value.avg_latency_ms,
  };
};

export const parseStatsTimeseriesBucketList = (
  value: unknown
): StatsTimeseriesBucket[] | null => parseArray(value, parseStatsTimeseriesBucket);

const parseStatsLatencyBucket = (value: unknown): StatsLatencyBucket | null => {
  if (
    !isRecord(value) ||
    !isString(value.bucket_start) ||
    !isNullableNumber(value.p50_ms) ||
    !isNullableNumber(value.p95_ms) ||
    !isNullableNumber(value.p99_ms)
  ) {
    return null;
  }
  return {
    bucket_start: value.bucket_start,
    p50_ms: value.p50_ms,
    p95_ms: value.p95_ms,
    p99_ms: value.p99_ms,
  };
};

export const parseStatsLatencyBucketList = (
  value: unknown
): StatsLatencyBucket[] | null => parseArray(value, parseStatsLatencyBucket);

const parseStatsDistributionItem = (
  value: unknown
): StatsDistributionItem | null => {
  if (
    !isRecord(value) ||
    !isString(value.name) ||
    !isNumber(value.request_count) ||
    !isNumber(value.total_tokens) ||
    !isNumber(value.percent)
  ) {
    return null;
  }
  return {
    name: value.name,
    request_count: value.request_count,
    total_tokens: value.total_tokens,
    percent: value.percent,
  };
};

export const parseStatsDistributionList = (
  value: unknown
): StatsDistributionItem[] | null => parseArray(value, parseStatsDistributionItem);

const parseStatsTopKey = (value: unknown): StatsTopKey | null => {
  if (
    !isRecord(value) ||
    !isNumber(value.api_key_id) ||
    !isString(value.endpoint_name) ||
    !isString(value.key_preview) ||
    !isNumber(value.request_count) ||
    !isNumber(value.total_tokens) ||
    !isNullableNumber(value.cache_hit_rate) ||
    !isNullableNumber(value.avg_latency_ms)
  ) {
    return null;
  }
  return {
    api_key_id: value.api_key_id,
    endpoint_name: value.endpoint_name,
    key_preview: value.key_preview,
    request_count: value.request_count,
    total_tokens: value.total_tokens,
    cache_hit_rate: value.cache_hit_rate,
    avg_latency_ms: value.avg_latency_ms,
  };
};

export const parseStatsTopKeyList = (value: unknown): StatsTopKey[] | null =>
  parseArray(value, parseStatsTopKey);

const parseDumpSearchItem = (value: unknown): DumpSearchItem | null => {
  if (
    !isRecord(value) ||
    !isString(value.request_id) ||
    !isString(value.trace_id) ||
    !isString(value.model_alias) ||
    !isString(value.real_model) ||
    !isNumber(value.endpoint_id) ||
    !isNullableString(value.endpoint_name) ||
    !isNullableNumber(value.api_key_id) ||
    !isString(value.rule_group) ||
    !isNullableNumber(value.prompt_tokens) ||
    !isNullableNumber(value.completion_tokens) ||
    !isNullableNumber(value.total_tokens) ||
    !isNullableNumber(value.cached_tokens) ||
    !isNullableNumber(value.latency_ms) ||
    !isBoolean(value.is_stream) ||
    !isBoolean(value.is_cache_hit) ||
    !isNullableBoolean(value.stream_complete) ||
    !isNullableString(value.previous_interaction_id) ||
    !isNullableNumber(value.status_code) ||
    !isNullableString(value.file_path) ||
    !isNullableString(value.hostname) ||
    !isString(value.created_at)
  ) {
    return null;
  }
  return {
    request_id: value.request_id,
    trace_id: value.trace_id,
    model_alias: value.model_alias,
    real_model: value.real_model,
    endpoint_id: value.endpoint_id,
    endpoint_name: value.endpoint_name,
    api_key_id: value.api_key_id,
    rule_group: value.rule_group,
    prompt_tokens: value.prompt_tokens,
    completion_tokens: value.completion_tokens,
    total_tokens: value.total_tokens,
    cached_tokens: value.cached_tokens,
    latency_ms: value.latency_ms,
    is_stream: value.is_stream,
    is_cache_hit: value.is_cache_hit,
    stream_complete: value.stream_complete,
    previous_interaction_id: value.previous_interaction_id,
    status_code: value.status_code,
    file_path: value.file_path,
    hostname: value.hostname,
    created_at: value.created_at,
  };
};

export const parseDumpSearchResult = (value: unknown): DumpSearchResult | null => {
  if (
    !isRecord(value) ||
    !isNumber(value.total) ||
    !isNumber(value.limit) ||
    !isNumber(value.offset) ||
    !isString(value.generated_at)
  ) {
    return null;
  }
  const items = parseArray(value.items, parseDumpSearchItem);
  if (!items) {
    return null;
  }
  return {
    items,
    total: value.total,
    limit: value.limit,
    offset: value.offset,
    generated_at: value.generated_at,
  };
};

export const parseDashboardOverview = (value: unknown): DashboardOverview | null => {
  if (
    !isRecord(value) ||
    !isNumber(value.endpoints) ||
    !isNumber(value.api_keys) ||
    !isNumber(value.model_maps) ||
    !isNumber(value.request_logs) ||
    !isString(value.generated_at)
  ) {
    return null;
  }
  return {
    endpoints: value.endpoints,
    api_keys: value.api_keys,
    model_maps: value.model_maps,
    request_logs: value.request_logs,
    generated_at: value.generated_at,
  };
};

export const parseHealthStatus = (value: unknown): HealthStatus | null => {
  if (
    !isRecord(value) ||
    !isNumber(value.api_key_id) ||
    !isString(value.probe_status) ||
    !isNullableNumber(value.probe_status_code) ||
    !isNullableNumber(value.probe_latency_ms) ||
    !isNullableString(value.probe_checked_at) ||
    !isString(value.circuit_state) ||
    !isNumber(value.circuit_failures)
  ) {
    return null;
  }
  return {
    api_key_id: value.api_key_id,
    probe_status: value.probe_status,
    probe_status_code: value.probe_status_code,
    probe_latency_ms: value.probe_latency_ms,
    probe_checked_at: value.probe_checked_at,
    circuit_state: value.circuit_state,
    circuit_failures: value.circuit_failures,
  };
};

export const parseHealthStatusList = (value: unknown): HealthStatus[] | null =>
  parseArray(value, parseHealthStatus);

const parseDashboardHealthStatus = (value: unknown): DashboardHealthStatus | null => {
  if (
    !isRecord(value) ||
    !isNumber(value.api_key_id) ||
    !isNumber(value.endpoint_id) ||
    !isString(value.endpoint_name) ||
    !isString(value.rule_group) ||
    !isBoolean(value.is_active) ||
    !isString(value.probe_status) ||
    !isNullableNumber(value.probe_status_code) ||
    !isNullableNumber(value.probe_latency_ms) ||
    !isNullableString(value.probe_checked_at) ||
    !isNullableString(value.probe_real_model) ||
    !isString(value.circuit_state) ||
    !isNumber(value.circuit_failures) ||
    !isNullableNumber(value.circuit_ttl_seconds)
  ) {
    return null;
  }
  return {
    api_key_id: value.api_key_id,
    endpoint_id: value.endpoint_id,
    endpoint_name: value.endpoint_name,
    rule_group: value.rule_group,
    is_active: value.is_active,
    probe_status: value.probe_status,
    probe_status_code: value.probe_status_code,
    probe_latency_ms: value.probe_latency_ms,
    probe_checked_at: value.probe_checked_at,
    probe_real_model: value.probe_real_model,
    circuit_state: value.circuit_state,
    circuit_failures: value.circuit_failures,
    circuit_ttl_seconds: value.circuit_ttl_seconds,
  };
};

export const parseDashboardHealthStatusList = (
  value: unknown
): DashboardHealthStatus[] | null => parseArray(value, parseDashboardHealthStatus);

const parseDashboardHealthProbeBucket = (
  value: unknown
): DashboardHealthProbeBucket | null => {
  if (
    !isRecord(value) ||
    !isString(value.bucket_start) ||
    !isNumber(value.success_count) ||
    !isNumber(value.failure_count) ||
    !isNumber(value.error_count) ||
    !isNullableNumber(value.avg_latency_ms)
  ) {
    return null;
  }
  return {
    bucket_start: value.bucket_start,
    success_count: value.success_count,
    failure_count: value.failure_count,
    error_count: value.error_count,
    avg_latency_ms: value.avg_latency_ms,
  };
};

export const parseDashboardHealthProbeBucketList = (
  value: unknown
): DashboardHealthProbeBucket[] | null =>
  parseArray(value, parseDashboardHealthProbeBucket);

const parseDashboardAlertPolicy = (value: unknown): DashboardAlertPolicy | null => {
  if (
    !isRecord(value) ||
    !isString(value.event) ||
    !isBoolean(value.enabled) ||
    !isNullableString(value.silence_until) ||
    !isNullableNumber(value.threshold_ms)
  ) {
    return null;
  }
  return {
    event: value.event,
    enabled: value.enabled,
    silence_until: value.silence_until,
    threshold_ms: value.threshold_ms,
  };
};

export const parseDashboardAlertPolicyList = (
  value: unknown
): DashboardAlertPolicy[] | null => parseArray(value, parseDashboardAlertPolicy);

export const parseTelegramConfig = (value: unknown): TelegramConfig | null => {
  if (
    !isRecord(value) ||
    !isBoolean(value.configured) ||
    !isNullableString(value.bot_token_masked) ||
    !isNullableString(value.chat_id)
  ) {
    return null;
  }
  return {
    configured: value.configured,
    bot_token_masked: value.bot_token_masked,
    chat_id: value.chat_id,
  };
};
