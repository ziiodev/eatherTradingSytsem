/**
 * Unit tests for the operativa lib (Phase 5.1 — project-operativa).
 *
 * Covers:
 * - Schema parsing for AccountSummary, OperativaMetrics, OrdersListResponse.
 * - Profit factor accepting both numbers AND the literal string "Infinity".
 * - WsEvent discriminated union routing across the five event types.
 * - URL composition for fetchOrders with the full filter matrix.
 */

import { describe, expect, it, vi, beforeEach } from "vitest";

vi.mock("@/lib/api", () => ({
  apiGet: vi.fn(),
  apiPost: vi.fn(),
}));

import {
  accountSummarySchema,
  metricsSchema,
  ordersListResponseSchema,
  wsEventSchema,
  fetchOrders,
  fetchAccountSummary,
} from "./operativa";
import { apiGet } from "@/lib/api";

const PROJECT_ID = "22222222-2222-2222-2222-222222222222";

describe("accountSummarySchema", () => {
  it("parses a fully-populated MCP-available summary", () => {
    const parsed = accountSummarySchema.parse({
      equity: "10250.50",
      balance: "10000.00",
      margin_used: "500.00",
      margin_free: "9500.00",
      current_drawdown: "0",
      pnl_day: "12.34",
      pnl_week: "45.67",
      pnl_month: "78.90",
      mcp_status: "available",
      source_at: "2026-05-29T18:00:00Z",
    });
    expect(parsed.equity).toBeCloseTo(10250.5);
    expect(parsed.pnl_day).toBeCloseTo(12.34);
    expect(parsed.mcp_status).toBe("available");
  });

  it("accepts null MCP fields when status is unavailable (degraded mode)", () => {
    const parsed = accountSummarySchema.parse({
      equity: null,
      balance: null,
      margin_used: null,
      margin_free: null,
      current_drawdown: null,
      pnl_day: "0",
      pnl_week: "0",
      pnl_month: "0",
      mcp_status: "unavailable",
      source_at: "2026-05-29T18:00:00Z",
    });
    expect(parsed.equity).toBeNull();
    expect(parsed.mcp_status).toBe("unavailable");
  });

  it("rejects unknown mcp_status values", () => {
    const result = accountSummarySchema.safeParse({
      pnl_day: 0,
      pnl_week: 0,
      pnl_month: 0,
      mcp_status: "bogus",
      source_at: "2026-05-29T18:00:00Z",
    });
    expect(result.success).toBe(false);
  });
});

describe("metricsSchema", () => {
  it("accepts a numeric profit_factor", () => {
    const parsed = metricsSchema.parse({
      trades_total: 10,
      win_rate: 0.6,
      profit_factor: 1.5,
      avg_rr: 0.8,
      total_pnl: "123.45",
    });
    expect(parsed.profit_factor).toBe(1.5);
  });

  it("accepts the literal string 'Infinity' for profit_factor (wins, zero losses)", () => {
    const parsed = metricsSchema.parse({
      trades_total: 3,
      win_rate: 1,
      profit_factor: "Infinity",
      avg_rr: null,
      total_pnl: "9.99",
    });
    expect(parsed.profit_factor).toBe("Infinity");
  });

  it("rejects other strings (only the literal 'Infinity' is allowed)", () => {
    const result = metricsSchema.safeParse({
      trades_total: 1,
      win_rate: 1,
      profit_factor: "inf",
      avg_rr: null,
      total_pnl: 0,
    });
    expect(result.success).toBe(false);
  });

  it("allows null avg_rr (no valid R denominator)", () => {
    const parsed = metricsSchema.parse({
      trades_total: 0,
      win_rate: 0,
      profit_factor: 0,
      avg_rr: null,
      total_pnl: 0,
    });
    expect(parsed.avg_rr).toBeNull();
  });
});

describe("ordersListResponseSchema", () => {
  it("parses a paged response with extended order columns", () => {
    const parsed = ordersListResponseSchema.parse({
      items: [
        {
          id: "11111111-1111-1111-1111-111111111111",
          project_id: PROJECT_ID,
          agent_id: null,
          symbol: "EURUSD",
          side: "buy",
          volume: "0.10",
          sl: "1.0800",
          tp: null,
          mt5_ticket: 12345,
          status: "closed",
          comment: null,
          magic: 0,
          created_at: "2026-05-28T12:00:00Z",
          filled_at: "2026-05-28T12:00:01Z",
          open_time: "2026-05-28T12:00:01Z",
          open_price: "1.0850",
          close_time: "2026-05-28T15:00:00Z",
          close_price: "1.0870",
          commission: "-2.00",
          swap: "0",
          profit_gross: "20.00",
          profit_net: "18.00",
          meta_data: { broker_ticket: 12345 },
        },
      ],
      total: 1,
      metrics: {
        trades_total: 1,
        win_rate: 1.0,
        profit_factor: "Infinity",
        avg_rr: null,
        total_pnl: "18.00",
      },
    });
    expect(parsed.items[0]?.profit_net).toBeCloseTo(18);
    expect(parsed.items[0]?.meta_data).toEqual({ broker_ticket: 12345 });
    expect(parsed.metrics.profit_factor).toBe("Infinity");
  });
});

describe("wsEventSchema", () => {
  it("routes an account_snapshot event", () => {
    const evt = wsEventSchema.parse({
      type: "account_snapshot",
      ts: "2026-05-29T18:00:00Z",
      data: {
        equity: "10000",
        balance: "10000",
        margin_used: "0",
        margin_free: "10000",
        current_drawdown: "0",
        pnl_day: "0",
        pnl_week: "0",
        pnl_month: "0",
        mcp_status: "available",
        source_at: "2026-05-29T18:00:00Z",
      },
    });
    expect(evt.type).toBe("account_snapshot");
  });

  it("routes a position_snapshot event", () => {
    const evt = wsEventSchema.parse({
      type: "position_snapshot",
      ts: "2026-05-29T18:00:00Z",
      data: { positions: [] },
    });
    expect(evt.type).toBe("position_snapshot");
  });

  it("routes an mcp_status event with reason", () => {
    const evt = wsEventSchema.parse({
      type: "mcp_status",
      ts: "2026-05-29T18:00:00Z",
      data: { status: "unavailable", reason: "connection_refused" },
    });
    expect(evt.type).toBe("mcp_status");
  });

  it("routes a ping heartbeat", () => {
    const evt = wsEventSchema.parse({
      type: "ping",
      ts: "2026-05-29T18:00:00Z",
    });
    expect(evt.type).toBe("ping");
  });

  it("rejects an unknown event type", () => {
    const result = wsEventSchema.safeParse({
      type: "bogus",
      ts: "2026-05-29T18:00:00Z",
    });
    expect(result.success).toBe(false);
  });
});

describe("fetchOrders URL composition", () => {
  const mockApiGet = apiGet as ReturnType<typeof vi.fn>;

  beforeEach(() => {
    mockApiGet.mockReset();
    mockApiGet.mockResolvedValue({
      items: [],
      total: 0,
      metrics: {
        trades_total: 0,
        win_rate: 0,
        profit_factor: 0,
        avg_rr: null,
        total_pnl: 0,
      },
    });
  });

  it("emits a clean URL with no query string when no filters are passed", async () => {
    await fetchOrders(PROJECT_ID);
    expect(mockApiGet).toHaveBeenCalledWith(
      `/api/projects/${PROJECT_ID}/operativa/orders`,
    );
  });

  it("encodes every filter via URLSearchParams", async () => {
    await fetchOrders(PROJECT_ID, {
      from: "2026-05-01T00:00:00Z",
      to: "2026-05-29T23:59:59Z",
      symbol: "EURUSD",
      side: "buy",
      result: "win",
      magic: 42,
      status: "closed",
      limit: 25,
      offset: 50,
    });

    const callArg = mockApiGet.mock.calls[0]?.[0] as string;
    expect(callArg.startsWith(
      `/api/projects/${PROJECT_ID}/operativa/orders?`,
    )).toBe(true);
    const qs = new URLSearchParams(callArg.split("?")[1]);
    expect(qs.get("from")).toBe("2026-05-01T00:00:00Z");
    expect(qs.get("to")).toBe("2026-05-29T23:59:59Z");
    expect(qs.get("symbol")).toBe("EURUSD");
    expect(qs.get("side")).toBe("buy");
    expect(qs.get("result")).toBe("win");
    expect(qs.get("magic")).toBe("42");
    expect(qs.get("status")).toBe("closed");
    expect(qs.get("limit")).toBe("25");
    expect(qs.get("offset")).toBe("50");
  });

  it("omits falsy optional params (but allows magic=0)", async () => {
    await fetchOrders(PROJECT_ID, { magic: 0, limit: 50 });
    const callArg = mockApiGet.mock.calls[0]?.[0] as string;
    const qs = new URLSearchParams(callArg.split("?")[1]);
    expect(qs.get("magic")).toBe("0");
    expect(qs.get("limit")).toBe("50");
    expect(qs.has("symbol")).toBe(false);
    expect(qs.has("from")).toBe(false);
  });
});

describe("fetchAccountSummary", () => {
  const mockApiGet = apiGet as ReturnType<typeof vi.fn>;

  it("calls the operativa account-summary endpoint and parses the result", async () => {
    mockApiGet.mockReset();
    mockApiGet.mockResolvedValue({
      equity: "10000",
      balance: "10000",
      margin_used: "0",
      margin_free: "10000",
      current_drawdown: "0",
      pnl_day: "1.23",
      pnl_week: "4.56",
      pnl_month: "7.89",
      mcp_status: "available",
      source_at: "2026-05-29T18:00:00Z",
    });

    const result = await fetchAccountSummary(PROJECT_ID);
    expect(mockApiGet).toHaveBeenCalledWith(
      `/api/projects/${PROJECT_ID}/operativa/account-summary`,
    );
    expect(result.pnl_day).toBeCloseTo(1.23);
  });
});
