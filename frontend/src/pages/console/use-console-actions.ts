import { type Dispatch, type SetStateAction } from "react";

import {
  apiBase,
  buildHeaders,
  type AgentBootstrapResult,
  type AgentDeployFormState,
  type AgentNode,
  type ApiKey,
  type Endpoint,
  type EndpointFormState,
  type ModelMap,
  type RoutingRule,
  type RoutingRuleSavePayload,
} from "./shared";
import {
  parseAgentBootstrapResult,
  parseEndpointProbeResult,
  parseModelMap,
  parseModelMapList,
} from "./response-validators";

type UseConsoleActionsOptions = {
  token: string | null;
  isAdmin: boolean;
  editingEndpoint: Endpoint | undefined | null;
  setEditingEndpoint: Dispatch<SetStateAction<Endpoint | undefined | null>>;
  manageKeysEndpoint: Endpoint | null;
  setManageKeysEndpoint: Dispatch<SetStateAction<Endpoint | null>>;
  editingRule: RoutingRule | undefined | null;
  setEditingRule: Dispatch<SetStateAction<RoutingRule | undefined | null>>;
  probeAliasEdits: Record<number, string>;
  setProbeEndpoint: Dispatch<SetStateAction<Endpoint | null>>;
  setProbeModels: Dispatch<SetStateAction<ModelMap[]>>;
  setProbeDiscoveredModels: Dispatch<SetStateAction<string[]>>;
  setProbeAliasEdits: Dispatch<SetStateAction<Record<number, string>>>;
  setProbeError: Dispatch<SetStateAction<string | null>>;
  setProbeLoading: Dispatch<SetStateAction<boolean>>;
  setAgentDeployResult: Dispatch<SetStateAction<AgentBootstrapResult | null>>;
  setAgentDeployTarget: Dispatch<SetStateAction<AgentNode | null>>;
  setAgentDeployOpen: Dispatch<SetStateAction<boolean>>;
  loadEndpoints: (authToken: string | null) => Promise<void>;
  loadHealthStatus: (authToken: string | null) => Promise<void>;
  loadRules: (authToken: string | null) => Promise<void>;
  loadAgents: (authToken: string | null) => Promise<void>;
};

export const readApiErrorMessage = async (
  response: Response,
  fallback: string
): Promise<string> => {
  try {
    const data = (await response.json()) as {
      detail?: unknown;
      message?: unknown;
    };
    if (typeof data.detail === "string" && data.detail.trim()) {
      return data.detail;
    }
    if (Array.isArray(data.detail)) {
      const details = data.detail
        .map((item) => {
          if (item && typeof item === "object" && "msg" in item) {
            return String((item as { msg?: unknown }).msg || "").trim();
          }
          return "";
        })
        .filter(Boolean);
      if (details.length > 0) {
        return details.join("; ");
      }
    }
    if (typeof data.message === "string" && data.message.trim()) {
      return data.message;
    }
  } catch {
    // ignore parse errors
  }
  return fallback;
};

const notifyApiError = async (response: Response, fallback: string) => {
  alert(await readApiErrorMessage(response, fallback));
};

export const useConsoleActions = ({
  token,
  isAdmin,
  editingEndpoint,
  setEditingEndpoint,
  manageKeysEndpoint,
  setManageKeysEndpoint,
  editingRule,
  setEditingRule,
  probeAliasEdits,
  setProbeEndpoint,
  setProbeModels,
  setProbeDiscoveredModels,
  setProbeAliasEdits,
  setProbeError,
  setProbeLoading,
  setAgentDeployResult,
  setAgentDeployTarget,
  setAgentDeployOpen,
  loadEndpoints,
  loadHealthStatus,
  loadRules,
  loadAgents,
}: UseConsoleActionsOptions) => {
  const refreshKeys = async () => {
    await loadEndpoints(token);
    await loadHealthStatus(token);
    await loadRules(token);
  };

  const resetProbeState = () => {
    setProbeEndpoint(null);
    setProbeModels([]);
    setProbeDiscoveredModels([]);
    setProbeAliasEdits({});
    setProbeError(null);
    setProbeLoading(false);
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
    const data = parseAgentBootstrapResult(await response.json());
    if (!data) {
      throw new Error("生成部署命令失败：响应格式异常");
    }
    await loadAgents(token);
    return data;
  };

  const handleSaveEndpoint = async (payload: EndpointFormState): Promise<boolean> => {
    if (!isAdmin) return false;
    const probeRaw = payload.probe_interval_seconds.trim();
    const probeInterval = probeRaw === "" ? null : Number(probeRaw);
    if (
      probeRaw !== "" &&
      (!Number.isFinite(probeInterval) || probeInterval < -1 || probeInterval > 86400)
    ) {
      alert("探针间隔需在 -1 到 86400 秒之间（-1 表示禁用自动探针）。");
      return false;
    }

    const method = editingEndpoint ? "PATCH" : "POST";
    const url = editingEndpoint
      ? `${apiBase}/admin/endpoints/${editingEndpoint.id}`
      : `${apiBase}/admin/endpoints`;
    const isCustomProvider = payload.provider === "custom";
    try {
      const response = await fetch(url, {
        method,
        headers: buildHeaders(token, true),
        body: JSON.stringify({
          name: payload.name,
          base_url: payload.base_url,
          auth_header_name: payload.auth_header_name,
          auth_header_prefix: payload.auth_header_prefix,
          provider: payload.provider,
          access_mode: payload.agent_node ? "via_agent" : "direct",
          agent_node: payload.agent_node || null,
          probe_interval_seconds: probeInterval,
          is_active: payload.is_active,
          url_path_suffix: isCustomProvider ? payload.url_path_suffix || null : null,
          extra_headers: isCustomProvider ? payload.extra_headers || null : null,
          extra_cookies: isCustomProvider ? payload.extra_cookies || null : null,
          extra_query_params: isCustomProvider ? payload.extra_query_params || null : null,
          oauth_config: isCustomProvider ? payload.oauth_config || null : null,
          request_body_template: isCustomProvider
            ? payload.request_body_template || null
            : null,
        }),
      });
      if (!response.ok) {
        await notifyApiError(response, "保存 API 端点失败");
        return false;
      }
      const savedEndpoint = (await response.json()) as { id?: number };
      if (!editingEndpoint && payload.provider === "codex" && payload.initial_api_key) {
        const endpointId = Number(savedEndpoint.id);
        if (!Number.isInteger(endpointId) || endpointId <= 0) {
          alert("端点已创建，但响应中缺少端点 ID，无法导入 Codex 凭据");
          await loadEndpoints(token);
          return false;
        }
        const keyResponse = await fetch(`${apiBase}/admin/endpoints/${endpointId}/keys`, {
          method: "POST",
          headers: buildHeaders(token, true),
          body: JSON.stringify({
            key: payload.initial_api_key,
            name: payload.initial_api_key_name?.trim() || "Codex Auth JSON",
            rule_group: "default",
            rule_groups: ["default"],
            is_active: true,
          }),
        });
        if (!keyResponse.ok) {
          await notifyApiError(keyResponse, "端点已创建，但 Codex 凭据导入失败");
          await loadEndpoints(token);
          return false;
        }
        const probeResponse = await fetch(`${apiBase}/admin/endpoints/${endpointId}/probe`, {
          method: "POST",
          headers: buildHeaders(token),
        });
        if (!probeResponse.ok) {
          await notifyApiError(probeResponse, "Codex 凭据已导入，但首次模型探测失败");
        }
      }
      await loadEndpoints(token);
      setEditingEndpoint(null);
      return true;
    } catch {
      alert("保存 API 端点失败");
      return false;
    }
  };

  const handleCreateKeyForEndpoint = async (
    endpointId: number,
    payload: Partial<ApiKey> & { key?: string }
  ): Promise<boolean> => {
    if (!isAdmin || endpointId <= 0) return false;
    try {
      const response = await fetch(`${apiBase}/admin/endpoints/${endpointId}/keys`, {
        method: "POST",
        headers: buildHeaders(token, true),
        body: JSON.stringify({
          key: payload.key,
          name: payload.name,
          rule_group: payload.rule_group || "default",
          rule_groups: payload.rule_groups,
          rpm_limit: payload.rpm_limit,
          daily_limit: payload.daily_limit,
          used_today: payload.used_today ?? 0,
          total_usage: 0,
          is_active: payload.is_active ?? true,
        }),
      });
      if (!response.ok) {
        await notifyApiError(response, "创建 API Key 失败");
        return false;
      }
      if (
        manageKeysEndpoint?.id === endpointId &&
        manageKeysEndpoint.provider.trim().toLowerCase() === "codex"
      ) {
        const probeResponse = await fetch(`${apiBase}/admin/endpoints/${endpointId}/probe`, {
          method: "POST",
          headers: buildHeaders(token),
        });
        if (!probeResponse.ok) {
          await notifyApiError(probeResponse, "Codex 凭据已创建，但模型探测失败");
        }
      }
      await refreshKeys();
      return true;
    } catch {
      alert("创建 API Key 失败");
      return false;
    }
  };

  const handleCreateKey = async (payload: Partial<ApiKey> & { key?: string }) => {
    if (!manageKeysEndpoint) return false;
    return handleCreateKeyForEndpoint(manageKeysEndpoint.id, payload);
  };

  const handleUpdateKey = async (
    keyId: number,
    payload: Partial<ApiKey> & { key?: string }
  ): Promise<boolean> => {
    try {
      const response = await fetch(`${apiBase}/admin/keys/${keyId}`, {
        method: "PUT",
        headers: buildHeaders(token, true),
        body: JSON.stringify({
          key: payload.key,
          name: payload.name,
          rule_group: payload.rule_group,
          rule_groups: payload.rule_groups,
          rpm_limit: payload.rpm_limit,
          daily_limit: payload.daily_limit,
          is_active: payload.is_active,
        }),
      });
      if (!response.ok) {
        await notifyApiError(response, "更新 API Key 失败");
        return false;
      }
      await refreshKeys();
      return true;
    } catch {
      alert("更新 API Key 失败");
      return false;
    }
  };

  const handleDeleteKey = async (keyId: number) => {
    if (!isAdmin) return;
    if (!window.confirm("确认删除该 API Key 吗？")) return;
    try {
      const response = await fetch(`${apiBase}/admin/api-keys/${keyId}`, {
        method: "DELETE",
        headers: buildHeaders(token),
      });
      if (!response.ok) {
        await notifyApiError(response, "删除 API Key 失败");
        return;
      }
      await refreshKeys();
    } catch {
      alert("删除 API Key 失败");
    }
  };

  const handleProbeEndpoint = async (endpoint: Endpoint) => {
    if (!isAdmin) return;
    setProbeEndpoint(endpoint);
    setProbeModels([]);
    setProbeDiscoveredModels([]);
    setProbeError(null);
    setProbeLoading(true);
    try {
      const response = await fetch(`${apiBase}/admin/endpoints/${endpoint.id}/probe`, {
        method: "POST",
        headers: buildHeaders(token),
      });
      if (!response.ok) {
        let message = "探测失败，请稍后再试。";
        try {
          const data = (await response.json()) as { detail?: string };
          if (data?.detail) {
            message = data.detail;
          }
        } catch {
          // ignore parse errors
        }
        setProbeError(message);
        return;
      }
      const responsePayload = await response.json();
      const payload = parseEndpointProbeResult(responsePayload);
      if (payload === null) {
        // 兼容旧后端：旧版 probe 接口直接返回 ModelMap[]
        const legacyModels = parseModelMapList(responsePayload);
        if (legacyModels === null) {
          setProbeError("探测结果格式异常。");
          return;
        }
        setProbeModels(legacyModels);
        setProbeDiscoveredModels(
          Array.from(new Set(legacyModels.map((item) => item.real_model || item.model_alias)))
        );
        setProbeError(null);
        const aliasSeed: Record<number, string> = {};
        legacyModels.forEach((model) => {
          aliasSeed[model.id] = model.model_alias;
        });
        setProbeAliasEdits(aliasSeed);
      } else {
        const mappingModels = payload.manual_models ?? [];
        setProbeModels(mappingModels);
        setProbeDiscoveredModels(payload.discovered_models ?? []);
        setProbeError(payload.probe_message || null);
        const aliasSeed: Record<number, string> = {};
        mappingModels.forEach((model) => {
          aliasSeed[model.id] = model.model_alias;
        });
        setProbeAliasEdits(aliasSeed);
      }
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
    try {
      const response = await fetch(`${apiBase}/admin/endpoints/${endpoint.id}`, {
        method: "DELETE",
        headers: buildHeaders(token),
      });
      if (!response.ok) {
        await notifyApiError(response, "删除 API 端点失败");
        return;
      }
      await loadEndpoints(token);
      if (manageKeysEndpoint?.id === endpoint.id) {
        setManageKeysEndpoint(null);
      }
      if (editingEndpoint?.id === endpoint.id) {
        setEditingEndpoint(null);
      }
    } catch {
      alert("删除 API 端点失败");
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
    const updated = parseModelMap(await response.json());
    if (!updated) {
      setProbeError("别名保存失败：响应格式异常。");
      return;
    }
    setProbeModels((prev) =>
      prev.map((item) => (item.id === updated.id ? updated : item))
    );
    setProbeAliasEdits((prev) => ({ ...prev, [updated.id]: updated.model_alias }));
    setProbeError(null);
  };

  const handleCreateProbeModel = async (
    endpointId: number,
    modelAlias: string,
    realModel: string
  ) => {
    if (!isAdmin) return;
    const alias = modelAlias.trim();
    const real = realModel.trim();
    if (!alias || !real) {
      setProbeError("请填写模型别名和真实模型。");
      return;
    }
    const response = await fetch(`${apiBase}/admin/model-maps`, {
      method: "POST",
      headers: buildHeaders(token, true),
      body: JSON.stringify({
        endpoint_id: endpointId,
        model_alias: alias,
        real_model: real,
      }),
    });
    if (!response.ok) {
      let message = "新增模型失败，请稍后再试。";
      try {
        const data = (await response.json()) as { detail?: string };
        if (data?.detail) {
          message = data.detail;
        }
      } catch {
        // ignore parse errors
      }
      setProbeError(message);
      return;
    }
    const created = parseModelMap(await response.json());
    if (!created) {
      setProbeError("新增模型失败：响应格式异常。");
      return;
    }
    setProbeModels((prev) => [created, ...prev]);
    setProbeAliasEdits((prev) => ({ ...prev, [created.id]: created.model_alias }));
    setProbeError(null);
    await loadEndpoints(token);
  };

  const handleDeleteProbeModel = async (model: ModelMap) => {
    if (!isAdmin) return;
    const response = await fetch(`${apiBase}/admin/model-maps/${model.id}`, {
      method: "DELETE",
      headers: buildHeaders(token),
    });
    if (!response.ok) {
      setProbeError("删除模型失败，请稍后再试。");
      return;
    }
    setProbeModels((prev) => prev.filter((item) => item.id !== model.id));
    setProbeAliasEdits((prev) => {
      const next = { ...prev };
      delete next[model.id];
      return next;
    });
    await loadEndpoints(token);
  };

  const handleSaveRule = async (
    payload: RoutingRuleSavePayload
  ): Promise<boolean> => {
    if (!isAdmin) return false;
    const method = payload.id ? "PATCH" : "POST";
    const url = payload.id
      ? `${apiBase}/admin/rules/${payload.id}`
      : `${apiBase}/admin/rules`;
    try {
      const response = await fetch(url, {
        method,
        headers: buildHeaders(token, true),
        body: JSON.stringify({
          model_pattern: payload.model_pattern,
          group_name: payload.group_name,
          exposure_format: payload.exposure_format,
          priority: payload.priority,
          strategy: payload.strategy,
          is_active: payload.is_active,
          dump_enabled: payload.dump_enabled,
          dump_path: payload.dump_path,
          target_key_ids: payload.target_key_ids,
        }),
      });
      if (!response.ok) {
        await notifyApiError(response, "保存路由规则失败");
        return false;
      }
      await loadRules(token);
      setEditingRule(null);
      return true;
    } catch {
      alert("保存路由规则失败");
      return false;
    }
  };

  const handleDeleteRule = async (rule: RoutingRule) => {
    if (!isAdmin) return;
    if (!window.confirm(`确认删除路由规则 "${rule.model_pattern}" 吗？`)) {
      return;
    }
    try {
      const response = await fetch(`${apiBase}/admin/rules/${rule.id}`, {
        method: "DELETE",
        headers: buildHeaders(token),
      });
      if (!response.ok) {
        await notifyApiError(response, "删除路由规则失败");
        return;
      }
      await loadRules(token);
      if (editingRule?.id === rule.id) {
        setEditingRule(null);
      }
    } catch {
      alert("删除路由规则失败");
    }
  };

  const handleDeleteAgent = async (agent: AgentNode) => {
    if (!isAdmin) return;
    if (!window.confirm(`确认删除 Agent 节点 "${agent.name}" 吗？`)) {
      return;
    }
    try {
      const response = await fetch(`${apiBase}/admin/agents/${agent.id}`, {
        method: "DELETE",
        headers: buildHeaders(token),
      });
      if (!response.ok) {
        await notifyApiError(response, "删除 Agent 节点失败");
        return;
      }
      await loadAgents(token);
    } catch {
      alert("删除 Agent 节点失败");
    }
  };

  const handleRotateAgentToken = async (agent: AgentNode) => {
    if (!isAdmin) return;
    if (agent.status !== "offline") {
      alert(
        `Agent "${agent.name}" 已部署，无法重新生成Token。如需重新部署，请先删除该Agent并创建新的。`
      );
      return;
    }

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
      const data = parseAgentBootstrapResult(await response.json());
      if (!data) {
        throw new Error("重新生成Token失败：响应格式异常");
      }
      await loadAgents(token);
      setAgentDeployResult(data);
      setAgentDeployTarget(agent);
      setAgentDeployOpen(true);
    } catch (err) {
      console.error("Failed to rotate token:", err);
      alert(err instanceof Error ? err.message : "重新生成Token失败");
    }
  };

  const handleSetAgentState = async (
    agent: AgentNode,
    action: "enable" | "drain" | "disable"
  ) => {
    if (!isAdmin) return;
    try {
      const response = await fetch(`${apiBase}/admin/agents/${agent.id}/${action}`, {
        method: "POST",
        headers: buildHeaders(token),
      });
      if (!response.ok) {
        const label =
          action === "enable" ? "启用" : action === "drain" ? "Drain" : "禁用";
        await notifyApiError(response, `${label} Agent 节点失败`);
        return;
      }
      await loadAgents(token);
    } catch {
      const label =
        action === "enable" ? "启用" : action === "drain" ? "Drain" : "禁用";
      alert(`${label} Agent 节点失败`);
    }
  };

  return {
    refreshKeys,
    resetProbeState,
    handleAgentBootstrap,
    handleSaveEndpoint,
    handleCreateKeyForEndpoint,
    handleCreateKey,
    handleUpdateKey,
    handleDeleteKey,
    handleProbeEndpoint,
    handleDeleteEndpoint,
    handleUpdateModelAlias,
    handleCreateProbeModel,
    handleDeleteProbeModel,
    handleSaveRule,
    handleDeleteRule,
    handleDeleteAgent,
    handleRotateAgentToken,
    handleSetAgentState,
  };
};
