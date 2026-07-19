import { describe, expect, it } from "vitest";

import { countEndpointsByProvider } from "./endpoints-panel";

describe("countEndpointsByProvider", () => {
  it("counts codex endpoints separately from custom providers", () => {
    expect(
      countEndpointsByProvider([
        { provider: "codex" },
        { provider: "Codex" },
        { provider: "vendor-specific" },
      ])
    ).toEqual({
      openai: 0,
      anthropic: 0,
      gemini: 0,
      codex: 2,
      custom: 1,
    });
  });
});
