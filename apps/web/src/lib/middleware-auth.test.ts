/**
 * Edge middleware behaviour tests.
 *
 * Lives under src/ so the vitest config's `include: ["src/**\/*.test.ts"]`
 * picks it up; it imports the middleware module from the project root.
 *
 * jose is mocked so the test never reaches across the network for a JWKS;
 * we drive the verify result and assert that the middleware:
 *   - allows requests when the access cookie carries a valid token;
 *   - redirects to /login with a sanitised return_to when no cookie is set;
 *   - redirects on verification failure (bad signature, expired, etc.);
 *   - fails closed on JWKS fetch errors (treated as a verify failure).
 */
import { describe, it, expect, vi, beforeEach } from "vitest";

// Mock jose BEFORE importing the middleware so the module-level
// `createRemoteJWKSet` call picks up our stub.
const jwtVerifyMock = vi.fn();
vi.mock("jose", () => {
  class JOSEError extends Error {}
  return {
    jwtVerify: (...args: unknown[]) => jwtVerifyMock(...args),
    createRemoteJWKSet: () => () => Promise.resolve(undefined),
    errors: { JOSEError },
  };
});

// Lightweight NextRequest stand-in. The real `next/server` types depend on
// the edge runtime; we only need the shape the middleware reads.
interface FakeCookieStore {
  get(name: string): { value: string } | undefined;
}

/**
 * Build a fake `URL` with a `clone()` method that matches the real
 * `NextURL.clone()` behaviour — `URL` itself is missing that method, but
 * the middleware calls it to derive the login redirect URL.
 */
function fakeNextUrl(href: string): URL & { clone(): URL & { clone(): unknown } } {
  const u = new URL(href);
  // Recursive clone so the cloned URL also has a `clone()` (mirrors NextURL).
  (u as URL & { clone(): URL }).clone = () => fakeNextUrl(u.href);
  return u as URL & { clone(): URL & { clone(): unknown } };
}

function fakeRequest(opts: {
  cookie?: string | null;
  pathname?: string;
  search?: string;
}): unknown {
  const pathname = opts.pathname ?? "/cuentas";
  const search = opts.search ?? "";
  const url = `http://localhost:3000${pathname}${search}`;
  const cookies: FakeCookieStore = {
    get(name: string) {
      if (name === "aether_access" && opts.cookie) {
        return { value: opts.cookie };
      }
      return undefined;
    },
  };
  return {
    cookies,
    nextUrl: fakeNextUrl(url),
  };
}

// Pull in the middleware after the mocks are wired.
let middleware: (req: unknown) => Promise<unknown>;

beforeEach(async () => {
  jwtVerifyMock.mockReset();
  vi.resetModules();
  const mod = await import("../../middleware");
  middleware = mod.middleware as typeof middleware;
});

describe("edge middleware (RS256 JWKS verify)", () => {
  it("passes through when the access cookie carries a valid token", async () => {
    jwtVerifyMock.mockResolvedValueOnce({
      payload: { sub: "abc" },
      protectedHeader: { alg: "RS256", kid: "test" },
    });

    const resp = (await middleware(
      fakeRequest({ cookie: "valid.jwt.token" })
    )) as { status?: number; headers?: Headers };

    // NextResponse.next() carries no redirect; status is undefined / 200.
    expect(resp).toBeTruthy();
    expect(jwtVerifyMock).toHaveBeenCalledOnce();
  });

  it("redirects to /login when no cookie is present", async () => {
    const resp = (await middleware(
      fakeRequest({ cookie: null, pathname: "/cuentas" })
    )) as { headers: Headers; status: number };

    const location = resp.headers.get("location");
    expect(location).toBeTruthy();
    expect(location).toContain("/login");
    expect(location).toContain("return_to=%2Fcuentas");
    expect(jwtVerifyMock).not.toHaveBeenCalled();
  });

  it("redirects to /login when the signature is invalid", async () => {
    jwtVerifyMock.mockRejectedValueOnce(new Error("signature verification failed"));

    const resp = (await middleware(
      fakeRequest({ cookie: "tampered.jwt.token", pathname: "/agentes" })
    )) as { headers: Headers };

    const location = resp.headers.get("location");
    expect(location).toContain("/login");
    expect(location).toContain("return_to=%2Fagentes");
  });

  it("redirects to /login when the token is expired", async () => {
    const expired = new Error('"exp" claim timestamp check failed');
    expired.name = "JWTExpired";
    jwtVerifyMock.mockRejectedValueOnce(expired);

    const resp = (await middleware(
      fakeRequest({ cookie: "expired.jwt.token", pathname: "/configuracion" })
    )) as { headers: Headers };

    expect(resp.headers.get("location")).toContain("/login");
  });

  it("fails closed when the JWKS fetch errors out", async () => {
    // jose surfaces JWKS network failures as a verify exception.
    jwtVerifyMock.mockRejectedValueOnce(new Error("JWKS fetch failed"));

    const resp = (await middleware(
      fakeRequest({ cookie: "any.jwt.token" })
    )) as { headers: Headers };

    expect(resp.headers.get("location")).toContain("/login");
  });

  it("validates return_to so a hostile path is collapsed to /", async () => {
    const resp = (await middleware(
      fakeRequest({ cookie: null, pathname: "/api/leak", search: "?x=1" })
    )) as { headers: Headers };

    // safe-redirect collapses /api/... back to "/" — verified there; here
    // we just confirm the redirect lands on /login with a sane return_to.
    const location = resp.headers.get("location")!;
    expect(location).toContain("/login");
    expect(location).toContain("return_to=%2F");
    expect(location).not.toContain("return_to=%2Fapi");
  });
});
