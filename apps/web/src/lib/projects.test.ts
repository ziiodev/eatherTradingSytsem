import { describe, expect, it } from "vitest";

import {
  canTransition,
  isDeletable,
  projectCreateSchema,
} from "@/lib/projects";

describe("project lifecycle helpers", () => {
  it("allows the expected edges from inactive", () => {
    expect(canTransition("inactive", "active")).toBe(true);
    expect(canTransition("inactive", "maintenance")).toBe(true);
    expect(canTransition("inactive", "paused")).toBe(false);
  });

  it("does NOT allow error -> active", () => {
    expect(canTransition("error", "active")).toBe(false);
    expect(canTransition("error", "stopped")).toBe(true);
    expect(canTransition("error", "maintenance")).toBe(true);
  });

  it("marks inactive and stopped as deletable", () => {
    expect(isDeletable("inactive")).toBe(true);
    expect(isDeletable("stopped")).toBe(true);
    expect(isDeletable("active")).toBe(false);
    expect(isDeletable("paused")).toBe(false);
  });
});

describe("projectCreateSchema", () => {
  it("accepts a minimal valid payload", () => {
    const result = projectCreateSchema.safeParse({
      name: "Aether-EURUSD",
      symbol: "EURUSD",
      timeframe: "H1",
      mcp_url: "http://localhost:8081",
      trading_sessions: ["europe"],
    });
    expect(result.success).toBe(true);
  });

  it("rejects unknown trading session", () => {
    const result = projectCreateSchema.safeParse({
      name: "X",
      symbol: "EURUSD",
      timeframe: "H1",
      mcp_url: "x",
      trading_sessions: ["mars"],
    });
    expect(result.success).toBe(false);
  });

  it("rejects invalid timeframe", () => {
    const result = projectCreateSchema.safeParse({
      name: "X",
      symbol: "EURUSD",
      timeframe: "H7",
      mcp_url: "x",
    });
    expect(result.success).toBe(false);
  });
});
