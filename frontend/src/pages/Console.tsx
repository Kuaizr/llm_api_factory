import {
  Activity,
  AlertTriangle,
  BarChart3,
  Bell,
  Check,
  CheckCircle2,
  ChevronDown,
  Cpu,
  Database,
  Edit2,
  Filter,
  Globe,
  Key,
  Lock,
  Network,
  PieChart,
  Plus,
  RefreshCw,
  RotateCw,
  Save,
  Search,
  Server,
  Settings,
  Shield,
  Trash2,
  XCircle,
  Zap,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";

const apiBase = import.meta.env.VITE_API_BASE ?? "http://localhost:8000";
const tokenStorageKey = "llm_admin_token";

type EndpointStatus = "online" | "degraded" | "offline";

type ApiKey = {
  id: number;
  key_preview: string;
  rpm_limit: number | null;
  daily_limit: number | null;
  used_today: number;
  is_active: boolean;
  name?: string | null;
};

type HealthStatus = {
  api_key_id: number;
  probe_status: string;
  probe_status_code: number | null;
  probe_latency_ms: number | null;
  probe_checked_at: string | null;
  circuit_state: string;
  circuit_failures: number;
};

type ModelMap = {
  id: number;
  endpoint_id: number;
  model_alias: string;
  real_model: string;
  created_at: string;
};

type Endpoint = {
  id: number;
  name: string;
  base_url: string;
  provider: string;
  status: EndpointStatus;
  latency: number;
  uptime: number;
  is_agent_enabled: boolean;
  agent_node?: string | null;
  model_count: number;
  keys: ApiKey[];
  strategy: string;
};

type RoutingRule = {
  id: number;
  model_pattern: string;
  group_name: string;
  target_key_ids: number[];
  priority: number;
  strategy: string;
  is_active: boolean;
  request_count?: number;
  total_tokens?: number;
  avg_ttft_ms?: number | null;
  avg_tps?: number | null;
};

type AgentNode = {
  id: number;
  name: string;
  region: string | null;
  status: string;
  last_seen_at: string | null;
  endpoint_url: string | null;
  supports_gpt?: boolean | null;
  supports_gemini?: boolean | null;
  supports_claude?: boolean | null;
  probe_latency_ms?: number | null;
  probe_checked_at?: string | null;
};

type AgentBootstrapResult = {
  agent_id: number;
  name: string;
  token: string;
  install_command: string;
};

type UsageGroup = {
  group_name: string;
  percent: number;
  total_tokens: number;
};

type UsageTopKey = {
  api_key_id: number;
  endpoint_name: string;
  key_preview: string;
  total_tokens: number;
};

type UsageStats = {
  groups: UsageGroup[];
  top_keys: UsageTopKey[];
  total_tokens_today: number;
  generated_at: string;
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

type UsageTrendRange = "hour" | "day" | "week";

const buildHeaders = (token: string | null, jsonBody = false) => {
  const headers: Record<string, string> = {};
  if (token) {
    headers.Authorization = `Bearer ${token}`;
  }
  if (jsonBody) {
    headers["Content-Type"] = "application/json";
  }
  return headers;
};

const formatTokens = (value: number) => {
  if (value >= 1_000_000_000_000) return `${(value / 1_000_000_000_000).toFixed(1)}T`;
  if (value >= 1_000_000_000) return `${(value / 1_000_000_000).toFixed(1)}G`;
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(1)}K`;
  return `${value}`;
};

const usageTrendConfig: Record<UsageTrendRange, { label: string; hours: number; bucketMinutes: number }> = {
  hour: { label: "按小时（近 24 小时）", hours: 24, bucketMinutes: 60 },
  day: { label: "按天（近 30 天）", hours: 24 * 30, bucketMinutes: 1440 },
  week: { label: "按周（近 12 周）", hours: 24 * 7 * 12, bucketMinutes: 10080 },
};

const formatTimestamp = (value: string | null) =>
  value ? new Date(value).toLocaleString() : "--";

const maskEndpointUrl = (value: string) => {
  if (!value) return "hidden";
  try {
    const url = new URL(value);
    return `${url.protocol}//***`;
  } catch {
    return "***";
  }
};

const resolveKeyStatus = (key: ApiKey, health: HealthStatus | undefined) => {
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

const StatusBadge = ({ status }: { status: EndpointStatus | "online" | "offline" }) => {
  const colors = {
    online: "bg-green-500/20 text-green-400 border-green-500/30",
    degraded: "bg-yellow-500/20 text-yellow-400 border-yellow-500/30",
    offline: "bg-red-500/20 text-red-400 border-red-500/30",
  };

  const labels: Record<string, string> = {
    online: "Online",
    degraded: "Degraded",
    offline: "Offline",
  };

  return (
    <span
      className={`px-2 py-0.5 rounded-full text-xs font-medium border ${
        colors[status]
      } flex items-center gap-1.5`}
    >
      <span
        className={`w-1.5 h-1.5 rounded-full ${
          status === "online"
            ? "bg-green-400"
            : status === "degraded"
              ? "bg-yellow-400"
              : "bg-red-400"
        } animate-pulse`}
      />
      {labels[status]}
    </span>
  );
};

const LatencyBar = ({ ms }: { ms: number }) => {
  let color = "bg-green-500";
  if (ms > 300) color = "bg-yellow-500";
  if (ms > 800) color = "bg-red-500";
  if (ms === 0) color = "bg-gray-700";

  const width = Math.min((ms / 1000) * 100, 100);

  return (
    <div className="flex items-center gap-2 text-xs text-gray-400 mt-2">
      <Activity size={12} />
      <div className="flex-1 h-1.5 bg-gray-800 rounded-full overflow-hidden">
        <div
          className={`h-full ${color} transition-all duration-500`}
          style={{ width: `${width}%` }}
        />
      </div>
      <span className="w-12 text-right font-mono">
        {ms > 0 ? `${ms}ms` : "N/A"}
      </span>
    </div>
  );
};

type EndpointFormState = {
  name: string;
  base_url: string;
  provider: string;
  agent_node: string;
  is_active: boolean;
};

type AgentDeployFormState = {
  name: string;
};

const EditEndpointModal = ({
  endpoint,
  agents,
  isAdmin,
  onClose,
  onSave,
  onDelete,
}: {
  endpoint: Endpoint | null;
  agents: AgentNode[];
  isAdmin: boolean;
  onClose: () => void;
  onSave: (payload: EndpointFormState) => void;
  onDelete?: (endpoint: Endpoint) => void;
}) => {
  const [form, setForm] = useState<EndpointFormState>({
    name: endpoint?.name ?? "",
    base_url: endpoint?.base_url ?? "",
    provider: endpoint?.provider ?? "openai",
    agent_node: endpoint?.agent_node ?? "",
    is_active: endpoint?.status !== "offline",
  });

  return (
    <div className="fixed inset-0 z-[120] flex items-center justify-center bg-black/70 backdrop-blur-sm">
      <div className="bg-[#0f1117] border border-gray-800 rounded-xl w-[500px] shadow-2xl animate-in fade-in zoom-in-95 duration-200">
        <div className="p-6 border-b border-gray-800 flex justify-between items-center">
          <h3 className="text-lg font-bold text-white flex items-center gap-2">
            <Settings size={18} className="text-blue-500" />
            {endpoint ? "编辑 API 端点配置" : "添加 API 端点"}
          </h3>
          <button onClick={onClose} className="text-gray-500 hover:text-white">
            <XCircle size={20} />
          </button>
        </div>
        <div className="p-6 space-y-4">
          <div>
            <label className="text-xs font-bold text-gray-500 uppercase mb-1.5 block">
              端点名称
            </label>
            <input
              value={form.name}
              onChange={(event) =>
                setForm((prev) => ({ ...prev, name: event.target.value }))
              }
              className="w-full bg-gray-900 border border-gray-700 rounded p-2.5 text-sm text-white focus:border-blue-500 focus:outline-none"
              disabled={!isAdmin}
            />
          </div>
          <div>
            <label className="text-xs font-bold text-gray-500 uppercase mb-1.5 block">
              Base URL
            </label>
            <input
              value={form.base_url}
              onChange={(event) =>
                setForm((prev) => ({ ...prev, base_url: event.target.value }))
              }
              className="w-full bg-gray-900 border border-gray-700 rounded p-2.5 text-sm text-white font-mono focus:border-blue-500 focus:outline-none"
              disabled={!isAdmin}
            />
          </div>
          <div>
            <label className="text-xs font-bold text-gray-500 uppercase mb-1.5 block">
              Provider 类型
            </label>
            <select
              value={form.provider}
              onChange={(event) =>
                setForm((prev) => ({ ...prev, provider: event.target.value }))
              }
              className="w-full bg-gray-900 border border-gray-700 rounded p-2.5 text-sm text-white focus:border-blue-500 focus:outline-none"
              disabled={!isAdmin}
            >
              <option value="openai">OpenAI Compatible</option>
              <option value="anthropic">Anthropic</option>
            </select>
          </div>
          <div className="pt-2 border-t border-gray-800">
            <div className="flex items-center justify-between mb-2">
              <label className="text-xs font-bold text-gray-500 uppercase">
                Agent 代理加速
              </label>
            </div>
            <select
              value={form.agent_node || ""}
              onChange={(event) =>
                setForm((prev) => ({ ...prev, agent_node: event.target.value }))
              }
              className="w-full bg-gray-900 border border-gray-700 rounded p-2.5 text-sm text-white focus:border-blue-500 focus:outline-none"
              disabled={!isAdmin}
            >
              <option value="">不使用代理</option>
              {agents.filter((agent) => agent.status === "online").length === 0 ? (
                <option value="" disabled>
                  暂无可用 Agent 节点
                </option>
              ) : (
                agents
                  .filter((agent) => agent.status === "online")
                  .map((agent) => (
                    <option key={agent.id} value={agent.name}>
                      {agent.name} ({agent.region || "未知区域"})
                    </option>
                  ))
              )}
            </select>
            {form.agent_node && (
              <p className="text-xs text-blue-400 mt-2">
                已启用 Agent 代理加速：{form.agent_node}
              </p>
            )}
          </div>
        </div>
        <div className="p-4 border-t border-gray-800 flex items-center justify-between gap-2">
          {endpoint && onDelete && (
            <button
              onClick={() => onDelete(endpoint)}
              disabled={!isAdmin}
              className="px-4 py-2 text-sm text-red-400 border border-red-500/40 rounded transition hover:bg-red-500/10 disabled:opacity-50"
            >
              删除端点
            </button>
          )}
          <div className="flex justify-end gap-2 ml-auto">
            <button
              onClick={onClose}
              className="px-4 py-2 text-sm text-gray-400 hover:text-white transition"
            >
              取消
            </button>
            <button
              onClick={() => onSave(form)}
              disabled={!isAdmin}
              className="px-4 py-2 bg-blue-600 hover:bg-blue-500 text-white text-sm font-bold rounded transition disabled:opacity-50"
            >
              保存修改
            </button>
          </div>
        </div>
      </div>
    </div>
  );
};

const KeyConfigModal = ({
  keyData,
  isAdmin,
  onClose,
  onSave,
}: {
  keyData?: ApiKey;
  isAdmin: boolean;
  onClose: () => void;
  onSave: (payload: Partial<ApiKey> & { key?: string }) => void;
}) => {
  const [keyValue, setKeyValue] = useState("");
  const [name, setName] = useState(keyData?.name ?? "");
  const [dailyLimit, setDailyLimit] = useState(String(keyData?.daily_limit ?? ""));
  const [rpmLimit, setRpmLimit] = useState(String(keyData?.rpm_limit ?? ""));
  const [isActive, setIsActive] = useState(keyData?.is_active ?? true);

  return (
    <div className="fixed inset-0 z-[150] flex items-center justify-center bg-black/80 backdrop-blur-[2px]">
      <div className="bg-[#1a1d24] border border-gray-700 rounded-lg w-[400px] shadow-2xl p-5 animate-in fade-in zoom-in-95 duration-200">
        <h4 className="text-md font-bold text-white mb-4 flex items-center gap-2">
          <Key size={16} className="text-green-500" />
          {keyData ? "编辑 API Key 限制" : "添加新 API Key"}
        </h4>
        <div className="space-y-3">
          {!keyData && (
            <div>
              <label className="block text-xs font-medium text-gray-400 mb-1">
                API Key (sk-...)
              </label>
              <input
                value={keyValue}
                onChange={(event) => setKeyValue(event.target.value)}
                placeholder="sk-..."
                className="w-full bg-gray-900 border border-gray-600 rounded p-2 text-sm text-white focus:border-green-500 outline-none"
                disabled={!isAdmin}
              />
            </div>
          )}
          <div>
            <label className="block text-xs font-medium text-gray-400 mb-1">
              备注名称
            </label>
            <input
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder="e.g. Free Tier Account"
              className="w-full bg-gray-900 border border-gray-600 rounded p-2 text-sm text-white focus:border-green-500 outline-none"
              disabled={!isAdmin}
            />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs font-medium text-gray-400 mb-1">
                每日配额 (Quota)
              </label>
              <input
                value={dailyLimit}
                onChange={(event) => setDailyLimit(event.target.value)}
                type="number"
                className="w-full bg-gray-900 border border-gray-600 rounded p-2 text-sm text-white focus:border-green-500 outline-none"
                disabled={!isAdmin}
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-gray-400 mb-1">
                速率限制 (RPM)
              </label>
              <input
                value={rpmLimit}
                onChange={(event) => setRpmLimit(event.target.value)}
                type="number"
                className="w-full bg-gray-900 border border-gray-600 rounded p-2 text-sm text-white focus:border-green-500 outline-none"
                disabled={!isAdmin}
              />
            </div>
          </div>
          <div className="flex items-center gap-2 mt-2">
            <input
              type="checkbox"
              checked={isActive}
              onChange={(event) => setIsActive(event.target.checked)}
              className="w-4 h-4 rounded border-gray-600 bg-gray-800 text-green-500 focus:ring-offset-gray-900"
              disabled={!isAdmin}
            />
            <span className="text-sm text-gray-300">启用此 Key</span>
          </div>
        </div>
        <div className="flex justify-end gap-2 mt-6">
          <button
            onClick={onClose}
            className="px-3 py-1.5 text-xs text-gray-400 hover:text-white"
          >
            取消
          </button>
          <button
            onClick={() =>
              onSave({
                key: keyValue || undefined,
                name: name || undefined,
                daily_limit: dailyLimit ? Number(dailyLimit) : null,
                rpm_limit: rpmLimit ? Number(rpmLimit) : null,
                is_active: isActive,
              })
            }
            disabled={!isAdmin}
            className="px-3 py-1.5 bg-green-600 hover:bg-green-500 text-white text-xs font-bold rounded disabled:opacity-50"
          >
            保存
          </button>
        </div>
      </div>
    </div>
  );
};

const ManageKeysModal = ({
  endpoint,
  isAdmin,
  healthStatusMap,
  onClose,
  onCreate,
  onUpdate,
  onDelete,
  onRefresh,
}: {
  endpoint: Endpoint;
  isAdmin: boolean;
  healthStatusMap: Record<number, HealthStatus>;
  onClose: () => void;
  onCreate: (payload: Partial<ApiKey> & { key?: string }) => void;
  onUpdate: (keyId: number, payload: Partial<ApiKey> & { key?: string }) => void;
  onDelete: (keyId: number) => void;
  onRefresh: () => void;
}) => {
  const [editingKey, setEditingKey] = useState<ApiKey | null>(null);
  const [isAddingKey, setIsAddingKey] = useState(false);

  const getKeyStatus = (key: ApiKey) =>
    resolveKeyStatus(key, healthStatusMap[key.id]);

  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/70 backdrop-blur-sm">
      <div className="bg-[#0f1117] border border-gray-800 rounded-xl w-[900px] max-h-[85vh] flex flex-col shadow-2xl animate-in fade-in zoom-in-95 duration-200">
        <div className="p-6 border-b border-gray-800 flex justify-between items-center">
          <div>
            <h3 className="text-xl font-bold text-white flex items-center gap-2">
              <Key size={20} className="text-blue-500" />
              管理 API Keys (纤维束)
            </h3>
            <p className="text-sm text-gray-500 mt-1">
              Endpoint: {endpoint.name} - 配置负载极限
            </p>
          </div>
          <button onClick={onClose} className="text-gray-500 hover:text-white">
            <XCircle size={24} />
          </button>
        </div>

        <div className="p-6 overflow-y-auto flex-1">
          <div className="flex justify-end mb-4 gap-2">
            <button
              onClick={onRefresh}
              disabled={!isAdmin}
              className="bg-gray-800/60 text-gray-300 border border-gray-700 px-3 py-1.5 rounded text-sm hover:bg-gray-800 flex items-center gap-2 transition disabled:opacity-50"
            >
              <RefreshCw size={14} /> 刷新
            </button>
            <button
              onClick={() => setIsAddingKey(true)}
              disabled={!isAdmin}
              className="bg-blue-600/20 text-blue-400 border border-blue-500/30 px-3 py-1.5 rounded text-sm hover:bg-blue-600/30 flex items-center gap-2 transition disabled:opacity-50"
            >
              <Plus size={14} /> 添加新 Key
            </button>
          </div>

          <table className="w-full text-left text-sm text-gray-400">
            <thead className="bg-gray-900/50 text-gray-200 uppercase font-medium">
              <tr>
                <th className="px-4 py-3 rounded-l-lg">备注名 / Key</th>
                <th className="px-4 py-3">每日限额 (Quota)</th>
                <th className="px-4 py-3">今日已用</th>
                <th className="px-4 py-3">速率 (RPM)</th>
                <th className="px-4 py-3">状态</th>
                <th className="px-4 py-3 rounded-r-lg text-right">操作</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-800/50">
              {endpoint.keys.map((key) => {
                const status = getKeyStatus(key);
                return (
                  <tr
                    key={key.id}
                    className="hover:bg-gray-900/30 transition-colors group"
                  >
                    <td className="px-4 py-3">
                      <div className="font-medium text-gray-200">
                        {key.name || "Untitled Key"}
                      </div>
                      <div className="font-mono text-xs text-gray-500 group-hover:text-blue-400 transition">
                        {key.key_preview}
                      </div>
                    </td>
                    <td className="px-4 py-3">
                      {key.daily_limit ?? "--"}
                    </td>
                    <td className="px-4 py-3">
                      <div className="flex items-center gap-2">
                        <div className="w-16 h-1.5 bg-gray-800 rounded-full overflow-hidden">
                          <div
                            className="h-full bg-blue-500"
                            style={{
                              width: key.daily_limit
                                ? `${(key.used_today / key.daily_limit) * 100}%`
                                : "0%",
                            }}
                          />
                        </div>
                        <span className="text-xs">{key.used_today}</span>
                      </div>
                    </td>
                    <td className="px-4 py-3">{key.rpm_limit ?? "--"}</td>
                    <td className="px-4 py-3">
                      <span
                        className={`text-xs px-2 py-0.5 rounded border ${status.className}`}
                      >
                        {status.label}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-right flex justify-end gap-2">
                      <button
                        onClick={() => setEditingKey(key)}
                        disabled={!isAdmin}
                        className="p-1.5 hover:bg-gray-800 rounded text-blue-400 transition disabled:opacity-50"
                      >
                        <Edit2 size={14} />
                      </button>
                      <button
                        onClick={() => onDelete(key.id)}
                        disabled={!isAdmin}
                        className="p-1.5 hover:bg-gray-800 rounded text-red-400 transition disabled:opacity-40"
                      >
                        <Trash2 size={14} />
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>

      {(editingKey || isAddingKey) && (
        <KeyConfigModal
          keyData={editingKey || undefined}
          isAdmin={isAdmin}
          onClose={() => {
            setEditingKey(null);
            setIsAddingKey(false);
          }}
          onSave={(payload) => {
            if (editingKey) {
              onUpdate(editingKey.id, payload);
            } else {
              onCreate(payload);
            }
            setEditingKey(null);
            setIsAddingKey(false);
          }}
        />
      )}
    </div>
  );
};

const RuleEditorModal = ({
  endpoints,
  rule,
  isAdmin,
  authToken,
  onClose,
  onSave,
}: {
  endpoints: Endpoint[];
  rule?: RoutingRule;
  isAdmin: boolean;
  authToken: string | null;
  onClose: () => void;
  onSave: (payload: RoutingRule) => void;
}) => {
  const [modelPattern, setModelPattern] = useState(rule?.model_pattern ?? "");
  const [groupName, setGroupName] = useState(rule?.group_name ?? "custom");
  const [priority, setPriority] = useState(String(rule?.priority ?? 10));
  const [strategy, setStrategy] = useState(
    rule?.strategy ?? "weighted_round_robin"
  );
  const [selectedKeyIds, setSelectedKeyIds] = useState<Set<number>>(
    new Set(rule?.target_key_ids ?? [])
  );
  const [scanResults, setScanResults] = useState<string[]>([]);
  const [scanError, setScanError] = useState<string | null>(null);
  const [scanEndpointIds, setScanEndpointIds] = useState<Set<number> | null>(null);
  const [hasScanned, setHasScanned] = useState(false);
  const [isScanning, setIsScanning] = useState(false);

  const toggleKey = (id: number) => {
    const next = new Set(selectedKeyIds);
    if (next.has(id)) {
      next.delete(id);
    } else {
      next.add(id);
    }
    setSelectedKeyIds(next);
  };

  const handleScan = async () => {
    if (!modelPattern || !isAdmin) return;
    setIsScanning(true);
    setScanError(null);
    setHasScanned(false);
    setScanEndpointIds(null);
    try {
      const response = await fetch(
        `${apiBase}/admin/rules/scan?pattern=${encodeURIComponent(modelPattern)}`,
        {
          headers: buildHeaders(authToken),
        }
      );
      if (!response.ok) {
        setScanError("扫描失败，请检查正则表达式。");
        setScanResults([]);
        setHasScanned(true);
        return;
      }
      const data = (await response.json()) as string[];
      setScanResults(data);
      if (data.length) {
        const mapsResponse = await fetch(`${apiBase}/admin/model-maps`, {
          headers: buildHeaders(authToken),
        });
        if (!mapsResponse.ok) {
          setScanError("模型映射获取失败，请稍后再试。");
          setScanEndpointIds(null);
          setHasScanned(true);
          return;
        }
        const maps = (await mapsResponse.json()) as ModelMap[];
        const matchedModels = new Set(data);
        const endpointIds = new Set<number>();
        maps.forEach((item) => {
          if (matchedModels.has(item.model_alias)) {
            endpointIds.add(item.endpoint_id);
          }
        });
        setScanEndpointIds(endpointIds);
        const allowedKeyIds = new Set<number>();
        endpoints.forEach((endpoint) => {
          if (endpointIds.has(endpoint.id)) {
            endpoint.keys.forEach((key) => {
              allowedKeyIds.add(key.id);
            });
          }
        });
        setSelectedKeyIds((prev) =>
          new Set([...prev].filter((keyId) => allowedKeyIds.has(keyId)))
        );
      } else {
        setScanEndpointIds(new Set());
        setSelectedKeyIds(new Set());
      }
      setHasScanned(true);
    } catch {
      setScanError("扫描失败，请稍后再试。");
      setScanResults([]);
      setScanEndpointIds(null);
      setHasScanned(true);
    } finally {
      setIsScanning(false);
    }
  };

  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/70 backdrop-blur-sm">
      <div className="bg-[#0f1117] border border-gray-800 rounded-xl w-[700px] h-[80vh] flex flex-col shadow-2xl animate-in fade-in zoom-in-95 duration-200">
        <div className="p-6 border-b border-gray-800 flex justify-between items-center">
          <h3 className="text-xl font-bold text-white flex items-center gap-2">
            <Network size={20} className="text-yellow-500" />
            {rule ? "编辑路由规则" : "创建路由规则"}
          </h3>
          <button onClick={onClose} className="text-gray-500 hover:text-white">
            <XCircle size={24} />
          </button>
        </div>

        <div className="p-6 overflow-y-auto flex-1 space-y-6">
          <div className="space-y-4">
            <div>
              <label className="block text-xs font-bold text-gray-500 uppercase mb-1.5">
                模型匹配模式 (Wildcard)
              </label>
              <div className="flex gap-2">
                <input
                  value={modelPattern}
                  onChange={(event) => {
                    setModelPattern(event.target.value);
                    setHasScanned(false);
                    setScanError(null);
                    setScanResults([]);
                    setScanEndpointIds(null);
                  }}
                  placeholder="e.g. gpt-4* or claude-3-opus"
                  className="flex-1 bg-gray-900 border border-gray-700 rounded p-2.5 text-sm text-white focus:border-yellow-500 focus:outline-none"
                  disabled={!isAdmin}
                />
                <button
                  onClick={handleScan}
                  disabled={!modelPattern || isScanning || !isAdmin}
                  className="bg-yellow-600/20 text-yellow-500 border border-yellow-600/50 px-4 rounded text-sm font-medium hover:bg-yellow-600/30 flex items-center gap-2 disabled:opacity-50 transition"
                >
                  {isScanning ? "扫描中" : "扫描"}
                </button>
              </div>
              {scanError && (
                <div className="text-xs text-red-400 mt-2">{scanError}</div>
              )}
              {!scanError && hasScanned && (
                <div className="mt-2">
                  {scanResults.length ? (
                    <div className="flex flex-wrap gap-2">
                      {scanResults.map((model) => (
                        <span
                          key={model}
                          className="px-2 py-0.5 rounded text-[10px] bg-gray-800 text-gray-300 border border-gray-700 font-mono"
                        >
                          {model}
                        </span>
                      ))}
                    </div>
                  ) : (
                    <div className="text-xs text-gray-500">未找到匹配模型</div>
                  )}
                </div>
              )}
            </div>
            <div className="grid grid-cols-3 gap-4">
              <div>
                <label className="block text-xs font-bold text-gray-500 uppercase mb-1.5">
                  规则组
                </label>
                <input
                  value={groupName}
                  onChange={(event) => setGroupName(event.target.value)}
                  className="w-full bg-gray-900 border border-gray-700 rounded p-2.5 text-sm text-white focus:border-yellow-500 focus:outline-none"
                  disabled={!isAdmin}
                />
              </div>
              <div>
                <label className="block text-xs font-bold text-gray-500 uppercase mb-1.5">
                  优先级
                </label>
                <input
                  value={priority}
                  onChange={(event) => setPriority(event.target.value)}
                  type="number"
                  className="w-full bg-gray-900 border border-gray-700 rounded p-2.5 text-sm text-white focus:border-yellow-500 focus:outline-none"
                  disabled={!isAdmin}
                />
              </div>
              <div>
                <label className="block text-xs font-bold text-gray-500 uppercase mb-1.5">
                  调度策略
                </label>
                <select
                  value={strategy}
                  onChange={(event) => setStrategy(event.target.value)}
                  className="w-full bg-gray-900 border border-gray-700 rounded p-2.5 text-sm text-white focus:border-yellow-500 focus:outline-none"
                  disabled={!isAdmin}
                >
                  <option value="weighted_round_robin">加权轮询</option>
                  <option value="sequential">顺序主备</option>
                </select>
              </div>
            </div>
          </div>

          <div className="space-y-3">
            <h4 className="text-xs font-bold text-gray-500 uppercase tracking-wider mb-2">
              选择 Key
            </h4>
            <div className="space-y-2">
              {(hasScanned && scanEndpointIds
                ? endpoints.filter((endpoint) => scanEndpointIds.has(endpoint.id))
                : endpoints
              ).map((endpoint) => (
                <div
                  key={endpoint.id}
                  className="bg-gray-900/40 border border-gray-800 rounded-lg p-3"
                >
                  <div className="flex items-center justify-between mb-2">
                    <div className="text-sm text-gray-300 font-medium">
                      {endpoint.name}
                    </div>
                    <span className="text-xs text-gray-500">
                      {endpoint.keys.length} keys
                    </span>
                  </div>
                  <div className="space-y-1">
                    {endpoint.keys.map((key) => {
                      const isSelected = selectedKeyIds.has(key.id);
                      return (
                        <button
                          key={key.id}
                          type="button"
                          onClick={() => (isAdmin ? toggleKey(key.id) : undefined)}
                          className={`w-full flex items-center gap-3 p-2 rounded border transition ${
                            isSelected
                              ? "bg-yellow-900/20 border-yellow-600/50"
                              : "border-gray-800"
                          }`}
                        >
                          <div
                            className={`w-4 h-4 rounded border flex items-center justify-center ${
                              isSelected
                                ? "bg-yellow-600 border-yellow-600 text-black"
                                : "border-gray-600 bg-gray-800"
                            }`}
                          >
                            {isSelected && <Check size={10} strokeWidth={4} />}
                          </div>
                          <div className="flex-1 text-left">
                            <div className="flex items-center gap-2">
                              <span
                                className={`text-sm ${
                                  isSelected
                                    ? "text-yellow-200 font-medium"
                                    : "text-gray-400"
                                }`}
                              >
                                {key.name || "Untitled Key"}
                              </span>
                              <span className="text-xs text-gray-600 font-mono">
                                {key.key_preview}
                              </span>
                            </div>
                            <div className="text-[10px] text-gray-500 flex gap-2 mt-0.5">
                              <span>RPM: {key.rpm_limit ?? "--"}</span>
                              <span>Quota: {key.daily_limit ?? "--"}</span>
                            </div>
                          </div>
                        </button>
                      );
                    })}
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>

        <div className="p-4 border-t border-gray-800 flex justify-end gap-2">
          <button
            onClick={onClose}
            className="px-4 py-2 text-sm text-gray-400 hover:text-white transition"
          >
            取消
          </button>
          <button
            onClick={() =>
                onSave({
                  id: rule?.id ?? 0,
                  model_pattern: modelPattern,
                  group_name: groupName,
                  priority: Number(priority) || 0,
                  strategy,
                  is_active: rule?.is_active ?? true,
                  target_key_ids: Array.from(selectedKeyIds),
                })
            }
            disabled={!isAdmin}
            className="px-4 py-2 bg-yellow-600 hover:bg-yellow-500 text-white text-sm font-bold rounded transition disabled:opacity-50"
          >
            保存规则
          </button>
        </div>
      </div>
    </div>
  );
};

export const AgentsView = ({
  agents,
  onCreate,
  onDeploy,
  onDelete,
  onRotateToken,
  isAdmin,
}: {
  agents: AgentNode[];
  onCreate: () => void;
  onDeploy: (agent: AgentNode) => void;
  onDelete: (agent: AgentNode) => void;
  onRotateToken: (agent: AgentNode) => void;
  isAdmin: boolean;
}) => {
  const renderCapability = (label: string, enabled: boolean | null | undefined) => {
    const active = enabled === true;
    const className = active
      ? "text-green-400 bg-green-900/20 border-green-500/30"
      : "text-gray-500 bg-gray-900/40 border-gray-800";
    const title = enabled === false ? "未支持" : enabled === true ? "支持" : "未知";
    return (
      <span
        key={label}
        title={title}
        className={`px-2 py-0.5 rounded border text-[10px] uppercase ${className}`}
      >
        {label}
      </span>
    );
  };

  return (
    <div className="space-y-6">
      <div className="flex justify-between items-center">
        <div>
          <h2 className="text-xl font-bold text-white flex items-center gap-2">
            <Globe size={20} className="text-green-500" />
            Agent 节点网络
          </h2>
          <p className="text-sm text-gray-500 mt-1">
            管理部署在全球各地的代理服务器，用于跨境加速请求。
          </p>
        </div>
        <button
          onClick={onCreate}
          className="bg-green-600 hover:bg-green-500 text-white px-4 py-2 rounded-lg text-sm font-medium flex items-center gap-2"
        >
          <Plus size={16} /> 部署新节点
        </button>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
        {agents.map((agent) => {
          const isOnline = agent.status === "online";
          return (
            <div
              key={agent.id}
              className="bg-[#0f1117] border border-gray-800 rounded-xl p-5 relative overflow-hidden group"
            >
              <div className="absolute -right-4 -top-4 text-gray-800/20 group-hover:text-gray-800/40 transition-colors">
                <Globe size={100} />
              </div>

              <div className="relative z-10">
                <div className="flex justify-between items-start mb-4">
                  <div className="flex items-center gap-3">
                    <div
                      className={`p-2 rounded-lg ${
                        isOnline
                          ? "bg-green-900/20 text-green-400"
                          : "bg-red-900/20 text-red-400"
                      }`}
                    >
                      <Zap size={18} />
                    </div>
                    <div>
                      <h3 className="text-sm font-bold text-white">{agent.name}</h3>
                      <p className="text-xs text-gray-500">{agent.region ?? "--"}</p>
                    </div>
                  </div>
                  <StatusBadge status={isOnline ? "online" : "offline"} />
                </div>

                <div className="space-y-2 mb-4">
                  <div className="flex justify-between text-xs border-b border-gray-800 pb-2">
                    <span className="text-gray-500">基础延迟</span>
                    <span className="font-mono text-gray-300">
                      {agent.probe_latency_ms != null
                        ? `${agent.probe_latency_ms}ms`
                        : "--"}
                    </span>
                  </div>
                  <div className="flex justify-between text-xs border-b border-gray-800 pb-2">
                    <span className="text-gray-500">心跳</span>
                    <span className="font-mono text-gray-300">
                      {agent.last_seen_at
                        ? new Date(agent.last_seen_at).toLocaleString()
                        : "--"}
                    </span>
                  </div>
                  <div className="flex justify-between text-xs">
                    <span className="text-gray-500">Endpoint</span>
                    <span className="font-mono text-gray-500">
                      {agent.endpoint_url ?? "--"}
                    </span>
                  </div>
                  <div className="flex flex-wrap gap-2 pt-2">
                    {renderCapability("GPT", agent.supports_gpt ?? null)}
                    {renderCapability("Gemini", agent.supports_gemini ?? null)}
                    {renderCapability("Claude", agent.supports_claude ?? null)}
                  </div>
                </div>

                <div className="flex gap-2">
                  <button
                    onClick={() => onRotateToken(agent)}
                    disabled={!isAdmin || agent.status === "online"}
                    className={`flex-1 py-2 text-xs rounded border transition ${
                      agent.status === "online"
                        ? "bg-gray-700/20 text-gray-500 border-gray-700/40 cursor-not-allowed"
                        : "bg-blue-600/20 hover:bg-blue-600/30 text-blue-200 border-blue-700/40 disabled:opacity-50"
                    }`}
                    title={
                      !isAdmin
                        ? "需要管理员权限"
                        : agent.status === "online"
                          ? "已部署的Agent无法重新生成Token"
                          : "重新生成 Token"
                    }
                  >
                    {agent.status === "online" ? "已部署" : "Token"}
                  </button>
                  <button
                    onClick={() => onDelete(agent)}
                    disabled={!isAdmin}
                    className="py-2 px-2 bg-red-600/20 hover:bg-red-600/30 text-red-200 text-xs rounded border border-red-700/40 transition disabled:opacity-50"
                    title={isAdmin ? "删除节点" : "需要管理员权限"}
                  >
                    <Trash2 size={14} />
                  </button>
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
};

const AgentDeployModal = ({
  initialValues,
  isAdmin,
  isRedeploy,
  onClose,
  onSubmit,
  preloadedResult,
}: {
  initialValues: AgentDeployFormState;
  isAdmin: boolean;
  isRedeploy: boolean;
  onClose: () => void;
  onSubmit: (payload: AgentDeployFormState) => Promise<AgentBootstrapResult>;
  preloadedResult?: AgentBootstrapResult | null;
}) => {
  const [form, setForm] = useState<AgentDeployFormState>(initialValues);
  const [result, setResult] = useState<AgentBootstrapResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    setForm(initialValues);
    // If there's a preloaded result (from token rotation), show it; otherwise clear
    setResult(preloadedResult || null);
    setError(null);
  }, [initialValues.name, preloadedResult]);

  const canSubmit = isAdmin && form.name.trim().length > 0 && !loading;

  const handleCopy = (value: string) => {
    if (!value) return;
    if (typeof navigator !== "undefined" && navigator.clipboard?.writeText) {
      navigator.clipboard.writeText(value).catch(() => undefined);
    }
  };

  const handleSubmit = async () => {
    const trimmedName = form.name.trim();
    if (!trimmedName) {
      setError("请输入节点名称。");
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const data = await onSubmit({
        name: trimmedName,
      });
      setResult(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "生成部署命令失败");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="fixed inset-0 z-[160] flex items-center justify-center bg-black/70 backdrop-blur-sm">
      <div className="bg-[#0f1117] border border-gray-800 rounded-xl w-[560px] shadow-2xl animate-in fade-in zoom-in-95 duration-200">
        <div className="p-6 border-b border-gray-800 flex items-center justify-between">
          <div>
            <h3 className="text-lg font-bold text-white flex items-center gap-2">
              <Globe size={18} className="text-green-500" />
              {isRedeploy ? "部署 Agent 节点" : "部署新 Agent 节点"}
            </h3>
            <p className="text-xs text-gray-500 mt-1">
              生成一键部署命令，命令内包含专属 Token。
            </p>
          </div>
          <button onClick={onClose} className="text-gray-500 hover:text-white">
            <XCircle size={20} />
          </button>
        </div>
        <div className="p-6 space-y-4">
          {!isAdmin && (
            <div className="text-xs text-yellow-300 bg-yellow-900/20 border border-yellow-900/30 rounded px-3 py-2">
              需要管理员权限才能生成部署命令。
            </div>
          )}
          <div>
            <label
              htmlFor="agent-name"
              className="text-xs font-bold text-gray-500 uppercase mb-1.5 block"
            >
              节点名称
            </label>
            <input
              id="agent-name"
              value={form.name}
              onChange={(event) =>
                setForm((prev) => ({ ...prev, name: event.target.value }))
              }
              className="w-full bg-gray-900 border border-gray-700 rounded p-2.5 text-sm text-white focus:border-green-500 focus:outline-none"
              disabled={loading}
            />
            <p className="text-[11px] text-gray-500 mt-2">
              区域与 Endpoint 将由 Agent 启动后自动上报。
            </p>
          </div>

          {error && <div className="text-sm text-red-400">{error}</div>}

          <div className="flex justify-end gap-2">
            <button
              onClick={onClose}
              className="px-4 py-2 text-sm text-gray-300 bg-gray-800 rounded border border-gray-700 hover:bg-gray-700"
              disabled={loading}
            >
              取消
            </button>
            <button
              onClick={handleSubmit}
              disabled={!canSubmit}
              className="px-4 py-2 text-sm text-white bg-green-600 rounded hover:bg-green-500 disabled:opacity-60"
            >
              {loading ? "生成中..." : "生成部署命令"}
            </button>
          </div>

          {result && (
            <div className="space-y-3 border-t border-gray-800 pt-4">
              <div className="flex items-center justify-between">
                <label
                  htmlFor="agent-token"
                  className="text-xs font-bold text-gray-500 uppercase"
                >
                  Agent Token
                </label>
                <button
                  onClick={() => handleCopy(result.token)}
                  className="text-[10px] uppercase text-green-300 hover:text-green-200"
                >
                  复制
                </button>
              </div>
              <input
                id="agent-token"
                value={result.token}
                readOnly
                className="w-full bg-gray-900 border border-gray-700 rounded p-2 text-xs text-white font-mono"
              />
              <div className="flex items-center justify-between">
                <label
                  htmlFor="agent-command"
                  className="text-xs font-bold text-gray-500 uppercase"
                >
                  一键部署命令
                </label>
                <button
                  onClick={() => handleCopy(result.install_command)}
                  className="text-[10px] uppercase text-green-300 hover:text-green-200"
                >
                  复制命令
                </button>
              </div>
              <textarea
                id="agent-command"
                aria-label="一键部署命令"
                value={result.install_command}
                readOnly
                rows={3}
                className="w-full bg-gray-900 border border-gray-700 rounded p-2 text-xs text-white font-mono"
              />
              <p className="text-xs text-gray-500">
                Token 仅展示一次，请立即保存。
              </p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

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

const UsageStatsView = ({
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
          <p className="text-sm text-gray-500 mt-1">
            查看规则组消耗及 Top Key 排名。
          </p>
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
                  <td className="py-3 font-mono text-blue-400">
                    {row.key_preview}
                  </td>
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

const RulesView = ({
  rules,
  isAdmin,
  onEdit,
  onDelete,
}: {
  rules: RoutingRule[];
  isAdmin: boolean;
  onEdit: (rule?: RoutingRule) => void;
  onDelete: (rule: RoutingRule) => void;
}) => (
  <div className="space-y-6">
    <div className="flex justify-between items-center">
      <div>
        <h2 className="text-xl font-bold text-white">路由规则拓扑</h2>
        <p className="text-sm text-gray-500 mt-1">
          定义不同请求特征（模型名、用户组）下的分流策略。
        </p>
      </div>
      <button
        onClick={() => onEdit()}
        disabled={!isAdmin}
        className="bg-blue-600 hover:bg-blue-500 text-white px-4 py-2 rounded-lg text-sm font-medium flex items-center gap-2 disabled:cursor-not-allowed disabled:opacity-50"
      >
        <Plus size={16} /> 新建规则
      </button>
    </div>

    <div className="grid gap-4">
      {rules.map((rule) => (
        <div
          key={rule.id}
          className="bg-[#0f1117] border border-gray-800 p-5 rounded-xl flex items-center justify-between hover:border-gray-700 transition group"
        >
          <div className="flex items-center gap-4">
            <div className="p-3 bg-gray-900 rounded-lg text-gray-400 group-hover:text-yellow-500 transition-colors">
              <Network size={20} />
            </div>
            <div>
              <div className="flex items-center gap-2 mb-1">
                <span className="font-mono font-bold text-yellow-400 text-lg">
                  {rule.model_pattern}
                </span>
                <span className="text-xs px-2 py-0.5 rounded bg-gray-800 text-gray-400 border border-gray-700">
                  Priority: {rule.priority}
                </span>
              </div>
              <div className="flex items-center gap-2 text-sm text-gray-400">
                <span className="flex items-center gap-1">
                  <Database size={12} /> Group:
                  <b className="text-gray-300">{rule.group_name}</b>
                </span>
                <span className="text-gray-600">→</span>
                <span className="flex items-center gap-1">
                  <RotateCw size={12} /> Strategy:
                  <b className="text-gray-300">
                    {rule.strategy === "sequential" ? "顺序主备" : "加权轮询"}
                  </b>
                </span>
                <span className="text-gray-600">→</span>
                <span className="flex items-center gap-1">
                  <Key size={12} /> Target Keys:
                  <b className="text-gray-300">{rule.target_key_ids.length} selected</b>
                </span>
              </div>
              <div className="mt-2 grid grid-cols-2 gap-2 text-xs text-gray-400">
                <div className="flex items-center gap-2">
                  <span className="text-gray-500">TTFT</span>
                  <span className="font-mono text-gray-200">
                    {rule.avg_ttft_ms != null ? `${rule.avg_ttft_ms}ms` : "--"}
                  </span>
                </div>
                <div className="flex items-center gap-2">
                  <span className="text-gray-500">TPS</span>
                  <span className="font-mono text-gray-200">
                    {rule.avg_tps != null ? rule.avg_tps.toFixed(2) : "--"}
                  </span>
                </div>
                <div className="flex items-center gap-2">
                  <span className="text-gray-500">调用次数</span>
                  <span className="font-mono text-gray-200">
                    {rule.request_count ?? 0}
                  </span>
                </div>
                <div className="flex items-center gap-2">
                  <span className="text-gray-500">Token 消耗</span>
                  <span className="font-mono text-gray-200">
                    {formatTokens(rule.total_tokens ?? 0)}
                  </span>
                </div>
              </div>
            </div>
          </div>
          <div className="flex items-center gap-3">
            <div
              className={`px-3 py-1 rounded-full text-xs font-medium border ${
                rule.is_active
                  ? "bg-green-900/20 border-green-900/30 text-green-400"
                  : "bg-gray-800 border-gray-700 text-gray-500"
              }`}
            >
              {rule.is_active ? "Active" : "Inactive"}
            </div>
            <button
              onClick={() => onDelete(rule)}
              disabled={!isAdmin}
              className="p-2 hover:bg-gray-800 rounded-lg text-gray-400 hover:text-white transition disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:text-gray-400"
              aria-label="删除规则"
              title="删除规则"
            >
              <Trash2 size={16} />
            </button>
            <button
              onClick={() => onEdit(rule)}
              disabled={!isAdmin}
              className="p-2 hover:bg-gray-800 rounded-lg text-gray-400 hover:text-white transition disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:text-gray-400"
            >
              <Edit2 size={16} />
            </button>
          </div>
        </div>
      ))}
      {!rules.length && (
        <div className="text-sm text-gray-500">暂无规则</div>
      )}
    </div>
  </div>
);

const SettingsView = ({
  token,
  isAdmin,
  onLogin,
}: {
  token: string | null;
  isAdmin: boolean;
  onLogin: (password: string) => void;
}) => {
  const [password, setPassword] = useState("");

  return (
    <div className="max-w-4xl space-y-8">
      {!isAdmin && (
        <div className="bg-[#0f1117] border border-gray-800 rounded-xl p-6">
          <h3 className="text-lg font-bold text-white mb-6 flex items-center gap-2">
            <Shield size={20} className="text-green-500" />
            管理员登录
          </h3>
          <div className="space-y-4 max-w-md">
            <input
              type="password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              placeholder="输入管理员密码"
              className="w-full bg-gray-900 border border-gray-700 rounded p-2.5 text-sm text-white focus:border-blue-500 focus:outline-none"
            />
            <button
              onClick={() => onLogin(password)}
              className="bg-blue-600 hover:bg-blue-500 text-white px-6 py-2 rounded text-sm font-medium"
            >
              登录
            </button>
          </div>
        </div>
      )}

      {isAdmin && (
        <div className="bg-[#0f1117] border border-gray-800 rounded-xl p-6">
          <h3 className="text-lg font-bold text-white mb-6 flex items-center gap-2">
            <Bell size={20} className="text-blue-500" />
            告警通知 (Telegram)
          </h3>
          <div className="space-y-4 max-w-2xl">
            <div>
              <label className="block text-xs font-medium text-gray-500 mb-1.5 uppercase">
                Bot Token
              </label>
              <div className="relative">
                <input
                  type="text"
                  defaultValue=""
                  className="w-full bg-gray-900 border border-gray-700 rounded p-2.5 text-sm text-gray-300 font-mono focus:border-blue-500 focus:outline-none transition pl-10"
                />
                <Lock size={14} className="absolute left-3.5 top-3 text-gray-600" />
              </div>
            </div>
            <div>
              <label className="block text-xs font-medium text-gray-500 mb-1.5 uppercase">
                Chat ID / Channel ID
              </label>
              <input
                type="text"
                defaultValue=""
                className="w-full bg-gray-900 border border-gray-700 rounded p-2.5 text-sm text-gray-300 font-mono focus:border-blue-500 focus:outline-none transition"
              />
            </div>

            <div className="pt-2 flex items-center gap-3">
              <button className="bg-blue-600 hover:bg-blue-500 text-white px-6 py-2 rounded text-sm font-medium transition flex items-center gap-2">
                <Save size={16} /> 保存配置
              </button>
              <button className="text-gray-400 hover:text-white px-4 py-2 text-sm transition">
                发送测试消息
              </button>
            </div>
          </div>
          <p className="text-xs text-gray-600 mt-4">
            当前 Token: {token ? "已登录" : "未登录"}
          </p>
        </div>
      )}

      <div className="bg-[#0f1117] border border-gray-800 rounded-xl p-6">
        <h3 className="text-lg font-bold text-white mb-6 flex items-center gap-2">
          <Shield size={20} className="text-green-500" />
          系统安全
        </h3>
        <div className="grid grid-cols-2 gap-8">
          <div className="space-y-4">
            <div>
              <label className="block text-xs font-medium text-gray-500 mb-1.5 uppercase">
                当前管理员密码
              </label>
              <input
                type="password"
                placeholder="••••••••"
                className="w-full bg-gray-900 border border-gray-700 rounded p-2.5 text-sm text-white focus:border-blue-500 focus:outline-none transition"
                disabled
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-gray-500 mb-1.5 uppercase">
                新密码
              </label>
              <input
                type="password"
                placeholder="••••••••"
                className="w-full bg-gray-900 border border-gray-700 rounded p-2.5 text-sm text-white focus:border-blue-500 focus:outline-none transition"
                disabled
              />
            </div>
            <button className="bg-gray-800 hover:bg-gray-700 text-white px-4 py-2 rounded text-sm font-medium border border-gray-700 transition" disabled>
              更新密码
            </button>
          </div>
          <div className="bg-yellow-900/10 border border-yellow-900/20 p-4 rounded-lg">
            <div className="flex items-start gap-3">
              <AlertTriangle className="text-yellow-500 shrink-0" size={18} />
              <div>
                <h4 className="text-sm font-bold text-yellow-500 mb-1">安全警告</h4>
                <p className="text-xs text-yellow-200/60 leading-relaxed">
                  该系统目前仅支持单用户（Admin）模式。请确保您的密码足够复杂。
                </p>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

export const Console = () => {
  const [activeTab, setActiveTab] = useState<
    "endpoints" | "agents" | "rules" | "usage" | "settings"
  >("endpoints");
  const [endpoints, setEndpoints] = useState<Endpoint[]>([]);
  const [agents, setAgents] = useState<AgentNode[]>([]);
  const [rules, setRules] = useState<RoutingRule[]>([]);
  const [usageStats, setUsageStats] = useState<UsageStats | null>(null);
  const [usageTrendRange, setUsageTrendRange] = useState<UsageTrendRange>("hour");
  const [usageTrendBuckets, setUsageTrendBuckets] = useState<MetricsBucket[]>([]);
  const [usageTrendUpdatedAt, setUsageTrendUpdatedAt] = useState<string | null>(null);
  const [usageTrendLoading, setUsageTrendLoading] = useState(false);
  const [usageTrendError, setUsageTrendError] = useState<string | null>(null);
  const [healthStatusMap, setHealthStatusMap] = useState<Record<number, HealthStatus>>({});

  const [token, setToken] = useState<string | null>(() => {
    if (
      typeof localStorage === "undefined" ||
      typeof localStorage.getItem !== "function"
    ) {
      return null;
    }
    return localStorage.getItem(tokenStorageKey);
  });
  const [isAdmin, setIsAdmin] = useState(false);
  const [manageKeysEndpoint, setManageKeysEndpoint] = useState<Endpoint | null>(
    null
  );
  const [editingEndpoint, setEditingEndpoint] = useState<
    Endpoint | undefined | null
  >(null);
  const [editingRule, setEditingRule] = useState<RoutingRule | undefined | null>(
    null
  );
  const [probeEndpoint, setProbeEndpoint] = useState<Endpoint | null>(null);
  const [probeModels, setProbeModels] = useState<ModelMap[]>([]);
  const [probeAliasEdits, setProbeAliasEdits] = useState<Record<number, string>>(
    {}
  );
  const [probeError, setProbeError] = useState<string | null>(null);
  const [probeLoading, setProbeLoading] = useState(false);
  const [agentDeployOpen, setAgentDeployOpen] = useState(false);
  const [agentDeployTarget, setAgentDeployTarget] = useState<AgentNode | null>(null);
  const [agentDeployResult, setAgentDeployResult] = useState<AgentBootstrapResult | null>(null);

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

  const agentDeployInitialValues = useMemo<AgentDeployFormState>(
    () => ({
      name: agentDeployTarget?.name ?? "",
    }),
    [agentDeployTarget]
  );

  const loadAuth = async (authToken: string | null) => {
    if (!authToken) {
      setIsAdmin(false);
      return;
    }
    try {
      const response = await fetch(`${apiBase}/auth/me`, {
        headers: buildHeaders(authToken),
      });
      if (!response.ok) {
        setIsAdmin(false);
        return;
      }
      const data = (await response.json()) as { is_admin: boolean };
      setIsAdmin(Boolean(data.is_admin));
    } catch {
      setIsAdmin(false);
    }
  };

  const loadEndpoints = async (authToken: string | null) => {
    try {
      const response = await fetch(`${apiBase}/admin/endpoints`, {
        headers: buildHeaders(authToken),
      });
      if (response.status === 401) {
        setIsAdmin(false);
        const fallback = await fetch(`${apiBase}/v1/status/dashboard`);
        const publicData = (await fallback.json()) as {
          endpoints: Array<
            Omit<Endpoint, "keys" | "model_count" | "is_agent_enabled" | "strategy">
          >;
        };
        setEndpoints(
          publicData.endpoints.map((endpoint) => ({
            ...endpoint,
            strategy: "weighted_round_robin",
            is_agent_enabled: Boolean(endpoint.agent_node),
            model_count: 0,
            keys: [],
          }))
        );
        return;
      }
      const data = (await response.json()) as Endpoint[];
      setEndpoints(data);
    } catch {
      setEndpoints([]);
    }
  };

  const loadAgents = async (authToken: string | null) => {
    try {
      const response = await fetch(`${apiBase}/admin/agents`, {
        headers: buildHeaders(authToken),
      });
      if (response.status === 401) {
        const fallback = await fetch(`${apiBase}/v1/status/dashboard`);
        const publicData = (await fallback.json()) as { agents: AgentNode[] };
        setAgents(publicData.agents);
        return;
      }
      const data = (await response.json()) as AgentNode[];
      setAgents(data);
    } catch {
      setAgents([]);
    }
  };

  const openAgentDeploy = (agent: AgentNode | null) => {
    setAgentDeployTarget(agent);
    setAgentDeployOpen(true);
  };

  const closeAgentDeploy = () => {
    setAgentDeployOpen(false);
    setAgentDeployTarget(null);
  };

  const handleAgentBootstrap = async (
    payload: AgentDeployFormState
  ): Promise<AgentBootstrapResult> => {
    if (!isAdmin) {
      throw new Error("请先登录管理员。");
    }
    const normalized = {
      name: payload.name.trim(),
    };
    const response = await fetch(`${apiBase}/admin/agents/bootstrap`, {
      method: "POST",
      headers: buildHeaders(token, true),
      body: JSON.stringify({
        name: normalized.name,
      }),
    });
    if (!response.ok) {
      let message = "生成部署命令失败";
      try {
        const data = (await response.json()) as { detail?: string };
        if (data?.detail) {
          message = data.detail;
        }
      } catch {
        // ignore parse errors
      }
      throw new Error(message);
    }
    const data = (await response.json()) as AgentBootstrapResult;
    await loadAgents(token);
    return data;
  };

  const loadRules = async (authToken: string | null) => {
    try {
      const response = await fetch(`${apiBase}/admin/rules`, {
        headers: buildHeaders(authToken),
      });
      if (!response.ok) {
        setRules([]);
        return;
      }
      const data = (await response.json()) as RoutingRule[];
      setRules(data);
    } catch {
      setRules([]);
    }
  };

  const loadUsage = async (authToken: string | null) => {
    if (!authToken) {
      setUsageStats(null);
      return;
    }
    try {
      const response = await fetch(`${apiBase}/admin/stats/usage`, {
        headers: buildHeaders(authToken),
      });
      if (!response.ok) {
        setUsageStats(null);
        return;
      }
      const data = (await response.json()) as UsageStats;
      setUsageStats(data);
    } catch {
      setUsageStats(null);
    }
  };

  const loadUsageTrend = async (
    authToken: string | null,
    nextRange: UsageTrendRange = usageTrendRange
  ) => {
    if (!authToken) {
      setUsageTrendBuckets([]);
      setUsageTrendUpdatedAt(null);
      setUsageTrendError(null);
      return;
    }
    const config = usageTrendConfig[nextRange];
    if (!config) {
      return;
    }
    setUsageTrendLoading(true);
    try {
      const response = await fetch(
        `${apiBase}/admin/metrics/timeseries?hours=${config.hours}&bucket_minutes=${config.bucketMinutes}`,
        {
          headers: buildHeaders(authToken),
        }
      );
      if (!response.ok) {
        setUsageTrendBuckets([]);
        setUsageTrendError("无法获取趋势数据");
        return;
      }
      const data = (await response.json()) as MetricsBucket[];
      setUsageTrendBuckets(data);
      setUsageTrendUpdatedAt(new Date().toISOString());
      setUsageTrendError(null);
    } catch {
      setUsageTrendBuckets([]);
      setUsageTrendError("无法获取趋势数据");
    } finally {
      setUsageTrendLoading(false);
    }
  };

  const handleUsageRangeChange = (range: UsageTrendRange) => {
    setUsageTrendRange(range);
    void loadUsageTrend(token, range);
  };

  const handleUsageRefresh = () => {
    void loadUsageTrend(token, usageTrendRange);
  };

  const loadHealthStatus = async (authToken: string | null) => {
    if (!authToken) {
      setHealthStatusMap({});
      return;
    }
    try {
      const response = await fetch(`${apiBase}/admin/health-status`, {
        headers: buildHeaders(authToken),
      });
      if (!response.ok) {
        setHealthStatusMap({});
        return;
      }
      const data = (await response.json()) as HealthStatus[];
      const map: Record<number, HealthStatus> = {};
      data.forEach((item) => {
        map[item.api_key_id] = item;
      });
      setHealthStatusMap(map);
    } catch {
      setHealthStatusMap({});
    }
  };

  const refreshAll = async (authToken: string | null) => {
    await Promise.all([
      loadAuth(authToken),
      loadEndpoints(authToken),
      loadAgents(authToken),
      loadRules(authToken),
      loadUsage(authToken),
      loadUsageTrend(authToken),
      loadHealthStatus(authToken),
    ]);
  };

  useEffect(() => {
    void refreshAll(token);
  }, [token]);

  useEffect(() => {
    if (!manageKeysEndpoint) return;
    const updated = endpoints.find(
      (endpoint) => endpoint.id === manageKeysEndpoint.id
    );
    if (updated) {
      setManageKeysEndpoint(updated);
    }
  }, [endpoints, manageKeysEndpoint]);

  useEffect(() => {
    const interval = window.setInterval(() => {
      void loadEndpoints(token);
      if (token) {
        void loadHealthStatus(token);
        void loadUsage(token);
      }
    }, 60_000);
    return () => window.clearInterval(interval);
  }, [token]);

  const handleLogin = async (password: string) => {
    if (!password) return;
    try {
      const response = await fetch(`${apiBase}/auth/login`, {
        method: "POST",
        headers: buildHeaders(null, true),
        body: JSON.stringify({ password }),
      });
      if (!response.ok) {
        setIsAdmin(false);
        return;
      }
      const data = (await response.json()) as { token: string };
      if (
        typeof localStorage !== "undefined" &&
        typeof localStorage.setItem === "function"
      ) {
        localStorage.setItem(tokenStorageKey, data.token);
      }
      setToken(data.token);
    } catch {
      setIsAdmin(false);
    }
  };

  const handleSaveEndpoint = async (payload: EndpointFormState) => {
    if (!isAdmin) return;
    const method = editingEndpoint ? "PATCH" : "POST";
    const url = editingEndpoint
      ? `${apiBase}/admin/endpoints/${editingEndpoint.id}`
      : `${apiBase}/admin/endpoints`;
    const response = await fetch(url, {
      method,
      headers: buildHeaders(token, true),
      body: JSON.stringify({
        name: payload.name,
        base_url: payload.base_url,
        provider: payload.provider,
        agent_node: payload.agent_node,
        is_active: payload.is_active,
      }),
    });
    if (response.ok) {
      await loadEndpoints(token);
    }
    setEditingEndpoint(null);
  };

  const refreshKeys = async () => {
    await loadEndpoints(token);
    await loadHealthStatus(token);
  };

  const handleCreateKey = async (payload: Partial<ApiKey> & { key?: string }) => {
    if (!manageKeysEndpoint) return;
    const response = await fetch(
      `${apiBase}/admin/endpoints/${manageKeysEndpoint.id}/keys`,
      {
        method: "POST",
        headers: buildHeaders(token, true),
        body: JSON.stringify({
          key: payload.key,
          name: payload.name,
          rpm_limit: payload.rpm_limit,
          daily_limit: payload.daily_limit,
          used_today: payload.used_today ?? 0,
          total_usage: 0,
          is_active: payload.is_active ?? true,
        }),
      }
    );
    if (response.ok) {
      await refreshKeys();
    }
  };

  const handleUpdateKey = async (
    keyId: number,
    payload: Partial<ApiKey> & { key?: string }
  ) => {
    const response = await fetch(`${apiBase}/admin/keys/${keyId}`, {
      method: "PUT",
      headers: buildHeaders(token, true),
      body: JSON.stringify({
        key: payload.key,
        name: payload.name,
        rpm_limit: payload.rpm_limit,
        daily_limit: payload.daily_limit,
        is_active: payload.is_active,
      }),
    });
    if (response.ok) {
      await refreshKeys();
    }
  };

  const handleDeleteKey = async (keyId: number) => {
    if (!isAdmin) return;
    if (!window.confirm("确认删除该 API Key 吗？")) return;
    const response = await fetch(`${apiBase}/admin/api-keys/${keyId}`, {
      method: "DELETE",
      headers: buildHeaders(token),
    });
    if (response.ok) {
      await refreshKeys();
    }
  };

  const handleProbeEndpoint = async (endpoint: Endpoint) => {
    if (!isAdmin) return;
    setProbeEndpoint(endpoint);
    setProbeModels([]);
    setProbeError(null);
    setProbeLoading(true);
    try {
      const response = await fetch(`${apiBase}/admin/endpoints/${endpoint.id}/probe`, {
        method: "POST",
        headers: buildHeaders(token),
      });
      if (!response.ok) {
        setProbeError("探测失败，请检查端点与 API Key 是否可用。");
        return;
      }
      const data = (await response.json()) as ModelMap[];
      setProbeModels(data);
      const aliasSeed: Record<number, string> = {};
      data.forEach((model) => {
        aliasSeed[model.id] = model.model_alias;
      });
      setProbeAliasEdits(aliasSeed);
      await loadEndpoints(token);
    } catch {
      setProbeError("探测失败，请稍后再试。");
    } finally {
      setProbeLoading(false);
    }
  };

  const handleDeleteEndpoint = async (endpoint: Endpoint) => {
    if (!isAdmin) return;
    if (!window.confirm(`确认删除端点 "${endpoint.name}" 吗？`)) return;
    const response = await fetch(`${apiBase}/admin/endpoints/${endpoint.id}`, {
      method: "DELETE",
      headers: buildHeaders(token),
    });
    if (response.ok) {
      await loadEndpoints(token);
      if (manageKeysEndpoint?.id === endpoint.id) {
        setManageKeysEndpoint(null);
      }
      if (editingEndpoint?.id === endpoint.id) {
        setEditingEndpoint(null);
      }
    }
  };

  const handleUpdateModelAlias = async (model: ModelMap) => {
    if (!isAdmin) return;
    const nextAlias = probeAliasEdits[model.id]?.trim() || model.model_alias;
    if (!nextAlias || nextAlias === model.model_alias) {
      return;
    }
    const response = await fetch(`${apiBase}/admin/model-maps/${model.id}`, {
      method: "PATCH",
      headers: buildHeaders(token, true),
      body: JSON.stringify({ model_alias: nextAlias }),
    });
    if (!response.ok) {
      setProbeError("别名保存失败，请稍后再试。");
      return;
    }
    setProbeModels((prev) =>
      prev.map((item) =>
        item.id === model.id ? { ...item, model_alias: nextAlias } : item
      )
    );
  };

  const handleSaveRule = async (payload: RoutingRule) => {
    if (!isAdmin) return;
    const method = payload.id ? "PATCH" : "POST";
    const url = payload.id
      ? `${apiBase}/admin/rules/${payload.id}`
      : `${apiBase}/admin/rules`;
    const response = await fetch(url, {
      method,
      headers: buildHeaders(token, true),
      body: JSON.stringify({
        model_pattern: payload.model_pattern,
        group_name: payload.group_name,
        priority: payload.priority,
        strategy: payload.strategy,
        is_active: payload.is_active,
        target_key_ids: payload.target_key_ids,
      }),
    });
    if (response.ok) {
      await loadRules(token);
    }
    setEditingRule(null);
  };

const handleDeleteRule = async (rule: RoutingRule) => {
  if (!isAdmin) return;
  if (!window.confirm(`确认删除路由规则 "${rule.model_pattern}" 吗？`)) {
    return;
  }
  const response = await fetch(`${apiBase}/admin/rules/${rule.id}`, {
    method: "DELETE",
    headers: buildHeaders(token),
  });
  if (response.ok) {
    await loadRules(token);
  }
  if (editingRule?.id === rule.id) {
    setEditingRule(null);
  }
};

const handleDeleteAgent = async (agent: AgentNode) => {
  if (!isAdmin) return;
  if (!window.confirm(`确认删除 Agent 节点 "${agent.name}" 吗？`)) {
    return;
  }
  const response = await fetch(`${apiBase}/admin/agents/${agent.id}`, {
    method: "DELETE",
    headers: buildHeaders(token),
  });
  if (response.ok) {
    await loadAgents(token);
  }
};

const handleRotateAgentToken = async (agent: AgentNode) => {
  if (!isAdmin) return;
  // Check if agent is already deployed
  if (agent.status === "online") {
    alert(`Agent "${agent.name}" 已部署，无法重新生成Token。如需重新部署，请先删除该Agent并创建新的。`);
    return;
  }
  // First clear any previous result
  setAgentDeployResult(null);
  try {
    const response = await fetch(`${apiBase}/admin/agents/${agent.id}/rotate-token`, {
      method: "POST",
      headers: buildHeaders(token),
    });
    if (!response.ok) {
      const data = (await response.json()) as { detail?: string };
      throw new Error(data.detail || "Failed to rotate token");
    }
    const data = (await response.json()) as AgentBootstrapResult;
    await loadAgents(token);
    // Set the result directly so the modal shows it
    setAgentDeployResult(data);
    setAgentDeployTarget(agent);
    setAgentDeployOpen(true);
  } catch (err) {
    console.error("Failed to rotate token:", err);
    alert(err instanceof Error ? err.message : "重新生成Token失败");
  }
};


  const EndpointCard = ({
    data,
    healthStatusMap,
  }: {
    data: Endpoint;
    healthStatusMap: Record<number, HealthStatus>;
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
                : "bg-purple-900/20 text-purple-400"
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
            onClick={() => setEditingEndpoint(data)}
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
        </div>

        <LatencyBar ms={data.latency} />
      </div>

      <div className="absolute bottom-0 left-0 w-full p-4 bg-[#0f1117]/95 backdrop-blur-sm border-t border-gray-800 rounded-b-xl opacity-0 group-hover:opacity-100 transition-opacity flex gap-2">
        <button
          onClick={() => setManageKeysEndpoint(data)}
          className="flex-1 bg-blue-600 hover:bg-blue-500 text-white text-xs py-2 rounded font-medium transition"
        >
          管理 Keys
        </button>
        <button
          onClick={() => handleProbeEndpoint(data)}
          disabled={!isAdmin}
          className="flex-1 bg-gray-800 hover:bg-gray-700 text-gray-300 text-xs py-2 rounded font-medium border border-gray-700 transition disabled:opacity-40"
        >
          探测模型
        </button>
      </div>
    </div>
  );
};

  return (
    <div className="min-h-screen bg-[#050505] text-gray-300 font-sans selection:bg-blue-500/30">
      <nav className="border-b border-gray-800 bg-[#0a0a0a]/80 backdrop-blur sticky top-0 z-50">
        <div className="max-w-7xl mx-auto px-6 h-16 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="bg-blue-600 p-1.5 rounded-lg">
              <Server className="text-white" size={20} />
            </div>
            <span className="font-bold text-lg tracking-tight text-white">
              LLM API Factory
            </span>
            <span className="px-2 py-0.5 rounded text-[10px] bg-gray-800 text-gray-400 border border-gray-700">
              v2.0-Probe
            </span>
          </div>

          <div className="flex items-center gap-6 h-full">
            {[
              { id: "endpoints", label: "端点管理" },
              { id: "agents", label: "节点管理" },
              { id: "rules", label: "路由规则" },
              { id: "usage", label: "流量统计" },
              { id: "settings", label: "系统设置" },
            ].map((tab) => (
              <button
                key={tab.id}
                onClick={() => setActiveTab(tab.id as typeof activeTab)}
                className={`text-sm font-medium transition-colors h-full border-b-2 pt-1 px-1 ${
                  activeTab === tab.id
                    ? "text-white border-blue-500"
                    : "text-gray-500 border-transparent hover:text-gray-300"
                }`}
              >
                {tab.label}
              </button>
            ))}
          </div>

          <div className="flex items-center gap-4">
            <div className="flex items-center gap-2 text-xs text-green-500 bg-green-900/10 px-3 py-1.5 rounded-full border border-green-900/20">
              <span className="w-1.5 h-1.5 rounded-full bg-green-500 animate-pulse" />
              系统运转正常
            </div>
            <div className="w-8 h-8 rounded-full bg-gradient-to-br from-blue-500 to-purple-600 border-2 border-gray-800 cursor-pointer hover:scale-105 transition" />
          </div>
        </div>
      </nav>

      <main className="max-w-7xl mx-auto px-6 py-8 animate-in fade-in slide-in-from-bottom-4 duration-500">
        {activeTab === "endpoints" && (
          <>
            <div className="grid grid-cols-4 gap-4 mb-8">
              {summaryStats.map((stat) => (
                <div
                  key={stat.label}
                  className="bg-[#0f1117] border border-gray-800 p-4 rounded-xl flex items-center justify-between"
                >
                  <div>
                    <p className="text-xs text-gray-500 font-medium mb-1">
                      {stat.label}
                    </p>
                    <p className="text-2xl font-bold text-gray-100">
                      {stat.value}
                    </p>
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
                onClick={() => setEditingEndpoint(undefined)}
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
                />
              ))}

              <button
                type="button"
                onClick={() => setEditingEndpoint(undefined)}
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
        )}

        {activeTab === "agents" && (
          <AgentsView
            agents={agents}
            onCreate={() => openAgentDeploy(null)}
            onDeploy={(agent) => openAgentDeploy(agent)}
            onDelete={handleDeleteAgent}
            onRotateToken={handleRotateAgentToken}
            isAdmin={isAdmin}
          />
        )}
        {activeTab === "rules" && (
          <RulesView
            rules={rules}
            isAdmin={isAdmin}
            onEdit={(rule) => setEditingRule(rule ?? undefined)}
            onDelete={handleDeleteRule}
          />
        )}
        {activeTab === "usage" && (
          <UsageStatsView
            stats={usageStats}
            buckets={usageTrendBuckets}
            range={usageTrendRange}
            updatedAt={usageTrendUpdatedAt}
            loading={usageTrendLoading}
            error={usageTrendError}
            onRangeChange={handleUsageRangeChange}
            onRefresh={handleUsageRefresh}
          />
        )}
        {activeTab === "settings" && (
          <SettingsView token={token} isAdmin={isAdmin} onLogin={handleLogin} />
        )}
      </main>

      {manageKeysEndpoint && (
        <ManageKeysModal
          endpoint={manageKeysEndpoint}
          isAdmin={isAdmin}
          healthStatusMap={healthStatusMap}
          onClose={() => setManageKeysEndpoint(null)}
          onCreate={handleCreateKey}
          onUpdate={handleUpdateKey}
          onDelete={handleDeleteKey}
          onRefresh={refreshKeys}
        />
      )}
      {editingEndpoint !== null && (
        <EditEndpointModal
          endpoint={editingEndpoint ?? null}
          agents={agents}
          isAdmin={isAdmin}
          onClose={() => setEditingEndpoint(null)}
          onSave={handleSaveEndpoint}
          onDelete={handleDeleteEndpoint}
        />
      )}
      {editingRule !== null && (
        <RuleEditorModal
          endpoints={endpoints}
          rule={editingRule ?? undefined}
          isAdmin={isAdmin}
          authToken={token}
          onClose={() => setEditingRule(null)}
          onSave={handleSaveRule}
        />
      )}
      {agentDeployOpen && (
        <AgentDeployModal
          initialValues={agentDeployInitialValues}
          isAdmin={isAdmin}
          isRedeploy={Boolean(agentDeployTarget)}
          onClose={() => {
            closeAgentDeploy();
            setAgentDeployResult(null);
          }}
          onSubmit={handleAgentBootstrap}
          preloadedResult={agentDeployResult}
        />
      )}
      {probeEndpoint && (
        <div className="fixed inset-0 z-[160] flex items-center justify-center bg-black/70 backdrop-blur-sm">
          <div className="bg-[#0f1117] border border-gray-800 rounded-xl w-[540px] max-h-[80vh] flex flex-col shadow-2xl animate-in fade-in zoom-in-95 duration-200">
            <div className="p-5 border-b border-gray-800 flex items-center justify-between">
              <div>
                <h3 className="text-lg font-bold text-white">模型探测结果</h3>
                <p className="text-xs text-gray-500 mt-1">
                  Endpoint: {probeEndpoint.name}
                </p>
              </div>
              <button
                onClick={() => {
                  setProbeEndpoint(null);
                  setProbeModels([]);
                  setProbeAliasEdits({});
                  setProbeError(null);
                  setProbeLoading(false);
                }}
                className="text-gray-500 hover:text-white"
              >
                <XCircle size={20} />
              </button>
            </div>
            <div className="p-5 overflow-y-auto">
              {probeLoading && (
                <div className="text-sm text-blue-400 flex items-center gap-2">
                  <RefreshCw size={14} className="animate-spin" /> 探测中...
                </div>
              )}
              {!probeLoading && probeError && (
                <div className="text-sm text-red-400">{probeError}</div>
              )}
              {!probeLoading && !probeError && probeModels.length === 0 && (
                <div className="text-sm text-gray-500">暂无模型数据</div>
              )}
              {!probeLoading && probeModels.length > 0 && (
                <ul className="space-y-3">
                  {probeModels.map((model) => (
                    <li
                      key={model.id}
                      className="flex items-center justify-between gap-3 px-3 py-2 bg-gray-900/60 border border-gray-800 rounded"
                    >
                      <div className="flex-1">
                        <div className="text-[11px] text-gray-500 mb-1">别名</div>
                        <input
                          value={probeAliasEdits[model.id] ?? model.model_alias}
                          onChange={(event) =>
                            setProbeAliasEdits((prev) => ({
                              ...prev,
                              [model.id]: event.target.value,
                            }))
                          }
                          disabled={!isAdmin}
                          className="w-full bg-gray-950 border border-gray-800 rounded px-2 py-1 text-sm text-gray-200 font-mono focus:border-blue-500 focus:outline-none disabled:opacity-50"
                        />
                      </div>
                      <div className="flex-1">
                        <div className="text-[11px] text-gray-500 mb-1">真实模型</div>
                        <div className="text-xs text-gray-400 font-mono truncate">
                          {model.real_model}
                        </div>
                      </div>
                      <button
                        onClick={() => handleUpdateModelAlias(model)}
                        disabled={!isAdmin}
                        className="px-3 py-1 text-xs text-blue-400 border border-blue-500/40 rounded hover:bg-blue-500/10 disabled:opacity-40"
                      >
                        保存
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </div>
            <div className="p-4 border-t border-gray-800 flex justify-end gap-2">
              <button
                onClick={() => handleProbeEndpoint(probeEndpoint)}
                disabled={!isAdmin || probeLoading}
                className="px-4 py-2 text-sm text-blue-400 border border-blue-500/40 rounded hover:bg-blue-500/10 disabled:opacity-40"
              >
                重新探测
              </button>
              <button
                onClick={() => {
                  setProbeEndpoint(null);
                  setProbeModels([]);
                  setProbeAliasEdits({});
                  setProbeError(null);
                  setProbeLoading(false);
                }}
                className="px-4 py-2 text-sm text-gray-400 hover:text-white"
              >
                关闭
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
