import { describe, expect, it } from "vitest";

import { normalizeCodexCredentialJson } from "./codex-credential";

describe("normalizeCodexCredentialJson", () => {
  it("keeps only the fields needed from a Sub2API export", () => {
    expect(
      JSON.parse(
        normalizeCodexCredentialJson(
          JSON.stringify({
            access_token: "access",
            refresh_token: "",
            account_id: "account",
            expired: "2026-10-17T04:59:04.800Z",
            email: "private@example.test",
            id_token: "unused",
          })
        )
      )
    ).toEqual({
      access_token: "access",
      account_id: "account",
      expires_at: "2026-10-17T04:59:04.800Z",
    });
  });

  it("supports nested tokens exports", () => {
    expect(
      JSON.parse(
        normalizeCodexCredentialJson(
          JSON.stringify({
            tokens: {
              access_token: "nested-access",
              refresh_token: "nested-refresh",
              account_id: "nested-account",
            },
          })
        )
      )
    ).toMatchObject({
      access_token: "nested-access",
      refresh_token: "nested-refresh",
      account_id: "nested-account",
    });
  });
});
