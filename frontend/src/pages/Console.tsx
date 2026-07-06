import { ChevronDown, Key, LogIn, LogOut, Moon, Server, Settings, Sun } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import { AgentDeployModal, AgentsView } from "@/pages/console/agents-panel";
import { EditEndpointModal, ManageKeysModal } from "@/pages/console/endpoint-modals";
import { EndpointsPanel } from "@/pages/console/endpoints-panel";
import { FactoryKeysPanel } from "@/pages/console/factory-keys-panel";
import { ProbeModelsModal } from "@/pages/console/probe-models-modal";
import { RuleEditorModal, RulesView } from "@/pages/console/rules-panel";
import { SettingsView } from "@/pages/console/settings-panel";
import {
  consoleThemeStorageKey,
  normalizeRuleGroups,
  type AgentBootstrapResult,
  type AgentDeployFormState,
  type AgentNode,
  type Endpoint,
  type ModelMap,
  type RoutingRule,
} from "@/pages/console/shared";
import { useConsoleActions } from "@/pages/console/use-console-actions";
import { useConsoleData } from "@/pages/console/use-console-data";
import { UsageStatsView } from "@/pages/console/usage-panel";

export { AgentsView } from "@/pages/console/agents-panel";

export const Console = () => {
  const [activeTab, setActiveTab] = useState<
    "endpoints" | "agents" | "factory-keys" | "rules" | "usage" | "settings"
  >("endpoints");
  const {
    endpoints,
    agents,
    rules,
    usageStats,
    usageTrendRange,
    usageTrendBuckets,
    statsOverview,
    statsTimeseries,
    statsLatency,
    statsModelDistribution,
    statsGroupDistribution,
    statsTopKeys,
    dumpSearch,
    dumpSearchOffset,
    usageTrendUpdatedAt,
    usageTrendLoading,
    usageTrendError,
    healthStatusMap,
    token,
    avatarUrl,
    telegramConfig,
    isAdmin,
    loadEndpoints,
    loadAgents,
    loadRules,
    loadHealthStatus,
    handleUsageRangeChange,
    handleUsageRefresh,
    handleDumpSearchPageChange,
    handleLogin,
    handleLogout,
    handleAvatarUpdate,
    handleTelegramConfigUpdate,
    handleTelegramTest,
    handlePasswordUpdate,
  } = useConsoleData();
  const [manageKeysEndpoint, setManageKeysEndpoint] = useState<Endpoint | null>(
    null
  );
  const [editingEndpoint, setEditingEndpoint] = useState<
    Endpoint | undefined | null
  >(null);
  const [editingRule, setEditingRule] = useState<RoutingRule | undefined | null>(
    null
  );
  const [probeEndpoint, setProbeEndpoint] = useState<Endpoint | null>(null);
  const [probeModels, setProbeModels] = useState<ModelMap[]>([]);
  const [probeDiscoveredModels, setProbeDiscoveredModels] = useState<string[]>([]);
  const [probeAliasEdits, setProbeAliasEdits] = useState<Record<number, string>>(
    {}
  );
  const [probeError, setProbeError] = useState<string | null>(null);
  const [probeLoading, setProbeLoading] = useState(false);
  const [agentDeployOpen, setAgentDeployOpen] = useState(false);
  const [agentDeployTarget, setAgentDeployTarget] = useState<AgentNode | null>(null);
  const [agentDeployResult, setAgentDeployResult] = useState<AgentBootstrapResult | null>(null);
  const [theme, setTheme] = useState<"dark" | "light">(() => {
    if (
      typeof localStorage === "undefined" ||
      typeof localStorage.getItem !== "function"
    ) {
      return "dark";
    }
    return localStorage.getItem(consoleThemeStorageKey) === "light"
      ? "light"
      : "dark";
  });

  const agentDeployInitialValues = useMemo<AgentDeployFormState>(
    () => ({
      name: agentDeployTarget?.name ?? "",
    }),
    [agentDeployTarget]
  );

  const availableRuleGroups = useMemo(() => {
    const groups = new Set<string>(["default"]);
    rules.forEach((rule) => {
      if (rule.group_name) {
        groups.add(rule.group_name);
      }
    });
    endpoints.forEach((endpoint) => {
      endpoint.keys.forEach((key) => {
        normalizeRuleGroups(key.rule_groups, key.rule_group).forEach((group) => {
          groups.add(group);
        });
      });
    });
    return Array.from(groups).sort((left, right) => {
      if (left === "default") return -1;
      if (right === "default") return 1;
      return left.localeCompare(right);
    });
  }, [rules, endpoints]);

  const [profileMenuOpen, setProfileMenuOpen] = useState(false);
  const profileMenuRef = useRef<HTMLDivElement | null>(null);

  const openAgentDeploy = (agent: AgentNode | null) => {
    setAgentDeployTarget(agent);
    setAgentDeployOpen(true);
  };

  const isLight = theme === "light";

  const visibleTabs: Array<{ id: typeof activeTab; label: string; icon?: typeof Key }> = isAdmin
    ? [
        { id: "endpoints", label: "端点管理" },
        { id: "agents", label: "节点管理" },
        { id: "factory-keys", label: "API Key", icon: Key },
        { id: "rules", label: "路由规则" },
        { id: "usage", label: "流量统计" },
      ]
    : [{ id: "endpoints", label: "端点管理" }];

  const closeAgentDeploy = () => {
    setAgentDeployOpen(false);
    setAgentDeployTarget(null);
  };

  useEffect(() => {
    if (!manageKeysEndpoint) return;
    const updated = endpoints.find(
      (endpoint) => endpoint.id === manageKeysEndpoint.id
    );
    if (updated) {
      setManageKeysEndpoint(updated);
    }
  }, [endpoints, manageKeysEndpoint]);


  useEffect(() => {
    if (
      typeof localStorage !== "undefined" &&
      typeof localStorage.setItem === "function"
    ) {
      localStorage.setItem(consoleThemeStorageKey, theme);
    }
  }, [theme]);

  useEffect(() => {
    if (!isAdmin && activeTab !== "endpoints" && activeTab !== "settings") {
      setActiveTab("endpoints");
    }
  }, [isAdmin, activeTab]);

  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (
        profileMenuRef.current &&
        event.target instanceof Node &&
        !profileMenuRef.current.contains(event.target)
      ) {
        setProfileMenuOpen(false);
      }
    };
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  const {
    refreshKeys,
    resetProbeState,
    handleAgentBootstrap,
    handleSaveEndpoint,
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
  } = useConsoleActions({
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
  });

  return (
    <div
      className={`min-h-screen font-sans selection:bg-blue-500/30 ${
        isLight ? "theme-light bg-gray-100 text-gray-800" : "bg-[#050505] text-gray-300"
      }`}
    >
      <nav
        className={`border-b backdrop-blur sticky top-0 z-50 ${
          isLight ? "border-gray-200 bg-white/90" : "border-gray-800 bg-[#0a0a0a]/80"
        }`}
      >
        <div className="max-w-7xl mx-auto px-6 h-16 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="bg-blue-600 p-1.5 rounded-lg">
              <Server className="text-white" size={20} />
            </div>
            <span className="font-bold text-lg tracking-tight text-white">
              LLM API Factory
            </span>
          </div>

          <div className="flex items-center gap-6 h-full">
            {visibleTabs.map((tab) => (
              <button
                key={tab.id}
                onClick={() => setActiveTab(tab.id)}
                className={`text-sm font-medium transition-colors h-full border-b-2 pt-1 px-1 inline-flex items-center gap-1.5 ${
                  activeTab === tab.id
                    ? "text-white border-blue-500"
                    : "text-gray-500 border-transparent hover:text-gray-300"
                }`}
              >
                {tab.icon && <tab.icon size={14} />}
                {tab.label}
              </button>
            ))}
          </div>

          <div className="flex items-center gap-3">
            <button
              onClick={() => setTheme((prev) => (prev === "dark" ? "light" : "dark"))}
              className={`inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg border text-xs transition ${
                isLight
                  ? "border-gray-300 text-gray-700 hover:bg-gray-100"
                  : "border-gray-700 text-gray-300 hover:bg-gray-800"
              }`}
              aria-label="切换主题"
              title={isLight ? "切换到深色" : "切换到浅色"}
            >
              {isLight ? <Moon size={13} /> : <Sun size={13} />}
              {isLight ? "Dark" : "Light"}
            </button>

            <div className="relative" ref={profileMenuRef}>
              <button
                onClick={() => setProfileMenuOpen((prev) => !prev)}
                className={`inline-flex items-center gap-2 px-2.5 py-1.5 rounded-lg border text-xs transition ${
                  isLight
                    ? "border-gray-300 text-gray-700 hover:bg-gray-100"
                    : "border-gray-700 text-gray-300 hover:bg-gray-800"
                }`}
              >
                <span className="w-6 h-6 rounded-full overflow-hidden border border-gray-500/40 bg-gray-800 flex items-center justify-center">
                  {avatarUrl ? (
                    <img src={avatarUrl} alt="avatar" className="w-full h-full object-cover" />
                  ) : (
                    <span className="text-[11px] font-semibold">A</span>
                  )}
                </span>
                <span>{isAdmin ? "管理员" : "访客"}</span>
                <ChevronDown size={12} />
              </button>

              {profileMenuOpen && (
                <div
                  className={`absolute right-0 mt-2 w-44 rounded-lg border shadow-xl overflow-hidden z-50 ${
                    isLight ? "bg-white border-gray-200" : "bg-[#0f1117] border-gray-800"
                  }`}
                >
                  <button
                    onClick={() => {
                      setActiveTab("settings");
                      setProfileMenuOpen(false);
                    }}
                    className={`w-full px-3 py-2 text-left text-sm inline-flex items-center gap-2 ${
                      isLight ? "text-gray-700 hover:bg-gray-50" : "text-gray-200 hover:bg-gray-900"
                    }`}
                  >
                    <Settings size={14} />
                    系统设置
                  </button>
                  {isAdmin ? (
                    <button
                      onClick={() => {
                        handleLogout();
                        setActiveTab("endpoints");
                        setProfileMenuOpen(false);
                      }}
                      className={`w-full px-3 py-2 text-left text-sm inline-flex items-center gap-2 ${
                        isLight ? "text-red-600 hover:bg-red-50" : "text-red-400 hover:bg-red-950/40"
                      }`}
                    >
                      <LogOut size={14} />
                      退出登录
                    </button>
                  ) : (
                    <button
                      onClick={() => {
                        setActiveTab("settings");
                        setProfileMenuOpen(false);
                      }}
                      className={`w-full px-3 py-2 text-left text-sm inline-flex items-center gap-2 ${
                        isLight ? "text-blue-600 hover:bg-blue-50" : "text-blue-400 hover:bg-blue-950/40"
                      }`}
                    >
                      <LogIn size={14} />
                      登录
                    </button>
                  )}
                </div>
              )}
            </div>
          </div>
        </div>
      </nav>

      <main className="max-w-7xl mx-auto px-6 py-8 animate-in fade-in slide-in-from-bottom-4 duration-500">
        {activeTab === "endpoints" && (
          <EndpointsPanel
            endpoints={endpoints}
            agents={agents}
            usageStats={usageStats}
            healthStatusMap={healthStatusMap}
            isAdmin={isAdmin}
            onCreateEndpoint={() => setEditingEndpoint(undefined)}
            onEditEndpoint={(endpoint) => setEditingEndpoint(endpoint)}
            onManageKeys={(endpoint) => setManageKeysEndpoint(endpoint)}
            onProbeEndpoint={handleProbeEndpoint}
          />
        )}

        {activeTab === "agents" && (
          <AgentsView
            agents={agents}
            onCreate={() => openAgentDeploy(null)}
            onDeploy={(agent) => openAgentDeploy(agent)}
            onDelete={handleDeleteAgent}
            onRotateToken={handleRotateAgentToken}
            onSetState={handleSetAgentState}
            isAdmin={isAdmin}
          />
        )}
        {activeTab === "factory-keys" && (
          <FactoryKeysPanel
            isAdmin={isAdmin}
            authToken={token}
            ruleGroups={availableRuleGroups}
          />
        )}
        {activeTab === "rules" && (
          <RulesView
            rules={rules}
            isAdmin={isAdmin}
            authToken={token}
            onEdit={(rule) => setEditingRule(rule ?? undefined)}
            onDelete={handleDeleteRule}
          />
        )}
        {activeTab === "usage" && (
          <UsageStatsView
            stats={usageStats}
            buckets={usageTrendBuckets}
            overview={statsOverview}
            timeseries={statsTimeseries}
            latency={statsLatency}
            modelDistribution={statsModelDistribution}
            groupDistribution={statsGroupDistribution}
            topKeys={statsTopKeys}
            dumpSearch={dumpSearch}
            dumpSearchOffset={dumpSearchOffset}
            range={usageTrendRange}
            updatedAt={usageTrendUpdatedAt}
            loading={usageTrendLoading}
            error={usageTrendError}
            onRangeChange={handleUsageRangeChange}
            onRefresh={handleUsageRefresh}
            onDumpSearchPageChange={handleDumpSearchPageChange}
          />
        )}
        {activeTab === "settings" && (
          <SettingsView
            token={token}
            isAdmin={isAdmin}
            avatarUrl={avatarUrl}
            telegramConfig={telegramConfig}
            onLogin={handleLogin}
            onLogout={handleLogout}
            onUpdateAvatar={handleAvatarUpdate}
            onUpdatePassword={handlePasswordUpdate}
            onUpdateTelegramConfig={handleTelegramConfigUpdate}
            onSendTelegramTest={handleTelegramTest}
          />
        )}
      </main>

      {manageKeysEndpoint && (
        <ManageKeysModal
          endpoint={manageKeysEndpoint}
          isAdmin={isAdmin}
          authToken={token}
          healthStatusMap={healthStatusMap}
          availableRuleGroups={availableRuleGroups}
          onClose={() => setManageKeysEndpoint(null)}
          onCreate={handleCreateKey}
          onUpdate={handleUpdateKey}
          onDelete={handleDeleteKey}
          onRefresh={refreshKeys}
        />
      )}
      {editingEndpoint !== null && (
        <EditEndpointModal
          endpoint={editingEndpoint ?? null}
          agents={agents}
          isAdmin={isAdmin}
          onClose={() => setEditingEndpoint(null)}
          onSave={handleSaveEndpoint}
          onDelete={handleDeleteEndpoint}
        />
      )}
      {editingRule !== null && (
        <RuleEditorModal
          endpoints={endpoints}
          rule={editingRule ?? undefined}
          isAdmin={isAdmin}
          authToken={token}
          onClose={() => setEditingRule(null)}
          onSave={handleSaveRule}
        />
      )}
      {agentDeployOpen && (
        <AgentDeployModal
          initialValues={agentDeployInitialValues}
          isAdmin={isAdmin}
          isRedeploy={Boolean(agentDeployTarget)}
          onClose={() => {
            closeAgentDeploy();
            setAgentDeployResult(null);
          }}
          onSubmit={handleAgentBootstrap}
          preloadedResult={agentDeployResult}
        />
      )}
      {probeEndpoint && (
        <ProbeModelsModal
          endpoint={probeEndpoint}
          models={probeModels}
          discoveredModels={probeDiscoveredModels}
          aliasEdits={probeAliasEdits}
          loading={probeLoading}
          error={probeError}
          isAdmin={isAdmin}
          isLight={isLight}
          onAliasChange={(modelId, value) =>
            setProbeAliasEdits((prev) => ({
              ...prev,
              [modelId]: value,
            }))
          }
          onSaveAlias={handleUpdateModelAlias}
          onCreateManualModel={(modelAlias, realModel) =>
            handleCreateProbeModel(probeEndpoint.id, modelAlias, realModel)
          }
          onDeleteManualModel={handleDeleteProbeModel}
          onRetry={() => handleProbeEndpoint(probeEndpoint)}
          onClose={resetProbeState}
        />
      )}
    </div>
  );
};
