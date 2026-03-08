import "@testing-library/jest-dom";

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { AgentsView } from "./Console";

const agent = {
  id: 1,
  name: "edge-hk",
  region: "hk",
  status: "offline",
  last_seen_at: null,
  endpoint_url: "https://agent.example.com",
  supports_gpt: true,
  supports_gemini: false,
  supports_claude: true,
  probe_latency_ms: 120,
};

describe("AgentsView", () => {
  it("renders capability badges and deploy actions", () => {
    const handleDeploy = vi.fn();
    const handleCreate = vi.fn();

    render(
      <AgentsView agents={[agent]} onDeploy={handleDeploy} onCreate={handleCreate} />
    );

    expect(screen.getByText("Agent 节点网络")).toBeInTheDocument();
    expect(screen.getByText("GPT")).toBeInTheDocument();
    expect(screen.getByText("Claude")).toBeInTheDocument();
    expect(screen.getByText("基础延迟")).toBeInTheDocument();

    fireEvent.click(screen.getByText("部署新节点"));
    expect(handleCreate).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByText("部署"));
    expect(handleDeploy).toHaveBeenCalledWith(agent);
  });
});
