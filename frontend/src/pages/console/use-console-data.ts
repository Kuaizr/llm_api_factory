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
  type DumpSearchResult,
  type RoutingRule,
  type StatsDistributionItem,
  type StatsLatencyBucket,
  type StatsOverview,
  type StatsTimeseriesBucket,
  type StatsTopKey,
  type TelegramConfig,
  type UsageStats,
  type UsageTrendRange,
  usageTrendConfig,
} from "./shared";
import {
  parseAgentList,
  parseDashboardStatus,
  parseDumpSearchResult,
  parseEndpointList,
  parseHealthStatusList,
  parseRoutingRuleList,
  parseStatsDistributionList,
  parseStatsLatencyBucketList,
  parseStatsOverview,
  parseStatsTimeseriesBucketList,
  parseStatsTopKeyList,
  parseTelegramConfig,
  parseUsageStats,
} from "./response-validators";

export const useConsoleData = () => {
  const [endpoints, setEndpoints] = useState<Endpoint[]>([]);
  const [agents, setAgents] = useState<AgentNode[]>([]);
  const [rules, setRules] = useState<RoutingRule[]>([]);
  const [usageStats, setUsageStats] = useState<UsageStats | null>(null);
  const [usageTrendRange, setUsageTrendRange] = useState<UsageTrendRange>("24h");
  const [usageTrendBuckets, setUsageTrendBuckets] = useState<MetricsBucket[]>([]);
  const [statsOverview, setStatsOverview] = useState<StatsOverview | null>(null);
  const [statsTimeseries, setStatsTimeseries] = useState<StatsTimeseriesBucket[]>([]);
  const [statsLatency, setStatsLatency] = useState<StatsLatencyBucket[]>([]);
  const [statsModelDistribution, setStatsModelDistribution] = useState<
    StatsDistributionItem[]
  >([]);
  const [statsGroupDistribution, setStatsGroupDistribution] = useState<
    StatsDistributionItem[]
  >([]);
  const [statsTopKeys, setStatsTopKeys] = useState<StatsTopKey[]>([]);
  const [dumpSearch, setDumpSearch] = useState<DumpSearchResult | null>(null);
  const [dumpSearchOffset, setDumpSearchOffset] = useState(0);
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
      const data = await response.json();
      if (
        typeof data !== "object" ||
        data === null ||
        !("is_admin" in data) ||
        typeof data.is_admin !== "boolean"
      ) {
        setIsAdmin(false);
        return;
      }
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
        const publicData = parseDashboardStatus(await fallback.json());
        if (!publicData) {
          setEndpoints([]);
          return;
        }
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
      const data = parseEndpointList(await response.json());
      if (!data) {
        setEndpoints([]);
        return;
      }
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
        const publicData = parseDashboardStatus(await fallback.json());
        if (!publicData) {
          setAgents([]);
          return;
        }
        setAgents(publicData.agents);
        return;
      }
      const data = parseAgentList(await response.json());
      if (!data) {
        setAgents([]);
        return;
      }
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
      const data = parseRoutingRuleList(await response.json());
      if (!data) {
        setRules([]);
        return;
      }
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
      const data = parseUsageStats(await response.json());
      if (!data) {
        setUsageStats(null);
        return;
      }
      setUsageStats(data);
    } catch {
      setUsageStats(null);
    }
  };

  const loadUsageTrend = async (
    authToken: string | null,
    nextRange: UsageTrendRange = usageTrendRange,
    nextDumpOffset = dumpSearchOffset
  ) => {
    if (!authToken) {
      setUsageTrendBuckets([]);
      setStatsOverview(null);
      setStatsTimeseries([]);
      setStatsLatency([]);
      setStatsModelDistribution([]);
      setStatsGroupDistribution([]);
      setStatsTopKeys([]);
      setDumpSearch(null);
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
      const query = `hours=${config.hours}&bucket_minutes=${config.bucketMinutes}`;
      const headers = buildHeaders(authToken);
      const fetchJson = async (path: string) => {
        const response = await fetch(`${apiBase}${path}`, { headers });
        if (!response.ok) {
          throw new Error(path);
        }
        return response.json();
      };
      const [
        overviewPayload,
        timeseriesPayload,
        latencyPayload,
        modelDistributionPayload,
        groupDistributionPayload,
        topKeysPayload,
        dumpSearchPayload,
      ] = await Promise.all([
        fetchJson(`/admin/stats/overview?hours=${config.hours}`),
        fetchJson(`/admin/stats/timeseries?${query}`),
        fetchJson(`/admin/stats/latency-percentiles?${query}`),
        fetchJson(`/admin/stats/distribution/models?hours=${config.hours}`),
        fetchJson(`/admin/stats/distribution/groups?hours=${config.hours}`),
        fetchJson(`/admin/stats/top-keys?hours=${config.hours}&limit=10`),
        fetchJson(`/admin/dump/search?hours=${config.hours}&limit=20&offset=${nextDumpOffset}`),
      ]);
      const overview = parseStatsOverview(overviewPayload);
      const timeseries = parseStatsTimeseriesBucketList(timeseriesPayload);
      const latency = parseStatsLatencyBucketList(latencyPayload);
      const modelDistribution = parseStatsDistributionList(modelDistributionPayload);
      const groupDistribution = parseStatsDistributionList(groupDistributionPayload);
      const topKeys = parseStatsTopKeyList(topKeysPayload);
      const dumpResult = parseDumpSearchResult(dumpSearchPayload);
      if (
        !overview ||
        !timeseries ||
        !latency ||
        !modelDistribution ||
        !groupDistribution ||
        !topKeys ||
        !dumpResult
      ) {
        throw new Error("invalid stats response");
      }
      setStatsOverview(overview);
      setStatsTimeseries(timeseries);
      setStatsLatency(latency);
      setStatsModelDistribution(modelDistribution);
      setStatsGroupDistribution(groupDistribution);
      setStatsTopKeys(topKeys);
      setDumpSearch(dumpResult);
      setUsageTrendBuckets(
        timeseries.map((bucket) => ({
          bucket_start: bucket.bucket_start,
          request_count: bucket.request_count,
          rps: 0,
          prompt_tokens: bucket.prompt_tokens,
          completion_tokens: bucket.completion_tokens,
          total_tokens: bucket.total_tokens,
          avg_latency_ms: bucket.avg_latency_ms,
        }))
      );
      setUsageTrendUpdatedAt(new Date().toISOString());
      setUsageTrendError(null);
    } catch {
      setUsageTrendBuckets([]);
      setStatsOverview(null);
      setStatsTimeseries([]);
      setStatsLatency([]);
      setStatsModelDistribution([]);
      setStatsGroupDistribution([]);
      setStatsTopKeys([]);
      setDumpSearch(null);
      setUsageTrendError("无法获取趋势数据");
    } finally {
      setUsageTrendLoading(false);
    }
  };

  const handleUsageRangeChange = (range: UsageTrendRange) => {
    setDumpSearchOffset(0);
    setUsageTrendRange(range);
    void loadUsageTrend(token, range, 0);
  };

  const handleUsageRefresh = () => {
    void loadUsageTrend(token, usageTrendRange, dumpSearchOffset);
  };

  const handleDumpSearchPageChange = (offset: number) => {
    const nextOffset = Math.max(0, offset);
    setDumpSearchOffset(nextOffset);
    void loadUsageTrend(token, usageTrendRange, nextOffset);
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
      const data = parseHealthStatusList(await response.json());
      if (!data) {
        setHealthStatusMap({});
        return;
      }
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
      const data = parseTelegramConfig(await response.json());
      if (!data) {
        setTelegramConfig(null);
        return;
      }
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
      const data = (await response.json()) as { token?: unknown };
      const issuedToken = typeof data.token === "string" ? data.token.trim() : "";
      if (!issuedToken) {
        setIsAdmin(false);
        return;
      }
      if (
        typeof localStorage !== "undefined" &&
        typeof localStorage.setItem === "function"
      ) {
        localStorage.setItem(tokenStorageKey, issuedToken);
      }
      setToken(issuedToken);
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
      const data = parseTelegramConfig(await response.json());
      if (!data) {
        return { ok: false, message: "服务端返回了无效的 Telegram 配置" };
      }
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
  };
};
