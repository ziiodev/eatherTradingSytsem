import { jwtVerify, createRemoteJWKSet, errors as joseErrors } from "jose";
import { NextResponse, type NextRequest } from "next/server";

import { validateReturnTo } from "@/lib/safe-redirect";

/**
 * Auth gate for every non-public route.
 *
 * The FastAPI backend issues an `aether_access` cookie (httpOnly, scoped to
 * "/") carrying an RS256-signed JWT. The edge middleware verifies the JWT's
 * signature locally against the public keys published at
 * `${NEXT_PUBLIC_API_URL}/.well-known/jwks.json` — the redirect gate is
 * "signature-valid", not merely "cookie-present".
 *
 * The matcher below excludes:
 *   - `/login`             — the only unauthenticated page
 *   - `/api/*`             — proxied to FastAPI which does its own auth
 *   - `/_next/*`           — Next.js internals (assets, RSC, HMR)
 *   - `/favicon.ico` etc.  — static public assets
 *
 * Failure modes (missing cookie, bad signature, expired token, JWKS fetch
 * error) all fail closed: the user is redirected to `/login?return_to=...`
 * with the original path preserved (and validated via `validateReturnTo`
 * so a hostile redirect target can't be stamped into the login URL).
 */

const ACCESS_COOKIE = "aether_access";

/**
 * Resolve the JWKS URL at module-load time.
 *
 * NEXT_PUBLIC_API_URL must be set in the edge runtime (Next.js inlines
 * `NEXT_PUBLIC_*` vars at build time). Falling back to a sensible local
 * default keeps the dev experience working when the env file isn't loaded
 * yet — production builds MUST set this explicitly.
 */
const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
const JWKS_URL = new URL("/.well-known/jwks.json", API_BASE_URL);

/**
 * Module-level singleton: `jose.createRemoteJWKSet` returns a function that
 * caches keys in memory, refreshing on `kid` miss. The 1h cache aligns with
 * the backend's `Cache-Control: public, max-age=3600` on the JWKS response;
 * the 6h cooldown is a stale-while-error window — if the API is briefly
 * unreachable, the cached key set keeps verifying instead of locking
 * everyone out.
 */
const remoteJWKS = createRemoteJWKSet(JWKS_URL, {
  cacheMaxAge: 60 * 60 * 1000, // 1h
  cooldownDuration: 6 * 60 * 60 * 1000, // 6h stale-while-error
});

/**
 * RS256 is the ONLY accepted algorithm. Passing this as the
 * `algorithms` allow-list to `jwtVerify` prevents the classic
 * `alg=HS256` / `alg=none` algorithm-confusion attack at the edge —
 * a forged token claiming HMAC will be rejected before the RSA verify
 * routine ever sees it.
 */
const ACCEPTED_ALGS = ["RS256"] as const;

export async function middleware(request: NextRequest): Promise<NextResponse> {
  const access = request.cookies.get(ACCESS_COOKIE);
  if (access?.value) {
    try {
      await jwtVerify(access.value, remoteJWKS, {
        algorithms: [...ACCEPTED_ALGS],
      });
      return NextResponse.next();
    } catch (err) {
      // Expected user-facing failures: bad signature, expired, unknown kid,
      // wrong alg. JWKS network failures during the cooldown window also
      // surface here — fail closed in every case, the user re-logs in.
      if (
        err instanceof joseErrors.JOSEError ||
        err instanceof Error
      ) {
        // Intentionally do not log token bodies or signatures.
      }
      // Fall through to the redirect path below.
    }
  }

  const { pathname, search } = request.nextUrl;
  // Validate before stamping `return_to` into the login URL so the login
  // page never has to handle a hostile or otherwise-blocked target. If the
  // origin path itself is blocked (e.g. `/api/...`, `/_next/...`),
  // `validateReturnTo` will collapse it to `/` — the safe default.
  const returnTo = validateReturnTo(`${pathname}${search}`);
  const loginUrl = request.nextUrl.clone();
  loginUrl.pathname = "/login";
  loginUrl.search = "";
  loginUrl.searchParams.set("return_to", returnTo);

  return NextResponse.redirect(loginUrl);
}

export const config = {
  /*
   * Run on everything EXCEPT:
   *   - /login         (the only public page)
   *   - /api/*         (proxied to FastAPI — it handles its own auth)
   *   - /_next/static  (build assets)
   *   - /_next/image   (image optimization)
   *   - /favicon.ico, sitemap.xml, robots.txt
   *
   * The matcher MUST keep the middleware off `/api/*` — otherwise the
   * proxied login call (which establishes the cookie) would itself be
   * gated by the missing cookie, deadlocking the bootstrap.
   */
  matcher: [
    "/((?!login|api/|_next/static|_next/image|favicon.ico|sitemap.xml|robots.txt).*)",
  ],
};
