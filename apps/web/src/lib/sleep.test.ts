/**
 * Unit tests for the sleep + learning client helpers.
 *
 * Strategy:
 * - Zod schemas: parse known-good fixtures + assert rejection of malformed
 *   payloads.
 * - Network helpers: stub `global.fetch` and assert the resulting URL +
 *   parsed response (mirrors the rest of the dashboard's lib tests).
 * - `diffQTables`: pure function — exhaustive small cases.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  diffQTables,
  episodicMemoryListResponseSchema,
  episodicMemorySchema,
  fetchEpisodicMemory,
  fetchQTable,
  fetchQTables,
  fetchSemanticMemory,
  fetchSleepReport,
  qTableListResponseSchema,
  qTableResponseSchema,
  semanticMemoryListResponseSchema,
  semanticMemorySchema,
  sleepReportSchema,
} from "@/lib/sleep";

const PROJECT_ID = "11111111-1111-1111-1111-111111111111";
const RUN_ID = "22222222-2222-2222-2222-222222222222";

interface MockFetchResult {
  url: string;
  init: RequestInit | undefined;
}

function mockFetch(body: unknown, status = 200): MockFetchResult {
  const captured: MockFetchResult = { url: "", init: undefined };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      captured.url = url;
      captured.init = init;
      return new Response(JSON.stringify(body), {
        status,
        headers: { "Content-Type": "application/json" },
      });
    }),
  );
  return captured;
}

beforeEach(() => {
  vi.stubGlobal("document", { cookie: "" });
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("qTableListResponseSchema", () => {
  it("accepts a well-shaped payload", () => {
    const parsed = qTableListResponseSchema.parse({
      items: [
        {
          id: "33333333-3333-3333-3333-333333333333",
          pair_id: PROJECT_ID,
          version: 2,
          alpha_normal: "0.15",
          alpha_special: "0.35",
          gamma: "0.92",
          episode_count: 12,
          created_by_sleep_run_id: null,
          created_at: "2026-05-28T10:00:00",
        },
      ],
      total: 2,
    });
    expect(parsed.items[0]?.version).toBe(2);
  });

  it("rejects non-uuid project id", () => {
    const result = qTableListResponseSchema.safeParse({
      items: [{ id: "x", pair_id: "not-a-uuid", version: 1 }],
      total: 1,
    });
    expect(result.success).toBe(false);
  });
});

describe("qTableResponseSchema", () => {
  it("preserves the table_data JSONB blob", () => {
    const parsed = qTableResponseSchema.parse({
      id: "33333333-3333-3333-3333-333333333333",
      pair_id: PROJECT_ID,
      version: 1,
      alpha_normal: "0.15",
      alpha_special: "0.35",
      gamma: "0.92",
      episode_count: 0,
      created_by_sleep_run_id: null,
      created_at: null,
      table_data: { "s|key": { buy: 0.1, sell: -0.2 } },
    });
    expect(parsed.table_data["s|key"]).toEqual({ buy: 0.1, sell: -0.2 });
  });
});

describe("episodicMemorySchema", () => {
  it("accepts a row", () => {
    const parsed = episodicMemorySchema.parse({
      id: "44444444-4444-4444-4444-444444444444",
      pair_id: PROJECT_ID,
      state_key: "abcd",
      action: "buy",
      reward: "0.42",
      next_state_key: null,
      order_id: null,
      consumed_by_sleep_run_id: null,
      meta_data: { foo: "bar" },
      created_at: "2026-05-28T12:00:00",
    });
    expect(parsed.state_key).toBe("abcd");
  });
});

describe("episodicMemoryListResponseSchema", () => {
  it("accepts an empty list", () => {
    expect(
      episodicMemoryListResponseSchema.parse({ items: [], total: 0 }).items,
    ).toEqual([]);
  });
});

describe("semanticMemorySchema", () => {
  it("accepts an active rule", () => {
    const parsed = semanticMemorySchema.parse({
      id: "55555555-5555-5555-5555-555555555555",
      pair_id: PROJECT_ID,
      rule_type: "avoid_state",
      body: "no operar en estado X",
      payload: {},
      superseded_by: null,
      active: true,
      created_by_sleep_run_id: null,
      created_at: null,
      updated_at: null,
    });
    expect(parsed.active).toBe(true);
  });
});

describe("semanticMemoryListResponseSchema", () => {
  it("validates total", () => {
    const parsed = semanticMemoryListResponseSchema.parse({
      items: [],
      total: 0,
    });
    expect(parsed.total).toBe(0);
  });
});

describe("sleepReportSchema", () => {
  it("accepts a payload", () => {
    const parsed = sleepReportSchema.parse({
      id: "66666666-6666-6666-6666-666666666666",
      sleep_run_id: RUN_ID,
      payload: { overall_score: 0.7 },
      summary_md: "## Resumen",
      created_at: "2026-05-28T13:00:00",
    });
    expect(parsed.payload.overall_score).toBe(0.7);
  });
});

describe("fetchQTables URL composition", () => {
  it("hits /api/pairs/{id}/q-tables with pagination params", async () => {
    const captured = mockFetch({ items: [], total: 0 });
    await fetchQTables(PROJECT_ID, { limit: 25, offset: 50 });
    expect(captured.url).toBe(
      `/api/pairs/${PROJECT_ID}/q-tables?limit=25&offset=50`,
    );
  });

  it("omits the query string when no params are provided", async () => {
    const captured = mockFetch({ items: [], total: 0 });
    await fetchQTables(PROJECT_ID);
    expect(captured.url).toBe(`/api/pairs/${PROJECT_ID}/q-tables`);
  });
});

describe("fetchQTable", () => {
  it("hits /api/pairs/{id}/q-tables/{version}", async () => {
    const captured = mockFetch({
      id: "33333333-3333-3333-3333-333333333333",
      pair_id: PROJECT_ID,
      version: 3,
      alpha_normal: "0.15",
      alpha_special: "0.35",
      gamma: "0.92",
      episode_count: 0,
      created_by_sleep_run_id: null,
      created_at: null,
      table_data: {},
    });
    const r = await fetchQTable(PROJECT_ID, 3);
    expect(r.version).toBe(3);
    expect(captured.url).toBe(`/api/pairs/${PROJECT_ID}/q-tables/3`);
  });
});

describe("fetchEpisodicMemory", () => {
  it("forwards since/until/state_key", async () => {
    const captured = mockFetch({ items: [], total: 0 });
    await fetchEpisodicMemory(PROJECT_ID, {
      since: "2026-05-20T00:00:00Z",
      until: "2026-05-27T00:00:00Z",
      stateKey: "abc",
      limit: 10,
      offset: 0,
    });
    expect(captured.url).toContain("since=");
    expect(captured.url).toContain("until=");
    expect(captured.url).toContain("state_key=abc");
  });
});

describe("fetchSemanticMemory", () => {
  it("forwards rule_type + active", async () => {
    const captured = mockFetch({ items: [], total: 0 });
    await fetchSemanticMemory(PROJECT_ID, {
      ruleType: "avoid_state",
      active: true,
    });
    expect(captured.url).toContain("rule_type=avoid_state");
    expect(captured.url).toContain("active=true");
  });
});

describe("fetchSleepReport", () => {
  it("hits the per-run report endpoint", async () => {
    const captured = mockFetch({
      id: "66666666-6666-6666-6666-666666666666",
      sleep_run_id: RUN_ID,
      payload: {},
      summary_md: null,
      created_at: null,
    });
    await fetchSleepReport(PROJECT_ID, RUN_ID);
    expect(captured.url).toBe(
      `/api/pairs/${PROJECT_ID}/sleep-runs/${RUN_ID}/report`,
    );
  });
});

describe("diffQTables", () => {
  it("treats every state as added when prev is null", () => {
    const d = diffQTables(null, { "s1": { buy: 0.1 }, "s2": { sell: 0.2 } });
    expect(d.addedStates.sort()).toEqual(["s1", "s2"]);
    expect(d.changedArgmaxStates).toEqual([]);
    expect(d.totalStates).toBe(2);
  });

  it("flags states whose argmax flipped", () => {
    const prev = { s1: { buy: 0.5, sell: 0.1 } };
    const next = { s1: { buy: 0.1, sell: 0.7 } };
    const d = diffQTables(prev, next);
    expect(d.addedStates).toEqual([]);
    expect(d.changedArgmaxStates).toEqual([
      { stateKey: "s1", prevAction: "buy", nextAction: "sell" },
    ]);
  });

  it("ignores unchanged argmax", () => {
    const prev = { s1: { buy: 0.5, sell: 0.1 } };
    const next = { s1: { buy: 0.9, sell: 0.1 } };
    const d = diffQTables(prev, next);
    expect(d.changedArgmaxStates).toEqual([]);
  });
});
