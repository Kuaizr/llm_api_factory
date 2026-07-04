import { useEffect, useState } from "react";

import {
  apiBase,
  buildHeaders,
  consoleAvatarStorageKey,
  tokenStorageKey,
  type AgentNode,
  type Endpoint,
  type HealthStatus,
  type MetricsBucket,
  type RoutingRule,
  type TelegramConfig,
  type UsageStats,
  type UsageTrendRange,
  usageTrendConfig,
} from "./shared";

type PublicEndpoint = Omit<
  Endpoint,
  "keys" | "model_count" | "is_agent_enabled" | "strategy" | "is_active"
>;

type DashboardStatusPayload = {
  endpoints: PublicEndpoint[];
  agents: AgentNode[];
};

export const useConsoleData = () => {
  const [endpoints, setEndpoints] = useState<Endpoint[]>([]);
  const [agents, setAgents] = useState<AgentNode[]>([]);
  const [rules, setRules] = useState<RoutingRule[]>([]);
  const [usageStats, setUsageStats] = useState<UsageStats | null>(null);
  const [usageTrendRange, setUsageTrendRange] = useState<UsageTrendRange>("hour");
  const [usageTrendBuckets, setUsageTrendBuckets] = useState<MetricsBucket[]>([]);
  const [usageTrendUpdatedAt, setUsageTrendUpdatedAt] = useState<string | null>(null);
  const [usageTrendLoading, setUsageTrendLoading] = useState(false);
  const [usageTrendError, setUsageTrendError] = useState<string | null>(null);
  const [healthStatusMap, setHealthStatusMap] = useState<Record<number, HealthStatus>>({});
  const [token, setToken] = useState<string | null>(() => {
    if (
      typeof localStorage === "undefined" ||
      typeof localStorage.getItem !== "function"
    ) {
      return null;
    }
    return localStorage.getItem(tokenStorageKey);
  });
  const [avatarUrl, setAvatarUrl] = useState<string>(() => {
    if (
      typeof localStorage === "undefined" ||
      typeof localStorage.getItem !== "function"
    ) {
      return "";
    }
    return localStorage.getItem(consoleAvatarStorageKey) || "";
  });
  const [telegramConfig, setTelegramConfig] = useState<TelegramConfig | null>(null);
  const [isAdmin, setIsAdmin] = useState(false);

  const loadAuth = async (authToken: string | null) => {
    if (!authToken) {
      setIsAdmin(false);
      return;
    }
    try {
      const response = await fetch(`${apiBase}/auth/me`, {
        headers: buildHeaders(authToken),
      });
      if (!response.ok) {
        setIsAdmin(false);
        return;
      }
      const data = (await response.json()) as { is_admin: boolean };
      setIsAdmin(Boolean(data.is_admin));
    } catch {
      setIsAdmin(false);
    }
  };

  const loadEndpoints = async (authToken: string | null) => {
    try {
      const response = await fetch(`${apiBase}/admin/endpoints`, {
        headers: buildHeaders(authToken),
      });
      if (response.status === 401) {
        setIsAdmin(false);
        const fallback = await fetch(`${apiBase}/v1/status/dashboard`);
        const publicData = (await fallback.json()) as DashboardStatusPayload;
        setEndpoints(
          publicData.endpoints.map((endpoint) => ({
            ...endpoint,
            is_active: endpoint.status !== "offline",
            strategy: "weighted_round_robin",
            is_agent_enabled:
              endpoint.access_mode === "via_agent" || Boolean(endpoint.agent_node),
            model_count: 0,
            probe_interval_seconds: null,
            keys: [],
          }))
        );
        return;
      }
      const data = (await response.json()) as Endpoint[];
      setEndpoints(data);
    } catch {
      setEndpoints([]);
    }
  };

  const loadAgents = async (authToken: string | null) => {
    try {
      const response = await fetch(`${apiBase}/admin/agents`, {
        headers: buildHeaders(authToken),
      });
      if (response.status === 401) {
        const fallback = await fetch(`${apiBase}/v1/status/dashboard`);
        const publicData = (await fallback.json()) as DashboardStatusPayload;
        setAgents(publicData.agents);
        return;
      }
      const data = (await response.json()) as AgentNode[];
      setAgents(data);
    } catch {
      setAgents([]);
    }
  };

  const loadRules = async (authToken: string | null) => {
    try {
      const response = await fetch(`${apiBase}/admin/rules`, {
        headers: buildHeaders(authToken),
      });
      if (!response.ok) {
        setRules([]);
        return;
      }
      const data = (await response.json()) as RoutingRule[];
      setRules(data);
    } catch {
      setRules([]);
    }
  };

  const loadUsage = async (authToken: string | null) => {
    if (!authToken) {
      setUsageStats(null);
      return;
    }
    try {
      const response = await fetch(`${apiBase}/admin/stats/usage`, {
        headers: buildHeaders(authToken),
      });
      if (!response.ok) {
        setUsageStats(null);
        return;
      }
      const data = (await response.json()) as UsageStats;
      setUsageStats(data);
    } catch {
      setUsageStats(null);
    }
  };

  const loadUsageTrend = async (
    authToken: string | null,
    nextRange: UsageTrendRange = usageTrendRange
  ) => {
    if (!authToken) {
      setUsageTrendBuckets([]);
      setUsageTrendUpdatedAt(null);
      setUsageTrendError(null);
      return;
    }
    const config = usageTrendConfig[nextRange];
    if (!config) {
      return;
    }
    setUsageTrendLoading(true);
    try {
      const response = await fetch(
        `${apiBase}/admin/metrics/timeseries?hours=${config.hours}&bucket_minutes=${config.bucketMinutes}`,
        {
          headers: buildHeaders(authToken),
        }
      );
      if (!response.ok) {
        setUsageTrendBuckets([]);
        setUsageTrendError("无法获取趋势数据");
        return;
      }
      const data = (await response.json()) as MetricsBucket[];
      setUsageTrendBuckets(data);
      setUsageTrendUpdatedAt(new Date().toISOString());
      setUsageTrendError(null);
    } catch {
      setUsageTrendBuckets([]);
      setUsageTrendError("无法获取趋势数据");
    } finally {
      setUsageTrendLoading(false);
    }
  };

  const handleUsageRangeChange = (range: UsageTrendRange) => {
    setUsageTrendRange(range);
    void loadUsageTrend(token, range);
  };

  const handleUsageRefresh = () => {
    void loadUsageTrend(token, usageTrendRange);
  };

  const loadHealthStatus = async (authToken: string | null) => {
    if (!authToken) {
      setHealthStatusMap({});
      return;
    }
    try {
      const response = await fetch(`${apiBase}/admin/health-status`, {
        headers: buildHeaders(authToken),
      });
      if (!response.ok) {
        setHealthStatusMap({});
        return;
      }
      const data = (await response.json()) as HealthStatus[];
      const map: Record<number, HealthStatus> = {};
      data.forEach((item) => {
        map[item.api_key_id] = item;
      });
      setHealthStatusMap(map);
    } catch {
      setHealthStatusMap({});
    }
  };

  const loadTelegramConfig = async (authToken: string | null) => {
    if (!authToken) {
      setTelegramConfig(null);
      return;
    }
    try {
      const response = await fetch(`${apiBase}/admin/telegram/config`, {
        headers: buildHeaders(authToken),
      });
      if (!response.ok) {
        setTelegramConfig(null);
        return;
      }
      const data = (await response.json()) as TelegramConfig;
      setTelegramConfig(data);
    } catch {
      setTelegramConfig(null);
    }
  };

  const refreshAll = async (authToken: string | null) => {
    await Promise.all([
      loadAuth(authToken),
      loadEndpoints(authToken),
      loadAgents(authToken),
      loadRules(authToken),
      loadUsage(authToken),
      loadUsageTrend(authToken),
      loadHealthStatus(authToken),
      loadTelegramConfig(authToken),
    ]);
  };

  useEffect(() => {
    void refreshAll(token);
  }, [token]);

  useEffect(() => {
    const interval = window.setInterval(() => {
      void loadEndpoints(token);
      if (token) {
        void loadHealthStatus(token);
        void loadUsage(token);
      }
    }, 60_000);
    return () => window.clearInterval(interval);
  }, [token]);

  const handleLogin = async (password: string) => {
    if (!password) return;
    try {
      const response = await fetch(`${apiBase}/auth/login`, {
        method: "POST",
        headers: buildHeaders(null, true),
        body: JSON.stringify({ password }),
      });
      if (!response.ok) {
        setIsAdmin(false);
        return;
      }
      const data = (await response.json()) as { token: string };
      if (
        typeof localStorage !== "undefined" &&
        typeof localStorage.setItem === "function"
      ) {
        localStorage.setItem(tokenStorageKey, data.token);
      }
      setToken(data.token);
    } catch {
      setIsAdmin(false);
    }
  };

  const handleLogout = () => {
    if (
      typeof localStorage !== "undefined" &&
      typeof localStorage.removeItem === "function"
    ) {
      localStorage.removeItem(tokenStorageKey);
    }
    setToken(null);
    setIsAdmin(false);
  };

  const handleAvatarUpdate = (nextAvatarUrl: string) => {
    const normalized = nextAvatarUrl.trim();
    if (
      typeof localStorage !== "undefined" &&
      typeof localStorage.setItem === "function"
    ) {
      localStorage.setItem(consoleAvatarStorageKey, normalized);
    }
    setAvatarUrl(normalized);
  };

  const handleTelegramConfigUpdate = async (botToken: string, chatId: string) => {
    if (!token) {
      return { ok: false, message: "请先登录管理员" };
    }
    const payload: { bot_token?: string | null; chat_id?: string | null } = {
      chat_id: chatId.trim() || null,
    };
    const trimmedBotToken = botToken.trim();
    if (trimmedBotToken) {
      payload.bot_token = trimmedBotToken;
    }
    try {
      const response = await fetch(`${apiBase}/admin/telegram/config`, {
        method: "PATCH",
        headers: buildHeaders(token, true),
        body: JSON.stringify(payload),
      });
      if (!response.ok) {
        let message = "保存 Telegram 配置失败";
        try {
          const payload = (await response.json()) as { detail?: string };
          if (payload?.detail) {
            message = payload.detail;
          }
        } catch {
          // ignore parse errors
        }
        return { ok: false, message };
      }
      const data = (await response.json()) as TelegramConfig;
      setTelegramConfig(data);
      return { ok: true, message: "Telegram 配置已更新" };
    } catch {
      return { ok: false, message: "保存 Telegram 配置失败，请稍后再试" };
    }
  };

  const handleTelegramTest = async () => {
    if (!token) {
      return { ok: false, message: "请先登录管理员" };
    }
    try {
      const response = await fetch(`${apiBase}/admin/telegram/test`, {
        method: "POST",
        headers: buildHeaders(token),
      });
      if (!response.ok) {
        let message = "发送测试消息失败";
        try {
          const payload = (await response.json()) as { detail?: string };
          if (payload?.detail) {
            message = payload.detail;
          }
        } catch {
          // ignore parse errors
        }
        return { ok: false, message };
      }
      const data = (await response.json()) as { detail?: string };
      return { ok: true, message: data.detail || "测试消息已发送" };
    } catch {
      return { ok: false, message: "发送测试消息失败，请稍后再试" };
    }
  };

  const handlePasswordUpdate = async (currentPassword: string, newPassword: string) => {
    if (!token) {
      return { ok: false, message: "请先登录管理员" };
    }
    const nextPassword = newPassword.trim();
    if (!currentPassword.trim() || !nextPassword) {
      return { ok: false, message: "请输入当前密码和新密码" };
    }

    try {
      const response = await fetch(`${apiBase}/auth/password`, {
        method: "POST",
        headers: buildHeaders(token, true),
        body: JSON.stringify({
          current_password: currentPassword,
          new_password: nextPassword,
        }),
      });
      if (!response.ok) {
        let message = "更新密码失败";
        try {
          const payload = (await response.json()) as { detail?: string };
          if (payload?.detail) {
            message = payload.detail;
          }
        } catch {
          // ignore parse errors
        }
        return { ok: false, message };
      }

      const data = (await response.json()) as { token?: string };
      const issuedToken = data.token;
      if (!issuedToken) {
        return { ok: false, message: "服务端未返回登录令牌" };
      }
      if (
        typeof localStorage !== "undefined" &&
        typeof localStorage.setItem === "function"
      ) {
        localStorage.setItem(tokenStorageKey, issuedToken);
      }
      setToken(issuedToken);
      return { ok: true, message: "管理员密码已更新" };
    } catch {
      return { ok: false, message: "更新密码失败，请稍后再试" };
    }
  };

  return {
    endpoints,
    agents,
    rules,
    usageStats,
    usageTrendRange,
    usageTrendBuckets,
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
    handleLogin,
    handleLogout,
    handleAvatarUpdate,
    handleTelegramConfigUpdate,
    handleTelegramTest,
    handlePasswordUpdate,
  };
};
