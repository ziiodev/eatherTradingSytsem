/**
 * Unit tests for the skills domain helpers.
 *
 * Exercises the zod schemas + canonical constants. Network helpers are
 * covered by the backend integration suite (``test_skills.py``).
 */

import { describe, expect, it } from "vitest";

import {
  SKILL_TYPES,
  SKILL_TYPE_LABEL,
  SKILL_TYPE_TEMPLATE,
  skillCreateSchema,
  skillDetailSchema,
  skillPatchSchema,
  skillSummarySchema,
} from "./skills";

describe("skill constants", () => {
  it("defines exactly five types matching the DB CHECK", () => {
    expect(SKILL_TYPES).toEqual([
      "indicator",
      "data_source",
      "analytic",
      "executor",
      "risk",
    ]);
  });

  it("ships a non-empty Python template for every type", () => {
    for (const kind of SKILL_TYPES) {
      expect(SKILL_TYPE_TEMPLATE[kind]).toMatch(/^#/);
      expect(SKILL_TYPE_TEMPLATE[kind].length).toBeGreaterThan(20);
    }
  });

  it("provides a Spanish label for every type", () => {
    for (const kind of SKILL_TYPES) {
      expect(typeof SKILL_TYPE_LABEL[kind]).toBe("string");
      expect(SKILL_TYPE_LABEL[kind].length).toBeGreaterThan(0);
    }
  });
});

describe("skillCreateSchema", () => {
  it("accepts a minimal valid payload", () => {
    const parsed = skillCreateSchema.parse({
      name: "RSI",
      type: "indicator",
      code: "def f():\n    return 1\n",
    });
    expect(parsed.name).toBe("RSI");
  });

  it("rejects an invalid type", () => {
    const result = skillCreateSchema.safeParse({
      name: "x",
      type: "orchestrator",
      code: "x",
    });
    expect(result.success).toBe(false);
  });

  it("rejects an empty code body", () => {
    const result = skillCreateSchema.safeParse({
      name: "x",
      type: "indicator",
      code: "",
    });
    expect(result.success).toBe(false);
  });

  it("accepts an optional input_signature", () => {
    const parsed = skillCreateSchema.parse({
      name: "x",
      type: "indicator",
      code: "x",
      input_signature: {
        inputs: [{ name: "series", type: "list[float]" }],
        outputs: [],
      },
    });
    expect(parsed.input_signature?.inputs[0]?.name).toBe("series");
  });
});

describe("skillPatchSchema", () => {
  it("requires updated_at (optimistic locking precondition)", () => {
    const result = skillPatchSchema.safeParse({ name: "renamed" });
    expect(result.success).toBe(false);
  });

  it("accepts a partial patch with updated_at", () => {
    const result = skillPatchSchema.parse({
      name: "renamed",
      updated_at: "2026-05-01T12:00:00",
    });
    expect(result.name).toBe("renamed");
  });
});

describe("skillSummarySchema", () => {
  it("validates a server-shaped summary row", () => {
    const parsed = skillSummarySchema.parse({
      id: "11111111-1111-1111-1111-111111111111",
      name: "x",
      type: "indicator",
      is_active: true,
      version: 1,
      updated_at: null,
    });
    expect(parsed.version).toBe(1);
  });
});

describe("skillDetailSchema", () => {
  it("coerces an empty-object signature to the {inputs, outputs} shape", () => {
    const parsed = skillDetailSchema.parse({
      id: "11111111-1111-1111-1111-111111111111",
      name: "x",
      type: "indicator",
      is_active: true,
      version: 1,
      description: null,
      code: "x",
      input_signature: {}, // legacy/default row from the DB default
      output_signature: {},
      created_at: null,
      updated_at: null,
    });
    expect(parsed.input_signature).toEqual({ inputs: [], outputs: [] });
    expect(parsed.output_signature).toEqual({ inputs: [], outputs: [] });
  });

  it("preserves a populated signature round-trip", () => {
    const parsed = skillDetailSchema.parse({
      id: "11111111-1111-1111-1111-111111111111",
      name: "x",
      type: "indicator",
      is_active: true,
      version: 1,
      description: null,
      code: "x",
      input_signature: {
        inputs: [{ name: "s", type: "list[float]" }],
        outputs: [],
      },
      output_signature: {
        inputs: [],
        outputs: [{ name: "rsi", type: "list[float]" }],
      },
      created_at: null,
      updated_at: null,
    });
    expect(parsed.input_signature.inputs[0]?.type).toBe("list[float]");
    expect(parsed.output_signature.outputs[0]?.name).toBe("rsi");
  });
});
