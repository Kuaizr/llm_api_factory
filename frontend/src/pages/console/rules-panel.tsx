import {
  AlertTriangle,
  Check,
  Database,
  Edit2,
  GripVertical,
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
  type RoutingRule,
  type RoutingRuleSavePayload,
} from "./shared";
import { parseModelMapList, parseStringList } from "./response-validators";

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
  onSave: (payload: RoutingRuleSavePayload) => void | Promise<boolean>;
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
  const [endpointOrder, setEndpointOrder] = useState<number[]>(() => {
    const targetOrder = new Map(
      (rule?.target_key_ids ?? []).map((keyId, index) => [keyId, index])
    );
    return [...endpoints]
      .map((endpoint, index) => {
        const rank = endpoint.keys.reduce((current, key) => {
          const keyRank = targetOrder.get(key.id);
          if (keyRank == null) {
            return current;
          }
          return Math.min(current, keyRank);
        }, Number.POSITIVE_INFINITY);
        return { endpointId: endpoint.id, index, rank };
      })
      .sort((left, right) => {
        if (left.rank !== right.rank) {
          return left.rank - right.rank;
        }
        return left.index - right.index;
      })
      .map((item) => item.endpointId);
  });
  const [keyOrderByEndpoint, setKeyOrderByEndpoint] = useState<Record<number, number[]>>(
    () => {
      const targetOrder = new Map(
        (rule?.target_key_ids ?? []).map((keyId, index) => [keyId, index])
      );
      const initialOrder: Record<number, number[]> = {};
      endpoints.forEach((endpoint) => {
        const originalIndexes = new Map(
          endpoint.keys.map((key, index) => [key.id, index])
        );
        initialOrder[endpoint.id] = [...endpoint.keys]
          .sort((left, right) => {
            const leftRank = targetOrder.get(left.id);
            const rightRank = targetOrder.get(right.id);
            if (leftRank != null && rightRank != null) {
              return leftRank - rightRank;
            }
            if (leftRank != null) {
              return -1;
            }
            if (rightRank != null) {
              return 1;
            }
            return (originalIndexes.get(left.id) ?? 0) - (originalIndexes.get(right.id) ?? 0);
          })
          .map((key) => key.id);
      });
      return initialOrder;
    }
  );
  const [scanResults, setScanResults] = useState<string[]>([]);
  const [scanError, setScanError] = useState<string | null>(null);
  const [scanEndpointIds, setScanEndpointIds] = useState<Set<number> | null>(null);
  const [hasScanned, setHasScanned] = useState(false);
  const [isScanning, setIsScanning] = useState(false);
  const [draggingEndpointId, setDraggingEndpointId] = useState<number | null>(null);
  const [draggingKey, setDraggingKey] = useState<{
    endpointId: number;
    keyId: number;
  } | null>(null);
  const isDefaultRule = (rule?.group_name ?? "").toLowerCase() === "default";

  useEffect(() => {
    setEndpointOrder((prev) => {
      const endpointIds = endpoints.map((endpoint) => endpoint.id);
      const merged = prev.filter((endpointId) => endpointIds.includes(endpointId));
      endpointIds.forEach((endpointId) => {
        if (!merged.includes(endpointId)) {
          merged.push(endpointId);
        }
      });
      return merged;
    });

    setKeyOrderByEndpoint((prev) => {
      const next: Record<number, number[]> = {};
      endpoints.forEach((endpoint) => {
        const endpointKeyIds = endpoint.keys.map((key) => key.id);
        const previousOrder = prev[endpoint.id] ?? [];
        const merged = previousOrder.filter((keyId) => endpointKeyIds.includes(keyId));
        endpointKeyIds.forEach((keyId) => {
          if (!merged.includes(keyId)) {
            merged.push(keyId);
          }
        });
        next[endpoint.id] = merged;
      });
      return next;
    });
  }, [endpoints]);

  const orderedEndpoints = useMemo(() => {
    const endpointMap = new Map(endpoints.map((endpoint) => [endpoint.id, endpoint]));
    return endpointOrder
      .map((endpointId) => endpointMap.get(endpointId))
      .filter((endpoint): endpoint is Endpoint => Boolean(endpoint))
      .map((endpoint) => {
        const keyMap = new Map(endpoint.keys.map((key) => [key.id, key]));
        const orderedKeyIds = keyOrderByEndpoint[endpoint.id] ?? endpoint.keys.map((key) => key.id);
        const mergedKeyIds = orderedKeyIds.filter((keyId) => keyMap.has(keyId));
        endpoint.keys.forEach((key) => {
          if (!mergedKeyIds.includes(key.id)) {
            mergedKeyIds.push(key.id);
          }
        });
        const orderedKeys = mergedKeyIds
          .map((keyId) => keyMap.get(keyId))
          .filter((key): key is (typeof endpoint.keys)[number] => Boolean(key));
        return { ...endpoint, keys: orderedKeys };
      });
  }, [endpoints, endpointOrder, keyOrderByEndpoint]);

  const filteredEndpoints = useMemo(
    () =>
      hasScanned && scanEndpointIds
        ? orderedEndpoints.filter((endpoint) => scanEndpointIds.has(endpoint.id))
        : orderedEndpoints,
    [hasScanned, orderedEndpoints, scanEndpointIds]
  );

  const orderedSelectedKeyIds = useMemo(() => {
    const ordered: number[] = [];
    const seen = new Set<number>();
    orderedEndpoints.forEach((endpoint) => {
      endpoint.keys.forEach((key) => {
        if (selectedKeyIds.has(key.id) && !seen.has(key.id)) {
          seen.add(key.id);
          ordered.push(key.id);
        }
      });
    });
    selectedKeyIds.forEach((keyId) => {
      if (!seen.has(keyId)) {
        ordered.push(keyId);
      }
    });
    return ordered;
  }, [orderedEndpoints, selectedKeyIds]);

  const toggleKey = (id: number) => {
    setSelectedKeyIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  };

  const reorderInList = (ids: number[], movingId: number, targetId: number) => {
    if (movingId === targetId) {
      return ids;
    }
    const next = [...ids];
    const fromIndex = next.indexOf(movingId);
    const targetIndex = next.indexOf(targetId);
    if (fromIndex < 0 || targetIndex < 0) {
      return ids;
    }
    next.splice(fromIndex, 1);
    next.splice(targetIndex, 0, movingId);
    return next;
  };

  const moveEndpoint = (sourceEndpointId: number, targetEndpointId: number) => {
    setEndpointOrder((prev) => reorderInList(prev, sourceEndpointId, targetEndpointId));
  };

  const moveKeyInEndpoint = (endpointId: number, sourceKeyId: number, targetKeyId: number) => {
    setKeyOrderByEndpoint((prev) => {
      const currentOrder = prev[endpointId] ?? [];
      const nextOrder = reorderInList(currentOrder, sourceKeyId, targetKeyId);
      if (nextOrder === currentOrder) {
        return prev;
      }
      return {
        ...prev,
        [endpointId]: nextOrder,
      };
    });
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
      const data = parseStringList(await response.json());
      if (data === null) {
        setScanError("扫描结果格式异常。");
        setScanResults([]);
        setHasScanned(true);
        return;
      }
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
        const maps = parseModelMapList(await mapsResponse.json());
        if (maps === null) {
          setScanError("模型映射响应格式异常。");
          setScanEndpointIds(null);
          setHasScanned(true);
          return;
        }
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
            <div className="flex items-center justify-between gap-3">
              <h4 className="text-xs font-bold text-gray-500 uppercase tracking-wider mb-2">
                选择 Key
              </h4>
              <span className="text-[11px] text-gray-500">
                可拖拽端点卡片与 Key 卡片排序；顺序主备按从上到下执行。
              </span>
            </div>
            <div className="space-y-2">
              {filteredEndpoints.map((endpoint) => (
                <div
                  key={endpoint.id}
                  draggable={isAdmin}
                  onDragStart={(event) => {
                    if (!isAdmin) {
                      return;
                    }
                    setDraggingKey(null);
                    setDraggingEndpointId(endpoint.id);
                    event.dataTransfer.effectAllowed = "move";
                  }}
                  onDragOver={(event) => {
                    if (
                      !isAdmin ||
                      draggingEndpointId === null ||
                      draggingEndpointId === endpoint.id
                    ) {
                      return;
                    }
                    event.preventDefault();
                    event.dataTransfer.dropEffect = "move";
                  }}
                  onDrop={(event) => {
                    if (
                      !isAdmin ||
                      draggingEndpointId === null ||
                      draggingEndpointId === endpoint.id
                    ) {
                      return;
                    }
                    event.preventDefault();
                    moveEndpoint(draggingEndpointId, endpoint.id);
                    setDraggingEndpointId(null);
                  }}
                  onDragEnd={() => {
                    setDraggingEndpointId(null);
                  }}
                  className={`bg-gray-900/40 border rounded-lg p-3 transition ${
                    draggingEndpointId === endpoint.id
                      ? "border-yellow-500/60 opacity-80"
                      : "border-gray-800"
                  }`}
                >
                  <div className="flex items-center justify-between mb-2">
                    <div className="text-sm text-gray-300 font-medium flex items-center gap-2">
                      <GripVertical size={14} className="text-gray-500" />
                      {endpoint.name}
                    </div>
                    <span className="text-xs text-gray-500">
                      {endpoint.keys.length} keys
                    </span>
                  </div>
                  <div className="space-y-1">
                    {endpoint.keys.map((key) => {
                      const isSelected = selectedKeyIds.has(key.id);
                      const isDraggingKey =
                        draggingKey?.endpointId === endpoint.id &&
                        draggingKey.keyId === key.id;
                      return (
                        <button
                          key={key.id}
                          type="button"
                          draggable={isAdmin}
                          onDragStart={(event) => {
                            if (!isAdmin) {
                              return;
                            }
                            event.stopPropagation();
                            setDraggingEndpointId(null);
                            setDraggingKey({ endpointId: endpoint.id, keyId: key.id });
                            event.dataTransfer.effectAllowed = "move";
                          }}
                          onDragOver={(event) => {
                            if (
                              !isAdmin ||
                              !draggingKey ||
                              draggingKey.endpointId !== endpoint.id ||
                              draggingKey.keyId === key.id
                            ) {
                              return;
                            }
                            event.preventDefault();
                            event.stopPropagation();
                            event.dataTransfer.dropEffect = "move";
                          }}
                          onDrop={(event) => {
                            if (
                              !isAdmin ||
                              !draggingKey ||
                              draggingKey.endpointId !== endpoint.id ||
                              draggingKey.keyId === key.id
                            ) {
                              return;
                            }
                            event.preventDefault();
                            event.stopPropagation();
                            moveKeyInEndpoint(endpoint.id, draggingKey.keyId, key.id);
                            setDraggingKey(null);
                          }}
                          onDragEnd={() => {
                            setDraggingKey(null);
                          }}
                          onClick={() => (isAdmin ? toggleKey(key.id) : undefined)}
                          className={`w-full flex items-center gap-3 p-2 rounded border transition ${
                            isSelected
                              ? "bg-yellow-900/20 border-yellow-600/50"
                              : "border-gray-800"
                          } ${isDraggingKey ? "opacity-70 border-yellow-500/70" : ""}`}
                        >
                          <GripVertical size={12} className="text-gray-600" />
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
                target_key_ids: orderedSelectedKeyIds,
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

type RuleAccessKey = {
  id: number;
  rule_id: number;
  name: string | null;
  key_preview: string;
  key?: string | null;
  is_active: boolean;
  created_at: string;
};

const parseRuleAccessKey = (value: unknown): RuleAccessKey | null => {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    return null;
  }
  const item = value as Record<string, unknown>;
  const id = item.id;
  const ruleId = item.rule_id;
  const name = item.name;
  const keyPreview = item.key_preview;
  const key = item.key;
  const isActive = item.is_active;
  const createdAt = item.created_at;
  if (
    typeof id !== "number" ||
    typeof ruleId !== "number" ||
    !(typeof name === "string" || name === null) ||
    typeof keyPreview !== "string" ||
    !(key === undefined || typeof key === "string" || key === null) ||
    typeof isActive !== "boolean" ||
    typeof createdAt !== "string"
  ) {
    return null;
  }
  return {
    id,
    rule_id: ruleId,
    name: name as string | null,
    key_preview: keyPreview as string,
    key: key as string | null | undefined,
    is_active: isActive,
    created_at: createdAt,
  };
};

const parseRuleAccessKeyList = (value: unknown): RuleAccessKey[] | null => {
  if (!Array.isArray(value)) {
    return null;
  }
  const items: RuleAccessKey[] = [];
  for (const item of value) {
    const parsed = parseRuleAccessKey(item);
    if (!parsed) {
      return null;
    }
    items.push(parsed);
  }
  return items;
};

const RuleAccessKeysModal = ({
  rule,
  isAdmin,
  authToken,
  onClose,
}: {
  rule: RoutingRule;
  isAdmin: boolean;
  authToken: string | null;
  onClose: () => void;
}) => {
  const [items, setItems] = useState<RuleAccessKey[]>([]);
  const [loading, setLoading] = useState(false);
  const [newName, setNewName] = useState("");
  const [error, setError] = useState<string | null>(null);

  const loadKeys = async () => {
    if (!authToken) return;
    setLoading(true);
    setError(null);
    try {
      const response = await fetch(`${apiBase}/admin/rules/${rule.id}/access-keys`, {
        headers: buildHeaders(authToken),
      });
      if (!response.ok) {
        setError("访问 Key 获取失败。");
        return;
      }
      const data = parseRuleAccessKeyList(await response.json());
      if (!data) {
        setError("访问 Key 响应格式异常。");
        setItems([]);
        return;
      }
      setItems(data);
    } catch {
      setError("访问 Key 获取失败。");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void loadKeys();
  }, [rule.id, authToken]);

  const createKey = async () => {
    if (!authToken || !isAdmin) return;
    setLoading(true);
    setError(null);
    try {
      const response = await fetch(`${apiBase}/admin/rules/${rule.id}/access-keys`, {
        method: "POST",
        headers: buildHeaders(authToken, true),
        body: JSON.stringify({ name: newName.trim() || null }),
      });
      if (!response.ok) {
        setError("访问 Key 创建失败。");
        return;
      }
      const item = parseRuleAccessKey(await response.json());
      if (!item) {
        setError("访问 Key 响应格式异常。");
        return;
      }
      setItems((prev) => [item, ...prev]);
      setNewName("");
    } catch {
      setError("访问 Key 创建失败。");
    } finally {
      setLoading(false);
    }
  };

  const copyKey = (value: string | null | undefined) => {
    if (!value) return;
    if (typeof navigator !== "undefined" && navigator.clipboard?.writeText) {
      navigator.clipboard.writeText(value).catch(() => undefined);
    }
  };

  return (
    <div className="fixed inset-0 z-[120] flex items-center justify-center bg-black/70 backdrop-blur-sm">
      <div className="bg-[#0f1117] border border-gray-800 rounded-xl w-[520px] shadow-2xl">
        <div className="p-5 border-b border-gray-800 flex items-center justify-between">
          <div>
            <h3 className="text-lg font-bold text-white flex items-center gap-2">
              <Key size={18} className="text-yellow-400" />
              管理访问 Key
            </h3>
            <p className="text-xs text-gray-500 mt-1">{rule.group_name}</p>
          </div>
          <button onClick={onClose} className="text-gray-500 hover:text-white">
            <XCircle size={20} />
          </button>
        </div>
        <div className="p-5 space-y-4">
          <div className="flex gap-2">
            <input
              value={newName}
              onChange={(event) => setNewName(event.target.value)}
              placeholder="Key 名称"
              className="flex-1 bg-gray-900 border border-gray-700 rounded p-2 text-sm text-white focus:border-yellow-500 focus:outline-none"
              disabled={!isAdmin || loading}
            />
            <button
              onClick={createKey}
              disabled={!isAdmin || loading}
              className="px-3 py-2 bg-yellow-600 hover:bg-yellow-500 text-white text-sm rounded disabled:opacity-50"
            >
              创建
            </button>
          </div>
          {error ? <div className="text-xs text-red-400">{error}</div> : null}
          <div className="space-y-2">
            {items.map((item) => (
              <div
                key={item.id}
                className="flex items-center justify-between gap-3 rounded border border-gray-800 bg-gray-900/40 p-3"
              >
                <div>
                  <div className="text-sm text-gray-200">{item.name || "未命名 Key"}</div>
                  <div className="text-xs font-mono text-gray-500">
                    {item.key_preview}
                  </div>
                </div>
                <button
                  onClick={() => copyKey(item.key)}
                  title="复制完整 Key"
                  className="px-2 py-1 text-xs rounded border border-gray-700 text-gray-300 hover:bg-gray-800"
                >
                  复制
                </button>
              </div>
            ))}
            {!loading && items.length === 0 ? (
              <div className="text-xs text-gray-500">暂无访问 Key</div>
            ) : null}
          </div>
        </div>
      </div>
    </div>
  );
};

export const RulesView = ({
  rules,
  isAdmin,
  authToken,
  onEdit,
  onDelete,
}: {
  rules: RoutingRule[];
  isAdmin: boolean;
  authToken: string | null;
  onEdit: (rule?: RoutingRule) => void;
  onDelete: (rule: RoutingRule) => void;
}) => {
  const [accessKeyRule, setAccessKeyRule] = useState<RoutingRule | null>(null);

  return (
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
                {rule.dump_enabled && (
                  <div className="flex items-center gap-2 col-span-2">
                    <span className="text-gray-500">Dump 路径</span>
                    <span className="text-[10px] text-purple-300 font-mono truncate max-w-[360px]">
                      {rule.dump_path || "未设置路径"}
                    </span>
                  </div>
                )}
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
              onClick={() => setAccessKeyRule(rule)}
              disabled={!isAdmin}
              className="p-2 hover:bg-gray-800 rounded-lg text-gray-400 hover:text-white transition disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:text-gray-400"
              title="管理访问 Key"
              aria-label="管理访问 Key"
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
    {accessKeyRule && (
      <RuleAccessKeysModal
        rule={accessKeyRule}
        isAdmin={isAdmin}
        authToken={authToken}
        onClose={() => setAccessKeyRule(null)}
      />
    )}
  </div>
  );
};
