import { describe, expect, it } from "vitest";

import {
  parseDashboardStatus,
  parseEndpointList,
  parseHealthStatusList,
  parseRoutingRuleList,
  parseUsageStats,
} from "./response-validators";

describe("console response validators", () => {
  it("accepts valid endpoint payloads and rejects malformed entries", () => {
    const valid = parseEndpointList([
      {
        id: 1,
        name: "OpenAI",
        base_url: "https://api.openai.com/v1",
        provider: "openai",
        is_active: true,
        status: "online",
        latency: 120,
        uptime: 99.9,
        is_agent_enabled: false,
        agent_node: null,
        probe_interval_seconds: null,
        model_count: 2,
        keys: [
          {
            id: 10,
            key_preview: "sk-...test",
            rpm_limit: null,
            daily_limit: null,
            used_today: 0,
            is_active: true,
          },
        ],
        strategy: "sequential",
      },
    ]);

    expect(valid?.[0].keys[0].key_preview).toBe("sk-...test");
    expect(parseEndpointList([{ id: 1, name: "broken" }])).toBeNull();
  });

  it("validates nested dashboard, rules, usage, and health payloads", () => {
    expect(
      parseDashboardStatus({
        endpoints: [
          {
            id: 1,
            name: "Public",
            base_url: "https://api.example.com",
            provider: "openai",
            status: "online",
            latency: 1,
            uptime: 100,
            agent_node: null,
            probe_interval_seconds: null,
          },
        ],
        agents: [
          {
            id: 1,
            name: "edge",
            status: "online",
            last_seen_at: null,
            endpoint_url: null,
            region: null,
          },
        ],
      })
    ).not.toBeNull();

    expect(
      parseRoutingRuleList([
        {
          id: 1,
          model_pattern: "gpt-.*",
          group_name: "default",
          target_key_ids: [1, 2],
          priority: 10,
          strategy: "sequential",
          is_active: true,
        },
      ])
    ).not.toBeNull();

    expect(
      parseUsageStats({
        groups: [{ group_name: "default", percent: 100, total_tokens: 12 }],
        top_keys: [
          {
            api_key_id: 1,
            endpoint_name: "OpenAI",
            key_preview: "sk-...",
            total_tokens: 12,
          },
        ],
        total_tokens_today: 12,
        generated_at: "2026-07-05T00:00:00Z",
      })
    ).not.toBeNull();

    expect(
      parseHealthStatusList([
        {
          api_key_id: 1,
          probe_status: "success",
          probe_status_code: 200,
          probe_latency_ms: 120,
          probe_checked_at: null,
          circuit_state: "closed",
          circuit_failures: 0,
        },
      ])
    ).not.toBeNull();
    expect(parseHealthStatusList([{ api_key_id: "1" }])).toBeNull();
  });
});
