import {
  Edit2,
  FlaskConical,
  Key,
  Plus,
  RefreshCw,
  Settings,
  Trash2,
  XCircle,
} from "lucide-react";
import { useMemo, useState } from "react";

import { ApiKeyTestModal } from "./api-key-test-modal";
import {
  apiBase,
  buildHeaders,
  getPrimaryRuleGroup,
  keyHasRuleGroup,
  normalizeRuleGroups,
  type AgentNode,
  type ApiKey,
  type Endpoint,
  type EndpointFormState,
  type HealthStatus,
  type RuleGroupEligibilityResult,
  resolveKeyStatus,
} from "./shared";

export const EditEndpointModal = ({
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
  onSave: (payload: EndpointFormState) => void | Promise<boolean>;
  onDelete?: (endpoint: Endpoint) => void;
}) => {
  const [form, setForm] = useState<EndpointFormState>({
    name: endpoint?.name ?? "",
    base_url: endpoint?.base_url ?? "",
    auth_header_name: endpoint?.auth_header_name ?? "Authorization",
    auth_header_prefix: endpoint?.auth_header_prefix ?? "Bearer",
    provider: endpoint?.provider ?? "openai",
    agent_node: endpoint?.agent_node ?? "",
    probe_interval_seconds:
      endpoint?.probe_interval_seconds != null
        ? String(endpoint.probe_interval_seconds)
        : "",
    is_active: endpoint?.is_active ?? true,
    url_path_suffix: endpoint?.url_path_suffix ?? "",
    extra_headers: endpoint?.extra_headers ?? undefined,
    extra_cookies: endpoint?.extra_cookies ?? "",
    extra_query_params: endpoint?.extra_query_params ?? undefined,
    oauth_config: endpoint?.oauth_config ?? undefined,
    request_body_template: endpoint?.request_body_template ?? "",
  });

  // 用于 JSON 字段编辑的字符串状态
  const [extraHeadersText, setExtraHeadersText] = useState(
    endpoint?.extra_headers ? JSON.stringify(endpoint.extra_headers, null, 2) : ""
  );
  const [extraQueryParamsText, setExtraQueryParamsText] = useState(
    endpoint?.extra_query_params ? JSON.stringify(endpoint.extra_query_params, null, 2) : ""
  );
  const [oauthConfigText, setOauthConfigText] = useState(
    endpoint?.oauth_config ? JSON.stringify(endpoint.oauth_config, null, 2) : ""
  );
  const isCustomProvider = form.provider === "custom";

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
        <div className="p-6 space-y-4 max-h-[70vh] overflow-y-auto">
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
              onChange={(event) => {
                const provider = event.target.value;
                if (provider !== "custom") {
                  setExtraHeadersText("");
                  setExtraQueryParamsText("");
                  setOauthConfigText("");
                }
                setForm((prev) => {
                  const customOnly =
                    provider === "custom"
                      ? {}
                      : {
                          url_path_suffix: "",
                          extra_headers: undefined,
                          extra_cookies: "",
                          extra_query_params: undefined,
                          oauth_config: undefined,
                          request_body_template: "",
                        };
                  if (provider === "anthropic") {
                    return {
                      ...prev,
                      ...customOnly,
                      provider,
                      auth_header_name: "x-api-key",
                      auth_header_prefix: "",
                    };
                  }
                  if (provider === "gemini") {
                    return {
                      ...prev,
                      ...customOnly,
                      provider,
                      auth_header_name: "x-goog-api-key",
                      auth_header_prefix: "",
                    };
                  }
                  if (provider === "openai") {
                    return {
                      ...prev,
                      ...customOnly,
                      provider,
                      auth_header_name: "Authorization",
                      auth_header_prefix: "Bearer",
                    };
                  }
                  return { ...prev, ...customOnly, provider };
                });
              }}
              className="w-full bg-gray-900 border border-gray-700 rounded p-2.5 text-sm text-white focus:border-blue-500 focus:outline-none"
              disabled={!isAdmin}
            >
              <option value="openai">OpenAI Compatible</option>
              <option value="anthropic">Anthropic</option>
              <option value="gemini">Gemini</option>
              <option value="custom">Custom Template</option>
            </select>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="text-xs font-bold text-gray-500 uppercase mb-1.5 block">
                认证 Header
              </label>
              <input
                value={form.auth_header_name}
                onChange={(event) =>
                  setForm((prev) => ({
                    ...prev,
                    auth_header_name: event.target.value,
                  }))
                }
                className="w-full bg-gray-900 border border-gray-700 rounded p-2.5 text-sm text-white font-mono focus:border-blue-500 focus:outline-none"
                disabled={!isAdmin}
              />
            </div>
            <div>
              <label className="text-xs font-bold text-gray-500 uppercase mb-1.5 block">
                Header 前缀
              </label>
              <input
                value={form.auth_header_prefix}
                onChange={(event) =>
                  setForm((prev) => ({
                    ...prev,
                    auth_header_prefix: event.target.value,
                  }))
                }
                placeholder="留空表示直接写入 Key"
                className="w-full bg-gray-900 border border-gray-700 rounded p-2.5 text-sm text-white font-mono focus:border-blue-500 focus:outline-none"
                disabled={!isAdmin}
              />
            </div>
          </div>
          <div>
            <label className="text-xs font-bold text-gray-500 uppercase mb-1.5 block">
              探针间隔（秒）
            </label>
            <input
              type="number"
              min={-1}
              max={86400}
              value={form.probe_interval_seconds}
              onChange={(event) =>
                setForm((prev) => ({
                  ...prev,
                  probe_interval_seconds: event.target.value,
                }))
              }
              className="w-full bg-gray-900 border border-gray-700 rounded p-2.5 text-sm text-white focus:border-blue-500 focus:outline-none"
              disabled={!isAdmin}
              placeholder="留空表示跟随系统默认"
            />
            <p className="mt-1 text-xs text-gray-500">
              可选。范围 -1~86400 秒，留空跟随系统默认；-1 表示禁用自动探针。
            </p>
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

          {/* 通用 Provider 扩展配置 */}
          {isCustomProvider && (
          <div className="pt-2 border-t border-gray-800 space-y-4">
            <div className="flex items-center justify-between">
              <label className="text-xs font-bold text-gray-500 uppercase">
                扩展配置（可选）
              </label>
            </div>
            
            <div>
              <label className="text-xs font-medium text-gray-400 mb-1.5 block">
                自定义 URL 后缀
              </label>
              <input
                value={form.url_path_suffix ?? ""}
                onChange={(event) =>
                  setForm((prev) => ({ ...prev, url_path_suffix: event.target.value || undefined }))
                }
                placeholder="例如: /api/chat 或 /v2/generate"
                className="w-full bg-gray-900 border border-gray-700 rounded p-2.5 text-sm text-white font-mono focus:border-blue-500 focus:outline-none"
                disabled={!isAdmin}
              />
              <p className="mt-1 text-xs text-gray-500">
                非标准 API 路径时使用。留空则使用默认路径逻辑。
              </p>
            </div>

            <div>
              <label className="text-xs font-medium text-gray-400 mb-1.5 block">
                额外请求头 (JSON)
              </label>
              <textarea
                value={extraHeadersText}
                onChange={(event) => {
                  const text = event.target.value;
                  setExtraHeadersText(text);
                  try {
                    if (text.trim()) {
                      const parsed = JSON.parse(text);
                      setForm((prev) => ({ ...prev, extra_headers: parsed }));
                    } else {
                      setForm((prev) => ({ ...prev, extra_headers: undefined }));
                    }
                  } catch {
                    // JSON 解析失败，不更新 form
                  }
                }}
                placeholder='{"X-Custom-Header": "value"}'
                rows={2}
                className="w-full bg-gray-900 border border-gray-700 rounded p-2.5 text-sm text-white font-mono focus:border-blue-500 focus:outline-none"
                disabled={!isAdmin}
              />
            </div>

            <div>
              <label className="text-xs font-medium text-gray-400 mb-1.5 block">
                Cookie
              </label>
              <input
                value={form.extra_cookies ?? ""}
                onChange={(event) =>
                  setForm((prev) => ({ ...prev, extra_cookies: event.target.value || undefined }))
                }
                placeholder="session_id=abc123; token=xyz"
                className="w-full bg-gray-900 border border-gray-700 rounded p-2.5 text-sm text-white font-mono focus:border-blue-500 focus:outline-none"
                disabled={!isAdmin}
              />
            </div>

            <div>
              <label className="text-xs font-medium text-gray-400 mb-1.5 block">
                额外查询参数 (JSON)
              </label>
              <textarea
                value={extraQueryParamsText}
                onChange={(event) => {
                  const text = event.target.value;
                  setExtraQueryParamsText(text);
                  try {
                    if (text.trim()) {
                      const parsed = JSON.parse(text);
                      setForm((prev) => ({ ...prev, extra_query_params: parsed }));
                    } else {
                      setForm((prev) => ({ ...prev, extra_query_params: undefined }));
                    }
                  } catch {
                    // JSON 解析失败，不更新 form
                  }
                }}
                placeholder='{"api_key": "xxx"}'
                rows={2}
                className="w-full bg-gray-900 border border-gray-700 rounded p-2.5 text-sm text-white font-mono focus:border-blue-500 focus:outline-none"
                disabled={!isAdmin}
              />
            </div>

            <div>
              <label className="text-xs font-medium text-gray-400 mb-1.5 block">
                OAuth 配置 (JSON)
              </label>
              <textarea
                value={oauthConfigText}
                onChange={(event) => {
                  const text = event.target.value;
                  setOauthConfigText(text);
                  try {
                    if (text.trim()) {
                      const parsed = JSON.parse(text);
                      setForm((prev) => ({ ...prev, oauth_config: parsed }));
                    } else {
                      setForm((prev) => ({ ...prev, oauth_config: undefined }));
                    }
                  } catch {
                    // JSON 解析失败，不更新 form
                  }
                }}
                placeholder='{"token_url": "https://auth.example.com/oauth/token", "client_id": "xxx", "client_secret": "xxx"}'
                rows={3}
                className="w-full bg-gray-900 border border-gray-700 rounded p-2.5 text-sm text-white font-mono focus:border-blue-500 focus:outline-none"
                disabled={!isAdmin}
              />
              <p className="mt-1 text-xs text-gray-500">
                OAuth Client Credentials 流程配置（即将支持）
              </p>
            </div>

            <div>
              <label className="text-xs font-medium text-gray-400 mb-1.5 block">
                请求体模板 (JSON)
              </label>
              <textarea
                value={form.request_body_template ?? ""}
                onChange={(event) =>
                  setForm((prev) => ({ ...prev, request_body_template: event.target.value || undefined }))
                }
                placeholder={'{\n  "model": "{{model}}",\n  "prompt": "{{prompt}}",\n  "max_tokens": 1024\n}'}
                rows={6}
                className="w-full bg-gray-900 border border-gray-700 rounded p-2.5 text-sm text-white font-mono focus:border-blue-500 focus:outline-none"
                disabled={!isAdmin}
              />
              <p className="mt-1 text-xs text-gray-500">
                支持 {`{{model}}`}、{`{{prompt}}`} 等变量替换。留空使用原始请求体。
              </p>
            </div>
          </div>
          )}
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

export const KeyConfigModal = ({
  keyData,
  endpointId,
  authToken,
  isAdmin,
  availableRuleGroups,
  onClose,
  onSave,
}: {
  keyData?: ApiKey;
  endpointId: number;
  authToken: string | null;
  isAdmin: boolean;
  availableRuleGroups: string[];
  onClose: () => void;
  onSave: (payload: Partial<ApiKey> & { key?: string }) => boolean | Promise<boolean>;
}) => {
  const [keyValue, setKeyValue] = useState("");
  const [name, setName] = useState(keyData?.name ?? "");
  const [ruleGroups, setRuleGroups] = useState<string[]>(() =>
    normalizeRuleGroups(keyData?.rule_groups, keyData?.rule_group)
  );
  const [dailyLimit, setDailyLimit] = useState(String(keyData?.daily_limit ?? ""));
  const [rpmLimit, setRpmLimit] = useState(String(keyData?.rpm_limit ?? ""));
  const [isActive, setIsActive] = useState(keyData?.is_active ?? true);
  const [checkingGroup, setCheckingGroup] = useState<string | null>(null);
  const [groupNotice, setGroupNotice] = useState<string | null>(null);
  const [formError, setFormError] = useState<string | null>(null);

  const groupOptions = useMemo(() => {
    const groups = new Set<string>(["default"]);
    availableRuleGroups.forEach((group) => {
      const normalized = String(group || "").trim();
      if (normalized) {
        groups.add(normalized.toLowerCase() === "default" ? "default" : normalized);
      }
    });
    normalizeRuleGroups(keyData?.rule_groups, keyData?.rule_group).forEach((group) => {
      groups.add(group);
    });
    return Array.from(groups).sort((left, right) => {
      if (left === "default") return -1;
      if (right === "default") return 1;
      return left.localeCompare(right);
    });
  }, [availableRuleGroups, keyData]);

  const checkRuleGroup = async (
    group: string,
    options?: { showNotice?: boolean }
  ): Promise<RuleGroupEligibilityResult | null> => {
    const groupName = String(group || "").trim() || "default";
    if (groupName.toLowerCase() === "default") {
      return {
        group_name: "default",
        eligible: true,
        reason: null,
        probed: false,
        required_patterns: [],
        matched_models: [],
      };
    }
    if (!authToken) {
      setFormError("请先登录管理员");
      return null;
    }
    if (!keyData && !keyValue.trim()) {
      setFormError("请先填写 API Key 后再选择分组");
      return null;
    }

    setCheckingGroup(groupName);
    try {
      const response = await fetch(
        `${apiBase}/admin/endpoints/${endpointId}/keys/check-rule-group`,
        {
          method: "POST",
          headers: buildHeaders(authToken, true),
          body: JSON.stringify({
            group_name: groupName,
            api_key_id: keyData?.id,
            api_key: keyData ? undefined : keyValue.trim() || undefined,
          }),
        }
      );
      if (!response.ok) {
        let message = "分组校验失败";
        try {
          const payload = (await response.json()) as { detail?: string };
          if (payload.detail) {
            message = payload.detail;
          }
        } catch {
          // ignore
        }
        setFormError(message);
        return null;
      }
      const data = (await response.json()) as RuleGroupEligibilityResult;
      if (data.eligible && options?.showNotice !== false) {
        if (data.probed) {
          setGroupNotice(`分组 ${groupName} 校验前已自动补充模型探测`);
        } else {
          setGroupNotice(null);
        }
      }
      return data;
    } catch {
      setFormError("分组校验失败，请稍后再试");
      return null;
    } finally {
      setCheckingGroup(null);
    }
  };

  const toggleRuleGroup = async (group: string) => {
    const normalized = String(group || "").trim() || "default";
    const lower = normalized.toLowerCase();
    if (lower === "default") {
      return;
    }
    setFormError(null);
    setGroupNotice(null);

    const isSelected = ruleGroups.some((item) => item.toLowerCase() === lower);
    if (isSelected) {
      setRuleGroups((prev) =>
        normalizeRuleGroups(
          prev.filter((item) => item.toLowerCase() !== lower),
          "default"
        )
      );
      return;
    }

    const eligibility = await checkRuleGroup(normalized, { showNotice: true });
    if (!eligibility) {
      return;
    }
    if (!eligibility.eligible) {
      setFormError(eligibility.reason || `分组 ${normalized} 与该 Key 不匹配`);
      return;
    }

    setRuleGroups((prev) => normalizeRuleGroups([...prev, normalized], "default"));
  };

  const handleSave = async () => {
    const normalizedRuleGroups = normalizeRuleGroups(ruleGroups, "default");
    const parsedDailyLimit = dailyLimit === "" ? null : Number(dailyLimit);
    const parsedRpmLimit = rpmLimit === "" ? null : Number(rpmLimit);

    if (!keyData && !keyValue.trim()) {
      setFormError("API Key 不能为空");
      return;
    }
    if (
      parsedDailyLimit !== null &&
      (!Number.isFinite(parsedDailyLimit) || parsedDailyLimit < 0)
    ) {
      setFormError("每日配额需为非负数");
      return;
    }
    if (
      parsedRpmLimit !== null &&
      (!Number.isFinite(parsedRpmLimit) || parsedRpmLimit < 0)
    ) {
      setFormError("RPM 需为非负数");
      return;
    }

    for (const group of normalizedRuleGroups) {
      if (group.toLowerCase() === "default") {
        continue;
      }
      const eligibility = await checkRuleGroup(group, { showNotice: false });
      if (!eligibility) {
        return;
      }
      if (!eligibility.eligible) {
        setFormError(eligibility.reason || `分组 ${group} 与该 Key 不匹配`);
        return;
      }
    }

    const primaryRuleGroup =
      normalizedRuleGroups.find((group) => group.toLowerCase() !== "default") || "default";

    setFormError(null);
    const saved = await onSave({
      key: keyValue.trim() || undefined,
      name: name.trim() || undefined,
      rule_group: primaryRuleGroup,
      rule_groups: normalizedRuleGroups,
      daily_limit: parsedDailyLimit,
      rpm_limit: parsedRpmLimit,
      is_active: isActive,
    });
    if (saved === false) {
      setFormError("保存失败，请稍后再试。");
    }
  };

  return (
    <div className="fixed inset-0 z-[150] flex items-center justify-center bg-black/80 backdrop-blur-[2px]">
      <div className="bg-[#1a1d24] border border-gray-700 rounded-lg w-[440px] shadow-2xl p-5 animate-in fade-in zoom-in-95 duration-200">
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
                aria-label="API Key"
                value={keyValue}
                onChange={(event) => {
                  setKeyValue(event.target.value);
                  if (formError) {
                    setFormError(null);
                  }
                  if (groupNotice) {
                    setGroupNotice(null);
                  }
                }}
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
              aria-label="Key 备注名称"
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder="e.g. Free Tier Account"
              className="w-full bg-gray-900 border border-gray-600 rounded p-2 text-sm text-white focus:border-green-500 outline-none"
              disabled={!isAdmin}
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-gray-400 mb-1">
              分组 (Rule Group)
            </label>
            <div className="space-y-1.5 rounded border border-gray-700 bg-gray-900/50 p-2 max-h-40 overflow-y-auto">
              {groupOptions.map((group) => {
                const lower = group.toLowerCase();
                const checked = ruleGroups.some((item) => item.toLowerCase() === lower);
                const disabled = !isAdmin || checkingGroup === group || lower === "default";
                return (
                  <label
                    key={group}
                    className={`flex items-center justify-between px-1.5 py-1 rounded ${
                      checked ? "bg-green-900/15" : ""
                    }`}
                  >
                    <span className="text-sm text-gray-200">{group}</span>
                    <input
                      type="checkbox"
                      aria-label={`Key 分组 ${group}`}
                      checked={checked}
                      disabled={disabled}
                      onChange={() => {
                        void toggleRuleGroup(group);
                      }}
                      className="w-4 h-4 rounded border-gray-600 bg-gray-800 text-green-500 focus:ring-offset-gray-900"
                    />
                  </label>
                );
              })}
            </div>
            <p className="mt-1 text-[11px] text-gray-500">
              default 为必选；选择其他分组会即时校验该 Key 对应端点是否满足模型匹配规则。
            </p>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs font-medium text-gray-400 mb-1">
                每日配额 (Quota)
              </label>
              <input
                aria-label="每日配额"
                value={dailyLimit}
                onChange={(event) => {
                  setDailyLimit(event.target.value);
                  if (formError) {
                    setFormError(null);
                  }
                }}
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
                aria-label="RPM 限额"
                value={rpmLimit}
                onChange={(event) => {
                  setRpmLimit(event.target.value);
                  if (formError) {
                    setFormError(null);
                  }
                }}
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
        {groupNotice && <p className="mt-3 text-xs text-blue-300">{groupNotice}</p>}
        {formError && <p className="mt-3 text-xs text-red-400">{formError}</p>}
        <div className="flex justify-end gap-2 mt-6">
          <button
            onClick={onClose}
            className="px-3 py-1.5 text-xs text-gray-400 hover:text-white"
          >
            取消
          </button>
          <button
            onClick={() => {
              void handleSave();
            }}
            disabled={!isAdmin || checkingGroup !== null}
            className="px-3 py-1.5 bg-green-600 hover:bg-green-500 text-white text-xs font-bold rounded disabled:opacity-50"
          >
            保存
          </button>
        </div>
      </div>
    </div>
  );
};

export const ManageKeysModal = ({
  endpoint,
  isAdmin,
  authToken,
  healthStatusMap,
  availableRuleGroups,
  onClose,
  onCreate,
  onUpdate,
  onDelete,
  onRefresh,
}: {
  endpoint: Endpoint;
  isAdmin: boolean;
  authToken: string | null;
  healthStatusMap: Record<number, HealthStatus>;
  availableRuleGroups: string[];
  onClose: () => void;
  onCreate: (payload: Partial<ApiKey> & { key?: string }) => boolean | Promise<boolean>;
  onUpdate: (
    keyId: number,
    payload: Partial<ApiKey> & { key?: string }
  ) => boolean | Promise<boolean>;
  onDelete: (keyId: number) => void;
  onRefresh: () => void;
}) => {
  const [editingKey, setEditingKey] = useState<ApiKey | null>(null);
  const [testingKey, setTestingKey] = useState<ApiKey | null>(null);
  const [isAddingKey, setIsAddingKey] = useState(false);
  const [selectedGroup, setSelectedGroup] = useState("all");

  const getKeyStatus = (key: ApiKey) =>
    resolveKeyStatus(key, healthStatusMap[key.id]);

  const sortedKeys = useMemo(
    () =>
      [...endpoint.keys].sort((left, right) => {
        const leftGroup = (left.rule_group ?? "default").toLowerCase();
        const rightGroup = (right.rule_group ?? "default").toLowerCase();
        if (leftGroup !== rightGroup) {
          return leftGroup.localeCompare(rightGroup);
        }
        return left.id - right.id;
      }),
    [endpoint.keys]
  );

  const groupOptions = useMemo(() => {
    const groups = new Set<string>(
      availableRuleGroups.length > 0 ? availableRuleGroups : ["default"]
    );
    sortedKeys.forEach((key) => {
      const normalized = key.rule_group?.trim() || "default";
      groups.add(normalized);
    });
    return Array.from(groups).sort((left, right) => {
      if (left === "default") return -1;
      if (right === "default") return 1;
      return left.localeCompare(right);
    });
  }, [availableRuleGroups, sortedKeys]);

  const filteredKeys = useMemo(() => {
    if (selectedGroup === "all") {
      return sortedKeys;
    }
    return sortedKeys.filter(
      (key) => (key.rule_group?.trim() || "default") === selectedGroup
    );
  }, [selectedGroup, sortedKeys]);

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
              Endpoint: {endpoint.name} - 按分组管理上游 Key 配置
            </p>
          </div>
          <button onClick={onClose} className="text-gray-500 hover:text-white">
            <XCircle size={24} />
          </button>
        </div>

        <div className="p-6 overflow-y-auto flex-1">
          <div className="flex items-center justify-between mb-4 gap-2">
            <div className="flex items-center gap-2">
              <label className="text-xs text-gray-500">分组筛选</label>
              <select
                aria-label="按分组筛选 Key"
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
            <div className="flex gap-2">
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
          </div>

          <table className="w-full text-left text-sm text-gray-400">
            <thead className="bg-gray-900/50 text-gray-200 uppercase font-medium">
              <tr>
                <th className="px-4 py-3 rounded-l-lg">备注名 / Key</th>
                <th className="px-4 py-3">分组</th>
                <th className="px-4 py-3">每日限额 (Quota)</th>
                <th className="px-4 py-3">今日已用</th>
                <th className="px-4 py-3">速率 (RPM)</th>
                <th className="px-4 py-3">状态</th>
                <th className="px-4 py-3 rounded-r-lg text-right">操作</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-800/50">
              {filteredKeys.map((key) => {
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
                      <div className="flex items-center gap-2">
                        <span className="text-xs px-2 py-0.5 rounded border border-gray-700 bg-gray-900 text-gray-300 font-mono">
                          {key.rule_group || "default"}
                        </span>
                        {(key.rule_group || "default").toLowerCase() === "default" && (
                          <span className="text-[10px] px-2 py-0.5 rounded border border-blue-700/50 bg-blue-900/20 text-blue-300">
                            System Group
                          </span>
                        )}
                      </div>
                    </td>
                    <td className="px-4 py-3">{key.daily_limit ?? "--"}</td>
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
                        onClick={() => setTestingKey(key)}
                        disabled={!isAdmin}
                        className="p-1.5 hover:bg-gray-800 rounded text-emerald-400 transition disabled:opacity-50"
                        title="测试 Key"
                      >
                        <FlaskConical size={14} />
                      </button>
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
              {filteredKeys.length === 0 && (
                <tr>
                  <td colSpan={7} className="px-4 py-8 text-center text-xs text-gray-500">
                    当前分组暂无 Key
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      {(editingKey || isAddingKey) && (
        <KeyConfigModal
          keyData={editingKey || undefined}
          endpointId={endpoint.id}
          authToken={authToken}
          isAdmin={isAdmin}
          availableRuleGroups={availableRuleGroups}
          onClose={() => {
            setEditingKey(null);
            setIsAddingKey(false);
          }}
          onSave={async (payload) => {
            let saved = false;
            if (editingKey) {
              saved = await onUpdate(editingKey.id, payload);
            } else {
              saved = await onCreate(payload);
            }
            if (saved) {
              setEditingKey(null);
              setIsAddingKey(false);
            }
            return saved;
          }}
        />
      )}
      {testingKey && (
        <ApiKeyTestModal
          apiKey={testingKey}
          endpoint={endpoint}
          endpointId={endpoint.id}
          authToken={authToken}
          isAdmin={isAdmin}
          onClose={() => setTestingKey(null)}
        />
      )}
    </div>
  );
};
