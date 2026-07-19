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
    if (url.includes("/admin/endpoints")) {
      return buildJsonResponse([]);
    }
    if (url.includes("/admin/agents")) {
      return buildJsonResponse([]);
    }
    if (url.includes("/admin/rules/1/access-keys")) {
      if (init?.method === "POST" || init?.method === "PATCH" || init?.method === "DELETE") {
        return buildJsonResponse({ status: "ok" });
      }
      return buildJsonResponse([
        {
          id: 101,
          rule_id: 1,
          name: "default-client",
          key_preview: "rk-...abcd",
          key: "rk-secret-1234",
          is_active: true,
          created_at: "2024-01-01T00:00:00Z",
        },
      ]);
    }
    if (url.includes("/admin/rules")) {
      if (init?.method === "DELETE" || init?.method === "PATCH") {
        return buildJsonResponse({ status: "ok" });
      }
      return buildJsonResponse([
        {
          id: 1,
          model_pattern: "gpt-4.*",
          group_name: "default",
          exposure_formats: ["chat", "response", "codex", "message", "claude_code", "gemini"],
          priority: 10,
          is_active: true,
          target_key_ids: [1],
          dump_enabled: false,
          dump_path: null,
          created_at: "2024-01-01T00:00:00Z",
          request_count: 12,
          total_tokens: 3200,
          avg_ttft_ms: 180,
          avg_tps: 12.5,
        },
        {
          id: 2,
          model_pattern: "claude-3.*",
          group_name: "canary",
          exposure_formats: ["message", "claude_code"],
          priority: 8,
          is_active: true,
          target_key_ids: [2],
          dump_enabled: false,
          dump_path: null,
          created_at: "2024-01-02T00:00:00Z",
          request_count: 6,
          total_tokens: 1200,
          avg_ttft_ms: 220,
          avg_tps: 9.5,
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

describe("Console rules", () => {
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

  it("shows routing rules", async () => {
    const user = userEvent.setup();
    window.localStorage.setItem("llm_admin_token", "token");
    render(<Console />);

    await user.click(await screen.findByText("路由规则"));
    expect(await screen.findByText("路由规则拓扑")).toBeInTheDocument();
    expect(await screen.findByText("gpt-4.*")).toBeInTheDocument();
    expect(await screen.findByText("System Group")).toBeInTheDocument();
  });

  it("supports selecting multiple API entries for a new routing rule", async () => {
    const user = userEvent.setup();
    window.localStorage.setItem("llm_admin_token", "token");
    render(<Console />);

    await user.click(await screen.findByText("路由规则"));
    await user.click(await screen.findByRole("button", { name: "新建规则" }));

    expect(screen.getByText("可用 API 入口")).toBeInTheDocument();
    const chatEntry = screen.getByRole("button", { name: "Chat" });
    const responsesEntry = screen.getByRole("button", { name: "Responses" });
    expect(chatEntry).toHaveAttribute("aria-pressed", "true");
    expect(responsesEntry).toHaveAttribute("aria-pressed", "false");
    await user.click(responsesEntry);
    expect(chatEntry).toHaveAttribute("aria-pressed", "true");
    expect(responsesEntry).toHaveAttribute("aria-pressed", "true");
  });

  it("copies access key from rule key list", async () => {
    const user = userEvent.setup();
    window.localStorage.setItem("llm_admin_token", "token");
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText },
      configurable: true,
    });

    render(<Console />);

    await user.click(await screen.findByText("路由规则"));
    const manageButtons = await screen.findAllByTitle("管理访问 Key");
    await user.click(manageButtons[0]);
    const copyButton = await screen.findByTitle("复制完整 Key");
    await user.click(copyButton);

    expect(writeText).toHaveBeenCalledWith("rk-secret-1234");
  });

  it("updates dump settings on rule edit", async () => {
    const user = userEvent.setup();
    window.localStorage.setItem("llm_admin_token", "token");

    render(<Console />);

    await user.click(await screen.findByText("路由规则"));
    const editButtons = await screen.findAllByLabelText("编辑规则");
    await user.click(editButtons[1]);

    expect(await screen.findByText("编辑路由规则")).toBeInTheDocument();
    const dumpSwitch = screen.getByRole("checkbox", { name: "启用" });
    await user.click(dumpSwitch);
    await user.type(screen.getByPlaceholderText("例如: /tmp/llm-dumps"), "/tmp/router-dump");
    await user.click(screen.getByRole("button", { name: "保存规则" }));

    const patchCall = fetchMock.mock.calls.find(
      ([url, init]) =>
        url.toString().includes("/admin/rules/2") &&
        (init as RequestInit | undefined)?.method === "PATCH"
    );
    expect(patchCall).toBeDefined();
    const patchInit = patchCall?.[1] as RequestInit | undefined;
    expect(patchInit?.body).toBeDefined();
    expect(JSON.parse(patchInit?.body as string)).toEqual(
      expect.objectContaining({
        dump_enabled: true,
        dump_path: "/tmp/router-dump",
      })
    );
  });

  it("deletes routing rules", async () => {
    const user = userEvent.setup();
    window.localStorage.setItem("llm_admin_token", "token");
    vi.spyOn(window, "confirm").mockReturnValue(true);

    render(<Console />);

    await user.click(await screen.findByText("路由规则"));
    const deleteButtons = await screen.findAllByLabelText("删除规则");
    const deletableButton = deleteButtons.find(
      (button) => !button.hasAttribute("disabled")
    );
    expect(deletableButton).toBeTruthy();
    await user.click(deletableButton!);

    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("/admin/rules/2"),
      expect.objectContaining({ method: "DELETE" })
    );
  });
});
