import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

type Endpoint = {
  id: number;
  name: string;
  base_url: string;
  auth_header_name: string;
  auth_header_prefix: string;
  agent_name: string | null;
  is_active: boolean;
  created_at: string;
};

type APIKey = {
  id: number;
  endpoint_id: number;
  key: string;
  rule_group: string;
  weight: number;
  is_active: boolean;
  created_at: string;
};

type ModelMap = {
  id: number;
  endpoint_id: number;
  model_alias: string;
  real_model: string;
  created_at: string;
};

type EndpointDraft = {
  name: string;
  base_url: string;
  auth_header_name: string;
  auth_header_prefix: string;
  agent_name: string;
};

type ApiKeyDraft = {
  rule_group: string;
  weight: string;
};

type ModelMapDraft = {
  model_alias: string;
  real_model: string;
};

const apiBase = import.meta.env.VITE_API_BASE ?? "http://localhost:8000";
const adminToken = import.meta.env.VITE_ADMIN_TOKEN;

const inputClass =
  "w-full rounded-md border border-muted bg-background/60 px-3 py-2 text-sm text-foreground";
const tableInputClass =
  "w-full rounded-md border border-muted bg-background/60 px-2 py-1 text-xs text-foreground";

const authHeaders = () => {
  const headers: Record<string, string> = {};
  if (adminToken) {
    headers.Authorization = `Bearer ${adminToken}`;
  }
  return headers;
};

const jsonHeaders = () => ({
  ...authHeaders(),
  "Content-Type": "application/json",
});

const maskKey = (key: string) => {
  if (key.length <= 8) {
    return key;
  }
  return `${key.slice(0, 4)}...${key.slice(-4)}`;
};

export const Assets = () => {
  const [endpoints, setEndpoints] = useState<Endpoint[]>([]);
  const [apiKeys, setApiKeys] = useState<APIKey[]>([]);
  const [modelMaps, setModelMaps] = useState<ModelMap[]>([]);
  const [endpointDrafts, setEndpointDrafts] = useState<Record<number, EndpointDraft>>(
    {}
  );
  const [apiKeyDrafts, setApiKeyDrafts] = useState<Record<number, ApiKeyDraft>>({});
  const [modelMapDrafts, setModelMapDrafts] = useState<Record<number, ModelMapDraft>>(
    {}
  );
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [apiKeyFilterEndpoint, setApiKeyFilterEndpoint] = useState("all");
  const [apiKeyFilterRuleGroup, setApiKeyFilterRuleGroup] = useState("all");

  const [endpointForm, setEndpointForm] = useState({
    name: "",
    base_url: "",
    auth_header_name: "Authorization",
    auth_header_prefix: "Bearer",
    agent_name: "",
  });
  const [apiKeyForm, setApiKeyForm] = useState({
    endpoint_id: "",
    key: "",
    rule_group: "default",
    weight: "1",
  });
  const [modelMapForm, setModelMapForm] = useState({
    endpoint_id: "",
    model_alias: "",
    real_model: "",
  });

  const loadAssets = async () => {
    setLoading(true);
    setError(null);
    try {
      const [endpointRes, apiKeyRes, modelMapRes] = await Promise.all([
        fetch(`${apiBase}/admin/endpoints`, { headers: authHeaders() }),
        fetch(`${apiBase}/admin/api-keys`, { headers: authHeaders() }),
        fetch(`${apiBase}/admin/model-maps`, { headers: authHeaders() }),
      ]);

      if (!endpointRes.ok || !apiKeyRes.ok || !modelMapRes.ok) {
        throw new Error("failed");
      }

      const [endpointData, apiKeyData, modelMapData] = await Promise.all([
        endpointRes.json(),
        apiKeyRes.json(),
        modelMapRes.json(),
      ]);

      setEndpoints(endpointData);
      setApiKeys(apiKeyData);
      setModelMaps(modelMapData);
      setEndpointDrafts({});
      setApiKeyDrafts({});
      setModelMapDrafts({});
    } catch (err) {
      setError("无法获取资产数据");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadAssets();
  }, []);

  const createEndpoint = async () => {
    if (!endpointForm.name || !endpointForm.base_url) {
      setError("请输入 Endpoint 名称与地址");
      return;
    }
    setError(null);
    await fetch(`${apiBase}/admin/endpoints`, {
      method: "POST",
      headers: jsonHeaders(),
      body: JSON.stringify({
        ...endpointForm,
        agent_name: endpointForm.agent_name.trim()
          ? endpointForm.agent_name.trim()
          : null,
      }),
    });
    setEndpointForm({
      name: "",
      base_url: "",
      auth_header_name: "Authorization",
      auth_header_prefix: "Bearer",
      agent_name: "",
    });
    await loadAssets();
  };

  const saveEndpoint = async (endpoint: Endpoint) => {
    const draft = endpointDrafts[endpoint.id] ?? {
      name: endpoint.name,
      base_url: endpoint.base_url,
      auth_header_name: endpoint.auth_header_name,
      auth_header_prefix: endpoint.auth_header_prefix,
      agent_name: endpoint.agent_name ?? "",
    };
    if (!draft.name || !draft.base_url) {
      setError("Endpoint 名称或地址不能为空");
      return;
    }
    await fetch(`${apiBase}/admin/endpoints/${endpoint.id}`, {
      method: "PATCH",
      headers: jsonHeaders(),
      body: JSON.stringify({
        ...draft,
        agent_name: draft.agent_name.trim() ? draft.agent_name.trim() : null,
      }),
    });
    await loadAssets();
  };

  const updateEndpointStatus = async (endpoint: Endpoint) => {
    await fetch(`${apiBase}/admin/endpoints/${endpoint.id}`, {
      method: "PATCH",
      headers: jsonHeaders(),
      body: JSON.stringify({ is_active: !endpoint.is_active }),
    });
    await loadAssets();
  };

  const createApiKey = async () => {
    if (!apiKeyForm.endpoint_id || !apiKeyForm.key) {
      setError("请输入 Endpoint ID 与 Key");
      return;
    }
    const endpointId = Number(apiKeyForm.endpoint_id);
    const weight = Number(apiKeyForm.weight || 1);
    if (Number.isNaN(endpointId) || Number.isNaN(weight)) {
      setError("Endpoint ID 或权重格式错误");
      return;
    }
    setError(null);
    await fetch(`${apiBase}/admin/api-keys`, {
      method: "POST",
      headers: jsonHeaders(),
      body: JSON.stringify({
        endpoint_id: endpointId,
        key: apiKeyForm.key,
        rule_group: apiKeyForm.rule_group || "default",
        weight,
      }),
    });
    setApiKeyForm({ endpoint_id: "", key: "", rule_group: "default", weight: "1" });
    await loadAssets();
  };

  const saveApiKey = async (item: APIKey) => {
    const draft = apiKeyDrafts[item.id] ?? {
      rule_group: item.rule_group,
      weight: String(item.weight),
    };
    const weight = Number(draft.weight);
    if (Number.isNaN(weight)) {
      setError("权重格式错误");
      return;
    }
    await fetch(`${apiBase}/admin/api-keys/${item.id}`, {
      method: "PATCH",
      headers: jsonHeaders(),
      body: JSON.stringify({ rule_group: draft.rule_group, weight }),
    });
    await loadAssets();
  };

  const toggleApiKeyStatus = async (item: APIKey) => {
    await fetch(`${apiBase}/admin/api-keys/${item.id}`, {
      method: "PATCH",
      headers: jsonHeaders(),
      body: JSON.stringify({ is_active: !item.is_active }),
    });
    await loadAssets();
  };

  const createModelMap = async () => {
    if (!modelMapForm.endpoint_id || !modelMapForm.model_alias || !modelMapForm.real_model) {
      setError("请输入 Endpoint ID 与模型映射");
      return;
    }
    const endpointId = Number(modelMapForm.endpoint_id);
    if (Number.isNaN(endpointId)) {
      setError("Endpoint ID 格式错误");
      return;
    }
    setError(null);
    await fetch(`${apiBase}/admin/model-maps`, {
      method: "POST",
      headers: jsonHeaders(),
      body: JSON.stringify({
        endpoint_id: endpointId,
        model_alias: modelMapForm.model_alias,
        real_model: modelMapForm.real_model,
      }),
    });
    setModelMapForm({ endpoint_id: "", model_alias: "", real_model: "" });
    await loadAssets();
  };

  const saveModelMap = async (item: ModelMap) => {
    const draft = modelMapDrafts[item.id] ?? {
      model_alias: item.model_alias,
      real_model: item.real_model,
    };
    if (!draft.model_alias || !draft.real_model) {
      setError("模型映射不能为空");
      return;
    }
    await fetch(`${apiBase}/admin/model-maps/${item.id}`, {
      method: "PATCH",
      headers: jsonHeaders(),
      body: JSON.stringify(draft),
    });
    await loadAssets();
  };

  const deleteEndpoint = async (id: number) => {
    await fetch(`${apiBase}/admin/endpoints/${id}`, {
      method: "DELETE",
      headers: authHeaders(),
    });
    await loadAssets();
  };

  const deleteApiKey = async (id: number) => {
    await fetch(`${apiBase}/admin/api-keys/${id}`, {
      method: "DELETE",
      headers: authHeaders(),
    });
    await loadAssets();
  };

  const deleteModelMap = async (id: number) => {
    await fetch(`${apiBase}/admin/model-maps/${id}`, {
      method: "DELETE",
      headers: authHeaders(),
    });
    await loadAssets();
  };

  const ruleGroups = Array.from(
    new Set(apiKeys.map((item) => item.rule_group).filter(Boolean))
  ).sort();
  const filteredApiKeys = apiKeys.filter((item) => {
    const endpointMatches =
      apiKeyFilterEndpoint === "all" ||
      item.endpoint_id === Number(apiKeyFilterEndpoint);
    const ruleGroupMatches =
      apiKeyFilterRuleGroup === "all" ||
      item.rule_group === apiKeyFilterRuleGroup;
    return endpointMatches && ruleGroupMatches;
  });

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle>API 资产</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <p className="text-sm text-zinc-300">
            在这里管理 Endpoint、API Key 与模型映射。
          </p>
          <div className="flex items-center gap-3 text-sm text-zinc-400">
            <span>{loading ? "加载中..." : "数据已更新"}</span>
            <Button variant="outline" onClick={loadAssets}>
              刷新
            </Button>
          </div>
          {error ? <p className="text-sm text-red-400">{error}</p> : null}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Endpoint</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid gap-3 md:grid-cols-3">
            <input
              className={inputClass}
              placeholder="名称"
              value={endpointForm.name}
              onChange={(event) =>
                setEndpointForm((prev) => ({ ...prev, name: event.target.value }))
              }
            />
            <input
              className={inputClass}
              placeholder="Base URL"
              value={endpointForm.base_url}
              onChange={(event) =>
                setEndpointForm((prev) => ({ ...prev, base_url: event.target.value }))
              }
            />
            <input
              className={inputClass}
              placeholder="Auth Header Name"
              value={endpointForm.auth_header_name}
              onChange={(event) =>
                setEndpointForm((prev) => ({
                  ...prev,
                  auth_header_name: event.target.value,
                }))
              }
            />
            <input
              className={inputClass}
              placeholder="Auth Header Prefix"
              value={endpointForm.auth_header_prefix}
              onChange={(event) =>
                setEndpointForm((prev) => ({
                  ...prev,
                  auth_header_prefix: event.target.value,
                }))
              }
            />
            <input
              className={inputClass}
              placeholder="Agent 名称（可选）"
              value={endpointForm.agent_name}
              onChange={(event) =>
                setEndpointForm((prev) => ({
                  ...prev,
                  agent_name: event.target.value,
                }))
              }
            />
          </div>
          <Button onClick={createEndpoint}>新增 Endpoint</Button>
          {endpoints.length === 0 ? (
            <p className="text-sm text-zinc-400">暂无 Endpoint。</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="text-left text-zinc-400">
                  <tr>
                    <th className="py-2">名称</th>
                    <th>地址</th>
                    <th>认证头</th>
                    <th>Agent</th>
                    <th>状态</th>
                    <th className="text-right">操作</th>
                  </tr>
                </thead>
                <tbody>
                  {endpoints.map((endpoint) => {
                    const draft = endpointDrafts[endpoint.id] ?? {
                      name: endpoint.name,
                      base_url: endpoint.base_url,
                      auth_header_name: endpoint.auth_header_name,
                      auth_header_prefix: endpoint.auth_header_prefix,
                      agent_name: endpoint.agent_name ?? "",
                    };

                    return (
                      <tr key={endpoint.id} className="border-t border-muted">
                        <td className="py-2">
                          <input
                            className={tableInputClass}
                            value={draft.name}
                            onChange={(event) =>
                              setEndpointDrafts((prev) => ({
                                ...prev,
                                [endpoint.id]: {
                                  ...draft,
                                  name: event.target.value,
                                },
                              }))
                            }
                          />
                        </td>
                        <td>
                          <input
                            className={tableInputClass}
                            value={draft.base_url}
                            onChange={(event) =>
                              setEndpointDrafts((prev) => ({
                                ...prev,
                                [endpoint.id]: {
                                  ...draft,
                                  base_url: event.target.value,
                                },
                              }))
                            }
                          />
                        </td>
                        <td>
                          <div className="flex gap-2">
                            <input
                              className={tableInputClass}
                              value={draft.auth_header_name}
                              onChange={(event) =>
                                setEndpointDrafts((prev) => ({
                                  ...prev,
                                  [endpoint.id]: {
                                    ...draft,
                                    auth_header_name: event.target.value,
                                  },
                                }))
                              }
                            />
                            <input
                              className={tableInputClass}
                              value={draft.auth_header_prefix}
                              onChange={(event) =>
                                setEndpointDrafts((prev) => ({
                                  ...prev,
                                  [endpoint.id]: {
                                    ...draft,
                                    auth_header_prefix: event.target.value,
                                  },
                                }))
                              }
                            />
                          </div>
                        </td>
                        <td>
                          <input
                            className={tableInputClass}
                            value={draft.agent_name}
                            onChange={(event) =>
                              setEndpointDrafts((prev) => ({
                                ...prev,
                                [endpoint.id]: {
                                  ...draft,
                                  agent_name: event.target.value,
                                },
                              }))
                            }
                          />
                        </td>
                        <td>{endpoint.is_active ? "启用" : "停用"}</td>
                        <td className="py-2 text-right">
                          <div className="flex justify-end gap-2">
                            <Button
                              variant="outline"
                              onClick={() => saveEndpoint(endpoint)}
                            >
                              保存
                            </Button>
                            <Button
                              variant="outline"
                              onClick={() => updateEndpointStatus(endpoint)}
                            >
                              {endpoint.is_active ? "停用" : "启用"}
                            </Button>
                            <Button
                              variant="outline"
                              onClick={() => deleteEndpoint(endpoint.id)}
                            >
                              删除
                            </Button>
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>API Key</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid gap-3 md:grid-cols-4">
            <input
              className={inputClass}
              placeholder="Endpoint ID"
              value={apiKeyForm.endpoint_id}
              onChange={(event) =>
                setApiKeyForm((prev) => ({ ...prev, endpoint_id: event.target.value }))
              }
            />
            <input
              className={inputClass}
              placeholder="Key"
              value={apiKeyForm.key}
              onChange={(event) =>
                setApiKeyForm((prev) => ({ ...prev, key: event.target.value }))
              }
            />
            <input
              className={inputClass}
              placeholder="规则组"
              value={apiKeyForm.rule_group}
              onChange={(event) =>
                setApiKeyForm((prev) => ({ ...prev, rule_group: event.target.value }))
              }
            />
            <input
              className={inputClass}
              placeholder="权重"
              value={apiKeyForm.weight}
              onChange={(event) =>
                setApiKeyForm((prev) => ({ ...prev, weight: event.target.value }))
              }
            />
          </div>
          <Button onClick={createApiKey}>新增 API Key</Button>
          <div className="grid gap-3 md:grid-cols-4">
            <select
              className={inputClass}
              aria-label="filter-endpoint"
              value={apiKeyFilterEndpoint}
              onChange={(event) => setApiKeyFilterEndpoint(event.target.value)}
            >
              <option value="all">全部 Endpoint</option>
              {endpoints.map((endpoint) => (
                <option key={endpoint.id} value={String(endpoint.id)}>
                  {endpoint.name} ({endpoint.id})
                </option>
              ))}
            </select>
            <select
              className={inputClass}
              aria-label="filter-rule-group"
              value={apiKeyFilterRuleGroup}
              onChange={(event) => setApiKeyFilterRuleGroup(event.target.value)}
            >
              <option value="all">全部规则组</option>
              {ruleGroups.map((group) => (
                <option key={group} value={group}>
                  {group}
                </option>
              ))}
            </select>
            <Button
              variant="outline"
              onClick={() => {
                setApiKeyFilterEndpoint("all");
                setApiKeyFilterRuleGroup("all");
              }}
            >
              重置筛选
            </Button>
          </div>
          {filteredApiKeys.length === 0 ? (
            <p className="text-sm text-zinc-400">暂无匹配的 API Key。</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="text-left text-zinc-400">
                  <tr>
                    <th className="py-2">ID</th>
                    <th>Endpoint</th>
                    <th>规则组</th>
                    <th>权重</th>
                    <th>Key</th>
                    <th>状态</th>
                    <th className="text-right">操作</th>
                  </tr>
                </thead>
                <tbody>
                  {filteredApiKeys.map((keyItem) => {
                    const draft = apiKeyDrafts[keyItem.id] ?? {
                      rule_group: keyItem.rule_group,
                      weight: String(keyItem.weight),
                    };

                    return (
                      <tr key={keyItem.id} className="border-t border-muted">
                        <td className="py-2">{keyItem.id}</td>
                        <td>{keyItem.endpoint_id}</td>
                        <td>
                          <input
                            className={tableInputClass}
                            value={draft.rule_group}
                            onChange={(event) =>
                              setApiKeyDrafts((prev) => ({
                                ...prev,
                                [keyItem.id]: {
                                  ...draft,
                                  rule_group: event.target.value,
                                },
                              }))
                            }
                          />
                        </td>
                        <td>
                          <input
                            className={tableInputClass}
                            value={draft.weight}
                            onChange={(event) =>
                              setApiKeyDrafts((prev) => ({
                                ...prev,
                                [keyItem.id]: {
                                  ...draft,
                                  weight: event.target.value,
                                },
                              }))
                            }
                          />
                        </td>
                        <td>{maskKey(keyItem.key)}</td>
                        <td>{keyItem.is_active ? "启用" : "停用"}</td>
                        <td className="py-2 text-right">
                          <div className="flex justify-end gap-2">
                            <Button
                              variant="outline"
                              onClick={() => saveApiKey(keyItem)}
                            >
                              保存
                            </Button>
                            <Button
                              variant="outline"
                              onClick={() => toggleApiKeyStatus(keyItem)}
                            >
                              {keyItem.is_active ? "停用" : "启用"}
                            </Button>
                            <Button
                              variant="outline"
                              onClick={() => deleteApiKey(keyItem.id)}
                            >
                              删除
                            </Button>
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>模型映射</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid gap-3 md:grid-cols-3">
            <input
              className={inputClass}
              placeholder="Endpoint ID"
              value={modelMapForm.endpoint_id}
              onChange={(event) =>
                setModelMapForm((prev) => ({
                  ...prev,
                  endpoint_id: event.target.value,
                }))
              }
            />
            <input
              className={inputClass}
              placeholder="模型别名"
              value={modelMapForm.model_alias}
              onChange={(event) =>
                setModelMapForm((prev) => ({
                  ...prev,
                  model_alias: event.target.value,
                }))
              }
            />
            <input
              className={inputClass}
              placeholder="真实模型名"
              value={modelMapForm.real_model}
              onChange={(event) =>
                setModelMapForm((prev) => ({
                  ...prev,
                  real_model: event.target.value,
                }))
              }
            />
          </div>
          <Button onClick={createModelMap}>新增模型映射</Button>
          {modelMaps.length === 0 ? (
            <p className="text-sm text-zinc-400">暂无模型映射。</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="text-left text-zinc-400">
                  <tr>
                    <th className="py-2">Endpoint</th>
                    <th>模型别名</th>
                    <th>真实模型</th>
                    <th className="text-right">操作</th>
                  </tr>
                </thead>
                <tbody>
                  {modelMaps.map((mapItem) => {
                    const draft = modelMapDrafts[mapItem.id] ?? {
                      model_alias: mapItem.model_alias,
                      real_model: mapItem.real_model,
                    };

                    return (
                      <tr key={mapItem.id} className="border-t border-muted">
                        <td className="py-2">{mapItem.endpoint_id}</td>
                        <td>
                          <input
                            className={tableInputClass}
                            value={draft.model_alias}
                            onChange={(event) =>
                              setModelMapDrafts((prev) => ({
                                ...prev,
                                [mapItem.id]: {
                                  ...draft,
                                  model_alias: event.target.value,
                                },
                              }))
                            }
                          />
                        </td>
                        <td>
                          <input
                            className={tableInputClass}
                            value={draft.real_model}
                            onChange={(event) =>
                              setModelMapDrafts((prev) => ({
                                ...prev,
                                [mapItem.id]: {
                                  ...draft,
                                  real_model: event.target.value,
                                },
                              }))
                            }
                          />
                        </td>
                        <td className="py-2 text-right">
                          <div className="flex justify-end gap-2">
                            <Button
                              variant="outline"
                              onClick={() => saveModelMap(mapItem)}
                            >
                              保存
                            </Button>
                            <Button
                              variant="outline"
                              onClick={() => deleteModelMap(mapItem.id)}
                            >
                              删除
                            </Button>
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
};
