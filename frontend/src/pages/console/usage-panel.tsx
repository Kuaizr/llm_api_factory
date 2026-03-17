import { Activity, BarChart3, PieChart, RefreshCw } from "lucide-react";

import {
  formatTimestamp,
  type MetricsBucket,
  type UsageStats,
  type UsageTrendRange,
  usageTrendConfig,
} from "./shared";

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
  <div className="bg-[#0f1117] border border-gray-800 rounded-xl p-6">
    <p className="text-sm font-bold text-gray-300 mb-4">{title}</p>
    <div className="flex items-end gap-1">
      {items.map((item) => {
        const rawValue = item[valueKey];
        const value = typeof rawValue === "number" ? rawValue : 0;
        const height = Math.max(4, Math.round((value / maxValue) * 64));
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

export const UsageStatsView = ({
  stats,
  buckets,
  range,
  updatedAt,
  loading,
  error,
  onRangeChange,
  onRefresh,
}: {
  stats: UsageStats | null;
  buckets: MetricsBucket[];
  range: UsageTrendRange;
  updatedAt: string | null;
  loading: boolean;
  error: string | null;
  onRangeChange: (range: UsageTrendRange) => void;
  onRefresh: () => void;
}) => {
  const statusText = updatedAt
    ? `最近刷新：${new Date(updatedAt).toLocaleString()}`
    : "等待趋势数据。";
  const maxRequests = Math.max(1, ...buckets.map((bucket) => bucket.request_count));
  const maxTokens = Math.max(1, ...buckets.map((bucket) => bucket.total_tokens));
  const latestBucket = buckets[buckets.length - 1];

  return (
    <div className="space-y-8">
      <div className="flex justify-between items-center">
        <div>
          <h2 className="text-xl font-bold text-white flex items-center gap-2">
            <BarChart3 size={20} className="text-purple-500" />
            流量与用量统计
          </h2>
          <p className="text-sm text-gray-500 mt-1">查看规则组消耗及 Top Key 排名。</p>
        </div>
        <div className="flex gap-2">
          <select
            value={range}
            onChange={(event) => onRangeChange(event.target.value as UsageTrendRange)}
            className="bg-gray-900 border border-gray-700 text-gray-300 text-sm rounded px-3 py-1.5 focus:outline-none"
          >
            {Object.entries(usageTrendConfig).map(([key, option]) => (
              <option key={key} value={key}>
                {option.label}
              </option>
            ))}
          </select>
          <button
            onClick={onRefresh}
            disabled={loading}
            className="p-2 bg-gray-800 rounded border border-gray-700 text-gray-400 hover:text-white disabled:opacity-60"
          >
            <RefreshCw size={16} />
          </button>
        </div>
      </div>

      <div className="bg-[#0f1117] border border-gray-800 rounded-xl p-6 space-y-4">
        <div className="flex flex-wrap items-center gap-3 text-sm text-gray-500">
          <span>{statusText}</span>
          {loading ? <span>加载中...</span> : null}
        </div>
        {error ? <p className="text-sm text-red-400">{error}</p> : null}
        {buckets.length === 0 ? (
          <div className="text-xs text-gray-600">暂无趋势数据</div>
        ) : (
          <div className="space-y-4">
            <div className="grid gap-6 md:grid-cols-2">
              <TrendBars
                title="请求量"
                items={buckets}
                maxValue={maxRequests}
                valueKey="request_count"
                colorClass="bg-sky-500/80"
              />
              <TrendBars
                title="Token 消耗"
                items={buckets}
                maxValue={maxTokens}
                valueKey="total_tokens"
                colorClass="bg-violet-500/80"
              />
            </div>
            <div className="grid gap-3 text-xs text-gray-400 md:grid-cols-4">
              <div>
                <p className="text-xs">最近桶</p>
                <p className="text-sm text-gray-200">
                  {latestBucket ? formatTimestamp(latestBucket.bucket_start) : "--"}
                </p>
              </div>
              <div>
                <p className="text-xs">请求数</p>
                <p className="text-sm text-gray-200">
                  {latestBucket ? latestBucket.request_count : "--"}
                </p>
              </div>
              <div>
                <p className="text-xs">Token</p>
                <p className="text-sm text-gray-200">
                  {latestBucket ? latestBucket.total_tokens.toLocaleString() : "--"}
                </p>
              </div>
              <div>
                <p className="text-xs">RPS</p>
                <p className="text-sm text-gray-200">
                  {latestBucket ? latestBucket.rps.toFixed(3) : "--"}
                </p>
              </div>
            </div>
          </div>
        )}
      </div>

      <div className="grid grid-cols-2 gap-6">
        <div className="bg-[#0f1117] border border-gray-800 rounded-xl p-6">
          <h3 className="text-sm font-bold text-gray-300 mb-6 flex items-center gap-2">
            <PieChart size={16} /> 规则组 Token 消耗占比
          </h3>
          <div className="space-y-4">
            {(stats?.groups ?? []).map((item) => (
              <div key={item.group_name}>
                <div className="flex justify-between text-xs mb-1">
                  <span className="text-gray-400">{item.group_name}</span>
                  <span className="text-gray-200 font-mono">
                    {item.percent.toFixed(1)}%
                  </span>
                </div>
                <div className="w-full h-2 bg-gray-800 rounded-full overflow-hidden">
                  <div
                    className="h-full bg-blue-500"
                    style={{ width: `${item.percent}%` }}
                  />
                </div>
              </div>
            ))}
            {!stats?.groups.length && (
              <div className="text-xs text-gray-600">暂无统计数据</div>
            )}
          </div>
        </div>

        <div className="bg-[#0f1117] border border-gray-800 rounded-xl p-6">
          <h3 className="text-sm font-bold text-gray-300 mb-6 flex items-center gap-2">
            <Activity size={16} /> Top 5 消耗最高的 API Keys
          </h3>
          <table className="w-full text-left text-xs">
            <thead className="text-gray-500 border-b border-gray-800">
              <tr>
                <th className="pb-2 font-medium">Key</th>
                <th className="pb-2 font-medium">Endpoint</th>
                <th className="pb-2 text-right font-medium">Usage</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-800/50">
              {(stats?.top_keys ?? []).map((row) => (
                <tr key={row.api_key_id}>
                  <td className="py-3 font-mono text-blue-400">{row.key_preview}</td>
                  <td className="py-3 text-gray-400">{row.endpoint_name}</td>
                  <td className="py-3 text-right text-gray-200">
                    {row.total_tokens.toLocaleString()}
                  </td>
                </tr>
              ))}
              {!stats?.top_keys.length && (
                <tr>
                  <td colSpan={3} className="py-4 text-center text-gray-600">
                    暂无数据
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
};
