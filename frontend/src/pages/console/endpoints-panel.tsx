import {
  Activity,
  ChevronDown,
  Cpu,
  Database,
  Globe,
  Key,
  PieChart,
  Plus,
  Server,
  Settings,
  Shield,
  Zap,
} from "lucide-react";
import { useMemo } from "react";

import { LatencyBar, StatusBadge } from "./common-widgets";
import {
  formatTokens,
  maskEndpointUrl,
  resolveKeyStatus,
  type AgentNode,
  type Endpoint,
  type HealthStatus,
  type UsageStats,
} from "./shared";

type EndpointsPanelProps = {
  endpoints: Endpoint[];
  agents: AgentNode[];
  usageStats: UsageStats | null;
  healthStatusMap: Record<number, HealthStatus>;
  isAdmin: boolean;
  onCreateEndpoint: () => void;
  onEditEndpoint: (endpoint: Endpoint) => void;
  onManageKeys: (endpoint: Endpoint) => void;
  onProbeEndpoint: (endpoint: Endpoint) => void;
};

const EndpointCard = ({
  data,
  healthStatusMap,
  isAdmin,
  onEdit,
  onManageKeys,
  onProbe,
}: {
  data: Endpoint;
  healthStatusMap: Record<number, HealthStatus>;
  isAdmin: boolean;
  onEdit: (endpoint: Endpoint) => void;
  onManageKeys: (endpoint: Endpoint) => void;
  onProbe: (endpoint: Endpoint) => void;
}) => {
  const availableKeys = data.keys.filter((key) =>
    resolveKeyStatus(key, healthStatusMap[key.id]).isAvailable
  ).length;

  return (
    <div className="group relative bg-[#0f1117] border border-gray-800 rounded-xl p-5 hover:border-blue-500/50 transition-all duration-300 shadow-lg hover:shadow-blue-900/10 flex flex-col h-full">
      <div className="flex justify-between items-start mb-4">
        <div className="flex items-center gap-3">
          <div
            className={`p-2 rounded-lg ${
              data.provider === "openai"
                ? "bg-green-900/20 text-green-400"
                : data.provider === "anthropic"
                  ? "bg-purple-900/20 text-purple-400"
                  : data.provider === "gemini"
                    ? "bg-blue-900/20 text-blue-400"
                  : "bg-amber-900/20 text-amber-400"
            }`}
          >
            <Cpu size={18} />
          </div>
          <div>
            <h3 className="text-sm font-bold text-gray-100 group-hover:text-blue-400 transition-colors">
              {data.name}
            </h3>
            <div className="flex items-center gap-2 mt-0.5">
              <Globe size={10} className="text-gray-500" />
              <p className="text-xs text-gray-500 font-mono truncate max-w-[150px]">
                {maskEndpointUrl(data.base_url)}
              </p>
            </div>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <StatusBadge status={data.status} />
          <button
            onClick={() => onEdit(data)}
            className="text-gray-500 hover:text-white p-1 rounded hover:bg-gray-800 transition"
          >
            <Settings size={16} />
          </button>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-3 mb-4">
        <div className="bg-gray-900/50 p-2.5 rounded-lg border border-gray-800">
          <div className="text-xs text-gray-500 flex items-center gap-1 mb-1">
            <Activity size={10} />
            基础连通延迟
          </div>
          <div className="text-lg font-mono font-semibold text-gray-200">
            {data.latency ? `${data.latency}ms` : "--"}
          </div>
        </div>
        <div className="bg-gray-900/50 p-2.5 rounded-lg border border-gray-800">
          <div className="text-xs text-gray-500 flex items-center gap-1 mb-1">
            <Shield size={10} />
            通道健康度
          </div>
          <div className="text-lg font-mono font-semibold text-gray-200">
            {Number.isFinite(data.uptime) ? `${data.uptime.toFixed(1)}%` : "--"}
          </div>
        </div>
      </div>

      {data.is_agent_enabled && (
        <div className="mb-4 px-3 py-1.5 bg-blue-900/10 border border-blue-800/30 rounded-md flex items-center justify-between group/agent">
          <div className="flex items-center gap-2 text-xs text-blue-400">
            <Zap size={12} />
            <span>Agent 代理加速</span>
          </div>
          <button className="flex items-center gap-1 text-[10px] font-mono text-blue-300/70 hover:text-white hover:bg-blue-600/50 px-1.5 py-0.5 rounded transition cursor-pointer">
            {data.agent_node ?? "选择"} <ChevronDown size={10} />
          </button>
        </div>
      )}

      <div className="flex-1" />

      <div className="space-y-3 border-t border-gray-800 pt-3">
        <div className="flex items-center justify-between text-xs text-gray-400">
          <div className="flex items-center gap-2">
            <Key size={12} />
            <span>
              Key 负载池: <b className="text-gray-200">{availableKeys}</b>/{data.keys.length}
            </span>
          </div>
          <span className="font-mono text-[10px] text-gray-500">
            Probe: {data.probe_interval_seconds === -1 ? "disabled" : data.probe_interval_seconds != null ? `${data.probe_interval_seconds}s` : "default"}
          </span>
        </div>

        <LatencyBar ms={data.latency} />
      </div>

      <div className="absolute bottom-0 left-0 w-full p-4 bg-[#0f1117]/95 backdrop-blur-sm border-t border-gray-800 rounded-b-xl opacity-0 group-hover:opacity-100 transition-opacity flex gap-2">
        <button
          onClick={() => onManageKeys(data)}
          className="flex-1 bg-blue-600 hover:bg-blue-500 text-white text-xs py-2 rounded font-medium transition"
        >
          管理 Keys
        </button>
        <button
          onClick={() => onProbe(data)}
          disabled={!isAdmin}
          className="flex-1 bg-gray-800 hover:bg-gray-700 text-gray-300 text-xs py-2 rounded font-medium border border-gray-700 transition disabled:opacity-40"
        >
          探测模型
        </button>
      </div>
    </div>
  );
};

export const EndpointsPanel = ({
  endpoints,
  agents,
  usageStats,
  healthStatusMap,
  isAdmin,
  onCreateEndpoint,
  onEditEndpoint,
  onManageKeys,
  onProbeEndpoint,
}: EndpointsPanelProps) => {
  const summaryStats = useMemo(() => {
    const activeCount = endpoints.filter((endpoint) => endpoint.status === "online").length;
    const avgLatency = endpoints.length
      ? Math.round(
          endpoints.reduce((sum, endpoint) => sum + endpoint.latency, 0) /
            endpoints.length
        )
      : 0;
    const agentOnline = agents.filter((agent) => agent.status === "online").length;
    const todayTokens = usageStats?.total_tokens_today ?? 0;

    return [
      {
        label: "活跃端点",
        value: `${activeCount}/${endpoints.length}`,
        icon: Server,
        color: "text-blue-400",
      },
      {
        label: "今日用量",
        value: `${formatTokens(todayTokens)} tokens`,
        icon: PieChart,
        color: "text-purple-400",
      },
      {
        label: "平均首字延迟",
        value: `${avgLatency}ms`,
        icon: Activity,
        color: "text-yellow-400",
      },
      {
        label: "Agent 节点",
        value: `${agentOnline} Online`,
        icon: Globe,
        color: "text-green-400",
      },
    ];
  }, [endpoints, agents, usageStats]);

  return (
    <>
      <div className="grid grid-cols-4 gap-4 mb-8">
        {summaryStats.map((stat) => (
          <div
            key={stat.label}
            className="bg-[#0f1117] border border-gray-800 p-4 rounded-xl flex items-center justify-between"
          >
            <div>
              <p className="text-xs text-gray-500 font-medium mb-1">{stat.label}</p>
              <p className="text-2xl font-bold text-gray-100">{stat.value}</p>
            </div>
            <div className={`p-3 rounded-lg bg-gray-900 ${stat.color}`}>
              <stat.icon size={20} />
            </div>
          </div>
        ))}
      </div>

      <div className="flex items-center justify-between mb-6">
        <h2 className="text-xl font-bold text-white flex items-center gap-2">
          <Database size={20} className="text-blue-500" />
          API 端点列表
        </h2>
        <button
          onClick={onCreateEndpoint}
          disabled={!isAdmin}
          className="bg-blue-600 hover:bg-blue-500 text-white px-4 py-2 rounded-lg text-sm font-medium flex items-center gap-2 transition hover:shadow-lg hover:shadow-blue-900/20 disabled:opacity-50"
        >
          <Plus size={16} />
          添加新端点
        </button>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
        {endpoints.map((endpoint) => (
          <EndpointCard
            key={endpoint.id}
            data={endpoint}
            healthStatusMap={healthStatusMap}
            isAdmin={isAdmin}
            onEdit={onEditEndpoint}
            onManageKeys={onManageKeys}
            onProbe={onProbeEndpoint}
          />
        ))}

        <button
          type="button"
          onClick={onCreateEndpoint}
          disabled={!isAdmin}
          className="border border-dashed border-gray-800 rounded-xl p-6 flex flex-col items-center justify-center text-gray-600 hover:border-gray-600 hover:text-gray-400 hover:bg-gray-900/30 transition-all cursor-pointer min-h-[240px] group disabled:opacity-50"
        >
          <div className="w-12 h-12 rounded-full bg-gray-900 flex items-center justify-center mb-3 group-hover:scale-110 transition-transform">
            <Plus size={24} />
          </div>
          <p className="text-sm font-medium">配置新的 API 提供商</p>
        </button>
      </div>
    </>
  );
};
