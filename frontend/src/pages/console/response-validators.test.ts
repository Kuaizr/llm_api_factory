import { describe, expect, it } from "vitest";

import {
  parseAgentBootstrapResult,
  parseApiKeyDirectTestResult,
  parseDashboardAlertPolicyList,
  parseDashboardHealthProbeBucketList,
  parseDashboardHealthStatusList,
  parseDashboardOverview,
  parseDashboardStatus,
  parseEndpointList,
  parseEndpointProbeResult,
  parseHealthStatusList,
  parseModelMap,
  parseModelMapList,
  parseRuleGroupEligibilityResult,
  parseRoutingRuleList,
  parseStringList,
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

  it("validates dashboard response payloads", () => {
    expect(
      parseDashboardOverview({
        endpoints: 1,
        api_keys: 2,
        model_maps: 3,
        request_logs: 4,
        generated_at: "2026-07-05T00:00:00Z",
      })?.request_logs
    ).toBe(4);
    expect(parseDashboardOverview({ endpoints: "1" })).toBeNull();

    expect(
      parseDashboardHealthStatusList([
        {
          api_key_id: 1,
          endpoint_id: 2,
          endpoint_name: "OpenAI",
          rule_group: "default",
          is_active: true,
          probe_status: "success",
          probe_status_code: 200,
          probe_latency_ms: 120,
          probe_checked_at: null,
          probe_real_model: "gpt-5",
          circuit_state: "closed",
          circuit_failures: 0,
          circuit_ttl_seconds: null,
        },
      ])?.[0].endpoint_name
    ).toBe("OpenAI");
    expect(parseDashboardHealthStatusList([{ api_key_id: 1 }])).toBeNull();

    expect(
      parseDashboardHealthProbeBucketList([
        {
          bucket_start: "2026-07-05T00:00:00Z",
          success_count: 1,
          failure_count: 2,
          error_count: 3,
          avg_latency_ms: null,
        },
      ])?.[0].error_count
    ).toBe(3);
    expect(parseDashboardHealthProbeBucketList([{ bucket_start: 1 }])).toBeNull();

    expect(
      parseDashboardAlertPolicyList([
        {
          event: "probe_latency",
          enabled: true,
          silence_until: null,
          threshold_ms: 2000,
        },
      ])?.[0].threshold_ms
    ).toBe(2000);
    expect(parseDashboardAlertPolicyList([{ event: "probe_latency" }])).toBeNull();
  });

  it("validates console action response payloads", () => {
    const modelMap = {
      id: 1,
      endpoint_id: 2,
      model_alias: "gpt-5",
      real_model: "gpt-5-2026",
      probe_managed: true,
      created_at: "2026-07-05T00:00:00Z",
    };

    expect(parseStringList(["gpt-5", "claude"])).toEqual(["gpt-5", "claude"]);
    expect(parseStringList(["gpt-5", 5])).toBeNull();
    expect(parseModelMap(modelMap)?.real_model).toBe("gpt-5-2026");
    expect(parseModelMapList([modelMap])?.[0].model_alias).toBe("gpt-5");
    expect(parseModelMap({ ...modelMap, endpoint_id: "2" })).toBeNull();

    expect(
      parseEndpointProbeResult({
        provider: "openai",
        probe_status: "success",
        probe_status_code: 200,
        probe_message: null,
        discovered_models: ["gpt-5"],
        manual_models: [modelMap],
      })?.manual_models[0].id
    ).toBe(1);
    expect(
      parseEndpointProbeResult({
        provider: "openai",
        probe_status: "weird",
        probe_status_code: 200,
        probe_message: null,
        discovered_models: [],
        manual_models: [],
      })
    ).toBeNull();

    expect(
      parseAgentBootstrapResult({
        agent_id: 1,
        name: "edge",
        token: "agent-token",
        install_command: "curl ...",
      })?.name
    ).toBe("edge");
    expect(parseAgentBootstrapResult({ agent_id: 1 })).toBeNull();

    expect(
      parseRuleGroupEligibilityResult({
        group_name: "claude",
        eligible: true,
        reason: null,
        probed: false,
        required_patterns: ["claude-*"],
        matched_models: ["claude-opus"],
      })?.eligible
    ).toBe(true);
    expect(
      parseRuleGroupEligibilityResult({
        group_name: "claude",
        eligible: "yes",
        reason: null,
        probed: false,
        required_patterns: [],
        matched_models: [],
      })
    ).toBeNull();

    expect(
      parseApiKeyDirectTestResult({
        api_key_id: 1,
        endpoint_id: 2,
        endpoint_name: "anyrouter",
        provider: "openai",
        request_template: "chat",
        model: "gpt-5",
        prompt: "你是什么模型",
        status_code: 200,
        ok: true,
        latency_ms: 123,
        output_text: "GPT",
        error_reason: null,
        upstream_url: "https://example.test/v1/chat/completions",
        raw_response: { ok: true },
      })?.ok
    ).toBe(true);
    expect(
      parseApiKeyDirectTestResult({
        api_key_id: 1,
        endpoint_id: 2,
        endpoint_name: "anyrouter",
        provider: "openai",
        request_template: "chat",
        model: "gpt-5",
        prompt: "你是什么模型",
        status_code: "200",
        ok: true,
        latency_ms: 123,
        output_text: "GPT",
        error_reason: null,
        upstream_url: "https://example.test/v1/chat/completions",
        raw_response: { ok: true },
      })
    ).toBeNull();
  });
});
