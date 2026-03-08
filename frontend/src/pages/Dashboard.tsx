import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

type Overview = {
  endpoints: number;
  api_keys: number;
  model_maps: number;
  request_logs: number;
  generated_at: string;
};

type HealthStatus = {
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

type MetricsBucket = {
  bucket_start: string;
  request_count: number;
  rps: number;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  avg_latency_ms: number | null;
};

type HealthProbeBucket = {
  bucket_start: string;
  success_count: number;
  failure_count: number;
  error_count: number;
  avg_latency_ms: number | null;
};

type AlertPolicy = {
  event: string;
  enabled: boolean;
  silence_until: string | null;
  threshold_ms: number | null;
};

type AgentStatus = {
  id: number;
  name: string;
  region: string | null;
  endpoint_url: string | null;
  is_active: boolean;
  last_seen_at: string | null;
  status: string;
};

const apiBase = import.meta.env.VITE_API_BASE ?? "http://localhost:8000";
const adminToken = import.meta.env.VITE_ADMIN_TOKEN;

const buildHeaders = () => {
  const headers: Record<string, string> = {};
  if (adminToken) {
    headers.Authorization = `Bearer ${adminToken}`;
  }
  return headers;
};

const formatTimestamp = (value: string | null) =>
  value ? new Date(value).toLocaleString() : "--";

const formatTtl = (value: number | null) => {
  if (value === null || value < 0) {
    return "--";
  }
  if (value < 60) {
    return `${value}s`;
  }
  const minutes = Math.floor(value / 60);
  if (minutes < 60) {
    return `${minutes}m`;
  }
  const hours = Math.floor(minutes / 60);
  return `${hours}h`;
};

const probeStatusLabel = (status: string) => {
  switch (status) {
    case "success":
      return { label: "健康", className: "text-emerald-400" };
    case "failure":
      return { label: "失败", className: "text-red-400" };
    case "error":
      return { label: "异常", className: "text-amber-400" };
    default:
      return { label: "未知", className: "text-zinc-400" };
  }
};

const alertEventLabels: Record<string, string> = {
  circuit_open: "熔断触发",
  circuit_recovered: "熔断恢复",
  probe_latency: "探针延迟",
  probe_failure: "探针失败",
  probe_error: "探针异常",
};

const alertEventLabel = (event: string) => alertEventLabels[event] ?? event;

export const Dashboard = () => {
  const [overview, setOverview] = useState<Overview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [healthStatuses, setHealthStatuses] = useState<HealthStatus[]>([]);
  const [healthError, setHealthError] = useState<string | null>(null);
  const [healthLoading, setHealthLoading] = useState(false);
  const [healthUpdatedAt, setHealthUpdatedAt] = useState<string | null>(null);
  const [healthProbeBuckets, setHealthProbeBuckets] = useState<HealthProbeBucket[]>([]);
  const [healthProbeError, setHealthProbeError] = useState<string | null>(null);
  const [healthProbeLoading, setHealthProbeLoading] = useState(false);
  const [healthProbeUpdatedAt, setHealthProbeUpdatedAt] = useState<string | null>(null);
  const [metricsBuckets, setMetricsBuckets] = useState<MetricsBucket[]>([]);
  const [metricsError, setMetricsError] = useState<string | null>(null);
  const [metricsLoading, setMetricsLoading] = useState(false);
  const [metricsUpdatedAt, setMetricsUpdatedAt] = useState<string | null>(null);
  const [agentStatuses, setAgentStatuses] = useState<AgentStatus[]>([]);
  const [agentError, setAgentError] = useState<string | null>(null);
  const [agentLoading, setAgentLoading] = useState(false);
  const [agentUpdatedAt, setAgentUpdatedAt] = useState<string | null>(null);
  const [alertPolicies, setAlertPolicies] = useState<AlertPolicy[]>([]);
  const [alertError, setAlertError] = useState<string | null>(null);
  const [alertLoading, setAlertLoading] = useState(false);
  const [alertUpdatedAt, setAlertUpdatedAt] = useState<string | null>(null);
  const [alertSilenceMinutes, setAlertSilenceMinutes] = useState<
    Record<string, string>
  >({});
  const [alertThresholds, setAlertThresholds] = useState<Record<string, string>>({});
  const [alertSaving, setAlertSaving] = useState<Record<string, boolean>>({});

  const loadOverview = async () => {
    try {
      const response = await fetch(`${apiBase}/admin/overview`, {
        headers: buildHeaders(),
      });
      if (!response.ok) {
        throw new Error(`Request failed: ${response.status}`);
      }
      const data = (await response.json()) as Overview;
      setOverview(data);
      setError(null);
    } catch (err) {
      setError("无法获取概览数据");
    }
  };

  const loadHealthStatus = async () => {
    setHealthLoading(true);
    try {
      const response = await fetch(`${apiBase}/admin/health-status`, {
        headers: buildHeaders(),
      });
      if (!response.ok) {
        throw new Error(`Request failed: ${response.status}`);
      }
      const data = (await response.json()) as HealthStatus[];
      setHealthStatuses(data);
      setHealthError(null);
      setHealthUpdatedAt(new Date().toISOString());
    } catch (err) {
      setHealthError("无法获取健康探针数据");
    } finally {
      setHealthLoading(false);
    }
  };

  const loadHealthProbeTrend = async () => {
    setHealthProbeLoading(true);
    try {
      const response = await fetch(
        `${apiBase}/admin/health-status/timeseries?hours=24&bucket_minutes=30`,
        {
          headers: buildHeaders(),
        }
      );
      if (!response.ok) {
        throw new Error(`Request failed: ${response.status}`);
      }
      const data = (await response.json()) as HealthProbeBucket[];
      setHealthProbeBuckets(data);
      setHealthProbeError(null);
      setHealthProbeUpdatedAt(new Date().toISOString());
    } catch (err) {
      setHealthProbeError("无法获取健康探针趋势");
    } finally {
      setHealthProbeLoading(false);
    }
  };

  const loadMetrics = async () => {
    setMetricsLoading(true);
    try {
      const response = await fetch(`${apiBase}/admin/metrics/timeseries?hours=24`, {
        headers: buildHeaders(),
      });
      if (!response.ok) {
        throw new Error(`Request failed: ${response.status}`);
      }
      const data = (await response.json()) as MetricsBucket[];
      setMetricsBuckets(data);
      setMetricsError(null);
      setMetricsUpdatedAt(new Date().toISOString());
    } catch (err) {
      setMetricsError("无法获取趋势数据");
    } finally {
      setMetricsLoading(false);
    }
  };

  const loadAgents = async () => {
    setAgentLoading(true);
    try {
      const response = await fetch(`${apiBase}/admin/agents`, {
        headers: buildHeaders(),
      });
      if (!response.ok) {
        throw new Error(`Request failed: ${response.status}`);
      }
      const data = (await response.json()) as AgentStatus[];
      setAgentStatuses(data);
      setAgentError(null);
      setAgentUpdatedAt(new Date().toISOString());
    } catch (err) {
      setAgentError("无法获取 Agent 状态");
    } finally {
      setAgentLoading(false);
    }
  };

  const loadAlertPolicies = async () => {
    setAlertLoading(true);
    try {
      const response = await fetch(`${apiBase}/admin/alerts`, {
        headers: buildHeaders(),
      });
      if (!response.ok) {
        throw new Error(`Request failed: ${response.status}`);
      }
      const data = (await response.json()) as AlertPolicy[];
      setAlertPolicies(data);
      setAlertError(null);
      setAlertUpdatedAt(new Date().toISOString());
      setAlertSilenceMinutes((prev) => {
        const next = { ...prev };
        data.forEach((policy) => {
          if (next[policy.event] === undefined) {
            next[policy.event] = "30";
          }
        });
        return next;
      });
      setAlertThresholds((prev) => {
        const next = { ...prev };
        data.forEach((policy) => {
          if (policy.event === "probe_latency") {
            const thresholdValue = policy.threshold_ms ?? null;
            next[policy.event] =
              thresholdValue !== null ? String(thresholdValue) : "";
          }
        });
        return next;
      });
    } catch (err) {
      setAlertError("无法获取告警配置");
    } finally {
      setAlertLoading(false);
    }
  };

  const updateAlertPolicy = async (
    event: string,
    payload: {
      enabled?: boolean;
      silenceMinutes?: number | null;
      thresholdMs?: number | null;
    }
  ) => {
    setAlertSaving((prev) => ({ ...prev, [event]: true }));
    try {
      const body: {
        enabled?: boolean;
        silence_minutes?: number | null;
        threshold_ms?: number | null;
      } = {};
      if (payload.enabled !== undefined) {
        body.enabled = payload.enabled;
      }
      if (payload.silenceMinutes !== undefined) {
        body.silence_minutes = payload.silenceMinutes;
      }
      if (payload.thresholdMs !== undefined) {
        body.threshold_ms = payload.thresholdMs;
      }
      const response = await fetch(`${apiBase}/admin/alerts/${event}`, {
        method: "PUT",
        headers: {
          ...buildHeaders(),
          "Content-Type": "application/json",
        },
        body: JSON.stringify(body),
      });
      if (!response.ok) {
        throw new Error(`Request failed: ${response.status}`);
      }
      await loadAlertPolicies();
    } catch (err) {
      setAlertError("无法更新告警配置");
    } finally {
      setAlertSaving((prev) => ({ ...prev, [event]: false }));
    }
  };

  const handleAlertSilence = async (
    event: string,
    minutesOverride?: number | null
  ) => {
    const rawValue =
      minutesOverride ?? Number.parseInt(alertSilenceMinutes[event] ?? "", 10);
    const silenceMinutes = Number.isFinite(rawValue) ? rawValue : 0;
    await updateAlertPolicy(event, { silenceMinutes });
  };

  const handleAlertThreshold = async (event: string) => {
    const rawValue = Number.parseInt(alertThresholds[event] ?? "", 10);
    const thresholdMs = Number.isFinite(rawValue) ? rawValue : null;
    await updateAlertPolicy(event, { thresholdMs });
  };

  useEffect(() => {
    const loadAll = async () => {
      await Promise.all([
        loadOverview(),
        loadHealthStatus(),
        loadHealthProbeTrend(),
        loadMetrics(),
        loadAgents(),
        loadAlertPolicies(),
      ]);
    };
    loadAll();
  }, []);

  const metrics = [
    {
      label: "Endpoint 数量",
      value: overview ? String(overview.endpoints) : "--",
      hint: "基础路由资产",
    },
    {
      label: "API Key 数量",
      value: overview ? String(overview.api_keys) : "--",
      hint: "可用鉴权密钥",
    },
    {
      label: "模型映射",
      value: overview ? String(overview.model_maps) : "--",
      hint: "别名 -> 真模型",
    },
  ];

  const statusText = error
    ? error
    : overview
    ? `最近刷新：${new Date(overview.generated_at).toLocaleString()}`
    : "等待接入真实 API Key。";

  const healthStatusText = healthError
    ? healthError
    : healthUpdatedAt
    ? `最近刷新：${new Date(healthUpdatedAt).toLocaleString()}`
    : "等待探针数据。";

  const healthProbeStatusText = healthProbeError
    ? healthProbeError
    : healthProbeUpdatedAt
    ? `最近刷新：${new Date(healthProbeUpdatedAt).toLocaleString()}`
    : "等待探针趋势数据。";

  const metricsStatusText = metricsError
    ? metricsError
    : metricsUpdatedAt
    ? `最近刷新：${new Date(metricsUpdatedAt).toLocaleString()}`
    : "等待趋势数据。";

  const agentStatusText = agentError
    ? agentError
    : agentUpdatedAt
    ? `最近刷新：${new Date(agentUpdatedAt).toLocaleString()}`
    : "等待 Agent 上报。";

  const alertStatusText = alertError
    ? alertError
    : alertUpdatedAt
    ? `最近刷新：${new Date(alertUpdatedAt).toLocaleString()}`
    : "等待告警配置。";

  const probeTrendBuckets = healthProbeBuckets.map((bucket) => ({
    ...bucket,
    failed_count: bucket.failure_count + bucket.error_count,
  }));
  const maxProbeSuccess = Math.max(
    1,
    ...probeTrendBuckets.map((bucket) => bucket.success_count)
  );
  const maxProbeFailed = Math.max(
    1,
    ...probeTrendBuckets.map((bucket) => bucket.failed_count)
  );
  const latestProbeBucket = probeTrendBuckets[probeTrendBuckets.length - 1];

  const maxRequests = Math.max(
    1,
    ...metricsBuckets.map((bucket) => bucket.request_count)
  );
  const maxTokens = Math.max(
    1,
    ...metricsBuckets.map((bucket) => bucket.total_tokens)
  );
  const latestBucket = metricsBuckets[metricsBuckets.length - 1];

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle>资产概览</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="grid gap-6 md:grid-cols-3">
            {metrics.map((metric) => (
              <Metric key={metric.label} {...metric} />
            ))}
          </div>
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>系统状态</CardTitle>
        </CardHeader>
        <CardContent className="space-y-2">
          <p>{statusText}</p>
          <p className="text-xs text-zinc-400">
            {overview
              ? `累计请求日志 ${overview.request_logs}`
              : "网关、路由与熔断模块已就绪。"}
          </p>
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>请求与 Token 趋势</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex flex-wrap items-center gap-3 text-sm text-zinc-400">
            <span>{metricsStatusText}</span>
            <Button variant="outline" onClick={loadMetrics} disabled={metricsLoading}>
              刷新
            </Button>
          </div>
          {metricsError ? <p className="text-sm text-red-400">{metricsError}</p> : null}
          {metricsBuckets.length === 0 ? (
            <p className="text-sm text-zinc-400">暂无趋势数据。</p>
          ) : (
            <div className="space-y-4">
              <div className="grid gap-4 md:grid-cols-2">
                <TrendBars
                  title="请求数"
                  items={metricsBuckets}
                  maxValue={maxRequests}
                  valueKey="request_count"
                  colorClass="bg-sky-500/70"
                />
                <TrendBars
                  title="Token"
                  items={metricsBuckets}
                  maxValue={maxTokens}
                  valueKey="total_tokens"
                  colorClass="bg-violet-500/70"
                />
              </div>
              <div className="grid gap-3 text-sm text-zinc-400 md:grid-cols-4">
                <div>
                  <p className="text-xs">最近桶</p>
                  <p className="text-sm text-foreground">
                    {latestBucket ? formatTimestamp(latestBucket.bucket_start) : "--"}
                  </p>
                </div>
                <div>
                  <p className="text-xs">请求数</p>
                  <p className="text-sm text-foreground">
                    {latestBucket ? latestBucket.request_count : "--"}
                  </p>
                </div>
                <div>
                  <p className="text-xs">RPS</p>
                  <p className="text-sm text-foreground">
                    {latestBucket ? latestBucket.rps.toFixed(3) : "--"}
                  </p>
                </div>
                <div>
                  <p className="text-xs">平均延迟</p>
                  <p className="text-sm text-foreground">
                    {latestBucket?.avg_latency_ms ?? "--"}
                  </p>
                </div>
              </div>
            </div>
          )}
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>Agent 节点状态</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="flex flex-wrap items-center gap-3 text-sm text-zinc-400">
            <span>{agentStatusText}</span>
            <Button variant="outline" onClick={loadAgents} disabled={agentLoading}>
              刷新
            </Button>
          </div>
          {agentError ? <p className="text-sm text-red-400">{agentError}</p> : null}
          {agentStatuses.length === 0 ? (
            <p className="text-sm text-zinc-400">暂无 Agent 状态。</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="text-left text-zinc-400">
                  <tr>
                    <th className="py-2">名称</th>
                    <th>区域</th>
                    <th>状态</th>
                    <th>最近心跳</th>
                    <th>地址</th>
                  </tr>
                </thead>
                <tbody>
                  {agentStatuses.map((agent) => {
                    const statusLabel = agent.status === "online" ? "在线" : "离线";
                    const statusClass =
                      agent.status === "online"
                        ? "text-emerald-400"
                        : "text-red-400";
                    return (
                      <tr key={agent.id} className="border-t border-muted">
                        <td className="py-2 font-medium">{agent.name}</td>
                        <td>{agent.region ?? "--"}</td>
                        <td className={statusClass}>{statusLabel}</td>
                        <td>{formatTimestamp(agent.last_seen_at)}</td>
                        <td className="max-w-[240px] truncate">
                          {agent.endpoint_url ?? "--"}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>健康探针趋势</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex flex-wrap items-center gap-3 text-sm text-zinc-400">
            <span>{healthProbeStatusText}</span>
            <Button
              variant="outline"
              onClick={loadHealthProbeTrend}
              disabled={healthProbeLoading}
            >
              刷新
            </Button>
          </div>
          {healthProbeError ? (
            <p className="text-sm text-red-400">{healthProbeError}</p>
          ) : null}
          {probeTrendBuckets.length === 0 ? (
            <p className="text-sm text-zinc-400">暂无探针趋势。</p>
          ) : (
            <div className="space-y-4">
              <div className="grid gap-4 md:grid-cols-2">
                <TrendBars
                  title="探针成功"
                  items={probeTrendBuckets}
                  maxValue={maxProbeSuccess}
                  valueKey="success_count"
                  colorClass="bg-emerald-500/70"
                />
                <TrendBars
                  title="探针失败/异常"
                  items={probeTrendBuckets}
                  maxValue={maxProbeFailed}
                  valueKey="failed_count"
                  colorClass="bg-rose-500/70"
                />
              </div>
              <div className="grid gap-3 text-sm text-zinc-400 md:grid-cols-4">
                <div>
                  <p className="text-xs">最近桶</p>
                  <p className="text-sm text-foreground">
                    {latestProbeBucket
                      ? formatTimestamp(latestProbeBucket.bucket_start)
                      : "--"}
                  </p>
                </div>
                <div>
                  <p className="text-xs">成功</p>
                  <p className="text-sm text-foreground">
                    {latestProbeBucket ? latestProbeBucket.success_count : "--"}
                  </p>
                </div>
                <div>
                  <p className="text-xs">失败/异常</p>
                  <p className="text-sm text-foreground">
                    {latestProbeBucket ? latestProbeBucket.failed_count : "--"}
                  </p>
                </div>
                <div>
                  <p className="text-xs">平均延迟</p>
                  <p className="text-sm text-foreground">
                    {latestProbeBucket?.avg_latency_ms ?? "--"}
                  </p>
                </div>
              </div>
            </div>
          )}
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>健康探针与熔断</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="flex flex-wrap items-center gap-3 text-sm text-zinc-400">
            <span>{healthStatusText}</span>
            <Button variant="outline" onClick={loadHealthStatus} disabled={healthLoading}>
              刷新
            </Button>
          </div>
          {healthError ? <p className="text-sm text-red-400">{healthError}</p> : null}
          {healthStatuses.length === 0 ? (
            <p className="text-sm text-zinc-400">暂无健康探针数据。</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="text-left text-zinc-400">
                  <tr>
                    <th className="py-2">Endpoint</th>
                    <th>API Key</th>
                    <th>规则组</th>
                    <th>模型</th>
                    <th>探针</th>
                    <th>延迟</th>
                    <th>状态码</th>
                    <th>最近探针</th>
                    <th>熔断</th>
                  </tr>
                </thead>
                <tbody>
                  {healthStatuses.map((item) => {
                    const probeLabel = probeStatusLabel(item.probe_status);
                    const circuitLabel =
                      item.circuit_state === "open" ? "熔断中" : "正常";
                    const circuitClass =
                      item.circuit_state === "open"
                        ? "text-red-400"
                        : "text-emerald-400";

                    return (
                      <tr
                        key={`${item.endpoint_id}-${item.api_key_id}`}
                        className="border-t border-muted"
                      >
                        <td className="py-2 font-medium">{item.endpoint_name}</td>
                        <td>{item.api_key_id}</td>
                        <td>{item.rule_group}</td>
                        <td>{item.probe_real_model ?? "--"}</td>
                        <td className={probeLabel.className}>{probeLabel.label}</td>
                        <td>{item.probe_latency_ms ?? "--"}</td>
                        <td>{item.probe_status_code ?? "--"}</td>
                        <td>{formatTimestamp(item.probe_checked_at)}</td>
                        <td>
                          <div className="flex flex-col">
                            <span className={circuitClass}>{circuitLabel}</span>
                            <span className="text-xs text-zinc-500">
                              失败 {item.circuit_failures} · TTL {formatTtl(item.circuit_ttl_seconds)}
                            </span>
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>告警订阅与静默</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="flex flex-wrap items-center gap-3 text-sm text-zinc-400">
            <span>{alertStatusText}</span>
            <Button variant="outline" onClick={loadAlertPolicies} disabled={alertLoading}>
              刷新
            </Button>
          </div>
          {alertError ? <p className="text-sm text-red-400">{alertError}</p> : null}
          {alertPolicies.length === 0 ? (
            <p className="text-sm text-zinc-400">暂无告警配置。</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="text-left text-zinc-400">
                  <tr>
                    <th className="py-2">事件</th>
                    <th>状态</th>
                    <th>静默到期</th>
                    <th>静默(分钟)</th>
                    <th>阈值(ms)</th>
                    <th className="text-right">操作</th>
                  </tr>
                </thead>
                <tbody>
                  {alertPolicies.map((policy) => {
                    const isEnabled = policy.enabled;
                    const statusLabel = isEnabled ? "已开启" : "已关闭";
                    const statusClass = isEnabled
                      ? "text-emerald-400"
                      : "text-zinc-400";
                    const silenceValue = alertSilenceMinutes[policy.event] ?? "30";
                    const isProbeLatency = policy.event === "probe_latency";
                    const thresholdValue = alertThresholds[policy.event] ?? "";
                    const saving = alertSaving[policy.event] ?? false;
                    return (
                      <tr key={policy.event} className="border-t border-muted">
                        <td className="py-2 font-medium">
                          {alertEventLabel(policy.event)}
                        </td>
                        <td className={statusClass}>{statusLabel}</td>
                        <td>{formatTimestamp(policy.silence_until)}</td>
                        <td>
                          <input
                            className="h-9 w-24 rounded-md border border-muted bg-background px-2 text-sm"
                            type="number"
                            min="0"
                            value={silenceValue}
                            aria-label={`silence-minutes-${policy.event}`}
                            onChange={(event) =>
                              setAlertSilenceMinutes((prev) => ({
                                ...prev,
                                [policy.event]: event.target.value,
                              }))
                            }
                          />
                        </td>
                        <td>
                          {isProbeLatency ? (
                            <input
                              className="h-9 w-28 rounded-md border border-muted bg-background px-2 text-sm"
                              type="number"
                              min="0"
                              value={thresholdValue}
                              aria-label={`threshold-ms-${policy.event}`}
                              onChange={(event) =>
                                setAlertThresholds((prev) => ({
                                  ...prev,
                                  [policy.event]: event.target.value,
                                }))
                              }
                            />
                          ) : (
                            "--"
                          )}
                        </td>
                        <td className="py-2">
                          <div className="flex flex-wrap justify-end gap-2">
                            <Button
                              variant="outline"
                              className="h-8 px-3"
                              onClick={() =>
                                updateAlertPolicy(policy.event, {
                                  enabled: !isEnabled,
                                })
                              }
                              disabled={saving}
                            >
                              {isEnabled ? "停用" : "启用"}
                            </Button>
                            {isProbeLatency ? (
                              <Button
                                variant="outline"
                                className="h-8 px-3"
                                onClick={() => handleAlertThreshold(policy.event)}
                                disabled={saving}
                              >
                                阈值
                              </Button>
                            ) : null}
                            <Button
                              variant="outline"
                              className="h-8 px-3"
                              onClick={() => handleAlertSilence(policy.event)}
                              disabled={saving}
                            >
                              静默
                            </Button>
                            <Button
                              variant="outline"
                              className="h-8 px-3"
                              onClick={() => handleAlertSilence(policy.event, 0)}
                              disabled={saving}
                            >
                              解除
                            </Button>
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
};

const Metric = ({ label, value, hint }: { label: string; value: string; hint: string }) => (
  <div className="rounded-lg border border-muted bg-background/40 p-4">
    <p className="text-xs uppercase tracking-[0.2em] text-zinc-400">{label}</p>
    <p className="text-2xl font-semibold">{value}</p>
    <p className="text-xs text-zinc-400">{hint}</p>
  </div>
);

type TrendItem = {
  bucket_start: string;
  [key: string]: number | string | null;
};

const TrendBars = ({
  title,
  items,
  maxValue,
  valueKey,
  colorClass,
}: {
  title: string;
  items: TrendItem[];
  maxValue: number;
  valueKey: string;
  colorClass: string;
}) => (
  <div className="rounded-lg border border-muted bg-background/40 p-4">
    <p className="mb-2 text-xs uppercase tracking-[0.2em] text-zinc-400">{title}</p>
    <div className="flex items-end gap-1">
      {items.map((item) => {
        const rawValue = item[valueKey];
        const value = typeof rawValue === "number" ? rawValue : 0;
        const height = Math.max(4, Math.round((value / maxValue) * 48));
        const titleValue = `${formatTimestamp(item.bucket_start)} · ${value}`;
        return (
          <div key={item.bucket_start} title={titleValue} className="flex flex-col">
            <div className={`w-2 rounded ${colorClass}`} style={{ height: `${height}px` }} />
          </div>
        );
      })}
    </div>
  </div>
);
