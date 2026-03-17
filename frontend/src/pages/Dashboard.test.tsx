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
    if (url.includes("/auth/password")) {
      const payload = JSON.parse((init?.body as string) ?? "{}") as {
        current_password?: string;
        new_password?: string;
      };
      if (payload.current_password !== "token") {
        return buildJsonResponse({ detail: "Current password is incorrect" }, 400);
      }
      return buildJsonResponse({
        token: payload.new_password,
        updated_at: "2026-03-16T00:00:00Z",
      });
    }
    if (url.includes("/admin/telegram/config")) {
      return buildJsonResponse({
        configured: true,
        bot_token_masked: "1234...abcd",
        chat_id: "-100123",
      });
    }
    if (url.includes("/admin/telegram/test")) {
      return buildJsonResponse({ status: "ok", detail: "测试消息已发送" });
    }
    if (init?.method === "DELETE") {
      return buildJsonResponse({ status: "ok" });
    }
    return buildJsonResponse([]);
  });
};

const createLocalStorageMock = (): Storage => {
  const store = new Map<string, string>();
  return {
    getItem: (key: string) => (store.has(key) ? store.get(key) ?? null : null),
    setItem: (key: string, value: string) => {
      store.set(key, String(value));
    },
    removeItem: (key: string) => {
      store.delete(key);
    },
    clear: () => {
      store.clear();
    },
    key: (index: number) => Array.from(store.keys())[index] ?? null,
    get length() {
      return store.size;
    },
  } as Storage;
};

const installLocalStorageMock = () => {
  Object.defineProperty(window, "localStorage", {
    value: createLocalStorageMock(),
    configurable: true,
  });
};

describe("Console layout", () => {
  let fetchMock: ReturnType<typeof buildFetchMock>;

  beforeEach(() => {
    installLocalStorageMock();
    fetchMock = buildFetchMock();
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("renders guest navigation tabs", async () => {
    render(<Console />);

    expect(await screen.findByText("端点管理")).toBeInTheDocument();
    expect(screen.queryByText("节点管理")).not.toBeInTheDocument();
    expect(screen.queryByText("路由规则")).not.toBeInTheDocument();
    expect(screen.queryByText("流量统计")).not.toBeInTheDocument();
    expect(screen.queryByText("系统设置")).not.toBeInTheDocument();
    expect(screen.queryByText("v2.0-Probe")).not.toBeInTheDocument();
  });

  it("toggles to light theme", async () => {
    const user = userEvent.setup();
    render(<Console />);

    const themeButton = await screen.findByRole("button", { name: "切换主题" });
    const root = document.querySelector("div.min-h-screen");
    expect(root).not.toHaveClass("theme-light");

    await user.click(themeButton);

    expect(root).toHaveClass("theme-light");
  });

  it("switches to agent view", async () => {
    const user = userEvent.setup();
    window.localStorage.setItem("llm_admin_token", "token");
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

  it("updates admin password in settings", async () => {
    const user = userEvent.setup();
    window.localStorage.setItem("llm_admin_token", "token");
    render(<Console />);

    await user.click(await screen.findByText("管理员"));
    await user.click(await screen.findByText("系统设置"));
    await user.type(screen.getByPlaceholderText("输入当前密码"), "token");
    await user.type(screen.getByPlaceholderText("至少 4 位"), "next-token");
    await user.click(screen.getByRole("button", { name: "更新密码" }));

    expect(await screen.findByText("管理员密码已更新")).toBeInTheDocument();
    const updateCall = fetchMock.mock.calls.find(([url]) =>
      url.toString().includes("/auth/password")
    );
    expect(updateCall).toBeDefined();
  });
});
