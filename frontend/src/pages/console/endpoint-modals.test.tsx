import "@testing-library/jest-dom";

import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { EditEndpointModal } from "./endpoint-modals";
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
    expect(screen.getByText("请求体模板 (JSON)")).toBeInTheDocument();
  });
});
