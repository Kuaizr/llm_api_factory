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
  type EndpointProbeResult,
  type ModelMap,
  type RoutingRule,
  type RoutingRuleSavePayload,
} from "./shared";

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
    const data = (await response.json()) as AgentBootstrapResult;
    await loadAgents(token);
    return data;
  };

  const handleSaveEndpoint = async (payload: EndpointFormState) => {
    if (!isAdmin) return;
    const probeRaw = payload.probe_interval_seconds.trim();
    const probeInterval = probeRaw === "" ? null : Number(probeRaw);
    if (
      probeRaw !== "" &&
      (!Number.isFinite(probeInterval) || probeInterval < -1 || probeInterval > 86400)
    ) {
      alert("探针间隔需在 -1 到 86400 秒之间（-1 表示禁用自动探针）。");
      return;
    }

    const method = editingEndpoint ? "PATCH" : "POST";
    const url = editingEndpoint
      ? `${apiBase}/admin/endpoints/${editingEndpoint.id}`
      : `${apiBase}/admin/endpoints`;
    const isCustomProvider = payload.provider === "custom";
    const response = await fetch(url, {
      method,
      headers: buildHeaders(token, true),
      body: JSON.stringify({
        name: payload.name,
        base_url: payload.base_url,
        auth_header_name: payload.auth_header_name,
        auth_header_prefix: payload.auth_header_prefix,
        provider: payload.provider,
        agent_node: payload.agent_node,
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
    if (response.ok) {
      await loadEndpoints(token);
    }
    setEditingEndpoint(null);
  };

  const handleCreateKeyForEndpoint = async (
    endpointId: number,
    payload: Partial<ApiKey> & { key?: string }
  ) => {
    if (!isAdmin || endpointId <= 0) return;
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
    if (response.ok) {
      await refreshKeys();
    }
  };

  const handleCreateKey = async (payload: Partial<ApiKey> & { key?: string }) => {
    if (!manageKeysEndpoint) return;
    await handleCreateKeyForEndpoint(manageKeysEndpoint.id, payload);
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
        rule_group: payload.rule_group,
        rule_groups: payload.rule_groups,
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
      const payload = (await response.json()) as EndpointProbeResult | ModelMap[];
      if (Array.isArray(payload)) {
        // 兼容旧后端：旧版 probe 接口直接返回 ModelMap[]
        const legacyModels = payload;
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
    const updated = (await response.json()) as ModelMap;
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
    const created = (await response.json()) as ModelMap;
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

  const handleSaveRule = async (payload: RoutingRuleSavePayload) => {
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
        dump_enabled: payload.dump_enabled,
        dump_path: payload.dump_path,
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
      const data = (await response.json()) as AgentBootstrapResult;
      await loadAgents(token);
      setAgentDeployResult(data);
      setAgentDeployTarget(agent);
      setAgentDeployOpen(true);
    } catch (err) {
      console.error("Failed to rotate token:", err);
      alert(err instanceof Error ? err.message : "重新生成Token失败");
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
  };
};
