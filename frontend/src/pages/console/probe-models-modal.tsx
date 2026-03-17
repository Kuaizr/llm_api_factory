import { Plus, RefreshCw, Trash2, XCircle } from "lucide-react";
import { useState } from "react";

import { type Endpoint, type ModelMap } from "./shared";

type ProbeModelsModalProps = {
  endpoint: Endpoint;
  models: ModelMap[];
  discoveredModels: string[];
  aliasEdits: Record<number, string>;
  loading: boolean;
  error: string | null;
  isAdmin: boolean;
  isLight: boolean;
  onAliasChange: (modelId: number, value: string) => void;
  onSaveAlias: (model: ModelMap) => void;
  onCreateManualModel: (modelAlias: string, realModel: string) => void;
  onDeleteManualModel: (model: ModelMap) => void;
  onRetry: () => void;
  onClose: () => void;
};

export const ProbeModelsModal = ({
  endpoint,
  models,
  discoveredModels,
  aliasEdits,
  loading,
  error,
  isAdmin,
  isLight,
  onAliasChange,
  onSaveAlias,
  onCreateManualModel,
  onDeleteManualModel,
  onRetry,
  onClose,
}: ProbeModelsModalProps) => {
  const [newModelAlias, setNewModelAlias] = useState("");
  const [newRealModel, setNewRealModel] = useState("");

  const handleCreate = () => {
    const modelAlias = newModelAlias.trim();
    const realModel = newRealModel.trim();
    if (!modelAlias || !realModel) {
      return;
    }
    onCreateManualModel(modelAlias, realModel);
    setNewModelAlias("");
    setNewRealModel("");
  };

  const isPermissionIssue = Boolean(error && error.includes("权限"));

  const shellClass = isLight
    ? "bg-white border border-gray-200"
    : "bg-[#0f1117] border border-gray-800";
  const sectionClass = isLight
    ? "border border-gray-200 bg-gray-50/80"
    : "border border-gray-800 bg-gray-900/40";
  const itemClass = isLight
    ? "border border-gray-200 bg-white text-gray-700"
    : "border border-gray-800 bg-gray-900/60 text-gray-300";
  const inputClass = isLight
    ? "bg-white border border-gray-300 text-gray-800 placeholder:text-gray-400"
    : "bg-gray-950 border border-gray-800 text-gray-200";

  return (
    <div
      className={`fixed inset-0 z-[160] flex items-center justify-center backdrop-blur-sm ${
        isLight ? "bg-white/72" : "bg-black/70"
      }`}
    >
      <div
        className={`${shellClass} rounded-xl w-[980px] max-h-[82vh] flex flex-col shadow-2xl animate-in fade-in zoom-in-95 duration-200`}
      >
        <div
          className={`p-5 border-b flex items-center justify-between ${
            isLight ? "border-gray-200" : "border-gray-800"
          }`}
        >
          <div>
            <h3 className={`text-lg font-bold ${isLight ? "text-gray-900" : "text-white"}`}>
              模型探测与手动维护
            </h3>
            <p className="text-xs text-gray-500 mt-1">Endpoint: {endpoint.name}</p>
          </div>
          <button
            onClick={onClose}
            className={`text-gray-500 ${isLight ? "hover:text-gray-800" : "hover:text-white"}`}
          >
            <XCircle size={20} />
          </button>
        </div>

        <div className="p-5 overflow-y-auto grid grid-cols-2 gap-4">
          <section className={`${sectionClass} rounded-lg p-4 space-y-3`}>
            <div className="flex items-center justify-between">
              <h4 className={`text-sm font-semibold ${isLight ? "text-gray-800" : "text-gray-200"}`}>
                自动探测区域
              </h4>
              <button
                onClick={onRetry}
                disabled={!isAdmin || loading}
                className={`h-8 px-3 text-xs rounded border disabled:opacity-40 ${
                  isLight
                    ? "text-blue-600 border-blue-300 hover:bg-blue-50"
                    : "text-blue-400 border-blue-500/40 hover:bg-blue-500/10"
                }`}
              >
                重新探测
              </button>
            </div>

            {loading && (
              <div className="text-sm text-blue-500 flex items-center gap-2">
                <RefreshCw size={14} className="animate-spin" /> 探测中...
              </div>
            )}

            {!loading && error && (
              <div
                className={`text-xs rounded border px-3 py-2 ${
                  isPermissionIssue
                    ? isLight
                      ? "text-red-600 border-red-200 bg-red-50"
                      : "text-red-300 border-red-500/40 bg-red-900/20"
                    : isLight
                      ? "text-amber-700 border-amber-200 bg-amber-50"
                      : "text-yellow-300 border-yellow-500/40 bg-yellow-900/20"
                }`}
              >
                {error}
              </div>
            )}

            {!loading && discoveredModels.length === 0 && (
              <div className="text-sm text-gray-500">未获取到可自动发现的模型</div>
            )}

            {!loading && discoveredModels.length > 0 && (
              <ul className="space-y-2">
                {discoveredModels.map((model) => (
                  <li
                    key={model}
                    className={`${itemClass} h-9 px-3 rounded text-xs font-mono flex items-center`}
                  >
                    {model}
                  </li>
                ))}
              </ul>
            )}

            <p className="text-[11px] text-gray-500">
              重新探测只更新此区域结果，不会改动右侧手动模型映射。
            </p>
          </section>

          <section className={`${sectionClass} rounded-lg p-4 space-y-3`}>
            <div className="h-8 flex items-center">
              <h4 className={`text-sm font-semibold ${isLight ? "text-gray-800" : "text-gray-200"}`}>
                手动模型映射
              </h4>
            </div>

            <div className="grid grid-cols-[minmax(0,1fr)_minmax(0,1fr)_88px] items-center gap-2">
              <input
                value={newModelAlias}
                onChange={(event) => setNewModelAlias(event.target.value)}
                placeholder="模型别名 (alias)"
                disabled={!isAdmin}
                className={`${inputClass} h-9 rounded px-2 text-xs font-mono focus:border-blue-500 focus:outline-none disabled:opacity-50`}
              />
              <input
                value={newRealModel}
                onChange={(event) => setNewRealModel(event.target.value)}
                placeholder="真实模型 (real_model)"
                disabled={!isAdmin}
                className={`${inputClass} h-9 rounded px-2 text-xs font-mono focus:border-blue-500 focus:outline-none disabled:opacity-50`}
              />
              <button
                onClick={handleCreate}
                disabled={!isAdmin}
                className={`h-9 px-2 text-xs rounded border disabled:opacity-40 inline-flex items-center justify-center gap-1 whitespace-nowrap ${
                  isLight
                    ? "text-green-700 border-green-300 hover:bg-green-50"
                    : "text-green-400 border-green-500/40 hover:bg-green-500/10"
                }`}
              >
                <Plus size={12} /> 新增
              </button>
            </div>

            {models.length === 0 && <div className="text-sm text-gray-500">暂无手动模型映射</div>}

            {models.length > 0 && (
              <ul className="space-y-2">
                {models.map((model) => (
                  <li
                    key={model.id}
                    className={`${itemClass} h-9 px-3 rounded grid grid-cols-[1fr_1fr_auto_auto] items-center gap-2`}
                  >
                    <input
                      value={aliasEdits[model.id] ?? model.model_alias}
                      onChange={(event) => onAliasChange(model.id, event.target.value)}
                      disabled={!isAdmin}
                      className={`${inputClass} h-7 rounded px-2 text-xs font-mono focus:border-blue-500 focus:outline-none disabled:opacity-50`}
                    />
                    <div className="text-xs text-gray-500 font-mono truncate">{model.real_model}</div>
                    <button
                      onClick={() => onSaveAlias(model)}
                      disabled={!isAdmin}
                      className={`h-7 px-2.5 text-xs rounded border disabled:opacity-40 whitespace-nowrap ${
                        isLight
                          ? "text-blue-600 border-blue-300 hover:bg-blue-50"
                          : "text-blue-400 border-blue-500/40 hover:bg-blue-500/10"
                      }`}
                    >
                      保存
                    </button>
                    <button
                      onClick={() => onDeleteManualModel(model)}
                      disabled={!isAdmin}
                      className={`h-7 px-2 text-xs rounded border disabled:opacity-40 inline-flex items-center whitespace-nowrap ${
                        isLight
                          ? "text-red-600 border-red-300 hover:bg-red-50"
                          : "text-red-400 border-red-500/40 hover:bg-red-500/10"
                      }`}
                    >
                      <Trash2 size={12} />
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </section>
        </div>

        <div
          className={`p-4 border-t flex justify-end gap-2 ${
            isLight ? "border-gray-200 bg-white/95" : "border-gray-800 bg-[#0f1117]/95"
          }`}
        >
          <button
            onClick={onClose}
            className={`px-4 py-2 text-sm ${isLight ? "text-gray-600 hover:text-gray-900" : "text-gray-400 hover:text-white"}`}
          >
            关闭
          </button>
        </div>
      </div>
    </div>
  );
};
