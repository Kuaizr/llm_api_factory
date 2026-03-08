import "@testing-library/jest-dom";

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { Console } from "./Console";

type FetchMockOptions = {
  bootstrapStatus?: number;
  bootstrapPayload?: unknown;
};

const buildJsonResponse = (payload: unknown, status = 200) =>
  Promise.resolve(
    new Response(JSON.stringify(payload), {
      status,
      headers: { "Content-Type": "application/json" },
    })
  );

const buildFetchMock = (options: FetchMockOptions = {}) => {
  const {
    bootstrapStatus = 200,
    bootstrapPayload = {
      agent_id: 1,
      name: "edge-hk",
      token: "agent-token",
      install_command: "curl -fsSL https://install.example.com | bash",
    },
  } = options;
  return vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    if (url.includes("/admin/agents/bootstrap")) {
      return buildJsonResponse(bootstrapPayload, bootstrapStatus);
    }
    if (url.includes("/admin/endpoints")) {
      return buildJsonResponse([
        {
          id: 1,
          name: "OpenAI",
          base_url: "https://api.openai.com/v1",
          provider: "openai",
          strategy: "weighted_round_robin",
          status: "online",
          latency: 120,
          uptime: 99.9,
          is_agent_enabled: false,
          agent_node: null,
          model_count: 2,
          keys: [],
        },
      ]);
    }
    if (url.includes("/admin/agents")) {
      return buildJsonResponse([
        {
          id: 1,
          name: "edge-hk",
          region: "hk",
          status: "online",
          last_seen_at: "2024-01-01T00:00:00Z",
          endpoint_url: null,
        },
      ]);
    }
    if (url.includes("/admin/rules")) {
      return buildJsonResponse([]);
    }
    if (url.includes("/admin/stats/usage")) {
      return buildJsonResponse({ groups: [], top_keys: [], generated_at: "" });
    }
    if (url.includes("/admin/metrics/timeseries")) {
      return buildJsonResponse([]);
    }
    if (url.includes("/auth/me")) {
      return buildJsonResponse({ role: "admin", is_admin: true });
    }
    if (init?.method === "DELETE") {
      return buildJsonResponse({ status: "ok" });
    }
    return buildJsonResponse([]);
  });
};

describe("Console layout", () => {
  let fetchMock: ReturnType<typeof buildFetchMock>;

  beforeEach(() => {
    window.localStorage.clear();
    fetchMock = buildFetchMock();
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    window.localStorage.clear();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("renders navigation tabs", async () => {
    render(<Console />);

    expect(await screen.findByText("端点管理")).toBeInTheDocument();
    expect(screen.getByText("节点管理")).toBeInTheDocument();
    expect(screen.getByText("路由规则")).toBeInTheDocument();
    expect(screen.getByText("流量统计")).toBeInTheDocument();
    expect(screen.getByText("系统设置")).toBeInTheDocument();
  });

  it("switches to agent view", async () => {
    const user = userEvent.setup();
    render(<Console />);

    await user.click(await screen.findByText("节点管理"));
    expect(await screen.findByText("Agent 节点网络")).toBeInTheDocument();
  });

  it("switches usage trend range", async () => {
    const user = userEvent.setup();
    window.localStorage.setItem("llm_admin_token", "token");
    render(<Console />);

    await user.click(await screen.findByText("流量统计"));
    const rangeSelect = await screen.findByRole("combobox");
    await user.selectOptions(rangeSelect, "day");

    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("bucket_minutes=1440"),
      expect.anything()
    );
  });

  it("builds agent bootstrap command", async () => {
    const user = userEvent.setup();
    window.localStorage.setItem("llm_admin_token", "token");
    render(<Console />);

    await user.click(await screen.findByText("节点管理"));
    await user.click(await screen.findByText("部署新节点"));

    await user.type(await screen.findByLabelText("节点名称"), "edge-hk");
    await user.click(screen.getByRole("button", { name: "生成部署命令" }));

    const bootstrapCall = fetchMock.mock.calls.find(([url]) =>
      url.toString().includes("/admin/agents/bootstrap")
    );
    expect(bootstrapCall).toBeDefined();
    const bootstrapInit = bootstrapCall?.[1] as RequestInit | undefined;
    expect(bootstrapInit?.method).toBe("POST");
    expect(JSON.parse(bootstrapInit?.body as string)).toEqual({
      name: "edge-hk",
    });

    expect(await screen.findByText("一键部署命令")).toBeInTheDocument();
    expect(await screen.findByDisplayValue("agent-token")).toBeInTheDocument();
  });

  it("surfaces bootstrap errors", async () => {
    fetchMock = buildFetchMock({
      bootstrapStatus: 400,
      bootstrapPayload: { detail: "Agent install script URL missing" },
    });
    vi.stubGlobal("fetch", fetchMock);

    const user = userEvent.setup();
    window.localStorage.setItem("llm_admin_token", "token");
    render(<Console />);

    await user.click(await screen.findByText("节点管理"));
    await user.click(await screen.findByText("部署新节点"));
    await user.type(await screen.findByLabelText("节点名称"), "edge-hk");
    await user.click(screen.getByRole("button", { name: "生成部署命令" }));

    expect(await screen.findByText("Agent install script URL missing")).toBeInTheDocument();
  });
});
