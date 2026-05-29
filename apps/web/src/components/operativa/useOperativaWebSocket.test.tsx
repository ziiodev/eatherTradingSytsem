/**
 * useOperativaWebSocket — transport behavior tests (Phase 5.2).
 *
 * Coverage:
 * - On mount: kicks off the initial REST fetch AND opens a WS.
 * - First WS event flips state to `live` and updates accountSummary/mcp.
 * - WS close with a non-1008 code triggers reconnect (via factory).
 * - WS close with 1008 (policy violation) sets state=error and DOES NOT
 *   reconnect — REST polling keeps running so UI stays fresh.
 * - REST polling fills the gap during reconnect attempts.
 */

import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/operativa", async (importOriginal) => {
  const actual = await importOriginal<Record<string, unknown>>();
  return {
    ...actual,
    fetchAccountSummary: vi.fn(),
    fetchOrders: vi.fn().mockResolvedValue({
      items: [],
      total: 0,
      metrics: {
        trades_total: 0,
        win_rate: 0,
        profit_factor: 0,
        avg_rr: null,
        total_pnl: 0,
      },
    }),
  };
});

import {
  useOperativaWebSocket,
  type UseOperativaWebSocketOptions,
} from "./useOperativaWebSocket";
import { fetchAccountSummary, fetchOrders } from "@/lib/operativa";

const PROJECT_ID = "33333333-3333-3333-3333-333333333333";

/**
 * Minimal hand-rolled WebSocket mock — enough surface to drive
 * open/message/close from tests. Real WebSocket events have CloseEvent
 * shape so we synthesise enough of it.
 */
class FakeWebSocket {
  static CONNECTING = 0;
  static OPEN = 1;
  static CLOSING = 2;
  static CLOSED = 3;

  readonly url: string;
  readyState: number = FakeWebSocket.CONNECTING;
  onopen: ((ev: Event) => void) | null = null;
  onmessage: ((ev: MessageEvent) => void) | null = null;
  onclose: ((ev: CloseEvent) => void) | null = null;
  onerror: ((ev: Event) => void) | null = null;

  constructor(url: string) {
    this.url = url;
  }

  send(): void {
    /* no-op */
  }

  close(code = 1000): void {
    this.readyState = FakeWebSocket.CLOSED;
    this.onclose?.({
      code,
      reason: "",
      wasClean: true,
    } as CloseEvent);
  }

  // Test helpers — not part of the WebSocket API.
  _open(): void {
    this.readyState = FakeWebSocket.OPEN;
    this.onopen?.({} as Event);
  }

  _emit(payload: unknown): void {
    this.onmessage?.({ data: JSON.stringify(payload) } as MessageEvent);
  }

  _closeWithCode(code: number): void {
    this.readyState = FakeWebSocket.CLOSED;
    this.onclose?.({
      code,
      reason: "",
      wasClean: true,
    } as CloseEvent);
  }
}

type Sockets = FakeWebSocket[];

function makeFactory(sockets: Sockets): (url: string) => WebSocket {
  return ((url: string): FakeWebSocket => {
    const ws = new FakeWebSocket(url);
    sockets.push(ws);
    return ws;
  }) as unknown as (url: string) => WebSocket;
}

function takeSocket(sockets: Sockets, idx: number): FakeWebSocket {
  const ws = sockets[idx];
  if (!ws) {
    throw new Error(`No socket at index ${idx}`);
  }
  return ws;
}

const ACCOUNT_FIXTURE = {
  equity: 10_000,
  balance: 10_000,
  margin_used: 0,
  margin_free: 10_000,
  current_drawdown: 0,
  pnl_day: 0,
  pnl_week: 0,
  pnl_month: 0,
  mcp_status: "available" as const,
  source_at: "2026-05-29T18:00:00Z",
};

function renderHookWithOpts(opts: UseOperativaWebSocketOptions) {
  return renderHook(() => useOperativaWebSocket(PROJECT_ID, opts));
}

describe("useOperativaWebSocket", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    (fetchAccountSummary as ReturnType<typeof vi.fn>).mockResolvedValue(
      ACCOUNT_FIXTURE,
    );
    (fetchOrders as ReturnType<typeof vi.fn>).mockResolvedValue({
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

  afterEach(() => {
    vi.useRealTimers();
    vi.clearAllMocks();
  });

  it("starts in `connecting` and runs the initial REST fetch on mount", async () => {
    const sockets: Sockets = [];
    const { result } = renderHookWithOpts({
      webSocketFactory: makeFactory(sockets),
      restPollMs: 60_000,
    });

    expect(result.current.transportState).toBe("connecting");
    expect(sockets).toHaveLength(1);

    // Initial REST fetch — flush microtasks.
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(fetchAccountSummary).toHaveBeenCalledWith(PROJECT_ID);
    expect(result.current.accountSummary?.equity).toBe(10_000);
  });

  it("flips to `live` once the first WS event is received", async () => {
    const sockets: Sockets = [];
    const { result } = renderHookWithOpts({
      webSocketFactory: makeFactory(sockets),
      restPollMs: 60_000,
    });

    const ws = takeSocket(sockets, 0);
    await act(async () => {
      ws._open();
      ws._emit({
        type: "account_snapshot",
        ts: "2026-05-29T18:00:01Z",
        data: { ...ACCOUNT_FIXTURE, equity: 10_050 },
      });
    });

    expect(result.current.transportState).toBe("live");
    expect(result.current.accountSummary?.equity).toBe(10_050);
    expect(result.current.mcpStatus).toBe("available");
  });

  it("reconnects after a non-1008 close (transient failure)", async () => {
    const sockets: Sockets = [];
    const { result } = renderHookWithOpts({
      webSocketFactory: makeFactory(sockets),
      restPollMs: 60_000,
    });

    await act(async () => {
      takeSocket(sockets, 0)._open();
    });

    // Server closes the connection with code 1011 (transient internal).
    await act(async () => {
      takeSocket(sockets, 0)._closeWithCode(1011);
    });

    expect(result.current.transportState).toBe("reconnecting");

    // Advance past the 1s initial backoff — the factory should be called
    // a second time, proving reconnection.
    await act(async () => {
      vi.advanceTimersByTime(1_100);
    });

    expect(sockets).toHaveLength(2);
  });

  it("DOES NOT reconnect after close code 1008 (policy violation)", async () => {
    const sockets: Sockets = [];
    const { result } = renderHookWithOpts({
      webSocketFactory: makeFactory(sockets),
      restPollMs: 60_000,
    });

    await act(async () => {
      const ws = takeSocket(sockets, 0);
      ws._open();
      ws._closeWithCode(1008);
    });

    expect(result.current.transportState).toBe("error");

    // Even after a generous time-skip, no new socket is created.
    await act(async () => {
      vi.advanceTimersByTime(60_000);
    });

    expect(sockets).toHaveLength(1);
  });

  it("REST polling fills the gap while reconnecting", async () => {
    const sockets: Sockets = [];
    const { result } = renderHookWithOpts({
      webSocketFactory: makeFactory(sockets),
      restPollMs: 500,
    });

    await act(async () => {
      const ws = takeSocket(sockets, 0);
      ws._open();
      ws._closeWithCode(1011);
    });

    // Reset call counter so we count only polling cycles (not the initial
    // fetch on mount).
    (fetchAccountSummary as ReturnType<typeof vi.fn>).mockClear();

    await act(async () => {
      vi.advanceTimersByTime(500);
      // microtask flush
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(fetchAccountSummary).toHaveBeenCalledWith(PROJECT_ID);
    expect(result.current.transportState).toBe("reconnecting");
  });

  it("does not poll once we are live (the WS is the source of truth)", async () => {
    const sockets: Sockets = [];
    renderHookWithOpts({
      webSocketFactory: makeFactory(sockets),
      restPollMs: 500,
    });

    await act(async () => {
      const ws = takeSocket(sockets, 0);
      ws._open();
      ws._emit({
        type: "account_snapshot",
        ts: "2026-05-29T18:00:01Z",
        data: ACCOUNT_FIXTURE,
      });
    });

    (fetchAccountSummary as ReturnType<typeof vi.fn>).mockClear();
    await act(async () => {
      vi.advanceTimersByTime(2_000);
      await Promise.resolve();
    });
    expect(fetchAccountSummary).not.toHaveBeenCalled();
  });

  it("returns to `rest` when enabled is false (does not open a WS)", () => {
    const sockets: Sockets = [];
    const { result } = renderHookWithOpts({
      enabled: false,
      webSocketFactory: makeFactory(sockets),
      restPollMs: 60_000,
    });

    expect(result.current.transportState).toBe("rest");
    expect(sockets).toHaveLength(0);
  });

  it("appends order_event entries to the bounded ring buffer", async () => {
    const sockets: Sockets = [];
    const { result } = renderHookWithOpts({
      webSocketFactory: makeFactory(sockets),
      restPollMs: 60_000,
      maxRecentOrderEvents: 3,
    });

    const orderFixture = {
      id: "44444444-4444-4444-4444-444444444444",
      project_id: PROJECT_ID,
      agent_id: null,
      symbol: "EURUSD",
      side: "buy",
      volume: "0.10",
      sl: "1.0800",
      tp: null,
      mt5_ticket: 1,
      status: "filled",
      comment: null,
      magic: 0,
      created_at: "2026-05-29T18:00:00Z",
      filled_at: "2026-05-29T18:00:01Z",
      meta_data: {},
    };

    await act(async () => {
      const ws = takeSocket(sockets, 0);
      ws._open();
      for (let i = 0; i < 5; i++) {
        ws._emit({
          type: "order_event",
          ts: "2026-05-29T18:00:02Z",
          data: { event: `evt_${i}`, order: { ...orderFixture, mt5_ticket: i } },
        });
      }
    });

    expect(result.current.recentOrderEvents.length).toBe(3);
    // Newest-first ordering: evt_4 should be at index 0.
    expect(result.current.recentOrderEvents[0]?.event).toBe("evt_4");
  });
});
