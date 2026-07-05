import {
  Activity,
  BarChart3,
  Clock,
  Database,
  RefreshCw,
  Search,
  Zap,
} from "lucide-react";
import { useMemo, useState } from "react";
import {
  Area,
  Bar,
  CartesianGrid,
  ComposedChart,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import {
  formatTimestamp,
  formatTokens,
  type DumpSearchResult,
  type MetricsBucket,
  type StatsDistributionItem,
  type StatsLatencyBucket,
  type StatsOverview,
  type StatsTimeseriesBucket,
  type StatsTopKey,
  type UsageStats,
  type UsageTrendRange,
  usageTrendConfig,
} from "./shared";

const chartTooltipStyle = {
  backgroundColor: "#111827",
  border: "1px solid #374151",
  borderRadius: "8px",
  color: "#e5e7eb",
};

const formatShortTime = (value: string) => {
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return value;
  return date.toLocaleString(undefined, {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
};

const formatLatency = (value: number | null | undefined) =>
  value == null ? "--" : value >= 1000 ? `${(value / 1000).toFixed(1)}s` : `${value}ms`;

const formatPercent = (value: number | null | undefined) =>
  value == null ? "--" : `${value.toFixed(1)}%`;

const KpiCard = ({
  label,
  value,
  change,
  detail,
  icon: Icon,
  accent,
}: {
  label: string;
  value: string;
  change: number | null | undefined;
  detail: string;
  icon: typeof Activity;
  accent: string;
}) => {
  const positive = (change ?? 0) >= 0;
  return (
    <div className="rounded-xl border border-gray-800 bg-[#0f1117] p-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="text-xs text-gray-500">{label}</p>
          <p className="mt-2 text-2xl font-bold text-gray-100">{value}</p>
        </div>
        <div className={`rounded-lg bg-gray-900 p-2 ${accent}`}>
          <Icon size={18} />
        </div>
      </div>
      <div className="mt-3 flex items-center justify-between gap-3 text-xs">
        <span className="text-gray-500">{detail}</span>
        <span className={positive ? "text-emerald-400" : "text-red-400"}>
          {change == null ? "--" : `${positive ? "+" : ""}${change.toFixed(1)}%`}
        </span>
      </div>
    </div>
  );
};

const DistributionBars = ({
  title,
  items,
  valueMode,
  selected,
  onSelect,
}: {
  title: string;
  items: StatsDistributionItem[];
  valueMode: "tokens" | "requests";
  selected: string | null;
  onSelect: (name: string | null) => void;
}) => (
  <div className="rounded-xl border border-gray-800 bg-[#0f1117] p-5">
    <div className="mb-4 flex items-center justify-between">
      <h3 className="text-sm font-bold text-gray-200">{title}</h3>
      {selected ? (
        <button
          type="button"
          onClick={() => onSelect(null)}
          className="text-xs text-gray-500 hover:text-gray-200"
        >
          清除筛选
        </button>
      ) : null}
    </div>
    <div className="space-y-3">
      {items.map((item) => {
        const active = selected === item.name;
        return (
          <button
            key={item.name}
            type="button"
            onClick={() => onSelect(active ? null : item.name)}
            className={`w-full rounded-lg border p-3 text-left transition ${
              active
                ? "border-indigo-500/60 bg-indigo-600/10"
                : "border-gray-800 bg-gray-950/40 hover:border-gray-700"
            }`}
          >
            <div className="mb-2 flex items-center justify-between gap-3 text-xs">
              <span className="truncate font-medium text-gray-200">{item.name}</span>
              <span className="font-mono text-gray-400">{item.percent.toFixed(1)}%</span>
            </div>
            <div className="h-2 overflow-hidden rounded-full bg-gray-800">
              <div
                className="h-full rounded-full bg-gradient-to-r from-indigo-500 to-cyan-400"
                style={{ width: `${Math.min(100, Math.max(0, item.percent))}%` }}
              />
            </div>
            <div className="mt-2 text-[11px] text-gray-500">
              {valueMode === "tokens"
                ? `${formatTokens(item.total_tokens)} tokens · ${item.request_count} req`
                : `${item.request_count} req · ${formatTokens(item.total_tokens)} tokens`}
            </div>
          </button>
        );
      })}
      {items.length === 0 ? (
        <div className="rounded-lg border border-gray-800 bg-gray-950/40 p-4 text-center text-xs text-gray-600">
          暂无数据
        </div>
      ) : null}
    </div>
  </div>
);

type SortKey = "tokens" | "requests" | "cache" | "latency";

export const UsageStatsView = ({
  overview,
  timeseries,
  latency,
  modelDistribution,
  groupDistribution,
  topKeys,
  dumpSearch,
  range,
  updatedAt,
  loading,
  error,
  onRangeChange,
  onRefresh,
}: {
  stats: UsageStats | null;
  buckets: MetricsBucket[];
  overview: StatsOverview | null;
  timeseries: StatsTimeseriesBucket[];
  latency: StatsLatencyBucket[];
  modelDistribution: StatsDistributionItem[];
  groupDistribution: StatsDistributionItem[];
  topKeys: StatsTopKey[];
  dumpSearch: DumpSearchResult | null;
  range: UsageTrendRange;
  updatedAt: string | null;
  loading: boolean;
  error: string | null;
  onRangeChange: (range: UsageTrendRange) => void;
  onRefresh: () => void;
}) => {
  const [selectedModel, setSelectedModel] = useState<string | null>(null);
  const [selectedGroup, setSelectedGroup] = useState<string | null>(null);
  const [statusFilter, setStatusFilter] = useState("all");
  const [traceSearch, setTraceSearch] = useState("");
  const [sortKey, setSortKey] = useState<SortKey>("tokens");
  const [expandedKeyId, setExpandedKeyId] = useState<number | null>(null);

  const sortedTopKeys = useMemo(() => {
    const rows = [...topKeys];
    rows.sort((left, right) => {
      if (sortKey === "requests") return right.request_count - left.request_count;
      if (sortKey === "cache") {
        return (right.cache_hit_rate ?? -1) - (left.cache_hit_rate ?? -1);
      }
      if (sortKey === "latency") {
        return (right.avg_latency_ms ?? Number.MAX_SAFE_INTEGER) -
          (left.avg_latency_ms ?? Number.MAX_SAFE_INTEGER);
      }
      return right.total_tokens - left.total_tokens;
    });
    return rows;
  }, [topKeys, sortKey]);

  const filteredLogs = useMemo(() => {
    const query = traceSearch.trim().toLowerCase();
    return (dumpSearch?.items ?? []).filter((item) => {
      if (selectedModel && item.model_alias !== selectedModel) return false;
      if (selectedGroup && item.rule_group !== selectedGroup) return false;
      if (statusFilter !== "all" && String(item.status_code ?? "") !== statusFilter) {
        return false;
      }
      if (
        query &&
        !item.trace_id.toLowerCase().includes(query) &&
        !item.request_id.toLowerCase().includes(query)
      ) {
        return false;
      }
      return true;
    });
  }, [dumpSearch?.items, selectedGroup, selectedModel, statusFilter, traceSearch]);

  const statusOptions = useMemo(() => {
    const values = new Set<string>();
    (dumpSearch?.items ?? []).forEach((item) => {
      if (item.status_code != null) values.add(String(item.status_code));
    });
    return Array.from(values).sort();
  }, [dumpSearch?.items]);

  const topKeyTimeline = useMemo(() => {
    if (expandedKeyId == null) return [];
    return (dumpSearch?.items ?? [])
      .filter((item) => item.api_key_id === expandedKeyId)
      .slice(0, 12)
      .reverse();
  }, [dumpSearch?.items, expandedKeyId]);

  const statusText = updatedAt
    ? `最近刷新：${new Date(updatedAt).toLocaleString()}`
    : "等待统计数据。";

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="flex items-center gap-2 text-xl font-bold text-white">
            <BarChart3 size={20} className="text-indigo-400" />
            流量统计
          </h2>
          <p className="mt-1 text-sm text-gray-500">
            请求量、Token、缓存命中、延迟和 dump 日志。
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <div className="flex rounded-lg border border-gray-800 bg-gray-950 p-1">
            {Object.entries(usageTrendConfig).map(([key, option]) => (
              <button
                key={key}
                type="button"
                onClick={() => onRangeChange(key as UsageTrendRange)}
                className={`rounded px-3 py-1.5 text-xs transition ${
                  range === key
                    ? "bg-indigo-600 text-white"
                    : "text-gray-400 hover:bg-gray-900 hover:text-gray-200"
                }`}
              >
                {option.label}
              </button>
            ))}
            <button
              type="button"
              disabled
              className="rounded px-3 py-1.5 text-xs text-gray-700"
              title="自定义时间范围待接入"
            >
              自定义
            </button>
          </div>
          <button
            onClick={onRefresh}
            disabled={loading}
            className="flex items-center gap-2 rounded-lg border border-gray-700 bg-gray-800/70 px-3 py-2 text-sm text-gray-300 hover:bg-gray-800 disabled:opacity-60"
          >
            <RefreshCw size={15} className={loading ? "animate-spin" : ""} />
            自动刷新
          </button>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-3 text-xs text-gray-500">
        <span>{statusText}</span>
        {loading ? <span>加载中...</span> : null}
        {error ? <span className="text-red-400">{error}</span> : null}
      </div>

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <KpiCard
          label="总请求数"
          value={(overview?.total_requests.value ?? 0).toLocaleString()}
          change={overview?.total_requests.change_percent}
          detail="vs 上个周期"
          icon={Activity}
          accent="text-indigo-400"
        />
        <KpiCard
          label="Token 消耗"
          value={formatTokens(overview?.total_tokens.value ?? 0)}
          change={overview?.total_tokens.change_percent}
          detail={`${formatTokens(overview?.prompt_tokens ?? 0)} in / ${formatTokens(
            overview?.completion_tokens ?? 0
          )} out`}
          icon={Zap}
          accent="text-cyan-300"
        />
        <KpiCard
          label="缓存命中率"
          value={formatPercent(overview?.cache_hit_rate.value)}
          change={overview?.cache_hit_rate.change_percent}
          detail={`${formatTokens(overview?.cached_tokens ?? 0)} cached tokens`}
          icon={Database}
          accent="text-emerald-400"
        />
        <KpiCard
          label="平均延迟"
          value={formatLatency(overview?.avg_latency_ms.value ?? null)}
          change={overview?.avg_latency_ms.change_percent}
          detail={`P95 ${formatLatency(overview?.p95_latency_ms)}`}
          icon={Clock}
          accent="text-amber-300"
        />
      </div>

      <div className="rounded-xl border border-gray-800 bg-[#0f1117] p-5">
        <h3 className="mb-4 text-sm font-bold text-gray-200">请求量 & Token 消耗趋势</h3>
        <div className="h-80">
          <ResponsiveContainer width="100%" height="100%">
            <ComposedChart data={timeseries}>
              <defs>
                <linearGradient id="tokenGradient" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="#22d3ee" stopOpacity={0.35} />
                  <stop offset="95%" stopColor="#22d3ee" stopOpacity={0.02} />
                </linearGradient>
              </defs>
              <CartesianGrid stroke="#1f2937" vertical={false} />
              <XAxis
                dataKey="bucket_start"
                tickFormatter={formatShortTime}
                stroke="#64748b"
                tick={{ fontSize: 11 }}
              />
              <YAxis yAxisId="requests" stroke="#818cf8" tick={{ fontSize: 11 }} />
              <YAxis
                yAxisId="tokens"
                orientation="right"
                stroke="#22d3ee"
                tickFormatter={formatTokens}
                tick={{ fontSize: 11 }}
              />
              <Tooltip
                contentStyle={chartTooltipStyle}
                labelFormatter={(value) => formatTimestamp(String(value))}
                formatter={(value, name) => [
                  typeof value === "number" ? value.toLocaleString() : value,
                  name,
                ]}
              />
              <Bar
                yAxisId="requests"
                dataKey="request_count"
                name="请求数"
                fill="#6366f1"
                radius={[4, 4, 0, 0]}
              />
              <Area
                yAxisId="tokens"
                type="monotone"
                dataKey="total_tokens"
                name="Token"
                stroke="#22d3ee"
                fill="url(#tokenGradient)"
                strokeWidth={2}
              />
            </ComposedChart>
          </ResponsiveContainer>
        </div>
      </div>

      <div className="grid gap-6 xl:grid-cols-2">
        <div className="rounded-xl border border-gray-800 bg-[#0f1117] p-5">
          <h3 className="mb-4 text-sm font-bold text-gray-200">缓存命中率趋势</h3>
          <div className="h-72">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={timeseries}>
                <CartesianGrid stroke="#1f2937" vertical={false} />
                <XAxis
                  dataKey="bucket_start"
                  tickFormatter={formatShortTime}
                  stroke="#64748b"
                  tick={{ fontSize: 11 }}
                />
                <YAxis domain={[0, 100]} stroke="#10b981" tick={{ fontSize: 11 }} />
                <Tooltip
                  contentStyle={chartTooltipStyle}
                  labelFormatter={(value) => formatTimestamp(String(value))}
                  formatter={(value) => [`${Number(value).toFixed(1)}%`, "Cache%"]}
                />
                <Line
                  type="monotone"
                  dataKey="cache_hit_rate"
                  stroke="#10b981"
                  strokeWidth={2}
                  dot={false}
                />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </div>

        <div className="rounded-xl border border-gray-800 bg-[#0f1117] p-5">
          <h3 className="mb-4 text-sm font-bold text-gray-200">延迟分布</h3>
          <div className="h-72">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={latency}>
                <CartesianGrid stroke="#1f2937" vertical={false} />
                <XAxis
                  dataKey="bucket_start"
                  tickFormatter={formatShortTime}
                  stroke="#64748b"
                  tick={{ fontSize: 11 }}
                />
                <YAxis stroke="#94a3b8" tick={{ fontSize: 11 }} />
                <Tooltip
                  contentStyle={chartTooltipStyle}
                  labelFormatter={(value) => formatTimestamp(String(value))}
                  formatter={(value, name) => [formatLatency(Number(value)), name]}
                />
                <Line type="monotone" dataKey="p50_ms" name="P50" stroke="#10b981" dot={false} />
                <Line type="monotone" dataKey="p95_ms" name="P95" stroke="#f59e0b" dot={false} />
                <Line type="monotone" dataKey="p99_ms" name="P99" stroke="#ef4444" dot={false} />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </div>
      </div>

      <div className="grid gap-6 xl:grid-cols-2">
        <DistributionBars
          title="按模型分布"
          items={modelDistribution}
          valueMode="tokens"
          selected={selectedModel}
          onSelect={setSelectedModel}
        />
        <DistributionBars
          title="按规则组分布"
          items={groupDistribution}
          valueMode="requests"
          selected={selectedGroup}
          onSelect={setSelectedGroup}
        />
      </div>

      <div className="rounded-xl border border-gray-800 bg-[#0f1117] p-5">
        <div className="mb-4 flex items-center justify-between">
          <h3 className="text-sm font-bold text-gray-200">Top API Keys 用量排行</h3>
          <div className="flex gap-1">
            {[
              ["tokens", "Tokens"],
              ["requests", "Requests"],
              ["cache", "Cache%"],
              ["latency", "Latency"],
            ].map(([key, label]) => (
              <button
                key={key}
                type="button"
                onClick={() => setSortKey(key as SortKey)}
                className={`rounded border px-2 py-1 text-xs ${
                  sortKey === key
                    ? "border-indigo-500/60 bg-indigo-600/20 text-indigo-200"
                    : "border-gray-800 text-gray-500 hover:text-gray-200"
                }`}
              >
                {label}
              </button>
            ))}
          </div>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead className="border-b border-gray-800 text-gray-500">
              <tr>
                <th className="pb-2 font-medium">Key</th>
                <th className="pb-2 font-medium">Endpoint</th>
                <th className="pb-2 text-right font-medium">Requests</th>
                <th className="pb-2 text-right font-medium">Tokens</th>
                <th className="pb-2 text-right font-medium">Cache%</th>
                <th className="pb-2 text-right font-medium">Avg Latency</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-800/50">
              {sortedTopKeys.map((row) => (
                <tr
                  key={row.api_key_id}
                  onClick={() =>
                    setExpandedKeyId(expandedKeyId === row.api_key_id ? null : row.api_key_id)
                  }
                  className="cursor-pointer hover:bg-gray-900/40"
                >
                  <td className="py-3 font-mono text-indigo-300">{row.key_preview}</td>
                  <td className="py-3 text-gray-400">{row.endpoint_name}</td>
                  <td className="py-3 text-right text-gray-300">{row.request_count}</td>
                  <td className="py-3 text-right text-gray-200">
                    {formatTokens(row.total_tokens)}
                  </td>
                  <td className="py-3 text-right text-emerald-300">
                    {formatPercent(row.cache_hit_rate)}
                  </td>
                  <td className="py-3 text-right text-gray-300">
                    {formatLatency(row.avg_latency_ms)}
                  </td>
                </tr>
              ))}
              {sortedTopKeys.length === 0 ? (
                <tr>
                  <td colSpan={6} className="py-5 text-center text-gray-600">
                    暂无数据
                  </td>
                </tr>
              ) : null}
            </tbody>
          </table>
        </div>
        {expandedKeyId != null ? (
          <div className="mt-4 rounded-lg border border-gray-800 bg-gray-950/50 p-3">
            <p className="mb-2 text-xs text-gray-500">Key #{expandedKeyId} 最近 dump 时间线</p>
            <div className="flex items-end gap-1">
              {topKeyTimeline.map((item) => (
                <div
                  key={item.request_id}
                  title={`${formatTimestamp(item.created_at)} · ${formatTokens(
                    item.total_tokens ?? 0
                  )}`}
                  className={`w-3 rounded-t ${
                    item.is_cache_hit ? "bg-emerald-500" : "bg-indigo-500"
                  }`}
                  style={{
                    height: `${Math.max(8, Math.min(64, ((item.total_tokens ?? 0) / 1000) * 8))}px`,
                  }}
                />
              ))}
              {topKeyTimeline.length === 0 ? (
                <span className="text-xs text-gray-600">当前时间窗内暂无 dump 记录</span>
              ) : null}
            </div>
          </div>
        ) : null}
      </div>

      <div className="rounded-xl border border-gray-800 bg-[#0f1117] p-5">
        <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
          <h3 className="text-sm font-bold text-gray-200">最近请求日志</h3>
          <div className="flex flex-wrap items-center gap-2">
            <select
              value={selectedModel ?? ""}
              onChange={(event) => setSelectedModel(event.target.value || null)}
              className="rounded border border-gray-700 bg-gray-900 px-2 py-1.5 text-xs text-gray-300"
            >
              <option value="">Model</option>
              {modelDistribution.map((item) => (
                <option key={item.name} value={item.name}>
                  {item.name}
                </option>
              ))}
            </select>
            <select
              value={selectedGroup ?? ""}
              onChange={(event) => setSelectedGroup(event.target.value || null)}
              className="rounded border border-gray-700 bg-gray-900 px-2 py-1.5 text-xs text-gray-300"
            >
              <option value="">Rule Group</option>
              {groupDistribution.map((item) => (
                <option key={item.name} value={item.name}>
                  {item.name}
                </option>
              ))}
            </select>
            <select
              value={statusFilter}
              onChange={(event) => setStatusFilter(event.target.value)}
              className="rounded border border-gray-700 bg-gray-900 px-2 py-1.5 text-xs text-gray-300"
            >
              <option value="all">Status</option>
              {statusOptions.map((status) => (
                <option key={status} value={status}>
                  {status}
                </option>
              ))}
            </select>
            <label className="flex items-center gap-1 rounded border border-gray-700 bg-gray-900 px-2 py-1.5 text-xs text-gray-300">
              <Search size={12} />
              <input
                value={traceSearch}
                onChange={(event) => setTraceSearch(event.target.value)}
                className="w-36 bg-transparent outline-none"
                placeholder="trace_id"
              />
            </label>
          </div>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead className="border-b border-gray-800 text-gray-500">
              <tr>
                <th className="pb-2 font-medium">Time</th>
                <th className="pb-2 font-medium">Model</th>
                <th className="pb-2 text-right font-medium">Tokens</th>
                <th className="pb-2 text-right font-medium">Latency</th>
                <th className="pb-2 text-right font-medium">Cache</th>
                <th className="pb-2 text-right font-medium">Status</th>
                <th className="pb-2 font-medium">Trace</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-800/50">
              {filteredLogs.map((item) => (
                <tr
                  key={item.request_id}
                  title={item.file_path ?? "metadata only"}
                  className="hover:bg-gray-900/40"
                >
                  <td className="py-3 text-gray-500">{formatShortTime(item.created_at)}</td>
                  <td className="py-3 text-gray-300">{item.model_alias}</td>
                  <td className="py-3 text-right text-gray-200">
                    {formatTokens(item.total_tokens ?? 0)}
                  </td>
                  <td className="py-3 text-right text-gray-300">
                    {formatLatency(item.latency_ms)}
                  </td>
                  <td className="py-3 text-right">
                    <span
                      className={
                        item.is_cache_hit ? "text-emerald-400" : "text-gray-500"
                      }
                    >
                      {item.is_cache_hit ? "HIT" : "MISS"}
                    </span>
                  </td>
                  <td className="py-3 text-right text-gray-300">
                    {item.status_code ?? "--"}
                  </td>
                  <td className="py-3 font-mono text-gray-500">{item.trace_id}</td>
                </tr>
              ))}
              {filteredLogs.length === 0 ? (
                <tr>
                  <td colSpan={7} className="py-5 text-center text-gray-600">
                    暂无匹配日志
                  </td>
                </tr>
              ) : null}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
};
