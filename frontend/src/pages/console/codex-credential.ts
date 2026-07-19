type JsonRecord = Record<string, unknown>;

const asRecord = (value: unknown): JsonRecord =>
  value && typeof value === "object" && !Array.isArray(value)
    ? (value as JsonRecord)
    : {};

const firstString = (...values: unknown[]) => {
  for (const value of values) {
    if (typeof value === "string" && value.trim()) {
      return value.trim();
    }
  }
  return "";
};

export const normalizeCodexCredentialJson = (raw: string): string => {
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    throw new Error("凭据不是有效的 JSON");
  }
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new Error("Codex 凭据必须是 JSON 对象");
  }

  const root = parsed as JsonRecord;
  const tokens = asRecord(root.tokens);
  const accessToken = firstString(root.access_token, tokens.access_token);
  const refreshToken = firstString(root.refresh_token, tokens.refresh_token);
  const accountId = firstString(
    root.account_id,
    root.chatgpt_account_id,
    tokens.account_id,
    tokens.chatgpt_account_id
  );
  if (!accessToken && !refreshToken) {
    throw new Error("JSON 中缺少 access_token 或 refresh_token");
  }

  const normalized: JsonRecord = {};
  if (accessToken) normalized.access_token = accessToken;
  if (refreshToken) normalized.refresh_token = refreshToken;
  if (accountId) normalized.account_id = accountId;
  const expiresAt =
    root.expires_at ?? root.expiry ?? root.expired ?? root.expires ?? tokens.expires_at;
  if (
    (typeof expiresAt === "string" && expiresAt.trim()) ||
    (typeof expiresAt === "number" && Number.isFinite(expiresAt))
  ) {
    normalized.expires_at = expiresAt;
  }
  return JSON.stringify(normalized, null, 2);
};
