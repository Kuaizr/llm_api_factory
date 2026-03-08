import "@testing-library/jest-dom";

import { render, screen } from "@testing-library/react";
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
  vi.fn((input: RequestInfo | URL) => {
    const url = typeof input === "string" ? input : input.toString();
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
              name: "Main",
              rpm_limit: 100,
              daily_limit: 1000,
              used_today: 100,
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
      return buildJsonResponse([]);
    }
    if (url.includes("/admin/stats/usage")) {
      return buildJsonResponse({ groups: [], top_keys: [], generated_at: "" });
    }
    if (url.includes("/auth/me")) {
      return buildJsonResponse({ role: "admin", is_admin: true });
    }
    return buildJsonResponse([]);
  });

describe("Console endpoints", () => {
  beforeEach(() => {
    window.localStorage.clear();
    vi.stubGlobal("fetch", buildFetchMock());
  });

  afterEach(() => {
    window.localStorage.clear();
    vi.unstubAllGlobals();
  });

  it("renders endpoint cards", async () => {
    render(<Console />);

    expect(await screen.findByText("API 端点列表")).toBeInTheDocument();
    expect(await screen.findByText("OpenAI")).toBeInTheDocument();
    expect(await screen.findByText("基础连通延迟")).toBeInTheDocument();
    expect(await screen.findByText("通道健康度")).toBeInTheDocument();
    expect(await screen.findByText("Key 负载池")).toBeInTheDocument();
    expect(await screen.findByText("管理 Keys")).toBeInTheDocument();
  });
});
