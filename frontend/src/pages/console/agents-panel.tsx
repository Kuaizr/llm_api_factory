import { Globe, PauseCircle, PlayCircle, Plus, Power, Trash2, XCircle, Zap } from "lucide-react";
import { useEffect, useState } from "react";

import { StatusBadge } from "./common-widgets";
import {
  type AgentBootstrapResult,
  type AgentDeployFormState,
  type AgentNode,
  formatTimestamp,
} from "./shared";

const formatRelativeTime = (value: string | null) => {
  if (!value) return "--";
  const timestamp = new Date(value).getTime();
  if (!Number.isFinite(timestamp)) return "--";
  const diffSeconds = Math.max(0, Math.round((Date.now() - timestamp) / 1000));
  if (diffSeconds < 60) return `${diffSeconds} 秒前`;
  const diffMinutes = Math.round(diffSeconds / 60);
  if (diffMinutes < 60) return `${diffMinutes} 分钟前`;
  const diffHours = Math.round(diffMinutes / 60);
  if (diffHours < 24) return `${diffHours} 小时前`;
  return `${Math.round(diffHours / 24)} 天前`;
};

export const AgentsView = ({
  agents,
  onCreate,
  onDeploy,
  onDelete,
  onRotateToken,
  onSetState = () => undefined,
  isAdmin,
}: {
  agents: AgentNode[];
  onCreate: () => void;
  onDeploy: (agent: AgentNode) => void;
  onDelete: (agent: AgentNode) => void;
  onRotateToken: (agent: AgentNode) => void;
  onSetState?: (agent: AgentNode, action: "enable" | "drain" | "disable") => void;
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
          const isDisabled = agent.is_active === false;
          const isOnline = agent.status === "online";
          const isDraining = agent.status === "draining";
          const routeStatus = isDraining ? "draining" : isOnline ? "online" : "offline";
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
                          : isDraining
                            ? "bg-amber-900/20 text-amber-300"
                          : "bg-red-900/20 text-red-400"
                      }`}
                    >
                      <Zap size={18} />
                    </div>
                    <div>
                      <h3 className="text-sm font-bold text-white">{agent.name}</h3>
                      <p className="text-xs text-gray-500">
                        {agent.region || agent.network_group || "未设置区域"}
                      </p>
                    </div>
                  </div>
                  <div className="flex flex-col items-end gap-1">
                    <StatusBadge status={routeStatus} />
                    {isDisabled ? (
                      <span className="rounded border border-gray-700 bg-gray-800 px-2 py-0.5 text-[10px] text-gray-500">
                        Disabled
                      </span>
                    ) : null}
                  </div>
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
                    <span
                      className="font-mono text-gray-300"
                      title={formatTimestamp(agent.last_seen_at)}
                    >
                      {formatRelativeTime(agent.last_seen_at)}
                    </span>
                  </div>
                  {agent.endpoint_url ? (
                    <div className="flex justify-between gap-3 text-xs border-b border-gray-800 pb-2">
                      <span className="text-gray-500">出口地址</span>
                      <span className="font-mono text-gray-500 truncate">
                        {agent.endpoint_url}
                      </span>
                    </div>
                  ) : null}
                  {agent.network_group ? (
                    <div className="flex justify-between text-xs border-b border-gray-800 pb-2">
                      <span className="text-gray-500">网络分组</span>
                      <span className="font-mono text-gray-300">
                        {agent.network_group}
                      </span>
                    </div>
                  ) : null}
                  {agent.labels && agent.labels.length > 0 ? (
                    <div className="flex flex-wrap gap-1.5">
                      {agent.labels.map((label) => (
                        <span
                          key={label}
                          className="px-2 py-0.5 rounded border border-gray-700 bg-gray-900/40 text-[10px] text-gray-300"
                        >
                          {label}
                        </span>
                      ))}
                    </div>
                  ) : null}
                  <div className="flex flex-wrap gap-2 pt-2">
                    {renderCapability("GPT", agent.supports_gpt ?? null)}
                    {renderCapability("Gemini", agent.supports_gemini ?? null)}
                    {renderCapability("Claude", agent.supports_claude ?? null)}
                  </div>
                </div>

                <div className="flex gap-2">
                  <button
                    onClick={() => onSetState(agent, isDisabled || isDraining ? "enable" : "drain")}
                    disabled={!isAdmin}
                    className="flex-1 py-2 text-xs rounded border border-amber-700/40 bg-amber-600/15 text-amber-200 transition hover:bg-amber-600/25 disabled:opacity-50"
                    title={isDisabled || isDraining ? "启用节点" : "进入 drain，不再分配新请求"}
                  >
                    {isDisabled || isDraining ? (
                      <span className="inline-flex items-center gap-1">
                        <PlayCircle size={13} /> 启用
                      </span>
                    ) : (
                      <span className="inline-flex items-center gap-1">
                        <PauseCircle size={13} /> Drain
                      </span>
                    )}
                  </button>
                  {!isDisabled ? (
                    <button
                      onClick={() => onSetState(agent, "disable")}
                      disabled={!isAdmin}
                      className="py-2 px-2 rounded border border-gray-700 bg-gray-800/50 text-gray-300 transition hover:bg-gray-800 disabled:opacity-50"
                      title="禁用节点，不参与路由"
                    >
                      <Power size={14} />
                    </button>
                  ) : null}
                  <button
                    onClick={() => onRotateToken(agent)}
                    disabled={!isAdmin || agent.status !== "offline"}
                    className={`flex-1 py-2 text-xs rounded border transition ${
                      agent.status !== "offline"
                        ? "bg-gray-700/20 text-gray-500 border-gray-700/40 cursor-not-allowed"
                        : "bg-blue-600/20 hover:bg-blue-600/30 text-blue-200 border-blue-700/40 disabled:opacity-50"
                    }`}
                    title={
                      !isAdmin
                        ? "需要管理员权限"
                        : agent.status !== "offline"
                          ? "已部署的Agent无法重新生成Token"
                          : "重新生成 Token"
                    }
                  >
                    {agent.status !== "offline" ? "已部署" : "Token"}
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

export const AgentDeployModal = ({
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
              <p className="text-xs text-gray-500">Token 仅展示一次，请立即保存。</p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};
