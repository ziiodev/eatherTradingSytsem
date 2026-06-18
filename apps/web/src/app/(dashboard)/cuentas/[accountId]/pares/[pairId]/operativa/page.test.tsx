/**
 * Operativa page — Phase 7.3 E2E smoke test.
 *
 * Mocks the lib layer (`useOperativaWebSocket` + `fetchOrders`) and
 * confirms the three sections (AccountSummaryCard, OpenPositionsTable,
 * OrdersHistoryTable) render together. Also covers:
 *
 *   - MCP available vs MCP unavailable rendering of the account card.
 *   - OpenPositionsTable empty state ("Sin posiciones abiertas").
 *   - Orders history with rows + Infinity profit factor rendering.
 */

import { Suspense } from "react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";

import type {
  AccountSummary,
  OperativaOrderRecord,
  OrdersListResponse,
  Position,
} from "@/lib/operativa";

/**
 * Resolve `params` synchronously the same way Next 16's `use(params)`
 * expects. Plain `Promise.resolve(...)` leaves status undefined → React
 * suspends. Pre-resolving avoids the suspension in tests.
 */
function resolvedParams<T>(value: T): Promise<T> {
  const p = Promise.resolve(value) as Promise<T> & {
    status?: string;
    value?: T;
  };
  p.status = "fulfilled";
  p.value = value;
  return p;
}

// --- Mock the hook so we drive the page state from the test directly --
const hookState: {
  accountSummary: AccountSummary | null;
  positions: Position[];
  mcpStatus: "available" | "unavailable" | null;
  transportState:
    | "connecting"
    | "live"
    | "reconnecting"
    | "rest"
    | "error";
} = {
  accountSummary: null,
  positions: [],
  mcpStatus: null,
  transportState: "connecting",
};

vi.mock(
  "@/components/operativa/useOperativaWebSocket",
  () => ({
    useOperativaWebSocket: () => ({
      ...hookState,
      recentOrderEvents: [],
    }),
  }),
);

// --- Mock the orders fetcher so the history table can resolve --------
vi.mock("@/lib/operativa", async (importOriginal) => {
  const actual = await importOriginal<Record<string, unknown>>();
  return {
    ...actual,
    fetchOrders: vi.fn(),
  };
});

import OperativaPage from "./page";
import { fetchOrders } from "@/lib/operativa";

const ACCOUNT_ID = "00000000-0000-0000-0000-000000000000";
const PROJECT_ID = "66666666-6666-6666-6666-666666666666";

const ORDER_FIXTURE: OperativaOrderRecord = {
  id: "77777777-7777-7777-7777-777777777777",
  pair_id: PROJECT_ID,
  agent_id: null,
  symbol: "EURUSD",
  side: "buy",
  volume: 0.1,
  sl: 1.08,
  tp: null,
  mt5_ticket: 9001,
  status: "closed",
  comment: null,
  magic: 0,
  created_at: "2026-05-28T12:00:00Z",
  filled_at: "2026-05-28T12:00:01Z",
  open_time: "2026-05-28T12:00:01Z",
  open_price: 1.085,
  close_time: "2026-05-28T15:00:00Z",
  close_price: 1.087,
  commission: -2,
  swap: 0,
  profit_gross: 20,
  profit_net: 18,
  meta_data: {},
};

const ORDERS_RESP: OrdersListResponse = {
  items: [ORDER_FIXTURE],
  total: 1,
  metrics: {
    trades_total: 1,
    win_rate: 1,
    profit_factor: "Infinity",
    avg_rr: null,
    total_pnl: 18,
  },
};

describe("OperativaPage (Phase 7.3 — smoke)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    (fetchOrders as ReturnType<typeof vi.fn>).mockResolvedValue(ORDERS_RESP);
    // Reset hookState
    hookState.accountSummary = null;
    hookState.positions = [];
    hookState.mcpStatus = null;
    hookState.transportState = "connecting";
  });

  it("renders the three sections with MCP available + positions + history rows + Infinity PF", async () => {
    hookState.accountSummary = {
      equity: 10_000,
      balance: 10_000,
      margin_used: 0,
      margin_free: 10_000,
      current_drawdown: 0,
      pnl_day: 1.23,
      pnl_week: 4.56,
      pnl_month: 7.89,
      mcp_status: "available",
      source_at: "2026-05-29T18:00:00Z",
    };
    hookState.mcpStatus = "available";
    hookState.transportState = "live";
    hookState.positions = [
      {
        ticket: 1001,
        symbol: "EURUSD",
        side: "buy",
        volume: 0.1,
        price_open: 1.085,
        sl: 1.08,
        tp: 1.09,
        profit: 12.5,
        time: "2026-05-29T17:00:00Z",
      },
    ];

    render(
      <Suspense fallback={<div>L</div>}>
        <OperativaPage params={resolvedParams({ accountId: ACCOUNT_ID, pairId: PROJECT_ID })} />
      </Suspense>,
    );

    expect(await screen.findByTestId("operativa-page")).toBeInTheDocument();
    expect(
      await screen.findByTestId("account-summary-card"),
    ).toBeInTheDocument();
    expect(
      screen.getByTestId("account-summary-mcp-up-badge"),
    ).toBeInTheDocument();

    expect(screen.getByTestId("open-positions-card")).toBeInTheDocument();
    expect(
      screen.getByTestId("open-positions-row-1001"),
    ).toBeInTheDocument();

    // History table renders async because fetchOrders is awaited inside the
    // table's effect.
    await waitFor(() => {
      expect(
        screen.getByTestId("orders-history-table"),
      ).toBeInTheDocument();
    });
    expect(
      screen.getByTestId("orders-history-metric-pf"),
    ).toHaveTextContent("∞");

    // Transport chip
    expect(screen.getByTestId("operativa-transport-badge")).toHaveTextContent(
      /en vivo/i,
    );
  });

  it("renders the MCP-down badge and the empty positions state when MCP is unavailable", async () => {
    hookState.accountSummary = {
      equity: null,
      balance: null,
      margin_used: null,
      margin_free: null,
      current_drawdown: null,
      pnl_day: 0,
      pnl_week: 0,
      pnl_month: 0,
      mcp_status: "unavailable",
      source_at: "2026-05-29T18:00:00Z",
    };
    hookState.mcpStatus = "unavailable";
    hookState.transportState = "rest";
    hookState.positions = [];

    render(
      <Suspense fallback={<div>L</div>}>
        <OperativaPage params={resolvedParams({ accountId: ACCOUNT_ID, pairId: PROJECT_ID })} />
      </Suspense>,
    );

    expect(
      await screen.findByTestId("account-summary-mcp-down-badge"),
    ).toBeInTheDocument();
    expect(
      screen.getByTestId("open-positions-empty"),
    ).toBeInTheDocument();
    // History still loads — fetchOrders is independent of MCP status.
    await waitFor(() => {
      expect(
        screen.getByTestId("orders-history-table"),
      ).toBeInTheDocument();
    });
  });
});
