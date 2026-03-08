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
    if (url.includes("/admin/rules")) {
      if (init?.method === "DELETE") {
        return buildJsonResponse({ status: "ok" });
      }
      return buildJsonResponse([
        {
          id: 1,
          model_pattern: "gpt-4.*",
          group_name: "default",
          priority: 10,
          is_active: true,
          target_key_ids: [1],
          created_at: "2024-01-01T00:00:00Z",
          request_count: 12,
          total_tokens: 3200,
          avg_ttft_ms: 180,
          avg_tps: 12.5,
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

describe("Console rules", () => {
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

  it("shows routing rules", async () => {
    const user = userEvent.setup();
    render(<Console />);

    await user.click(await screen.findByText("路由规则"));
    expect(await screen.findByText("路由规则拓扑")).toBeInTheDocument();
    expect(await screen.findByText("gpt-4.*")).toBeInTheDocument();
    expect(await screen.findByText("TTFT")).toBeInTheDocument();
    expect(await screen.findByText("TPS")).toBeInTheDocument();
  });

  it("deletes routing rules", async () => {
    const user = userEvent.setup();
    window.localStorage.setItem("llm_admin_token", "token");
    vi.spyOn(window, "confirm").mockReturnValue(true);

    render(<Console />);

    await user.click(await screen.findByText("路由规则"));
    const deleteButtons = await screen.findAllByLabelText("删除规则");
    await user.click(deleteButtons[0]);

    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("/admin/rules/1"),
      expect.objectContaining({ method: "DELETE" })
    );
  });
});
