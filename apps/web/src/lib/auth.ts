/**
 * Frontend auth helpers.
 *
 * Tokens (access / refresh) live in **httpOnly cookies** set by the FastAPI
 * backend — JavaScript NEVER touches them. We do, however, need to read the
 * (non-httpOnly) `csrf_token` cookie to implement the double-submit pattern
 * on state-changing requests. That's all this module does on the client.
 *
 * IMPORTANT: never persist tokens to `localStorage` or `sessionStorage`. The
 * Charter forbids it (XSS exfiltration risk).
 */

export const CSRF_COOKIE_NAME = "csrf_token";
export const CSRF_HEADER_NAME = "X-CSRF-Token";

/**
 * Read a cookie by name from `document.cookie`. Returns `null` when the
 * cookie is absent or when called from a non-browser environment (SSR).
 */
export function getCookie(name: string): string | null {
  if (typeof document === "undefined") return null;
  const target = `${encodeURIComponent(name)}=`;
  const parts = document.cookie.split(";");
  for (const raw of parts) {
    const part = raw.trim();
    if (part.startsWith(target)) {
      return decodeURIComponent(part.slice(target.length));
    }
  }
  return null;
}

/**
 * Read the CSRF token cookie. The backend issues this cookie on login (and
 * rotates it on refresh) as a NON-httpOnly cookie so the JS client can mirror
 * it in the `X-CSRF-Token` header.
 */
export function getCsrfTokenFromCookie(): string | null {
  return getCookie(CSRF_COOKIE_NAME);
}

/**
 * Wrap a `RequestInit` with the CSRF header attached. Safe to call when no
 * token is present yet (it just returns the original init unchanged) — the
 * backend will reject the request with 403, which is the correct behavior.
 */
export function withCsrfHeader(init: RequestInit = {}): RequestInit {
  const token = getCsrfTokenFromCookie();
  if (!token) return init;
  const headers = new Headers(init.headers ?? {});
  headers.set(CSRF_HEADER_NAME, token);
  return { ...init, headers };
}

/**
 * POST `/api/auth/logout`, then redirect to `/login`. The backend is the one
 * that actually clears the cookies (httpOnly = JS can't), so the redirect
 * happens regardless of the response status to avoid leaving the user in a
 * half-authenticated UI.
 */
export async function logout(): Promise<void> {
  try {
    await fetch("/api/auth/logout", {
      method: "POST",
      credentials: "include",
      headers: withCsrfHeader().headers,
    });
  } catch {
    // Intentionally swallow — we still redirect below.
  }
  if (typeof window !== "undefined") {
    window.location.href = "/login";
  }
}
