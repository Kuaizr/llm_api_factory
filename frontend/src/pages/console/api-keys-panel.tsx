import { Edit2, FlaskConical, Key, Plus, RefreshCw, Trash2 } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { ApiKeyTestModal } from "./api-key-test-modal";
import { KeyConfigModal } from "./endpoint-modals";
import {
  keyHasRuleGroup,
  normalizeRuleGroups,
  resolveKeyStatus,
  type ApiKey,
  type Endpoint,
  type HealthStatus,
} from "./shared";

type KeySavePayload = Partial<ApiKey> & { key?: string };

type GlobalKeyRow = ApiKey & {
  endpoint_id: number;
  endpoint_name: string;
};

export const APIKeysView = ({
  endpoints,
  healthStatusMap,
  availableRuleGroups,
  isAdmin,
  authToken,
  onRefresh,
  onCreate,
  onUpdate,
  onDelete,
}: {
  endpoints: Endpoint[];
  healthStatusMap: Record<number, HealthStatus>;
  availableRuleGroups: string[];
  isAdmin: boolean;
  authToken: string | null;
  onRefresh: () => void;
  onCreate: (endpointId: number, payload: KeySavePayload) => void;
  onUpdate: (keyId: number, payload: KeySavePayload) => void;
  onDelete: (keyId: number) => void;
}) => {
  const keyRows = useMemo<GlobalKeyRow[]>(() => {
    return endpoints
      .flatMap((endpoint) =>
        endpoint.keys.map((key) => ({
          ...key,
          endpoint_id: endpoint.id,
          endpoint_name: endpoint.name,
        }))
      )
      .sort((left, right) => {
        const endpointDiff = left.endpoint_name.localeCompare(right.endpoint_name);
        if (endpointDiff !== 0) {
          return endpointDiff;
        }
        return left.id - right.id;
      });
  }, [endpoints]);

  const endpointOptions = useMemo(
    () => endpoints.map((endpoint) => ({ id: endpoint.id, name: endpoint.name })),
    [endpoints]
  );

  const [selectedEndpoint, setSelectedEndpoint] = useState("all");
  const [selectedGroup, setSelectedGroup] = useState("all");
  const [createEndpointId, setCreateEndpointId] = useState<string>(
    endpointOptions[0] ? String(endpointOptions[0].id) : ""
  );
  const [editingKey, setEditingKey] = useState<GlobalKeyRow | null>(null);
  const [testingKey, setTestingKey] = useState<GlobalKeyRow | null>(null);
  const [isAddingKey, setIsAddingKey] = useState(false);

  useEffect(() => {
    if (!endpointOptions.length) {
      setCreateEndpointId("");
      return;
    }
    const exists = endpointOptions.some(
      (endpoint) => String(endpoint.id) === createEndpointId
    );
    if (!exists) {
      setCreateEndpointId(String(endpointOptions[0].id));
    }
  }, [endpointOptions, createEndpointId]);

  const groupOptions = useMemo(() => {
    const groups = new Set<string>(availableRuleGroups.length ? availableRuleGroups : ["default"]);
    keyRows.forEach((key) => {
      normalizeRuleGroups(key.rule_groups, key.rule_group).forEach((group) => groups.add(group));
    });
    return Array.from(groups).sort((left, right) => {
      if (left === "default") return -1;
      if (right === "default") return 1;
      return left.localeCompare(right);
    });
  }, [availableRuleGroups, keyRows]);

  const filteredKeys = useMemo(() => {
    return keyRows.filter((key) => {
      if (selectedEndpoint !== "all" && String(key.endpoint_id) !== selectedEndpoint) {
        return false;
      }
      if (selectedGroup !== "all" && !keyHasRuleGroup(key, selectedGroup)) {
        return false;
      }
      return true;
    });
  }, [keyRows, selectedEndpoint, selectedGroup]);

  const activeModalEndpointId = editingKey
    ? editingKey.endpoint_id
    : Number(createEndpointId || "0");

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-xl font-bold text-white flex items-center gap-2">
            <Key size={18} className="text-blue-400" />
            API Key 全局管理
          </h2>
          <p className="text-sm text-gray-500 mt-1">
            API Key 已与规则组解耦；单个 Key 可关联多个规则组。
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={onRefresh}
            disabled={!isAdmin}
            className="bg-gray-800/60 text-gray-300 border border-gray-700 px-3 py-2 rounded text-sm hover:bg-gray-800 flex items-center gap-2 transition disabled:opacity-50"
          >
            <RefreshCw size={14} /> 刷新
          </button>
          <button
            onClick={() => setIsAddingKey(true)}
            disabled={!isAdmin || !endpointOptions.length}
            className="bg-blue-600/20 text-blue-400 border border-blue-500/30 px-3 py-2 rounded text-sm hover:bg-blue-600/30 flex items-center gap-2 transition disabled:opacity-50"
          >
            <Plus size={14} /> 新建 API Key
          </button>
        </div>
      </div>

      <div className="bg-[#0f1117] border border-gray-800 rounded-xl p-4">
        <div className="flex flex-wrap items-center gap-3 mb-4">
          <div className="flex items-center gap-2">
            <label className="text-xs text-gray-500">端点筛选</label>
            <select
              value={selectedEndpoint}
              onChange={(event) => setSelectedEndpoint(event.target.value)}
              className="bg-gray-900 border border-gray-700 rounded px-2 py-1.5 text-sm text-gray-200 focus:border-blue-500 outline-none"
            >
              <option value="all">全部端点</option>
              {endpointOptions.map((endpoint) => (
                <option key={endpoint.id} value={String(endpoint.id)}>
                  {endpoint.name}
                </option>
              ))}
            </select>
          </div>
          <div className="flex items-center gap-2">
            <label className="text-xs text-gray-500">分组筛选</label>
            <select
              value={selectedGroup}
              onChange={(event) => setSelectedGroup(event.target.value)}
              className="bg-gray-900 border border-gray-700 rounded px-2 py-1.5 text-sm text-gray-200 focus:border-blue-500 outline-none"
            >
              <option value="all">全部分组</option>
              {groupOptions.map((group) => (
                <option key={group} value={group}>
                  {group}
                </option>
              ))}
            </select>
          </div>
          {isAddingKey && (
            <div className="flex items-center gap-2 ml-auto">
              <label className="text-xs text-gray-500">新 Key 所属端点</label>
              <select
                value={createEndpointId}
                onChange={(event) => setCreateEndpointId(event.target.value)}
                className="bg-gray-900 border border-gray-700 rounded px-2 py-1.5 text-sm text-gray-200 focus:border-blue-500 outline-none"
              >
                {endpointOptions.map((endpoint) => (
                  <option key={endpoint.id} value={String(endpoint.id)}>
                    {endpoint.name}
                  </option>
                ))}
              </select>
            </div>
          )}
        </div>

        <table className="w-full text-left text-sm text-gray-400">
          <thead className="bg-gray-900/50 text-gray-200 uppercase font-medium">
            <tr>
              <th className="px-4 py-3 rounded-l-lg">端点</th>
              <th className="px-4 py-3">备注名 / Key</th>
              <th className="px-4 py-3">关联分组</th>
              <th className="px-4 py-3">Quota / RPM</th>
              <th className="px-4 py-3">状态</th>
              <th className="px-4 py-3 rounded-r-lg text-right">操作</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-800/50">
            {filteredKeys.map((key) => {
              const status = resolveKeyStatus(key, healthStatusMap[key.id]);
              const groups = normalizeRuleGroups(key.rule_groups, key.rule_group);
              return (
                <tr key={key.id} className="hover:bg-gray-900/30 transition-colors">
                  <td className="px-4 py-3 text-gray-300">{key.endpoint_name}</td>
                  <td className="px-4 py-3">
                    <div className="font-medium text-gray-200">{key.name || "Untitled Key"}</div>
                    <div className="font-mono text-xs text-gray-500">{key.key_preview}</div>
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex flex-wrap gap-1.5">
                      {groups.map((group) => (
                        <span
                          key={`${key.id}-${group}`}
                          className="text-[11px] px-2 py-0.5 rounded border border-gray-700 bg-gray-900 text-gray-300"
                        >
                          {group}
                        </span>
                      ))}
                    </div>
                  </td>
                  <td className="px-4 py-3 text-xs text-gray-300">
                    <div>Quota: {key.daily_limit ?? "--"}</div>
                    <div>RPM: {key.rpm_limit ?? "--"}</div>
                  </td>
                  <td className="px-4 py-3">
                    <span className={`text-xs px-2 py-0.5 rounded border ${status.className}`}>
                      {status.label}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-right">
                    <div className="inline-flex items-center gap-2">
                      <button
                        onClick={() => setTestingKey(key)}
                        disabled={!isAdmin}
                        className="p-1.5 hover:bg-gray-800 rounded text-emerald-400 transition disabled:opacity-50"
                        title="测试 Key"
                      >
                        <FlaskConical size={14} />
                      </button>
                      <button
                        onClick={() => {
                          setEditingKey(key);
                          setIsAddingKey(false);
                        }}
                        disabled={!isAdmin}
                        className="p-1.5 hover:bg-gray-800 rounded text-blue-400 transition disabled:opacity-50"
                        title="编辑 Key"
                      >
                        <Edit2 size={14} />
                      </button>
                      <button
                        onClick={() => onDelete(key.id)}
                        disabled={!isAdmin}
                        className="p-1.5 hover:bg-gray-800 rounded text-red-400 transition disabled:opacity-50"
                        title="删除 Key"
                      >
                        <Trash2 size={14} />
                      </button>
                    </div>
                  </td>
                </tr>
              );
            })}
            {filteredKeys.length === 0 && (
              <tr>
                <td colSpan={6} className="px-4 py-10 text-center text-xs text-gray-500">
                  当前筛选下暂无 API Key
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {(editingKey || isAddingKey) && activeModalEndpointId > 0 && (
        <KeyConfigModal
          keyData={editingKey || undefined}
          endpointId={activeModalEndpointId}
          authToken={authToken}
          isAdmin={isAdmin}
          availableRuleGroups={availableRuleGroups}
          onClose={() => {
            setEditingKey(null);
            setIsAddingKey(false);
          }}
          onSave={(payload) => {
            if (editingKey) {
              onUpdate(editingKey.id, payload);
            } else {
              onCreate(activeModalEndpointId, payload);
            }
            setEditingKey(null);
            setIsAddingKey(false);
          }}
        />
      )}
      {testingKey && (
        <ApiKeyTestModal
          apiKey={testingKey}
          endpoint={endpoints.find((endpoint) => endpoint.id === testingKey.endpoint_id)}
          endpointId={testingKey.endpoint_id}
          authToken={authToken}
          isAdmin={isAdmin}
          onClose={() => setTestingKey(null)}
        />
      )}
    </div>
  );
};
