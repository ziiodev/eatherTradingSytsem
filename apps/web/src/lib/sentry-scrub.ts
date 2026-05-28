/**
 * Frontend mirror of ``apps/api/src/aether_api/core/pii.py``.
 *
 * Keep the FORBIDDEN_KEYS set in lockstep with the backend — drift in
 * either direction leaks secrets through the slow path. The JWT regex is
 * identical to the backend's _JWT_PATTERN.
 *
 * Used by ``instrumentation-client.ts`` / ``sentry.server.config.ts``
 * via ``beforeSend`` to scrub error events before they leave the browser.
 */

export const MASK = "***REDACTED***";

export const FORBIDDEN_KEYS: ReadonlySet<string> = new Set(
  [
    "password",
    "password_hash",
    "current_password",
    "new_password",
    "csrf_token",
    "x_csrf_token",
    "aether_access",
    "aether_refresh",
    "refresh_token",
    "refresh_token_hash",
    "access_token",
    "mfa_secret",
    "mfa_secret_ref",
    "jwt_secret",
    "authorization",
    "cookie",
    "set_cookie",
    "api_key",
    "mt5_password",
  ].map((k) => k.toLowerCase()),
);

const JWT_PATTERN = /eyJ[A-Za-z0-9_-]+\.eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+/g;

function scrubString(value: string): string {
  return value.replace(JWT_PATTERN, MASK);
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return (
    typeof value === "object" &&
    value !== null &&
    !Array.isArray(value) &&
    Object.getPrototypeOf(value) === Object.prototype
  );
}

function scrubValue(key: string | null, value: unknown): unknown {
  if (key !== null && FORBIDDEN_KEYS.has(key.toLowerCase())) {
    return MASK;
  }
  if (typeof value === "string") {
    return scrubString(value);
  }
  if (Array.isArray(value)) {
    return value.map((item) => scrubValue(null, item));
  }
  if (isPlainObject(value)) {
    const out: Record<string, unknown> = {};
    for (const k of Object.keys(value)) {
      out[k] = scrubValue(k, value[k]);
    }
    return out;
  }
  return value;
}

export function scrubMapping<T extends Record<string, unknown>>(data: T): T {
  return scrubValue(null, data) as T;
}
