/**
 * Unit tests for the agents domain helpers.
 *
 * These exercise the zod schemas and the canonical constants — the
 * network helpers are covered by integration tests on the backend side.
 */

import { describe, expect, it } from "vitest";

import {
  agentCreateSchema,
  agentPatchSchema,
  agentSummarySchema,
  AGENT_TYPES,
  AGENT_TYPE_DEFAULT_ENTRYPOINT,
  AGENT_TYPE_TEMPLATE,
} from "./agents";

describe("agent constants", () => {
  it("defines exactly three types", () => {
    expect(AGENT_TYPES).toEqual(["worker", "investigator", "auditor"]);
  });

  it("ships a template for every type that includes the canonical entrypoint", () => {
    for (const kind of AGENT_TYPES) {
      const tpl = AGENT_TYPE_TEMPLATE[kind];
      const ep = AGENT_TYPE_DEFAULT_ENTRYPOINT[kind];
      expect(tpl).toContain(`def ${ep}(`);
    }
  });
});

describe("agentCreateSchema", () => {
  it("accepts a minimal valid payload", () => {
    const parsed = agentCreateSchema.parse({
      name: "alpha",
      type: "worker",
      logica: "def on_tick(ctx): return None",
    });
    expect(parsed.name).toBe("alpha");
  });

  it("rejects an invalid type", () => {
    const result = agentCreateSchema.safeParse({
      name: "x",
      type: "orchestrator",
      logica: "x",
    });
    expect(result.success).toBe(false);
  });

  it("rejects an entrypoint that does not match the regex", () => {
    const result = agentCreateSchema.safeParse({
      name: "x",
      type: "worker",
      logica: "x",
      entrypoint: "1invalid",
    });
    expect(result.success).toBe(false);
  });
});

describe("agentPatchSchema", () => {
  it("requires updated_at (optimistic locking precondition)", () => {
    const result = agentPatchSchema.safeParse({ name: "renamed" });
    expect(result.success).toBe(false);
  });

  it("accepts a partial patch with updated_at", () => {
    const result = agentPatchSchema.parse({
      name: "renamed",
      updated_at: "2026-05-01T12:00:00",
    });
    expect(result.name).toBe("renamed");
  });
});

describe("agentSummarySchema", () => {
  it("validates a server-shaped summary row", () => {
    const parsed = agentSummarySchema.parse({
      id: "11111111-1111-1111-1111-111111111111",
      name: "x",
      type: "worker",
      is_active: true,
      version: 1,
      updated_at: null,
      projects_using: 0,
    });
    expect(parsed.version).toBe(1);
  });
});
