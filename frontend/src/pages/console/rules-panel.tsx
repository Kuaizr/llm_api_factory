import {
  AlertTriangle,
  Check,
  Copy,
  Database,
  Edit2,
  Key,
  Network,
  Plus,
  RefreshCw,
  RotateCw,
  Trash2,
  XCircle,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import {
  apiBase,
  buildHeaders,
  formatTimestamp,
  formatTokens,
  type Endpoint,
  type ModelMap,
  type RoutingRule,
  type RoutingRuleSavePayload,
  type RuleAccessKeyIssue,
  type RuleAccessKeyItem,
} from "./shared";

export const RuleEditorModal = ({
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
  onSave: (payload: RoutingRuleSavePayload) => void;
}) => {
  const [modelPattern, setModelPattern] = useState(rule?.model_pattern ?? "");
  const [groupName, setGroupName] = useState(rule?.group_name ?? "custom");
  const [priority, setPriority] = useState(String(rule?.priority ?? 10));
  const [strategy, setStrategy] = useState(
    rule?.strategy ?? "weighted_round_robin"
  );
  const [dumpEnabled, setDumpEnabled] = useState(Boolean(rule?.dump_enabled));
  const [dumpPath, setDumpPath] = useState(rule?.dump_path ?? "");
  const [selectedKeyIds, setSelectedKeyIds] = useState<Set<number>>(
    new Set(rule?.target_key_ids ?? [])
  );
  const [scanResults, setScanResults] = useState<string[]>([]);
  const [scanError, setScanError] = useState<string | null>(null);
  const [scanEndpointIds, setScanEndpointIds] = useState<Set<number> | null>(null);
  const [hasScanned, setHasScanned] = useState(false);
  const [isScanning, setIsScanning] = useState(false);
  const isDefaultRule = (rule?.group_name ?? "").toLowerCase() === "default";

  const filteredEndpoints = useMemo(
    () =>
      hasScanned && scanEndpointIds
        ? endpoints.filter((endpoint) => scanEndpointIds.has(endpoint.id))
        : endpoints,
    [endpoints, hasScanned, scanEndpointIds]
  );

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
      <div className="bg-[#0f1117] border border-gray-800 rounded-xl w-[760px] h-[85vh] flex flex-col shadow-2xl animate-in fade-in zoom-in-95 duration-200">
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
                  disabled={!isAdmin || isDefaultRule}
                />
                {isDefaultRule && (
                  <p className="mt-1 text-[11px] text-gray-500">系统默认分组不可重命名。</p>
                )}
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
            <div className="bg-gray-900/30 border border-gray-800 rounded-lg p-4 space-y-3">
              <div className="flex items-center justify-between">
                <label className="text-xs font-bold text-gray-500 uppercase">
                  对话 Dump
                </label>
                <label className="inline-flex items-center gap-2 text-xs text-gray-300">
                  <input
                    type="checkbox"
                    checked={dumpEnabled}
                    onChange={(event) => setDumpEnabled(event.target.checked)}
                    disabled={!isAdmin}
                  />
                  启用
                </label>
              </div>
              <input
                value={dumpPath}
                onChange={(event) => setDumpPath(event.target.value)}
                placeholder="例如: /tmp/llm-dumps"
                className="w-full bg-gray-950 border border-gray-800 rounded p-2.5 text-sm text-white font-mono focus:border-yellow-500 focus:outline-none disabled:opacity-40"
                disabled={!isAdmin || !dumpEnabled}
              />
              <p className="text-[11px] text-gray-500">
                开启后将按命中规则把请求/响应写入本地路径。未填写路径时不会写盘。
              </p>
            </div>
          </div>

          <div className="space-y-3">
            <h4 className="text-xs font-bold text-gray-500 uppercase tracking-wider mb-2">
              选择 Key
            </h4>
            <div className="space-y-2">
              {filteredEndpoints.map((endpoint) => (
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
              {filteredEndpoints.length === 0 && (
                <div className="text-xs text-gray-500 px-1">暂无匹配端点</div>
              )}
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
                id: rule?.id,
                model_pattern: modelPattern,
                group_name: groupName,
                priority: Number(priority) || 0,
                strategy,
                is_active: rule?.is_active ?? true,
                target_key_ids: Array.from(selectedKeyIds),
                dump_enabled: dumpEnabled,
                dump_path: dumpEnabled && dumpPath.trim() ? dumpPath.trim() : null,
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

export const RulesView = ({
  rules,
  isAdmin,
  onEdit,
  onDelete,
  onManageAccessKeys,
}: {
  rules: RoutingRule[];
  isAdmin: boolean;
  onEdit: (rule?: RoutingRule) => void;
  onDelete: (rule: RoutingRule) => void;
  onManageAccessKeys: (rule: RoutingRule) => void;
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
      {rules.map((rule) => {
        const isDefaultGroup = rule.group_name.toLowerCase() === "default";
        return (
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
                {isDefaultGroup && (
                  <span className="text-xs px-2 py-0.5 rounded bg-blue-900/20 text-blue-300 border border-blue-700/50">
                    System Group
                  </span>
                )}
                <span
                  className={`text-xs px-2 py-0.5 rounded border ${
                    rule.dump_enabled
                      ? "text-purple-300 bg-purple-900/20 border-purple-700/40"
                      : "text-gray-500 bg-gray-900 border-gray-700"
                  }`}
                >
                  {rule.dump_enabled ? "Dump On" : "Dump Off"}
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
                <div className="flex items-center gap-2 col-span-2">
                  <span className="text-gray-500">访问 Key</span>
                  <span className="font-mono text-gray-200">
                    {rule.access_keys?.length ?? 0}
                  </span>
                  {rule.dump_enabled && (
                    <span className="text-[10px] text-purple-300 font-mono truncate max-w-[360px]">
                      {rule.dump_path || "未设置路径"}
                    </span>
                  )}
                </div>
              </div>
            </div>
          </div>
          <div className="flex items-center gap-2">
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
              onClick={() => onManageAccessKeys(rule)}
              disabled={!isAdmin}
              className="p-2 hover:bg-gray-800 rounded-lg text-blue-300 hover:text-white transition disabled:cursor-not-allowed disabled:opacity-50"
              title="管理访问 Key"
            >
              <Key size={16} />
            </button>
            <button
              onClick={() => onDelete(rule)}
              disabled={!isAdmin || isDefaultGroup}
              className="p-2 hover:bg-gray-800 rounded-lg text-gray-400 hover:text-white transition disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:text-gray-400"
              aria-label="删除规则"
              title={isDefaultGroup ? "系统默认分组不可删除" : "删除规则"}
            >
              <Trash2 size={16} />
            </button>
            <button
              onClick={() => onEdit(rule)}
              disabled={!isAdmin}
              aria-label="编辑规则"
              className="p-2 hover:bg-gray-800 rounded-lg text-gray-400 hover:text-white transition disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:text-gray-400"
            >
              <Edit2 size={16} />
            </button>
          </div>
        </div>
      );
      })}
      {!rules.length && <div className="text-sm text-gray-500">暂无规则</div>}
    </div>
  </div>
);

export const RuleAccessKeysModal = ({
  rule,
  isAdmin,
  authToken,
  onClose,
  onRulesRefresh,
}: {
  rule: RoutingRule;
  isAdmin: boolean;
  authToken: string | null;
  onClose: () => void;
  onRulesRefresh: () => Promise<void>;
}) => {
  const [keys, setKeys] = useState<RuleAccessKeyItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [newName, setNewName] = useState("");
  const [issuedKey, setIssuedKey] = useState<string | null>(null);

  const loadKeys = async () => {
    setLoading(true);
    setError(null);
    try {
      const response = await fetch(
        `${apiBase}/admin/rules/${rule.id}/access-keys`,
        {
          headers: buildHeaders(authToken),
        }
      );
      if (!response.ok) {
        setError("无法加载访问 Key 列表");
        setKeys([]);
        return;
      }
      const data = (await response.json()) as RuleAccessKeyItem[];
      setKeys(data);
    } catch {
      setError("无法加载访问 Key 列表");
      setKeys([]);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void loadKeys();
  }, [rule.id, authToken]);

  const handleCopy = (value: string | null) => {
    if (!value) return;
    if (typeof navigator !== "undefined" && navigator.clipboard?.writeText) {
      navigator.clipboard.writeText(value).catch(() => undefined);
    }
  };

  const refreshRuleData = async () => {
    await onRulesRefresh();
    await loadKeys();
  };

  const issueKey = async () => {
    if (!isAdmin) return;
    setError(null);
    const response = await fetch(`${apiBase}/admin/rules/${rule.id}/access-keys`, {
      method: "POST",
      headers: buildHeaders(authToken, true),
      body: JSON.stringify({ name: newName.trim() || null }),
    });
    if (!response.ok) {
      setError("创建访问 Key 失败");
      return;
    }
    const data = (await response.json()) as RuleAccessKeyIssue;
    setIssuedKey(data.key);
    setNewName("");
    await refreshRuleData();
  };

  const updateKey = async (
    accessKeyId: number,
    payload: { name?: string | null; is_active?: boolean }
  ) => {
    if (!isAdmin) return;
    const response = await fetch(
      `${apiBase}/admin/rules/access-keys/${accessKeyId}`,
      {
        method: "PATCH",
        headers: buildHeaders(authToken, true),
        body: JSON.stringify(payload),
      }
    );
    if (!response.ok) {
      setError("更新访问 Key 失败");
      return;
    }
    await refreshRuleData();
  };

  const rotateKey = async (accessKeyId: number) => {
    if (!isAdmin) return;
    const response = await fetch(
      `${apiBase}/admin/rules/access-keys/${accessKeyId}/rotate`,
      {
        method: "POST",
        headers: buildHeaders(authToken),
      }
    );
    if (!response.ok) {
      setError("轮换访问 Key 失败");
      return;
    }
    const data = (await response.json()) as RuleAccessKeyIssue;
    setIssuedKey(data.key);
    await refreshRuleData();
  };

  const removeKey = async (accessKeyId: number) => {
    if (!isAdmin) return;
    if (!window.confirm("确认删除该访问 Key 吗？")) {
      return;
    }
    const response = await fetch(
      `${apiBase}/admin/rules/access-keys/${accessKeyId}`,
      {
        method: "DELETE",
        headers: buildHeaders(authToken),
      }
    );
    if (!response.ok) {
      setError("删除访问 Key 失败");
      return;
    }
    await refreshRuleData();
  };

  return (
    <div className="fixed inset-0 z-[170] flex items-center justify-center bg-black/70 backdrop-blur-sm">
      <div className="bg-[#0f1117] border border-gray-800 rounded-xl w-[860px] max-h-[85vh] flex flex-col shadow-2xl animate-in fade-in zoom-in-95 duration-200">
        <div className="p-6 border-b border-gray-800 flex justify-between items-center">
          <div>
            <h3 className="text-lg font-bold text-white flex items-center gap-2">
              <Key size={18} className="text-blue-400" />
              路由访问 Key 管理
            </h3>
            <p className="text-xs text-gray-500 mt-1">
              Rule #{rule.id} · {rule.model_pattern} · Group {rule.group_name}
            </p>
          </div>
          <button onClick={onClose} className="text-gray-500 hover:text-white">
            <XCircle size={20} />
          </button>
        </div>

        <div className="p-6 flex-1 overflow-y-auto space-y-4">
          <div className="flex items-center gap-2">
            <input
              value={newName}
              onChange={(event) => setNewName(event.target.value)}
              placeholder="可选备注名"
              disabled={!isAdmin}
              className="flex-1 bg-gray-900 border border-gray-700 rounded p-2.5 text-sm text-white focus:border-blue-500 focus:outline-none disabled:opacity-50"
            />
            <button
              onClick={() => void issueKey()}
              disabled={!isAdmin}
              className="px-3 py-2 bg-blue-600 hover:bg-blue-500 text-white text-sm rounded disabled:opacity-50"
            >
              生成访问 Key
            </button>
            <button
              onClick={() => void loadKeys()}
              className="p-2 bg-gray-800 border border-gray-700 rounded text-gray-300 hover:text-white"
              title="刷新"
            >
              <RefreshCw size={14} />
            </button>
          </div>

          {issuedKey && (
            <div className="p-3 rounded border border-green-700/40 bg-green-900/20">
              <div className="flex items-center gap-2 text-green-300 text-sm mb-2">
                <AlertTriangle size={14} />
                原始 Key 仅展示一次，请立即保存。
              </div>
              <div className="flex items-center gap-2">
                <input
                  value={issuedKey}
                  readOnly
                  className="flex-1 bg-gray-950 border border-gray-800 rounded px-2 py-1 text-xs text-green-200 font-mono"
                />
                <button
                  onClick={() => handleCopy(issuedKey)}
                  className="px-2 py-1 text-xs text-green-300 border border-green-700/40 rounded hover:bg-green-800/30"
                >
                  <Copy size={12} />
                </button>
              </div>
            </div>
          )}

          {error && <div className="text-sm text-red-400">{error}</div>}

          <table className="w-full text-left text-sm text-gray-400">
            <thead className="bg-gray-900/50 text-gray-200 uppercase font-medium">
              <tr>
                <th className="px-4 py-3 rounded-l-lg">名称</th>
                <th className="px-4 py-3">Key 预览</th>
                <th className="px-4 py-3">状态</th>
                <th className="px-4 py-3">创建时间</th>
                <th className="px-4 py-3 rounded-r-lg text-right">操作</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-800/50">
              {keys.map((item) => (
                <tr key={item.id} className="hover:bg-gray-900/30 transition-colors">
                  <td className="px-4 py-3">{item.name || "-"}</td>
                  <td className="px-4 py-3 font-mono text-xs text-gray-300">
                    {item.key_preview}
                  </td>
                  <td className="px-4 py-3">
                    <span
                      className={`text-xs px-2 py-0.5 rounded border ${
                        item.is_active
                          ? "text-green-400 bg-green-900/20 border-green-900/30"
                          : "text-gray-500 bg-gray-800 border-gray-700"
                      }`}
                    >
                      {item.is_active ? "Active" : "Inactive"}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-xs text-gray-500">
                    {formatTimestamp(item.created_at)}
                  </td>
                  <td className="px-4 py-3 text-right">
                    <div className="inline-flex items-center gap-2">
                      <button
                        onClick={() => handleCopy(item.key ?? null)}
                        disabled={!item.key}
                        className="p-1.5 hover:bg-gray-800 rounded text-green-300 transition disabled:opacity-40"
                        title="复制完整 Key"
                      >
                        <Copy size={14} />
                      </button>
                      <button
                        onClick={() => {
                          const nextName = window.prompt(
                            "输入新的备注名（留空表示清空）",
                            item.name ?? ""
                          );
                          if (nextName === null) return;
                          void updateKey(item.id, {
                            name: nextName.trim() ? nextName.trim() : null,
                          });
                        }}
                        disabled={!isAdmin}
                        className="p-1.5 hover:bg-gray-800 rounded text-blue-300 transition disabled:opacity-50"
                        title="重命名"
                      >
                        <Edit2 size={14} />
                      </button>
                      <button
                        onClick={() =>
                          void updateKey(item.id, { is_active: !item.is_active })
                        }
                        disabled={!isAdmin}
                        className="px-2 py-1 text-xs border border-gray-700 rounded hover:bg-gray-800 disabled:opacity-50"
                        title={item.is_active ? "禁用" : "启用"}
                      >
                        {item.is_active ? "禁用" : "启用"}
                      </button>
                      <button
                        onClick={() => void rotateKey(item.id)}
                        disabled={!isAdmin}
                        className="p-1.5 hover:bg-gray-800 rounded text-yellow-300 transition disabled:opacity-50"
                        title="轮换 Key"
                      >
                        <RotateCw size={14} />
                      </button>
                      <button
                        onClick={() => void removeKey(item.id)}
                        disabled={!isAdmin}
                        className="p-1.5 hover:bg-gray-800 rounded text-red-400 transition disabled:opacity-50"
                        title="删除"
                      >
                        <Trash2 size={14} />
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>

          {!loading && keys.length === 0 && (
            <div className="text-sm text-gray-500">暂无访问 Key</div>
          )}
          {loading && <div className="text-sm text-gray-500">加载中...</div>}
        </div>
      </div>
    </div>
  );
};
