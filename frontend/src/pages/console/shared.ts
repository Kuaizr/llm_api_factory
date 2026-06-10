export const apiBase =
  import.meta.env.VITE_API_BASE ??
  (typeof window !== "undefined" ? window.location.origin : "http://localhost:8000");

export const tokenStorageKey = "llm_admin_token";
export const consoleThemeStorageKey = "llm_console_theme";
export const consoleAvatarStorageKey = "llm_console_avatar";

export type EndpointStatus = "online" | "degraded" | "offline";

export type ApiKey = {
  id: number;
  key_preview: string;
  rule_group?: string;
  rule_groups?: string[];
  rpm_limit: number | null;
  daily_limit: number | null;
  used_today: number;
  is_active: boolean;
  name?: string | null;
};

export type HealthStatus = {
  api_key_id: number;
  probe_status: string;
  probe_status_code: number | null;
  probe_latency_ms: number | null;
  probe_checked_at: string | null;
  circuit_state: string;
  circuit_failures: number;
};

export type ModelMap = {
  id: number;
  endpoint_id: number;
  model_alias: string;
  real_model: string;
  probe_managed?: boolean;
  created_at: string;
};

export type EndpointProbeResult = {
  provider: string;
  probe_status: "success" | "failure" | "error";
  probe_status_code: number | null;
  probe_message: string | null;
  discovered_models: string[];
  manual_models: ModelMap[];
};

export type TelegramConfig = {
  configured: boolean;
  bot_token_masked: string | null;
  chat_id: string | null;
};

export type Endpoint = {
  id: number;
  name: string;
  base_url: string;
  auth_header_name?: string;
  auth_header_prefix?: string;
  provider: string;
  access_mode?: "direct" | "via_agent";
  is_active: boolean;
  status: EndpointStatus;
  latency: number;
  uptime: number;
  is_agent_enabled: boolean;
  agent_node?: string | null;
  probe_interval_seconds?: number | null;
  model_count: number;
  keys: ApiKey[];
  strategy: string;
  // 扩展字段
  url_path_suffix?: string | null;
  extra_headers?: Record<string, string> | null;
  extra_cookies?: string | null;
  extra_query_params?: Record<string, string> | null;
  oauth_config?: Record<string, string> | null;
  request_body_template?: string | null;
};

export type RoutingRule = {
  id: number;
  model_pattern: string;
  group_name: string;
  target_key_ids: number[];
  priority: number;
  strategy: string;
  is_active: boolean;
  dump_enabled?: boolean;
  dump_path?: string | null;
  request_count?: number;
  total_tokens?: number;
  avg_ttft_ms?: number | null;
  avg_tps?: number | null;
};

export type RoutingRuleSavePayload = {
  id?: number;
  model_pattern: string;
  group_name: string;
  target_key_ids: number[];
  priority: number;
  strategy: string;
  is_active: boolean;
  dump_enabled: boolean;
  dump_path: string | null;
};

export type RuleGroupEligibilityResult = {
  group_name: string;
  eligible: boolean;
  reason: string | null;
  probed: boolean;
  required_patterns: string[];
  matched_models: string[];
};

export type AgentNode = {
  id: number;
  name: string;
  region: string | null;
  network_group?: string | null;
  labels?: string[];
  status: string;
  last_seen_at: string | null;
  endpoint_url: string | null;
  supports_gpt?: boolean | null;
  supports_gemini?: boolean | null;
  supports_claude?: boolean | null;
  probe_latency_ms?: number | null;
  probe_checked_at?: string | null;
  is_draining?: boolean;
};

export type AgentBootstrapResult = {
  agent_id: number;
  name: string;
  token: string;
  install_command: string;
};

export type UsageGroup = {
  group_name: string;
  percent: number;
  total_tokens: number;
};

export type UsageTopKey = {
  api_key_id: number;
  endpoint_name: string;
  key_preview: string;
  total_tokens: number;
};

export type UsageStats = {
  groups: UsageGroup[];
  top_keys: UsageTopKey[];
  total_tokens_today: number;
  generated_at: string;
};

export type MetricsBucket = {
  bucket_start: string;
  request_count: number;
  rps: number;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  avg_latency_ms: number | null;
};

export type UsageTrendRange = "hour" | "day" | "week";

export type EndpointFormState = {
  name: string;
  base_url: string;
  auth_header_name: string;
  auth_header_prefix: string;
  provider: string;
  agent_node: string;
  probe_interval_seconds: string;
  is_active: boolean;
  url_path_suffix?: string;
  extra_headers?: Record<string, string>;
  extra_cookies?: string;
  extra_query_params?: Record<string, string>;
  oauth_config?: Record<string, string>;
  request_body_template?: string;
};

export type AgentDeployFormState = {
  name: string;
};

export const normalizeRuleGroups = (
  groups: string[] | null | undefined,
  fallback?: string | null
) => {
  const values: string[] = [];
  if (Array.isArray(groups)) {
    values.push(...groups);
  }
  if (fallback) {
    values.push(fallback);
  }

  const normalized: string[] = [];
  const seen = new Set<string>();
  values.forEach((raw) => {
    const trimmed = String(raw ?? "").trim();
    if (!trimmed) {
      return;
    }
    const canonical = trimmed.toLowerCase() === "default" ? "default" : trimmed;
    const key = canonical.toLowerCase();
    if (seen.has(key)) {
      return;
    }
    seen.add(key);
    normalized.push(canonical);
  });

  if (!seen.has("default")) {
    normalized.unshift("default");
  } else if (normalized[0]?.toLowerCase() !== "default") {
    return ["default", ...normalized.filter((item) => item.toLowerCase() !== "default")];
  }
  return normalized;
};

export const getPrimaryRuleGroup = (key: Pick<ApiKey, "rule_group" | "rule_groups">) => {
  const groups = normalizeRuleGroups(key.rule_groups, key.rule_group);
  return groups.find((group) => group.toLowerCase() !== "default") || "default";
};

export const keyHasRuleGroup = (
  key: Pick<ApiKey, "rule_group" | "rule_groups">,
  group: string
) => {
  const target = String(group || "").trim().toLowerCase() || "default";
  return normalizeRuleGroups(key.rule_groups, key.rule_group).some(
    (item) => item.toLowerCase() === target
  );
};

export const buildHeaders = (token: string | null, jsonBody = false) => {
  const headers: Record<string, string> = {};
  if (token) {
    headers.Authorization = `Bearer ${token}`;
  }
  if (jsonBody) {
    headers["Content-Type"] = "application/json";
  }
  return headers;
};

export const formatTokens = (value: number) => {
  if (value >= 1_000_000_000_000) return `${(value / 1_000_000_000_000).toFixed(1)}T`;
  if (value >= 1_000_000_000) return `${(value / 1_000_000_000).toFixed(1)}G`;
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(1)}K`;
  return `${value}`;
};

export const usageTrendConfig: Record<
  UsageTrendRange,
  { label: string; hours: number; bucketMinutes: number }
> = {
  hour: { label: "按小时（近 24 小时）", hours: 24, bucketMinutes: 60 },
  day: { label: "按天（近 30 天）", hours: 24 * 30, bucketMinutes: 1440 },
  week: { label: "按周（近 12 周）", hours: 24 * 7 * 12, bucketMinutes: 10080 },
};

export const formatTimestamp = (value: string | null) =>
  value ? new Date(value).toLocaleString() : "--";

export const maskEndpointUrl = (value: string) => {
  if (!value) return "hidden";
  try {
    const url = new URL(value);
    return `${url.protocol}//***`;
  } catch {
    return "***";
  }
};

export const resolveKeyStatus = (key: ApiKey, health: HealthStatus | undefined) => {
  if (!key.is_active) {
    return {
      label: "Disabled",
      className: "text-gray-500 bg-gray-800 border-gray-700",
      isAvailable: false,
    };
  }
  if (key.daily_limit && key.used_today >= key.daily_limit) {
    return {
      label: "超出限额",
      className: "text-orange-400 bg-orange-900/20 border-orange-900/30",
      isAvailable: false,
    };
  }
  if (health?.probe_status_code === 429) {
    return {
      label: "超出RPM/TPM",
      className: "text-yellow-400 bg-yellow-900/20 border-yellow-900/30",
      isAvailable: false,
    };
  }
  if (health?.circuit_state === "open") {
    return {
      label: "等待恢复中",
      className: "text-blue-400 bg-blue-900/20 border-blue-900/30",
      isAvailable: false,
    };
  }
  if (health && health.probe_status !== "success") {
    return {
      label: "出错",
      className: "text-red-400 bg-red-900/20 border-red-900/30",
      isAvailable: false,
    };
  }
  return {
    label: "Active",
    className: "text-green-400 bg-green-900/20 border-green-900/30",
    isAvailable: true,
  };
};
