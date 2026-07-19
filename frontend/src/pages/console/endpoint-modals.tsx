import {
  ClipboardPaste,
  Edit2,
  FileJson,
  FlaskConical,
  Key,
  Plus,
  RefreshCw,
  Settings,
  Trash2,
  Upload,
  XCircle,
} from "lucide-react";
import { useMemo, useState } from "react";

import { ApiKeyTestModal } from "./api-key-test-modal";
import { normalizeCodexCredentialJson } from "./codex-credential";
import { parseRuleGroupEligibilityResult } from "./response-validators";
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

const readCodexUsageWindow = (
  usage: Record<string, unknown> | null | undefined,
  window: "primary" | "secondary"
) => {
  const raw = usage?.[window];
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
  const item = raw as Record<string, unknown>;
  return {
    usedPercent:
      typeof item.used_percent === "number" && Number.isFinite(item.used_percent)
        ? item.used_percent
        : null,
    windowMinutes:
      typeof item.window_minutes === "number" && Number.isFinite(item.window_minutes)
        ? item.window_minutes
        : null,
    resetAfterSeconds:
      typeof item.reset_after_seconds === "number" &&
      Number.isFinite(item.reset_after_seconds)
        ? item.reset_after_seconds
        : null,
  };
};

const formatCodexWindow = (fallback: string, minutes: number | null | undefined) => {
  if (!minutes) return fallback;
  if (minutes % (60 * 24 * 7) === 0) return `${minutes / (60 * 24 * 7)}w`;
  if (minutes % 60 === 0) return `${minutes / 60}h`;
  return `${minutes}m`;
};

const formatResetAfter = (
  seconds: number | null | undefined,
  updatedAt: number | null
) => {
  if (seconds == null) return null;
  const elapsed = updatedAt == null ? 0 : Math.max(0, Date.now() / 1000 - updatedAt);
  const remaining = Math.max(0, seconds - elapsed);
  if (remaining === 0) return "等待用量刷新";
  if (remaining >= 86400) return `${Math.ceil(remaining / 86400)}天后重置`;
  if (remaining >= 3600) return `${Math.ceil(remaining / 3600)}小时后重置`;
  return `${Math.max(1, Math.ceil(remaining / 60))}分钟后重置`;
};

const CodexKeyUsage = ({ usage }: { usage?: Record<string, unknown> | null }) => {
  const primary = readCodexUsageWindow(usage, "primary");
  const secondary = readCodexUsageWindow(usage, "secondary");
  const updatedAt =
    typeof usage?.updated_at === "number" && Number.isFinite(usage.updated_at)
      ? usage.updated_at
      : null;
  const rows = [
    {
      label: formatCodexWindow("5h", primary?.windowMinutes),
      percent: primary?.usedPercent ?? null,
      reset: formatResetAfter(primary?.resetAfterSeconds, updatedAt),
    },
    {
      label: formatCodexWindow("1w", secondary?.windowMinutes),
      percent: secondary?.usedPercent ?? null,
      reset: formatResetAfter(secondary?.resetAfterSeconds, updatedAt),
    },
  ];
  return (
    <div className="min-w-[150px] space-y-2">
      {rows.map((row) => (
        <div key={row.label} aria-label={`Codex ${row.label} 用量`}>
          <div className="mb-1 flex items-center justify-between gap-3 text-[10px] text-cyan-200/80">
            <span>{row.label}</span>
            <span className="font-mono">
              {row.percent == null ? "--" : `${row.percent.toFixed(1)}%`}
            </span>
          </div>
          <div className="h-1.5 overflow-hidden rounded-full bg-gray-800">
            <div
              className="h-full rounded-full bg-cyan-400"
              style={{ width: `${Math.min(Math.max(row.percent ?? 0, 0), 100)}%` }}
            />
          </div>
          {row.reset && <div className="mt-0.5 text-[9px] text-gray-600">{row.reset}</div>}
        </div>
      ))}
      {!usage && <div className="text-[10px] text-gray-600">等待首次成功请求</div>}
    </div>
  );
};

const CodexCredentialInput = ({
  value,
  onChange,
  disabled,
}: {
  value: string;
  onChange: (value: string) => void;
  disabled: boolean;
}) => {
  const [error, setError] = useState<string | null>(null);

  const importRaw = (raw: string) => {
    try {
      onChange(normalizeCodexCredentialJson(raw));
      setError(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Codex 凭据格式错误");
    }
  };

  const pasteFromClipboard = async () => {
    try {
      importRaw(await navigator.clipboard.readText());
    } catch {
      setError("无法读取剪贴板，请直接粘贴到下方文本框");
    }
  };

  return (
    <div className="space-y-2 rounded-lg border border-cyan-900/60 bg-cyan-950/10 p-3">
      <div className="flex items-center justify-between gap-2">
        <label className="flex items-center gap-1.5 text-xs font-bold uppercase text-cyan-300">
          <FileJson size={14} /> Codex Auth JSON
        </label>
        <div className="flex gap-2">
          <label className="flex cursor-pointer items-center gap-1 rounded border border-cyan-700/60 px-2 py-1 text-[11px] text-cyan-200 hover:bg-cyan-900/30">
            <Upload size={12} /> 上传 JSON
            <input
              aria-label="上传 Codex Auth JSON"
              type="file"
              accept=".json,application/json"
              className="hidden"
              disabled={disabled}
              onChange={(event) => {
                const file = event.target.files?.[0];
                if (file) {
                  void file.text().then(importRaw).catch(() => setError("读取 JSON 文件失败"));
                }
                event.target.value = "";
              }}
            />
          </label>
          <button
            type="button"
            onClick={() => void pasteFromClipboard()}
            disabled={disabled}
            className="flex items-center gap-1 rounded border border-cyan-700/60 px-2 py-1 text-[11px] text-cyan-200 hover:bg-cyan-900/30 disabled:opacity-50"
          >
            <ClipboardPaste size={12} /> 粘贴 JSON
          </button>
        </div>
      </div>
      <textarea
        aria-label="Codex Auth JSON"
        value={value}
        onChange={(event) => {
          onChange(event.target.value);
          setError(null);
        }}
        placeholder={'{\n  "access_token": "...",\n  "account_id": "...",\n  "refresh_token": "..."\n}'}
        rows={6}
        spellCheck={false}
        disabled={disabled}
        className="w-full rounded border border-gray-700 bg-gray-950 p-2 font-mono text-xs text-gray-200 outline-none focus:border-cyan-500"
      />
      <p className="text-[11px] leading-relaxed text-gray-500">
        支持 Sub2API 导出格式和 tokens 嵌套格式；保存时仅保留令牌、账号 ID 与过期时间。
      </p>
      {error && <p className="text-xs text-red-400">{error}</p>}
    </div>
  );
};

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
    initial_api_key: "",
    initial_api_key_name: "",
  });
  const [formError, setFormError] = useState<string | null>(null);

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
                  if (provider === "codex") {
                    return {
                      ...prev,
                      ...customOnly,
                      provider,
                      auth_header_name: "Authorization",
                      auth_header_prefix: "Bearer",
                      base_url: prev.base_url.trim() || "https://chatgpt.com",
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
              <option value="codex">Codex OAuth</option>
              <option value="custom">Custom Template</option>
            </select>
            {form.provider === "codex" && (
              <p className="mt-2 text-[11px] text-cyan-300/80 leading-relaxed">
                Codex OAuth 端点使用 API Key JSON 中的 access_token / refresh_token /
                account_id，转发到 Codex backend API。
              </p>
            )}
          </div>
          {form.provider === "codex" && !endpoint && (
            <div className="space-y-3 border-t border-gray-800 pt-3">
              <CodexCredentialInput
                value={form.initial_api_key ?? ""}
                onChange={(value) =>
                  setForm((prev) => ({ ...prev, initial_api_key: value }))
                }
                disabled={!isAdmin}
              />
              <div>
                <label className="mb-1.5 block text-xs font-bold uppercase text-gray-500">
                  凭据备注名称
                </label>
                <input
                  value={form.initial_api_key_name ?? ""}
                  onChange={(event) =>
                    setForm((prev) => ({
                      ...prev,
                      initial_api_key_name: event.target.value,
                    }))
                  }
                  placeholder="例如：Sub2API 导入账号"
                  disabled={!isAdmin}
                  className="w-full rounded border border-gray-700 bg-gray-900 p-2.5 text-sm text-white outline-none focus:border-cyan-500"
                />
              </div>
            </div>
          )}
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
                Agent 网络出口
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
              <option value="">主服务直连</option>
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
                此渠道的模型探测、Key 测试、Token 刷新和真实请求均通过 {form.agent_node}；
                Agent 不可用时不会回退主服务。
              </p>
            )}
            {!form.agent_node && (
              <p className="text-xs text-gray-500 mt-2">
                未配置 Agent，此渠道的所有上游请求均由主服务发出。
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
        {formError && <p className="px-6 pb-2 text-xs text-red-400">{formError}</p>}
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
              onClick={() => {
                let payload = form;
                if (form.provider === "codex" && !endpoint && form.initial_api_key?.trim()) {
                  try {
                    payload = {
                      ...form,
                      initial_api_key: normalizeCodexCredentialJson(form.initial_api_key),
                    };
                    setFormError(null);
                  } catch (reason) {
                    setFormError(
                      reason instanceof Error ? reason.message : "Codex 凭据格式错误"
                    );
                    return;
                  }
                }
                void onSave(payload);
              }}
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
  provider,
  onClose,
  onSave,
}: {
  keyData?: ApiKey;
  endpointId: number;
  authToken: string | null;
  isAdmin: boolean;
  availableRuleGroups: string[];
  provider?: string;
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
  const isCodex = provider?.trim().toLowerCase() === "codex";

  const normalizedKeyForRequest = () => {
    const raw = keyValue.trim();
    if (!raw || !isCodex) return raw;
    return normalizeCodexCredentialJson(raw);
  };

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
            api_key: keyData ? undefined : normalizedKeyForRequest() || undefined,
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
      const data = parseRuleGroupEligibilityResult(await response.json());
      if (!data) {
        setFormError("分组校验响应格式异常");
        return null;
      }
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
    let normalizedKeyValue = keyValue.trim();
    if (!keyData && isCodex) {
      try {
        normalizedKeyValue = normalizedKeyForRequest();
      } catch (reason) {
        setFormError(reason instanceof Error ? reason.message : "Codex 凭据格式错误");
        return;
      }
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
      key: normalizedKeyValue || undefined,
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
              {isCodex ? (
                <CodexCredentialInput
                  value={keyValue}
                  onChange={(value) => {
                    setKeyValue(value);
                    setFormError(null);
                    setGroupNotice(null);
                  }}
                  disabled={!isAdmin}
                />
              ) : (
                <>
                  <label className="block text-xs font-medium text-gray-400 mb-1">
                    API Key (sk-...)
                  </label>
                  <input
                    aria-label="API Key"
                    value={keyValue}
                    onChange={(event) => {
                      setKeyValue(event.target.value);
                      if (formError) setFormError(null);
                      if (groupNotice) setGroupNotice(null);
                    }}
                    placeholder="sk-..."
                    className="w-full bg-gray-900 border border-gray-600 rounded p-2 text-sm text-white focus:border-green-500 outline-none"
                    disabled={!isAdmin}
                  />
                </>
              )}
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
      <div className="bg-[#0f1117] border border-gray-800 rounded-xl w-[1050px] max-w-[95vw] max-h-[85vh] flex flex-col shadow-2xl animate-in fade-in zoom-in-95 duration-200">
        <div className="p-6 border-b border-gray-800 flex justify-between items-center">
          <div>
            <h3 className="text-xl font-bold text-white flex items-center gap-2">
              <Key size={20} className="text-blue-500" />
              管理 API Keys
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
                {endpoint.provider === "codex" && (
                  <th className="px-4 py-3">Codex 用量</th>
                )}
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
                    {endpoint.provider === "codex" && (
                      <td className="px-4 py-3">
                        <CodexKeyUsage usage={key.codex_usage} />
                      </td>
                    )}
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
                  <td
                    colSpan={endpoint.provider === "codex" ? 8 : 7}
                    className="px-4 py-8 text-center text-xs text-gray-500"
                  >
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
          provider={endpoint.provider}
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
