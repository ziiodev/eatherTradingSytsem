/**
 * Unit tests for the `/api/me` client.
 *
 * Mocks the global `fetch` via vi.stubGlobal so we exercise:
 * - body shape sent for PATCH (display_name / avatar_url),
 * - query-string assembly for `listSessions(cursor, limit)`,
 * - HTTP error surface from ApiError.
 */

import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/lib/api";
import {
  changeEmail,
  changePassword,
  listSessions,
  patchMe,
  revokeOtherSessions,
  revokeSession,
} from "@/lib/me";

function mockFetchOnce(
  body: unknown,
  init: { status?: number; contentType?: string } = {},
): { mock: ReturnType<typeof vi.fn>; calls: unknown[] } {
  const calls: unknown[] = [];
  const mock = vi.fn(async (input: RequestInfo | URL, opts?: RequestInit) => {
    calls.push({ input: String(input), opts });
    return new Response(
      init.status === 204 ? null : JSON.stringify(body),
      {
        status: init.status ?? 200,
        headers: {
          "Content-Type":
            init.contentType ?? "application/json",
        },
      },
    );
  });
  vi.stubGlobal("fetch", mock);
  return { mock, calls };
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

describe("patchMe", () => {
  it("sends only the provided fields", async () => {
    const { calls } = mockFetchOnce({
      id: "00000000-0000-0000-0000-000000000001",
      email: "x@example.com",
      display_name: "X",
      avatar_url: null,
      email_verified_at: null,
      is_admin: false,
      created_at: null,
    });
    await patchMe({ display_name: "X" });
    expect(calls).toHaveLength(1);
    const opts = (calls[0] as { opts: RequestInit }).opts;
    expect(opts.method).toBe("PATCH");
    expect(opts.body).toBe(JSON.stringify({ display_name: "X" }));
  });
});

describe("listSessions", () => {
  it("encodes cursor and limit", async () => {
    const { calls } = mockFetchOnce({ items: [], next_cursor: null });
    await listSessions({ limit: 5, cursor: "abc==" });
    expect(String((calls[0] as { input: string }).input)).toContain(
      "/api/me/sessions?limit=5&cursor=abc%3D%3D",
    );
  });

  it("omits empty cursor", async () => {
    const { calls } = mockFetchOnce({ items: [], next_cursor: null });
    await listSessions({ limit: 10 });
    const url = String((calls[0] as { input: string }).input);
    expect(url).toContain("/api/me/sessions?limit=10");
    expect(url).not.toContain("cursor");
  });
});

describe("revokeSession", () => {
  it("targets the right endpoint", async () => {
    const { calls } = mockFetchOnce(null, { status: 204 });
    await revokeSession("11111111-1111-1111-1111-111111111111");
    expect((calls[0] as { input: string }).input).toContain(
      "/api/me/sessions/11111111-1111-1111-1111-111111111111/revoke",
    );
    expect((calls[0] as { opts: RequestInit }).opts.method).toBe("POST");
  });

  it("propagates ApiError on 400 use_logout_instead", async () => {
    mockFetchOnce(
      { detail: { code: "use_logout_instead", message: "x" } },
      { status: 400 },
    );
    await expect(
      revokeSession("11111111-1111-1111-1111-111111111111"),
    ).rejects.toBeInstanceOf(ApiError);
  });
});

describe("revokeOtherSessions", () => {
  it("returns the count from the response body", async () => {
    mockFetchOnce({ revoked: 3 });
    const result = await revokeOtherSessions();
    expect(result.revoked).toBe(3);
  });
});

describe("changeEmail", () => {
  it("posts the payload", async () => {
    const { calls } = mockFetchOnce({
      id: "00000000-0000-0000-0000-000000000001",
      email: "new@x.com",
      display_name: null,
      avatar_url: null,
      email_verified_at: null,
      is_admin: false,
      created_at: null,
    });
    await changeEmail({
      new_email: "new@x.com",
      current_password: "supersecret",
    });
    const opts = (calls[0] as { opts: RequestInit }).opts;
    expect(opts.method).toBe("POST");
    expect(opts.body).toBe(
      JSON.stringify({
        new_email: "new@x.com",
        current_password: "supersecret",
      }),
    );
  });
});

describe("changePassword", () => {
  it("returns the structured result", async () => {
    mockFetchOnce({ ok: true, revoked_other_sessions: 2 });
    const result = await changePassword({
      current_password: "old",
      new_password: "newnewnew",
      sign_out_others: true,
    });
    expect(result.ok).toBe(true);
    expect(result.revoked_other_sessions).toBe(2);
  });
});
