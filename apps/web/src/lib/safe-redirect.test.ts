import { describe, expect, it } from "vitest";

import { validateReturnTo } from "./safe-redirect";

/**
 * Behavioural pin for `validateReturnTo`.
 *
 * Each scenario maps 1:1 to a numbered case in the `specs/auth` delta
 * `open-redirect-guard` (spec #1975) so a failure points at the exact rule.
 */
describe("validateReturnTo", () => {
  // ── nullish / empty ────────────────────────────────────────────────────
  it("returns '/' for null", () => {
    expect(validateReturnTo(null)).toBe("/");
  });

  it("returns '/' for undefined", () => {
    expect(validateReturnTo(undefined)).toBe("/");
  });

  it("returns '/' for empty string", () => {
    expect(validateReturnTo("")).toBe("/");
  });

  // ── plain safe paths ───────────────────────────────────────────────────
  it("returns '/' for '/' unchanged", () => {
    expect(validateReturnTo("/")).toBe("/");
  });

  it("returns '/cuentas' unchanged", () => {
    expect(validateReturnTo("/cuentas")).toBe("/cuentas");
  });

  it("returns '/cuentas?id=1' unchanged (preserves query string)", () => {
    expect(validateReturnTo("/cuentas?id=1")).toBe("/cuentas?id=1");
  });

  // ── open-redirect vectors ──────────────────────────────────────────────
  it("returns '/' for protocol-relative '//evil.com'", () => {
    expect(validateReturnTo("//evil.com")).toBe("/");
  });

  it("returns '/' for absolute URL 'https://evil.com'", () => {
    expect(validateReturnTo("https://evil.com")).toBe("/");
  });

  it("returns '/' for 'javascript:alert(1)'", () => {
    expect(validateReturnTo("javascript:alert(1)")).toBe("/");
  });

  it("returns '/' for 'data:text/html,x'", () => {
    expect(validateReturnTo("data:text/html,x")).toBe("/");
  });

  // ── blocked prefixes ───────────────────────────────────────────────────
  it("returns '/' for '/login' (prevents bounce loop)", () => {
    expect(validateReturnTo("/login")).toBe("/");
  });

  it("returns '/' for '/login?return_to=/x' (prevents nested bounce loop)", () => {
    expect(validateReturnTo("/login?return_to=/x")).toBe("/");
  });

  it("returns '/' for '/api/whatever' (API endpoints are not user-navigable)", () => {
    expect(validateReturnTo("/api/whatever")).toBe("/");
  });

  it("returns '/' for '/_next/static/x' (framework internals)", () => {
    expect(validateReturnTo("/_next/static/x")).toBe("/");
  });

  // ── encoding preservation ──────────────────────────────────────────────
  it("returns '/path/with%20encoded' unchanged (preserves percent-encoding)", () => {
    expect(validateReturnTo("/path/with%20encoded")).toBe(
      "/path/with%20encoded",
    );
  });

  it("returns '/path?key=value&other=x' unchanged (preserves multi-key query)", () => {
    expect(validateReturnTo("/path?key=value&other=x")).toBe(
      "/path?key=value&other=x",
    );
  });
});
