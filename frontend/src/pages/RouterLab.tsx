import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

type RouteCandidate = {
  order: number;
  endpoint_id: number;
  endpoint_name: string;
  api_key_id: number;
  weight: number;
  real_model: string;
};

type RouteTestResponse = {
  model: string;
  rule_group: string;
  candidates: RouteCandidate[];
};

type DebugInfo = {
  requestId: string | null;
  traceId: string | null;
  endpointId: string | null;
  endpointName: string | null;
  apiKeyId: string | null;
  realModel: string | null;
};

type RequestLog = {
  id: number;
  request_id: string;
  trace_id: string | null;
  model_alias: string;
  endpoint_id: number;
  api_key_id: number;
  prompt_tokens: number | null;
  completion_tokens: number | null;
  total_tokens: number | null;
  latency_ms: number;
  status_code: number;
  created_at: string;
};

type LogFilters = {
  model_alias: string;
  endpoint_id: string;
  api_key_id: string;
  status_code: string;
  since: string;
  until: string;
};

const apiBase =
  import.meta.env.VITE_API_BASE ??
  (typeof window !== "undefined" ? window.location.origin : "http://localhost:8000");
const adminToken = import.meta.env.VITE_ADMIN_TOKEN;

const inputClass =
  "w-full rounded-md border border-muted bg-background/60 px-3 py-2 text-sm text-foreground";
const compactInputClass =
  "w-full rounded-md border border-muted bg-background/60 px-2 py-1 text-xs text-foreground";

const authHeaders = () => {
  const headers: Record<string, string> = {};
  if (adminToken) {
    headers.Authorization = `Bearer ${adminToken}`;
  }
  return headers;
};

const shortId = (value: string) =>
  value.length > 10 ? `${value.slice(0, 10)}...` : value;

const toInputValue = (date: Date) => {
  const offset = date.getTimezoneOffset();
  const local = new Date(date.getTime() - offset * 60000);
  return local.toISOString().slice(0, 16);
};

const toIsoParam = (value: string) => {
  if (!value) {
    return "";
  }
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return "";
  }
  return parsed.toISOString();
};

const defaultLogFilters: LogFilters = {
  model_alias: "",
  endpoint_id: "",
  api_key_id: "",
  status_code: "",
  since: "",
  until: "",
};

export const RouterLab = () => {
  const [model, setModel] = useState("gpt-4o-mini");
  const [ruleGroup, setRuleGroup] = useState("default");
  const [prompt, setPrompt] = useState("ping");
  const [routeResult, setRouteResult] = useState<RouteTestResponse | null>(null);
  const [completion, setCompletion] = useState<string | null>(null);
  const [debugInfo, setDebugInfo] = useState<DebugInfo | null>(null);
  const [requestLogs, setRequestLogs] = useState<RequestLog[]>([]);
  const [logFilters, setLogFilters] = useState<LogFilters>(defaultLogFilters);
  const [error, setError] = useState<string | null>(null);
  const [logsError, setLogsError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [logsLoading, setLogsLoading] = useState(false);
  const [streamEnabled, setStreamEnabled] = useState(false);
  const [streaming, setStreaming] = useState(false);
  const [copyStatus, setCopyStatus] = useState<string | null>(null);

  const buildLogParams = (filters: LogFilters) => {
    const params = new URLSearchParams({ limit: "50" });
    if (filters.model_alias.trim()) {
      params.set("model_alias", filters.model_alias.trim());
    }
    if (filters.endpoint_id.trim()) {
      params.set("endpoint_id", filters.endpoint_id.trim());
    }
    if (filters.api_key_id.trim()) {
      params.set("api_key_id", filters.api_key_id.trim());
    }
    if (filters.status_code.trim()) {
      params.set("status_code", filters.status_code.trim());
    }
    if (filters.since.trim()) {
      const sinceValue = toIsoParam(filters.since.trim());
      if (sinceValue) {
        params.set("since", sinceValue);
      }
    }
    if (filters.until.trim()) {
      const untilValue = toIsoParam(filters.until.trim());
      if (untilValue) {
        params.set("until", untilValue);
      }
    }
    return params;
  };

  const loadRequestLogs = async (filters: LogFilters = logFilters) => {
    setLogsLoading(true);
    setLogsError(null);
    try {
      const params = buildLogParams(filters);
      const response = await fetch(
        `${apiBase}/admin/request-logs?${params.toString()}`,
        {
          headers: authHeaders(),
        }
      );
      if (!response.ok) {
        throw new Error("log fetch failed");
      }
      const data = (await response.json()) as RequestLog[];
      setRequestLogs(data);
    } catch (err) {
      setLogsError("无法获取请求日志");
    } finally {
      setLogsLoading(false);
    }
  };

  const clearLogFilters = async () => {
    setLogFilters(defaultLogFilters);
    await loadRequestLogs(defaultLogFilters);
  };

  const applyQuickRange = async (hours: number) => {
    const now = new Date();
    const sinceDate = new Date(now.getTime() - hours * 60 * 60 * 1000);
    const nextFilters: LogFilters = {
      ...logFilters,
      since: toInputValue(sinceDate),
      until: toInputValue(now),
    };
    setLogFilters(nextFilters);
    await loadRequestLogs(nextFilters);
  };

  const csvCell = (value: string | number | null) => {
    if (value === null || value === undefined) {
      return "";
    }
    const text = String(value);
    if (/[",\n]/.test(text)) {
      return `"${text.replace(/"/g, "\"\"")}"`;
    }
    return text;
  };

  const buildCsv = (logs: RequestLog[]) => {
    const headers = [
      "id",
      "request_id",
      "trace_id",
      "model_alias",
      "endpoint_id",
      "api_key_id",
      "prompt_tokens",
      "completion_tokens",
      "total_tokens",
      "latency_ms",
      "status_code",
      "created_at",
    ];
    const rows = logs.map((log) =>
      [
        log.id,
        log.request_id,
        log.trace_id,
        log.model_alias,
        log.endpoint_id,
        log.api_key_id,
        log.prompt_tokens,
        log.completion_tokens,
        log.total_tokens,
        log.latency_ms,
        log.status_code,
        log.created_at,
      ]
        .map((value) => csvCell(value))
        .join(",")
    );
    return [headers.join(","), ...rows].join("\n");
  };

  const downloadText = (content: string, filename: string, mime: string) => {
    const blob = new Blob([content], { type: mime });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
  };

  const exportLogsAsJson = () => {
    if (!requestLogs.length) {
      return;
    }
    const content = JSON.stringify(requestLogs, null, 2);
    downloadText(content, `request-logs-${Date.now()}.json`, "application/json");
  };

  const exportLogsAsCsv = () => {
    if (!requestLogs.length) {
      return;
    }
    const content = buildCsv(requestLogs);
    downloadText(content, `request-logs-${Date.now()}.csv`, "text/csv;charset=utf-8");
  };

  const copyLog = async (log: RequestLog) => {
    setLogsError(null);
    setCopyStatus(null);
    if (!navigator.clipboard?.writeText) {
      setLogsError("当前浏览器不支持复制");
      return;
    }
    try {
      await navigator.clipboard.writeText(JSON.stringify(log, null, 2));
      setCopyStatus(`已复制日志 ${log.request_id}`);
    } catch (err) {
      setLogsError("复制失败");
    }
  };

  useEffect(() => {
    loadRequestLogs();
  }, []);

  const previewRoute = async () => {
    setLoading(true);
    setError(null);
    try {
      const response = await fetch(`${apiBase}/admin/route-test`, {
        method: "POST",
        headers: { ...authHeaders(), "Content-Type": "application/json" },
        body: JSON.stringify({ model, rule_group: ruleGroup || "default" }),
      });
      if (!response.ok) {
        throw new Error("route-test failed");
      }
      const data = (await response.json()) as RouteTestResponse;
      setRouteResult(data);
    } catch (err) {
      setError("无法获取路由候选列表");
    } finally {
      setLoading(false);
    }
  };

  const sendRequest = async () => {
    const shouldStream = streamEnabled;
    setLoading(true);
    setStreaming(shouldStream);
    setError(null);
    setCompletion(shouldStream ? "" : null);
    setDebugInfo(null);
    try {
      const headers: Record<string, string> = {
        ...authHeaders(),
        "Content-Type": "application/json",
      };
      if (ruleGroup) {
        headers["X-Rule-Group"] = ruleGroup;
      }
      const response = await fetch(`${apiBase}/v1/chat/completions`, {
        method: "POST",
        headers,
        body: JSON.stringify({
          model,
          stream: shouldStream,
          messages: [{ role: "user", content: prompt }],
        }),
      });
      if (!response.ok) {
        throw new Error(`请求失败: ${response.status}`);
      }
      setDebugInfo({
        requestId: response.headers.get("x-request-id"),
        traceId: response.headers.get("x-trace-id"),
        endpointId: response.headers.get("x-endpoint-id"),
        endpointName: response.headers.get("x-endpoint-name"),
        apiKeyId: response.headers.get("x-api-key-id"),
        realModel: response.headers.get("x-real-model"),
      });
      if (shouldStream) {
        if (!response.body) {
          throw new Error("stream response missing");
        }
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        while (true) {
          const { value, done } = await reader.read();
          if (done) {
            break;
          }
          if (value) {
            const text = decoder.decode(value, { stream: true });
            if (text) {
              setCompletion((prev) => `${prev ?? ""}${text}`);
            }
          }
        }
        const tail = decoder.decode();
        if (tail) {
          setCompletion((prev) => `${prev ?? ""}${tail}`);
        }
      } else {
        const raw = await response.text();
        let data: unknown = raw;
        try {
          data = JSON.parse(raw);
        } catch (parseError) {
          data = raw;
        }
        setCompletion(
          typeof data === "string" ? data : JSON.stringify(data, null, 2)
        );
      }
      await loadRequestLogs();
    } catch (err) {
      setError("请求失败，请检查配置");
    } finally {
      setLoading(false);
      setStreaming(false);
    }
  };

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle>路由测试台</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid gap-3 md:grid-cols-3">
            <input
              className={inputClass}
              placeholder="模型别名"
              value={model}
              onChange={(event) => setModel(event.target.value)}
            />
            <input
              className={inputClass}
              placeholder="规则组"
              value={ruleGroup}
              onChange={(event) => setRuleGroup(event.target.value)}
            />
            <input
              className={inputClass}
              placeholder="提示词"
              value={prompt}
              onChange={(event) => setPrompt(event.target.value)}
            />
          </div>
          <div className="flex flex-wrap items-center gap-3">
            <label className="flex items-center gap-2 text-sm text-zinc-300">
              <input
                aria-label="stream-output"
                type="checkbox"
                className="h-4 w-4 rounded border-muted bg-background/60"
                checked={streamEnabled}
                disabled={loading}
                onChange={(event) => setStreamEnabled(event.target.checked)}
              />
              流式输出
            </label>
            <Button variant="outline" onClick={previewRoute} disabled={loading}>
              预览路由
            </Button>
            <Button onClick={sendRequest} disabled={loading}>
              发送请求
            </Button>
          </div>
          {streaming ? (
            <p className="text-xs text-zinc-400">流式输出中...</p>
          ) : null}
          {error ? <p className="text-sm text-red-400">{error}</p> : null}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>候选路径</CardTitle>
        </CardHeader>
        <CardContent>
          {routeResult && routeResult.candidates.length > 0 ? (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="text-left text-zinc-400">
                  <tr>
                    <th className="py-2">顺序</th>
                    <th>Endpoint</th>
                    <th>API Key</th>
                    <th>权重</th>
                    <th>真实模型</th>
                  </tr>
                </thead>
                <tbody>
                  {routeResult.candidates.map((item) => (
                    <tr key={item.order} className="border-t border-muted">
                      <td className="py-2">{item.order}</td>
                      <td>
                        {item.endpoint_name} ({item.endpoint_id})
                      </td>
                      <td>{item.api_key_id}</td>
                      <td>{item.weight}</td>
                      <td>{item.real_model}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="text-sm text-zinc-400">尚未生成候选列表。</p>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>请求结果</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          {debugInfo ? (
            <div className="grid gap-2 text-sm text-zinc-300 md:grid-cols-2">
              <span>Request ID: {debugInfo.requestId ?? "--"}</span>
              <span>Trace ID: {debugInfo.traceId ?? "--"}</span>
              <span>Endpoint: {debugInfo.endpointName ?? "--"}</span>
              <span>Endpoint ID: {debugInfo.endpointId ?? "--"}</span>
              <span>API Key ID: {debugInfo.apiKeyId ?? "--"}</span>
              <span>Real Model: {debugInfo.realModel ?? "--"}</span>
            </div>
          ) : null}
          {completion ? (
            <pre className="max-h-80 overflow-auto rounded-lg border border-muted bg-background/60 p-4 text-xs text-zinc-200">
              {completion}
            </pre>
          ) : (
            <p className="text-sm text-zinc-400">尚未发送请求。</p>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <CardTitle>请求日志</CardTitle>
            <div className="flex flex-wrap items-center gap-2">
              <Button
                variant="outline"
                onClick={() => {
                  void loadRequestLogs();
                }}
                disabled={logsLoading}
              >
                {logsLoading ? "加载中" : "刷新"}
              </Button>
              <Button
                variant="outline"
                onClick={exportLogsAsJson}
                disabled={logsLoading || requestLogs.length === 0}
              >
                导出JSON
              </Button>
              <Button
                variant="outline"
                onClick={exportLogsAsCsv}
                disabled={logsLoading || requestLogs.length === 0}
              >
                导出CSV
              </Button>
            </div>
          </div>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid gap-3 md:grid-cols-6">
            <input
              className={compactInputClass}
              placeholder="模型别名"
              value={logFilters.model_alias}
              onChange={(event) =>
                setLogFilters((prev) => ({ ...prev, model_alias: event.target.value }))
              }
            />
            <input
              className={compactInputClass}
              placeholder="Endpoint ID"
              value={logFilters.endpoint_id}
              onChange={(event) =>
                setLogFilters((prev) => ({ ...prev, endpoint_id: event.target.value }))
              }
            />
            <input
              className={compactInputClass}
              placeholder="API Key ID"
              value={logFilters.api_key_id}
              onChange={(event) =>
                setLogFilters((prev) => ({ ...prev, api_key_id: event.target.value }))
              }
            />
            <input
              className={compactInputClass}
              placeholder="状态码"
              value={logFilters.status_code}
              onChange={(event) =>
                setLogFilters((prev) => ({ ...prev, status_code: event.target.value }))
              }
            />
            <input
              className={compactInputClass}
              type="datetime-local"
              placeholder="开始时间"
              value={logFilters.since}
              onChange={(event) =>
                setLogFilters((prev) => ({ ...prev, since: event.target.value }))
              }
            />
            <input
              className={compactInputClass}
              type="datetime-local"
              placeholder="结束时间"
              value={logFilters.until}
              onChange={(event) =>
                setLogFilters((prev) => ({ ...prev, until: event.target.value }))
              }
            />
          </div>
          <div className="flex flex-wrap gap-3">
            <Button
              variant="outline"
              onClick={() => {
                void loadRequestLogs();
              }}
              disabled={logsLoading}
            >
              筛选
            </Button>
            <Button variant="outline" onClick={clearLogFilters} disabled={logsLoading}>
              清空
            </Button>
            <Button
              variant="outline"
              onClick={() => applyQuickRange(1)}
              disabled={logsLoading}
            >
              近1h
            </Button>
            <Button
              variant="outline"
              onClick={() => applyQuickRange(6)}
              disabled={logsLoading}
            >
              近6h
            </Button>
            <Button
              variant="outline"
              onClick={() => applyQuickRange(24)}
              disabled={logsLoading}
            >
              近24h
            </Button>
          </div>
          {logsError ? <p className="text-sm text-red-400">{logsError}</p> : null}
          {copyStatus ? (
            <p className="text-xs text-emerald-400">{copyStatus}</p>
          ) : null}
          {requestLogs.length > 0 ? (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="text-left text-zinc-400">
                  <tr>
                    <th className="py-2">请求</th>
                    <th>Trace</th>
                    <th>模型</th>
                    <th>Endpoint</th>
                    <th>Key</th>
                    <th>Tokens</th>
                    <th>耗时</th>
                    <th>状态</th>
                    <th>时间</th>
                    <th>操作</th>
                  </tr>
                </thead>
                <tbody>
                  {requestLogs.map((log) => (
                    <tr key={log.id} className="border-t border-muted">
                      <td className="py-2">{shortId(log.request_id)}</td>
                      <td>{log.trace_id ? shortId(log.trace_id) : "--"}</td>
                      <td>{log.model_alias}</td>
                      <td>{log.endpoint_id}</td>
                      <td>{log.api_key_id}</td>
                      <td>{log.total_tokens ?? "--"}</td>
                      <td>{log.latency_ms} ms</td>
                      <td>{log.status_code}</td>
                      <td>{new Date(log.created_at).toLocaleString()}</td>
                      <td>
                        <Button
                          variant="outline"
                          className="px-2 py-1 text-xs"
                          aria-label={`copy-log-${log.id}`}
                          onClick={() => copyLog(log)}
                        >
                          复制
                        </Button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="text-sm text-zinc-400">暂无请求日志。</p>
          )}
        </CardContent>
      </Card>
    </div>
  );
};
