import { afterEach, describe, expect, it, vi } from "vitest";

import {
  readApiErrorMessage,
  useConsoleActions,
} from "./use-console-actions";
import {
  type EndpointFormState,
  type RoutingRuleSavePayload,
} from "./shared";

const jsonResponse = (payload: unknown, status = 200) =>
  new Response(JSON.stringify(payload), {
    status,
    headers: { "Content-Type": "application/json" },
  });

const endpointForm: EndpointFormState = {
  name: "OpenAI",
  base_url: "https://api.openai.com/v1",
  auth_header_name: "Authorization",
  auth_header_prefix: "Bearer",
  provider: "openai",
  agent_node: "",
  probe_interval_seconds: "",
  is_active: true,
};

const rulePayload: RoutingRuleSavePayload = {
  model_pattern: "gpt-.*",
  group_name: "default",
  exposure_format: "any",
  target_key_ids: [],
  priority: 10,
  strategy: "sequential",
  is_active: true,
  dump_enabled: false,
  dump_path: null,
};

const savedRule = {
  id: 42,
  model_pattern: "gpt-.*",
  group_name: "default",
  exposure_format: "any",
  target_key_ids: [],
  priority: 10,
  strategy: "sequential",
  is_active: true,
};

type ConsoleActionsOptions = Parameters<typeof useConsoleActions>[0];

const buildActions = (overrides: Partial<ConsoleActionsOptions> = {}) => {
  const options: ConsoleActionsOptions = {
    token: "adm.test",
    isAdmin: true,
    editingEndpoint: null,
    setEditingEndpoint: vi.fn(),
    manageKeysEndpoint: null,
    setManageKeysEndpoint: vi.fn(),
    editingRule: null,
    setEditingRule: vi.fn(),
    probeAliasEdits: {},
    setProbeEndpoint: vi.fn(),
    setProbeModels: vi.fn(),
    setProbeDiscoveredModels: vi.fn(),
    setProbeAliasEdits: vi.fn(),
    setProbeError: vi.fn(),
    setProbeLoading: vi.fn(),
    setAgentDeployResult: vi.fn(),
    setAgentDeployTarget: vi.fn(),
    setAgentDeployOpen: vi.fn(),
    loadEndpoints: vi.fn(),
    loadHealthStatus: vi.fn(),
    loadRules: vi.fn(),
    loadAgents: vi.fn(),
    ...overrides,
  };
  return useConsoleActions(options);
};

describe("console actions", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("extracts API error details from FastAPI validation payloads", async () => {
    const response = jsonResponse(
      { detail: [{ msg: "Invalid pattern" }, { msg: "Invalid path" }] },
      422
    );

    await expect(readApiErrorMessage(response, "fallback")).resolves.toBe(
      "Invalid pattern; Invalid path"
    );
  });

  it("keeps endpoint editor open and surfaces backend detail on save failure", async () => {
    const setEditingEndpoint = vi.fn();
    const loadEndpoints = vi.fn();
    const alertSpy = vi.spyOn(window, "alert").mockImplementation(() => undefined);
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.resolve(jsonResponse({ detail: "Endpoint already exists" }, 409)))
    );

    const actions = buildActions({ setEditingEndpoint, loadEndpoints });

    await expect(actions.handleSaveEndpoint(endpointForm)).resolves.toBe(false);
    expect(alertSpy).toHaveBeenCalledWith("Endpoint already exists");
    expect(loadEndpoints).not.toHaveBeenCalled();
    expect(setEditingEndpoint).not.toHaveBeenCalled();
  });

  it("sends direct access mode when endpoint agent is cleared", async () => {
    const loadEndpoints = vi.fn();
    const fetchMock = vi.fn(() => Promise.resolve(jsonResponse({ id: 1 })));
    vi.stubGlobal("fetch", fetchMock);

    const actions = buildActions({ loadEndpoints });

    await expect(actions.handleSaveEndpoint(endpointForm)).resolves.toBe(true);

    const [, init] = (fetchMock.mock.calls as unknown as [string, RequestInit][])[0];
    expect(JSON.parse(String(init.body))).toMatchObject({
      access_mode: "direct",
      agent_node: null,
    });
    expect(loadEndpoints).toHaveBeenCalledWith("adm.test");
  });

  it("sends via_agent access mode when endpoint agent is selected", async () => {
    const fetchMock = vi.fn(() => Promise.resolve(jsonResponse({ id: 1 })));
    vi.stubGlobal("fetch", fetchMock);

    const actions = buildActions();

    await expect(
      actions.handleSaveEndpoint({ ...endpointForm, agent_node: "edge-vps" })
    ).resolves.toBe(true);

    const [, init] = (fetchMock.mock.calls as unknown as [string, RequestInit][])[0];
    expect(JSON.parse(String(init.body))).toMatchObject({
      access_mode: "via_agent",
      agent_node: "edge-vps",
    });
  });

  it("creates the endpoint and initial Codex credential atomically", async () => {
    const loadEndpoints = vi.fn();
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse({ id: 77 }))
      .mockResolvedValueOnce(
        jsonResponse({
          provider: "codex",
          probe_status: "success",
          probe_status_code: 200,
          probe_message: null,
          discovered_models: ["gpt-5.6-sol"],
          manual_models: [],
        })
      );
    vi.stubGlobal("fetch", fetchMock);

    const actions = buildActions({ loadEndpoints });
    await expect(
      actions.handleSaveEndpoint({
        ...endpointForm,
        provider: "codex",
        base_url: "https://chatgpt.com",
        initial_api_key: '{"access_token":"access","account_id":"account"}',
        initial_api_key_name: "Imported Codex",
      })
    ).resolves.toBe(true);

    expect(fetchMock).toHaveBeenCalledTimes(2);
    const [endpointUrl, endpointInit] = (
      fetchMock.mock.calls as unknown as [string, RequestInit][]
    )[0];
    expect(endpointUrl).toContain("/admin/endpoints");
    expect(JSON.parse(String(endpointInit.body))).toMatchObject({
      provider: "codex",
      initial_key: {
        key: '{"access_token":"access","account_id":"account"}',
        name: "Imported Codex",
        rule_groups: ["default"],
      },
    });
    expect(fetchMock.mock.calls[1][0]).toContain("/admin/endpoints/77/probe");
    expect(loadEndpoints).toHaveBeenCalledWith("adm.test");
  });

  it("keeps rule editor open and surfaces backend detail on save failure", async () => {
    const setEditingRule = vi.fn();
    const loadRules = vi.fn();
    const alertSpy = vi.spyOn(window, "alert").mockImplementation(() => undefined);
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.resolve(jsonResponse({ detail: "Unsafe dump path" }, 400)))
    );

    const actions = buildActions({ setEditingRule, loadRules });

    await expect(actions.handleSaveRule(rulePayload)).resolves.toBe(false);
    expect(alertSpy).toHaveBeenCalledWith("Unsafe dump path");
    expect(loadRules).not.toHaveBeenCalled();
    expect(setEditingRule).not.toHaveBeenCalled();
  });

  it("keeps rule editor open and surfaces backend detail on delete failure", async () => {
    const setEditingRule = vi.fn();
    const loadRules = vi.fn();
    const alertSpy = vi.spyOn(window, "alert").mockImplementation(() => undefined);
    vi.spyOn(window, "confirm").mockReturnValue(true);
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.resolve(jsonResponse({ detail: "Rule is still in use" }, 409)))
    );

    const actions = buildActions({
      editingRule: savedRule,
      setEditingRule,
      loadRules,
    });

    await actions.handleDeleteRule(savedRule);

    expect(alertSpy).toHaveBeenCalledWith("Rule is still in use");
    expect(loadRules).not.toHaveBeenCalled();
    expect(setEditingRule).not.toHaveBeenCalled();
  });
});
