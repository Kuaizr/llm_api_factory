import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { tokenStorageKey } from "./shared";
import { useConsoleData } from "./use-console-data";

const jsonResponse = (payload: unknown, status = 200) =>
  Promise.resolve(
    new Response(JSON.stringify(payload), {
      status,
      headers: { "Content-Type": "application/json" },
    })
  );

const buildFetchMock = (options?: {
  loginPayload?: unknown;
  passwordPayload?: unknown;
}) =>
  vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    if (url.includes("/auth/login")) {
      return jsonResponse(options?.loginPayload ?? { token: "adm.issued" });
    }
    if (url.includes("/auth/password") && init?.method === "POST") {
      return jsonResponse(options?.passwordPayload ?? { token: "adm.rotated" });
    }
    if (url.includes("/auth/me")) {
      return jsonResponse({ is_admin: true });
    }
    if (url.includes("/admin/endpoints")) {
      return jsonResponse([]);
    }
    if (url.includes("/admin/agents")) {
      return jsonResponse([]);
    }
    if (url.includes("/admin/rules")) {
      return jsonResponse([]);
    }
    if (url.includes("/admin/stats/usage")) {
      return jsonResponse({
        groups: [],
        top_keys: [],
        total_tokens_today: 0,
        generated_at: "",
      });
    }
    if (url.includes("/admin/metrics/timeseries")) {
      return jsonResponse([]);
    }
    if (url.includes("/admin/health-status")) {
      return jsonResponse([]);
    }
    if (url.includes("/admin/telegram/config")) {
      return jsonResponse({
        configured: false,
        bot_token_masked: null,
        chat_id: null,
      });
    }
    return jsonResponse([]);
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
  };
};

describe("useConsoleData auth storage", () => {
  beforeEach(() => {
    const storage = createLocalStorageMock();
    vi.stubGlobal("localStorage", storage);
    Object.defineProperty(window, "localStorage", {
      value: storage,
      configurable: true,
    });
  });

  afterEach(() => {
    window.localStorage.clear();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("does not persist the submitted password when login returns no token", async () => {
    vi.stubGlobal("fetch", buildFetchMock({ loginPayload: {} }));
    const { result } = renderHook(() => useConsoleData());

    await act(async () => {
      await result.current.handleLogin("secret-password");
    });

    expect(window.localStorage.getItem(tokenStorageKey)).toBeNull();
    expect(result.current.token).toBeNull();
  });

  it("persists only the issued login token", async () => {
    vi.stubGlobal("fetch", buildFetchMock({ loginPayload: { token: " adm.issued " } }));
    const { result } = renderHook(() => useConsoleData());

    await act(async () => {
      await result.current.handleLogin("secret-password");
    });

    await waitFor(() => expect(result.current.token).toBe("adm.issued"));
    expect(window.localStorage.getItem(tokenStorageKey)).toBe("adm.issued");
    expect(window.localStorage.getItem(tokenStorageKey)).not.toBe("secret-password");
  });

  it("keeps the existing token when password update returns no replacement token", async () => {
    window.localStorage.setItem(tokenStorageKey, "adm.current");
    vi.stubGlobal("fetch", buildFetchMock({ passwordPayload: {} }));
    const { result } = renderHook(() => useConsoleData());

    await waitFor(() => expect(result.current.token).toBe("adm.current"));
    let update: Awaited<ReturnType<typeof result.current.handlePasswordUpdate>> | undefined;
    await act(async () => {
      update = await result.current.handlePasswordUpdate(
        "old-password",
        "new-password"
      );
    });

    expect(update).toEqual({ ok: false, message: "服务端未返回登录令牌" });
    expect(window.localStorage.getItem(tokenStorageKey)).toBe("adm.current");
    expect(window.localStorage.getItem(tokenStorageKey)).not.toBe("new-password");
  });
});
