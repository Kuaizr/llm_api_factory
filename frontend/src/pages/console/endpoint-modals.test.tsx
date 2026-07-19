import "@testing-library/jest-dom";

import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { EditEndpointModal, KeyConfigModal, ManageKeysModal } from "./endpoint-modals";
import { type Endpoint } from "./shared";

const baseEndpoint: Endpoint = {
  id: 1,
  name: "OpenAI",
  base_url: "https://api.openai.com/v1",
  auth_header_name: "Authorization",
  auth_header_prefix: "Bearer",
  provider: "openai",
  access_mode: "direct",
  is_active: true,
  status: "online",
  latency: 0,
  uptime: 100,
  is_agent_enabled: false,
  agent_node: null,
  probe_interval_seconds: null,
  model_count: 0,
  keys: [],
  strategy: "weighted_round_robin",
};

describe("EditEndpointModal", () => {
  it("hides custom endpoint extensions for standard providers", () => {
    render(
      <EditEndpointModal
        endpoint={baseEndpoint}
        agents={[]}
        isAdmin
        onClose={vi.fn()}
        onSave={vi.fn()}
      />
    );

    expect(screen.queryByText("额外请求头 (JSON)")).not.toBeInTheDocument();
    expect(screen.queryByText("额外查询参数 (JSON)")).not.toBeInTheDocument();
    expect(screen.queryByText("OAuth 配置 (JSON)")).not.toBeInTheDocument();
    expect(screen.queryByText("请求体模板 (JSON)")).not.toBeInTheDocument();
  });

  it("shows custom endpoint extensions for custom providers", () => {
    render(
      <EditEndpointModal
        endpoint={{ ...baseEndpoint, provider: "custom" }}
        agents={[]}
        isAdmin
        onClose={vi.fn()}
        onSave={vi.fn()}
      />
    );

    expect(screen.getByText("额外请求头 (JSON)")).toBeInTheDocument();
    expect(screen.getByText("额外查询参数 (JSON)")).toBeInTheDocument();
    expect(screen.getByText("OAuth 配置 (JSON)")).toBeInTheDocument();
    expect(screen.getByText("请求体模板 (JSON)")).toBeInTheDocument();
  });

  it("accepts pasted Codex JSON while creating an endpoint", async () => {
    const user = userEvent.setup();
    const onSave = vi.fn().mockResolvedValue(true);
    render(
      <EditEndpointModal
        endpoint={null}
        agents={[]}
        isAdmin
        onClose={vi.fn()}
        onSave={onSave}
      />
    );

    await act(async () => {
      await user.selectOptions(screen.getByDisplayValue("OpenAI Compatible"), "codex");
      await user.click(screen.getByLabelText("Codex Auth JSON"));
      await user.paste(
        '{"access_token":"access","account_id":"account","email":"not-persisted"}'
      );
      await user.click(screen.getByRole("button", { name: "保存修改" }));
    });

    expect(onSave).toHaveBeenCalledWith(
      expect.objectContaining({
        provider: "codex",
        base_url: "https://chatgpt.com",
        initial_api_key: JSON.stringify(
          { access_token: "access", account_id: "account" },
          null,
          2
        ),
      })
    );
  });

  it("shows Codex JSON import controls in API key management", () => {
    render(
      <KeyConfigModal
        endpointId={baseEndpoint.id}
        provider="codex"
        authToken="adm.test"
        isAdmin
        availableRuleGroups={["default"]}
        onClose={vi.fn()}
        onSave={vi.fn()}
      />
    );

    expect(screen.getByLabelText("上传 Codex Auth JSON")).toBeInTheDocument();
    expect(screen.getByLabelText("Codex Auth JSON")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /粘贴 JSON/ })).toBeInTheDocument();
    expect(screen.queryByLabelText("API Key")).not.toBeInTheDocument();
  });

  it("shows usage windows for every Codex API key", () => {
    render(
      <ManageKeysModal
        endpoint={{
          ...baseEndpoint,
          provider: "codex",
          keys: [
            {
              id: 11,
              key_preview: "json-...one",
              rule_group: "default",
              rpm_limit: null,
              daily_limit: null,
              used_today: 0,
              is_active: true,
              name: "Auth One",
              codex_usage: {
                primary: { used_percent: 12.5, window_minutes: 300 },
                secondary: { used_percent: 25, window_minutes: 10080 },
              },
            },
            {
              id: 12,
              key_preview: "json-...two",
              rule_group: "default",
              rpm_limit: null,
              daily_limit: null,
              used_today: 0,
              is_active: true,
              name: "Auth Two",
              codex_usage: {
                primary: { used_percent: 37.5, window_minutes: 300 },
                secondary: { used_percent: 50, window_minutes: 10080 },
              },
            },
          ],
        }}
        isAdmin
        authToken="adm.test"
        healthStatusMap={{}}
        availableRuleGroups={["default"]}
        onClose={vi.fn()}
        onCreate={vi.fn()}
        onUpdate={vi.fn()}
        onDelete={vi.fn()}
        onRefresh={vi.fn()}
      />
    );

    expect(screen.getAllByLabelText("Codex 5h 用量")).toHaveLength(2);
    expect(screen.getAllByLabelText("Codex 1w 用量")).toHaveLength(2);
    expect(screen.getByText("12.5%")).toBeInTheDocument();
    expect(screen.getByText("37.5%")).toBeInTheDocument();
    expect(screen.getByText("50.0%")).toBeInTheDocument();
  });

  it("keeps API key form open when save fails", async () => {
    const user = userEvent.setup();
    const onSave = vi.fn().mockResolvedValue(false);

    render(
      <KeyConfigModal
        endpointId={baseEndpoint.id}
        authToken="adm.test"
        isAdmin
        availableRuleGroups={["default"]}
        onClose={vi.fn()}
        onSave={onSave}
      />
    );

    await act(async () => {
      await user.type(screen.getByLabelText("API Key"), "sk-test");
      await user.click(screen.getByRole("button", { name: "保存" }));
    });

    expect(onSave).toHaveBeenCalled();
    expect(await screen.findByText("保存失败，请稍后再试。")).toBeInTheDocument();
    expect(screen.getByText("添加新 API Key")).toBeInTheDocument();
  });
});
