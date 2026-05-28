/**
 * Open-redirect guard for post-login navigation and middleware bounce-backs.
 *
 * `validateReturnTo` is the ONLY sanctioned place where an untrusted
 * `return_to`-style value is converted into a navigable path. Every surface
 * that consumes such a value (login submit handler, edge middleware,
 * future password-reset / OAuth callback flows) MUST import this function
 * — inline re-implementations are prohibited by `specs/auth`.
 *
 * The function is pure: no I/O, no globals, no `window` access. It is safe
 * to call from React Server Components, Client Components, Route Handlers,
 * and Edge Middleware.
 *
 * Contract (see `specs/auth` delta `open-redirect-guard`):
 *   - Never throws.
 *   - Always returns a string that starts with `"/"`.
 *   - Defaults to `"/"` whenever the input fails any rule.
 *
 * Rules, applied in order — first match wins:
 *   1. Empty / nullish input              → "/"
 *   2. Starts with "//" (protocol-rel.)   → "/"
 *   3. Scheme before any path (e.g.
 *      `javascript:…`, `https://…`)       → "/"
 *   4. Does not start with "/"            → "/"
 *   5. Starts with `/login`, `/api`, or
 *      `/_next` (loop / internal paths)   → "/"
 *   6. Contains a character outside the
 *      safe URL-path charset              → "/"
 *   7. Otherwise return input unchanged,
 *      preserving query string and
 *      percent-encoding.
 */

const SCHEME_BEFORE_PATH = /^[^/]*:/;
const SAFE_PATH_CHARSET = /^[A-Za-z0-9_\-./?&=%#]+$/;

const BLOCKED_PREFIXES = ["/login", "/api", "/_next"] as const;

export function validateReturnTo(
  input: string | null | undefined,
): string {
  // 1. Null / undefined / empty.
  if (!input || input.length === 0) return "/";

  // 2. Protocol-relative URLs (`//evil.com/...`) — browsers resolve these
  //    against the current scheme, which is an open redirect.
  if (input.startsWith("//")) return "/";

  // 3. Any scheme before a path (`javascript:`, `data:`, `https://...`).
  //    A literal `:` appearing before the first `/` means there is a URI
  //    scheme — never a same-origin path.
  if (SCHEME_BEFORE_PATH.test(input)) return "/";

  // 4. Bare paths like `evil.com/foo` — must start with `/`.
  if (!input.startsWith("/")) return "/";

  // 5. Blocked path prefixes: `/login` (bounce loop), `/api` (not a
  //    user-navigable surface), `/_next` (framework internals).
  for (const prefix of BLOCKED_PREFIXES) {
    if (input === prefix || input.startsWith(`${prefix}/`) || input.startsWith(`${prefix}?`)) {
      return "/";
    }
  }

  // 6. Allow only the conservative URL-path charset. Anything outside
  //    (control chars, whitespace, backslashes, raw unicode) is rejected
  //    rather than guessed at.
  if (!SAFE_PATH_CHARSET.test(input)) return "/";

  // 7. Safe — return unchanged so query string + percent-encoding survive.
  return input;
}
