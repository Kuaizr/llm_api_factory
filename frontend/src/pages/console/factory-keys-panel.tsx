import {
  AlertTriangle,
  Check,
  Copy,
  Edit2,
  Key,
  Plus,
  RefreshCw,
  RotateCw,
  Trash2,
  XCircle,
} from "lucide-react";
import { useEffect, useState } from "react";

import { apiBase, buildHeaders, formatTimestamp } from "./shared";

export interface FactoryAccessKeyItem {
  id: number;
  name: string | null;
  key_preview: string;
  key: string | null;
  rule_groups: string[];
  is_active: boolean;
  created_at: string;
}

export interface FactoryAccessKeyIssue {
  id: number;
  name: string | null;
  key: string;
  rule_groups: string[];
  is_active: boolean;
  created_at: string;
}

const isStringList = (value: unknown): value is string[] =>
  Array.isArray(value) && value.every((item) => typeof item === "string");

const parseFactoryAccessKeyItem = (
  value: unknown
): FactoryAccessKeyItem | null => {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    return null;
  }
  const item = value as Record<string, unknown>;
  const id = item.id;
  const name = item.name;
  const keyPreview = item.key_preview;
  const key = item.key;
  const ruleGroups = item.rule_groups;
  const isActive = item.is_active;
  const createdAt = item.created_at;
  if (
    typeof id !== "number" ||
    !(typeof name === "string" || name === null) ||
    typeof keyPreview !== "string" ||
    !(typeof key === "string" || key === null) ||
    !isStringList(ruleGroups) ||
    typeof isActive !== "boolean" ||
    typeof createdAt !== "string"
  ) {
    return null;
  }
  return {
    id,
    name: name as string | null,
    key_preview: keyPreview as string,
    key: key as string | null,
    rule_groups: ruleGroups as string[],
    is_active: isActive,
    created_at: createdAt,
  };
};

const parseFactoryAccessKeyList = (
  value: unknown
): FactoryAccessKeyItem[] | null => {
  if (!Array.isArray(value)) {
    return null;
  }
  const items: FactoryAccessKeyItem[] = [];
  for (const item of value) {
    const parsed = parseFactoryAccessKeyItem(item);
    if (!parsed) {
      return null;
    }
    items.push(parsed);
  }
  return items;
};

const parseFactoryAccessKeyIssue = (
  value: unknown
): FactoryAccessKeyIssue | null => {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    return null;
  }
  const item = value as Record<string, unknown>;
  const id = item.id;
  const name = item.name;
  const key = item.key;
  const ruleGroups = item.rule_groups;
  const isActive = item.is_active;
  const createdAt = item.created_at;
  if (
    typeof id !== "number" ||
    !(typeof name === "string" || name === null) ||
    typeof key !== "string" ||
    !isStringList(ruleGroups) ||
    typeof isActive !== "boolean" ||
    typeof createdAt !== "string"
  ) {
    return null;
  }
  return {
    id,
    name: name as string | null,
    key: key as string,
    rule_groups: ruleGroups as string[],
    is_active: isActive,
    created_at: createdAt,
  };
};

export const FactoryKeysPanel = ({
  isAdmin,
  authToken,
  ruleGroups,
}: {
  isAdmin: boolean;
  authToken: string | null;
  ruleGroups: string[];
}) => {
  const [keys, setKeys] = useState<FactoryAccessKeyItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [issuedKey, setIssuedKey] = useState<FactoryAccessKeyIssue | null>(null);
  const [editingKey, setEditingKey] = useState<FactoryAccessKeyItem | null>(null);
  const [showEditor, setShowEditor] = useState(false);

  const loadKeys = async () => {
    setLoading(true);
    setError(null);
    try {
      const response = await fetch(`${apiBase}/admin/factory-keys`, {
        headers: buildHeaders(authToken),
      });
      if (!response.ok) {
        setError("无法加载访问 Key 列表");
        setKeys([]);
        return;
      }
      const data = parseFactoryAccessKeyList(await response.json());
      if (!data) {
        setError("访问 Key 列表响应格式异常");
        setKeys([]);
        return;
      }
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
  }, [authToken]);

  const createKey = async (name: string, ruleGroups: string[]) => {
    if (!isAdmin) return;
    setError(null);
    const response = await fetch(`${apiBase}/admin/factory-keys`, {
      method: "POST",
      headers: buildHeaders(authToken, true),
      body: JSON.stringify({ name: name.trim() || null, rule_groups: ruleGroups }),
    });
    if (!response.ok) {
      setError("创建访问 Key 失败");
      return;
    }
    const data = parseFactoryAccessKeyIssue(await response.json());
    if (!data) {
      setError("创建访问 Key 响应格式异常");
      return;
    }
    setIssuedKey(data);
    await loadKeys();
  };

  const updateKey = async (
    keyId: number,
    payload: { name?: string | null; rule_groups?: string[]; is_active?: boolean }
  ) => {
    if (!isAdmin) return;
    const response = await fetch(`${apiBase}/admin/factory-keys/${keyId}`, {
      method: "PATCH",
      headers: buildHeaders(authToken, true),
      body: JSON.stringify(payload),
    });
    if (!response.ok) {
      setError("更新访问 Key 失败");
      return;
    }
    await loadKeys();
  };

  const rotateKey = async (keyId: number) => {
    if (!isAdmin) return;
    const response = await fetch(`${apiBase}/admin/factory-keys/${keyId}/rotate`, {
      method: "POST",
      headers: buildHeaders(authToken),
    });
    if (!response.ok) {
      setError("轮换访问 Key 失败");
      return;
    }
    const data = parseFactoryAccessKeyIssue(await response.json());
    if (!data) {
      setError("轮换访问 Key 响应格式异常");
      return;
    }
    setIssuedKey(data);
    await loadKeys();
  };

  const deleteKey = async (keyId: number) => {
    if (!isAdmin) return;
    if (!window.confirm("确认删除该访问 Key 吗？")) {
      return;
    }
    const response = await fetch(`${apiBase}/admin/factory-keys/${keyId}`, {
      method: "DELETE",
      headers: buildHeaders(authToken),
    });
    if (!response.ok) {
      setError("删除访问 Key 失败");
      return;
    }
    await loadKeys();
  };

  const handleCopy = (value: string | null) => {
    if (!value) return;
    if (typeof navigator !== "undefined" && navigator.clipboard?.writeText) {
      navigator.clipboard.writeText(value).catch(() => undefined);
    }
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-bold text-white flex items-center gap-2">
            <Key size={18} className="text-blue-400" />
            API Key 全局管理
          </h2>
          <p className="text-xs text-gray-500 mt-1">
            管理系统对外提供的访问密钥，每个 Key 可绑定多个规则组
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => {
              setEditingKey(null);
              setShowEditor(true);
            }}
            disabled={!isAdmin}
            className="px-3 py-2 bg-blue-600 hover:bg-blue-500 text-white text-sm rounded flex items-center gap-1 disabled:opacity-50"
          >
            <Plus size={14} />
            创建 Key
          </button>
          <button
            onClick={() => void loadKeys()}
            className="p-2 bg-gray-800 border border-gray-700 rounded text-gray-300 hover:text-white"
            title="刷新"
          >
            <RefreshCw size={14} />
          </button>
        </div>
      </div>

      {error && <div className="text-sm text-red-400">{error}</div>}

      {issuedKey && (
        <div className="p-3 rounded border border-green-700/40 bg-green-900/20">
          <div className="flex items-center gap-2 text-green-300 text-sm mb-2">
            <AlertTriangle size={14} />
            新 Key 已生成，请及时保存，后续列表仅显示预览值，无法再次查看完整 Key。
          </div>
          <div className="space-y-2">
            <div className="flex items-center gap-2">
              <span className="text-xs text-gray-400 w-16">名称</span>
              <input
                value={issuedKey.name || "-"}
                readOnly
                className="flex-1 bg-gray-950 border border-gray-800 rounded px-2 py-1 text-xs text-gray-200"
              />
            </div>
            <div className="flex items-center gap-2">
              <span className="text-xs text-gray-400 w-16">Key</span>
              <input
                value={issuedKey.key}
                readOnly
                className="flex-1 bg-gray-950 border border-gray-800 rounded px-2 py-1 text-xs text-green-200 font-mono"
              />
              <button
                onClick={() => handleCopy(issuedKey.key)}
                className="px-2 py-1 text-xs text-green-300 border border-green-700/40 rounded hover:bg-green-800/30"
              >
                <Copy size={12} />
              </button>
            </div>
            <div className="flex items-center gap-2">
              <span className="text-xs text-gray-400 w-16">规则组</span>
              <div className="flex flex-wrap gap-1">
                {issuedKey.rule_groups.map((g) => (
                  <span
                    key={g}
                    className="px-2 py-0.5 text-xs bg-blue-900/30 border border-blue-800/30 rounded text-blue-300"
                  >
                    {g}
                  </span>
                ))}
              </div>
            </div>
          </div>
          <button
            onClick={() => setIssuedKey(null)}
            className="mt-3 px-3 py-1 text-xs text-gray-400 border border-gray-700 rounded hover:bg-gray-800"
          >
            关闭
          </button>
        </div>
      )}

      <div className="bg-[#0f1117] border border-gray-800 rounded-xl overflow-hidden">
        <table className="w-full text-left text-sm text-gray-400">
          <thead className="bg-gray-900/50 text-gray-200 uppercase font-medium">
            <tr>
              <th className="px-4 py-3 rounded-l-lg">名称</th>
              <th className="px-4 py-3">Key 预览</th>
              <th className="px-4 py-3">规则组</th>
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
                  <div className="flex flex-wrap gap-1">
                    {item.rule_groups.map((g) => (
                      <span
                        key={g}
                        className="px-2 py-0.5 text-xs bg-blue-900/30 border border-blue-800/30 rounded text-blue-300"
                      >
                        {g}
                      </span>
                    ))}
                  </div>
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
                      onClick={() => {
                        setEditingKey(item);
                        setShowEditor(true);
                      }}
                      disabled={!isAdmin}
                      className="p-1.5 hover:bg-gray-800 rounded text-blue-300 transition disabled:opacity-50"
                      title="编辑"
                    >
                      <Edit2 size={14} />
                    </button>
                    <button
                      onClick={() => void updateKey(item.id, { is_active: !item.is_active })}
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
                      onClick={() => void deleteKey(item.id)}
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
          <div className="text-sm text-gray-500 p-6 text-center">暂无访问 Key</div>
        )}
        {loading && <div className="text-sm text-gray-500 p-6 text-center">加载中...</div>}
      </div>

      {showEditor && (
        <FactoryKeyEditorModal
          keyItem={editingKey}
          ruleGroups={ruleGroups}
          isAdmin={isAdmin}
          onCreate={createKey}
          onUpdate={updateKey}
          onClose={() => {
            setShowEditor(false);
            setEditingKey(null);
          }}
        />
      )}
    </div>
  );
};

const FactoryKeyEditorModal = ({
  keyItem,
  ruleGroups,
  isAdmin,
  onCreate,
  onUpdate,
  onClose,
}: {
  keyItem: FactoryAccessKeyItem | null;
  ruleGroups: string[];
  isAdmin: boolean;
  onCreate: (name: string, ruleGroups: string[]) => Promise<void>;
  onUpdate: (keyId: number, payload: { name?: string | null; rule_groups?: string[] }) => Promise<void>;
  onClose: () => void;
}) => {
  const [name, setName] = useState(keyItem?.name ?? "");
  const [selectedGroups, setSelectedGroups] = useState<Set<string>>(
    new Set(keyItem?.rule_groups ?? ["default"])
  );
  const [saving, setSaving] = useState(false);

  const toggleGroup = (group: string) => {
    const next = new Set(selectedGroups);
    if (next.has(group)) {
      next.delete(group);
    } else {
      next.add(group);
    }
    setSelectedGroups(next);
  };

  const handleSave = async () => {
    if (!isAdmin) return;
    setSaving(true);
    try {
      const groups = Array.from(selectedGroups);
      if (keyItem) {
        await onUpdate(keyItem.id, { name: name.trim() || null, rule_groups: groups });
      } else {
        await onCreate(name.trim() || null, groups);
      }
      onClose();
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="fixed inset-0 z-[170] flex items-center justify-center bg-black/70 backdrop-blur-sm">
      <div className="bg-[#0f1117] border border-gray-800 rounded-xl w-[520px] max-h-[85vh] flex flex-col shadow-2xl animate-in fade-in zoom-in-95 duration-200">
        <div className="p-6 border-b border-gray-800 flex justify-between items-center">
          <div>
            <h3 className="text-lg font-bold text-white flex items-center gap-2">
              <Key size={18} className="text-blue-400" />
              {keyItem ? "编辑访问 Key" : "创建访问 Key"}
            </h3>
            {keyItem && (
              <p className="text-xs text-gray-500 mt-1">
                Key #{keyItem.id} · {keyItem.key_preview}
              </p>
            )}
          </div>
          <button onClick={onClose} className="text-gray-500 hover:text-white">
            <XCircle size={20} />
          </button>
        </div>

        <div className="p-6 flex-1 overflow-y-auto space-y-4">
          <div>
            <label className="block text-xs text-gray-400 mb-1">名称（可选）</label>
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              disabled={!isAdmin}
              placeholder="如：生产环境密钥"
              className="w-full bg-gray-900 border border-gray-700 rounded p-2.5 text-sm text-white focus:border-blue-500 focus:outline-none disabled:opacity-50"
            />
          </div>

          <div>
            <label className="block text-xs text-gray-400 mb-2">绑定规则组</label>
            <div className="flex flex-wrap gap-2">
              {ruleGroups.map((group) => {
                const selected = selectedGroups.has(group);
                return (
                  <button
                    key={group}
                    onClick={() => toggleGroup(group)}
                    disabled={!isAdmin}
                    className={`px-3 py-1.5 text-sm rounded border transition ${
                      selected
                        ? "bg-blue-600/20 border-blue-600 text-blue-300"
                        : "bg-gray-900 border-gray-700 text-gray-400 hover:border-gray-600"
                    } disabled:opacity-50`}
                  >
                    {selected && <Check size={12} className="inline mr-1" />}
                    {group}
                  </button>
                );
              })}
            </div>
            {selectedGroups.size === 0 && (
              <p className="text-xs text-red-400 mt-2">请至少选择一个规则组</p>
            )}
          </div>
        </div>

        <div className="p-6 border-t border-gray-800 flex justify-end gap-2">
          <button
            onClick={onClose}
            className="px-4 py-2 text-sm text-gray-400 border border-gray-700 rounded hover:bg-gray-800"
          >
            取消
          </button>
          <button
            onClick={() => void handleSave()}
            disabled={!isAdmin || saving || selectedGroups.size === 0}
            className="px-4 py-2 text-sm bg-blue-600 hover:bg-blue-500 text-white rounded disabled:opacity-50"
          >
            {saving ? "保存中..." : keyItem ? "保存" : "创建"}
          </button>
        </div>
      </div>
    </div>
  );
};
