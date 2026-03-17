import "@testing-library/jest-dom";

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { Console } from "./Console";

const buildJsonResponse = (payload: unknown) =>
  Promise.resolve(
    new Response(JSON.stringify(payload), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    })
  );

const buildFetchMock = () =>
  vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    if (url.includes("/admin/endpoints/1/keys") && init?.method === "POST") {
      return buildJsonResponse({ status: "ok" });
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
          uptime: 97.5,
          is_agent_enabled: false,
          agent_node: null,
          model_count: 2,
          keys: [
            {
              id: 10,
              key_preview: "sk-...1234",
              rule_group: "default",
              name: "Main",
              rpm_limit: 100,
              daily_limit: 1000,
              used_today: 100,
              is_active: true,
            },
            {
              id: 11,
              key_preview: "sk-...5678",
              rule_group: "canary",
              name: "Canary",
              rpm_limit: 60,
              daily_limit: 500,
              used_today: 40,
              is_active: true,
            },
          ],
        },
      ]);
    }
    if (url.includes("/admin/agents")) {
      return buildJsonResponse([]);
    }
    if (url.includes("/admin/rules")) {
      return buildJsonResponse([
        {
          id: 1,
          model_pattern: "gpt-4.*",
          group_name: "default",
          priority: 10,
          strategy: "weighted_round_robin",
          is_active: true,
          target_key_ids: [10],
        },
        {
          id: 2,
          model_pattern: "claude-3.*",
          group_name: "canary",
          priority: 8,
          strategy: "weighted_round_robin",
          is_active: true,
          target_key_ids: [11],
        },
      ]);
    }
    if (url.includes("/admin/stats/usage")) {
      return buildJsonResponse({ groups: [], top_keys: [], generated_at: "" });
    }
    if (url.includes("/auth/me")) {
      return buildJsonResponse({ role: "admin", is_admin: true });
    }
    return buildJsonResponse([]);
  });

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

describe("Console endpoints", () => {
  let fetchMock: ReturnType<typeof buildFetchMock>;

  beforeEach(() => {
    installLocalStorageMock();
    fetchMock = buildFetchMock();
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("renders endpoint cards", async () => {
    render(<Console />);

    expect(await screen.findByText("API 端点列表")).toBeInTheDocument();
    expect(await screen.findByText("OpenAI")).toBeInTheDocument();
    expect(await screen.findByText("基础连通延迟")).toBeInTheDocument();
    expect(await screen.findByText("通道健康度")).toBeInTheDocument();
    expect(await screen.findByText(/Key 负载池/)).toBeInTheDocument();
    expect(await screen.findByText("管理 Keys")).toBeInTheDocument();
  });

  it("creates endpoint key with selected rule group", async () => {
    const user = userEvent.setup();
    window.localStorage.setItem("llm_admin_token", "token");

    render(<Console />);

    await user.click(await screen.findByRole("button", { name: "管理 Keys" }));
    expect(await screen.findByText(/按分组管理上游 Key 配置/)).toBeInTheDocument();
    expect(await screen.findByText("System Group")).toBeInTheDocument();

    await user.selectOptions(
      screen.getByRole("combobox", { name: "按分组筛选 Key" }),
      "canary"
    );
    expect(screen.getByText("Canary")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "添加新 Key" }));
    await user.type(screen.getByRole("textbox", { name: "API Key" }), "sk-new-key");
    await user.type(screen.getByRole("textbox", { name: "Key 备注名称" }), "Canary B");
    await user.selectOptions(screen.getByRole("combobox", { name: "Key 分组" }), "canary");
    await user.type(screen.getByRole("spinbutton", { name: "每日配额" }), "300");
    await user.type(screen.getByRole("spinbutton", { name: "RPM 限额" }), "30");
    await user.click(screen.getByRole("button", { name: "保存" }));

    const createCall = fetchMock.mock.calls.find(
      ([url, init]) =>
        url.toString().includes("/admin/endpoints/1/keys") &&
        (init as RequestInit | undefined)?.method === "POST"
    );
    expect(createCall).toBeDefined();
    const createInit = createCall?.[1] as RequestInit | undefined;
    expect(createInit?.body).toBeDefined();
    expect(JSON.parse(createInit?.body as string)).toEqual(
      expect.objectContaining({
        key: "sk-new-key",
        name: "Canary B",
        rule_group: "canary",
      })
    );
  });

  it("validates empty api key when creating", async () => {
    const user = userEvent.setup();
    window.localStorage.setItem("llm_admin_token", "token");

    render(<Console />);

    await user.click(await screen.findByRole("button", { name: "管理 Keys" }));
    await user.click(screen.getByRole("button", { name: "添加新 Key" }));
    await user.click(screen.getByRole("button", { name: "保存" }));

    expect(await screen.findByText("API Key 不能为空")).toBeInTheDocument();

    const createCall = fetchMock.mock.calls.find(
      ([url, init]) =>
        url.toString().includes("/admin/endpoints/1/keys") &&
        (init as RequestInit | undefined)?.method === "POST"
    );
    expect(createCall).toBeUndefined();
  });
});
