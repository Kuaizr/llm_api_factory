import { RefreshCw, Send, XCircle } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import {
  apiBase,
  buildHeaders,
  type ApiKey,
  type Endpoint,
  type ApiKeyDirectTestResult,
} from "./shared";
import {
  parseApiKeyDirectTestResult,
  parseEndpointProbeResult,
} from "./response-validators";

const requestTemplates = [
  { value: "chat", label: "Chat" },
  { value: "response", label: "Response" },
  { value: "codex", label: "Codex" },
  { value: "claude", label: "Claude" },
  { value: "claude-code", label: "Claude Code" },
  { value: "gemini", label: "Gemini" },
];

const defaultTemplateForProvider = (provider?: string | null) => {
  const normalized = (provider ?? "").trim().toLowerCase();
  if (normalized === "anthropic") {
    return "claude";
  }
  if (normalized === "gemini") {
    return "gemini";
  }
  return "chat";
};

export const ApiKeyTestModal = ({
  apiKey,
  endpoint,
  endpointId,
  authToken,
  isAdmin,
  onClose,
}: {
  apiKey: ApiKey;
  endpoint?: Endpoint;
  endpointId: number;
  authToken: string | null;
  isAdmin: boolean;
  onClose: () => void;
}) => {
  const [probeLoading, setProbeLoading] = useState(false);
  const [testing, setTesting] = useState(false);
  const [models, setModels] = useState<string[]>([]);
  const [selectedModel, setSelectedModel] = useState("");
  const [manualModel, setManualModel] = useState("");
  const [prompt, setPrompt] = useState("");
  const [requestTemplate, setRequestTemplate] = useState(
    defaultTemplateForProvider(endpoint?.provider)
  );
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<ApiKeyDirectTestResult | null>(null);

  const modelOptions = useMemo(
    () => Array.from(new Set(models.map((item) => item.trim()).filter(Boolean))).sort(),
    [models]
  );

  const effectiveModel = manualModel.trim() || selectedModel.trim();

  const runProbe = async () => {
    setProbeLoading(true);
    setError(null);
    try {
      const response = await fetch(`${apiBase}/admin/endpoints/${endpointId}/probe`, {
        method: "POST",
        headers: buildHeaders(authToken),
      });
      if (!response.ok) {
        throw new Error("probe failed");
      }
      const payload = parseEndpointProbeResult(await response.json());
      if (!payload) {
        throw new Error("invalid probe response");
      }
      const discovered = [
        ...payload.discovered_models,
        ...payload.manual_models.flatMap((item) => [item.model_alias, item.real_model]),
      ];
      const nextModels = Array.from(
        new Set(discovered.map((item) => item.trim()).filter(Boolean))
      ).sort();
      setModels(nextModels);
      setSelectedModel((current) => current || nextModels[0] || "");
      if (payload.probe_message && nextModels.length === 0) {
        setError(payload.probe_message);
      }
    } catch (err) {
      setError("模型探测失败，可以手动输入模型名后测试");
    } finally {
      setProbeLoading(false);
    }
  };

  const runTest = async () => {
    if (!effectiveModel || testing) {
      return;
    }
    setTesting(true);
    setError(null);
    setResult(null);
    try {
      const response = await fetch(`${apiBase}/admin/api-keys/${apiKey.id}/test`, {
        method: "POST",
        headers: buildHeaders(authToken, true),
        body: JSON.stringify({
          model: effectiveModel,
          request_template: requestTemplate,
          prompt: prompt.trim() || undefined,
        }),
      });
      if (!response.ok) {
        throw new Error("test failed");
      }
      const payload = parseApiKeyDirectTestResult(await response.json());
      if (!payload) {
        throw new Error("invalid test response");
      }
      setResult(payload);
    } catch (err) {
      setError("测试请求失败");
    } finally {
      setTesting(false);
    }
  };

  useEffect(() => {
    void runProbe();
  }, [endpointId, apiKey.id]);

  useEffect(() => {
    setRequestTemplate(defaultTemplateForProvider(endpoint?.provider));
  }, [endpoint?.provider, apiKey.id]);

  return (
    <div className="fixed inset-0 z-[120] flex items-center justify-center bg-black/70 backdrop-blur-sm">
      <div className="flex max-h-[85vh] w-[760px] flex-col rounded-xl border border-gray-800 bg-[#0f1117] shadow-2xl">
        <div className="flex items-center justify-between border-b border-gray-800 p-5">
          <div>
            <h3 className="text-lg font-bold text-white">测试 API Key</h3>
            <p className="mt-1 font-mono text-xs text-gray-500">{apiKey.key_preview}</p>
          </div>
          <button onClick={onClose} className="text-gray-500 hover:text-white">
            <XCircle size={22} />
          </button>
        </div>

        <div className="flex-1 space-y-4 overflow-y-auto p-5">
          <div className="grid gap-3 md:grid-cols-[160px_1fr_1fr_auto]">
            <div>
              <label className="mb-1 block text-xs text-gray-500">请求模板</label>
              <select
                value={requestTemplate}
                onChange={(event) => setRequestTemplate(event.target.value)}
                className="w-full rounded border border-gray-700 bg-gray-900 px-2 py-2 text-sm text-gray-200 outline-none focus:border-blue-500"
              >
                {requestTemplates.map((template) => (
                  <option key={template.value} value={template.value}>
                    {template.label}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label className="mb-1 block text-xs text-gray-500">探测模型</label>
              <select
                value={selectedModel}
                onChange={(event) => setSelectedModel(event.target.value)}
                className="w-full rounded border border-gray-700 bg-gray-900 px-2 py-2 text-sm text-gray-200 outline-none focus:border-blue-500"
              >
                <option value="">选择模型</option>
                {modelOptions.map((model) => (
                  <option key={model} value={model}>
                    {model}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label className="mb-1 block text-xs text-gray-500">手动模型名</label>
              <input
                value={manualModel}
                onChange={(event) => setManualModel(event.target.value)}
                className="w-full rounded border border-gray-700 bg-gray-900 px-2 py-2 text-sm text-gray-200 outline-none focus:border-blue-500"
                placeholder="例如 gpt-5.5"
              />
            </div>
            <div className="flex items-end gap-2">
              <button
                onClick={() => void runProbe()}
                disabled={!isAdmin || probeLoading}
                className="flex items-center gap-1 rounded border border-gray-700 bg-gray-800/60 px-3 py-2 text-sm text-gray-300 hover:bg-gray-800 disabled:opacity-50"
              >
                <RefreshCw size={14} /> {probeLoading ? "探测中" : "探测"}
              </button>
              <button
                onClick={() => void runTest()}
                disabled={!isAdmin || testing || !effectiveModel}
                className="flex items-center gap-1 rounded border border-blue-500/30 bg-blue-600/20 px-3 py-2 text-sm text-blue-300 hover:bg-blue-600/30 disabled:opacity-50"
              >
                <Send size={14} /> {testing ? "测试中" : "测试"}
              </button>
            </div>
          </div>

          <div>
            <label className="mb-1 block text-xs text-gray-500">测试 Prompt</label>
            <textarea
              value={prompt}
              onChange={(event) => setPrompt(event.target.value)}
              rows={3}
              className="w-full rounded border border-gray-700 bg-gray-900 px-3 py-2 text-sm text-gray-200 outline-none focus:border-blue-500"
              placeholder="默认：你是什么模型"
            />
          </div>

          {error ? <p className="text-sm text-red-400">{error}</p> : null}

          {result ? (
            <div className="space-y-3 rounded-lg border border-gray-800 bg-gray-950/50 p-4">
              <div className="flex flex-wrap gap-3 text-xs text-gray-400">
                <span className={result.ok ? "text-emerald-400" : "text-red-400"}>
                  {result.ok ? "OK" : "失败"}
                </span>
                <span>HTTP {result.status_code}</span>
                <span>{result.latency_ms} ms</span>
                <span>{result.provider}</span>
                <span>{result.request_template}</span>
                <span>{result.model}</span>
              </div>
              <div className="text-xs text-gray-500">{result.upstream_url}</div>
              {result.error_reason ? (
                <div className="text-sm text-red-300">{result.error_reason}</div>
              ) : null}
              {result.output_text ? (
                <pre className="max-h-40 overflow-auto rounded border border-gray-800 bg-black/30 p-3 text-xs text-gray-200">
                  {result.output_text}
                </pre>
              ) : null}
              <pre className="max-h-60 overflow-auto rounded border border-gray-800 bg-black/30 p-3 text-xs text-gray-400">
                {JSON.stringify(result.raw_response, null, 2)}
              </pre>
            </div>
          ) : null}
        </div>
      </div>
    </div>
  );
};
