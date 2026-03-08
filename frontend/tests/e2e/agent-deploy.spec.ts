import { expect, test, type Route } from "@playwright/test";

test("agent bootstrap command flow", async ({ page }) => {
  const fulfillJson = (route: Route, payload: unknown, status = 200) =>
    route.fulfill({
      status,
      contentType: "application/json",
      body: JSON.stringify(payload),
    });

  await page.addInitScript(() => {
    window.localStorage.setItem("llm_admin_token", "token");
  });

  await page.route("**/auth/me", (route) =>
    fulfillJson(route, { role: "admin", is_admin: true })
  );
  await page.route("**/admin/endpoints", (route) =>
    fulfillJson(route, [
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
        model_count: 0,
        keys: [],
      },
    ])
  );
  await page.route("**/admin/agents", (route) =>
    fulfillJson(route, [
      {
        id: 1,
        name: "edge-hk",
        region: "hk",
        status: "online",
        last_seen_at: "2024-01-01T00:00:00Z",
        endpoint_url: null,
      },
    ])
  );
  await page.route("**/admin/rules", (route) => fulfillJson(route, []));
  await page.route("**/admin/health-status", (route) => fulfillJson(route, []));
  await page.route("**/admin/metrics/timeseries**", (route) => fulfillJson(route, []));
  await page.route("**/admin/stats/usage", (route) =>
    fulfillJson(route, {
      groups: [],
      top_keys: [],
      total_tokens_today: 0,
      generated_at: "2024-01-01T00:00:00Z",
    })
  );
  await page.route("**/admin/agents/bootstrap", (route) =>
    fulfillJson(route, {
      agent_id: 1,
      name: "edge-hk",
      token: "agent-token",
      install_command: "curl -fsSL https://install.example.com | bash",
    })
  );

  await page.goto("/");
  await page.getByRole("button", { name: "节点管理" }).click();
  await page.getByRole("button", { name: "部署新节点" }).click();

  await page.getByLabel("节点名称").fill("edge-hk");
  await page.getByRole("button", { name: "生成部署命令" }).click();

  await expect(page.getByText("一键部署命令")).toBeVisible();
  await expect(page.getByDisplayValue("agent-token")).toBeVisible();
});
