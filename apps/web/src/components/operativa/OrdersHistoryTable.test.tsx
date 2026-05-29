/**
 * OrdersHistoryTable — Phase 6.3 tests.
 *
 * - Initial render fetches + renders metrics block + row.
 * - Profit Factor 'Infinity' renders as ∞.
 * - Toggling filters bar reveals filter inputs, applying re-fetches
 *   with the new opts.
 * - Pagination "siguiente" advances the offset.
 * - Empty state caption renders when items.length === 0.
 */

import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { OrdersHistoryTable } from "./OrdersHistoryTable";
import type {
  OperativaOrderRecord,
  OrdersListResponse,
  FetchOrdersOptions,
} from "@/lib/operativa";

const PROJECT_ID = "55555555-5555-5555-5555-555555555555";

const BASE_ORDER: OperativaOrderRecord = {
  id: "11111111-1111-1111-1111-111111111111",
  project_id: PROJECT_ID,
  agent_id: null,
  symbol: "EURUSD",
  side: "buy",
  volume: 0.1,
  sl: 1.08,
  tp: null,
  mt5_ticket: 9001,
  status: "closed",
  comment: "limpia",
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

function makeResp(
  items: OperativaOrderRecord[],
  total: number,
  pf: number | "Infinity" = "Infinity",
): OrdersListResponse {
  return {
    items,
    total,
    metrics: {
      trades_total: items.length,
      win_rate: 1,
      profit_factor: pf,
      avg_rr: null,
      total_pnl: items.reduce((s, o) => s + (o.profit_net ?? 0), 0),
    },
  };
}

describe("OrdersHistoryTable", () => {
  it("renders metrics block + row on first fetch (Profit Factor Infinity → ∞)", async () => {
    const fetcher = vi.fn().mockResolvedValue(makeResp([BASE_ORDER], 1));
    render(<OrdersHistoryTable projectId={PROJECT_ID} fetcher={fetcher} />);

    await waitFor(() => {
      expect(
        screen.getByTestId("orders-history-table"),
      ).toBeInTheDocument();
    });

    expect(
      screen.getByTestId("orders-history-row-" + BASE_ORDER.id),
    ).toBeInTheDocument();

    // Profit factor renders as ∞ (literal string mapped).
    expect(
      screen.getByTestId("orders-history-metric-pf"),
    ).toHaveTextContent("∞");
  });

  it("renders numeric profit factor as a two-decimal number", async () => {
    const fetcher = vi.fn().mockResolvedValue(makeResp([BASE_ORDER], 1, 1.75));
    render(<OrdersHistoryTable projectId={PROJECT_ID} fetcher={fetcher} />);
    await waitFor(() => {
      expect(
        screen.getByTestId("orders-history-metric-pf"),
      ).toHaveTextContent("1.75");
    });
  });

  it("renders the empty state when items is []", async () => {
    const fetcher = vi.fn().mockResolvedValue(makeResp([], 0, 0));
    render(<OrdersHistoryTable projectId={PROJECT_ID} fetcher={fetcher} />);
    await waitFor(() => {
      expect(
        screen.getByTestId("orders-history-empty"),
      ).toBeInTheDocument();
    });
  });

  it("re-fetches with new opts when filters are applied", async () => {
    const fetcher = vi
      .fn<
        (
          projectId: string,
          opts: FetchOrdersOptions,
        ) => Promise<OrdersListResponse>
      >()
      .mockResolvedValue(makeResp([BASE_ORDER], 1));

    render(<OrdersHistoryTable projectId={PROJECT_ID} fetcher={fetcher} />);
    await waitFor(() => {
      expect(fetcher).toHaveBeenCalledTimes(1);
    });
    // First call had no symbol filter.
    expect(fetcher.mock.calls[0]?.[1].symbol).toBeUndefined();

    // Open filters → change symbol → apply.
    fireEvent.click(screen.getByTestId("orders-history-toggle-filters"));
    fireEvent.change(screen.getByTestId("orders-history-filter-symbol"), {
      target: { value: "eurusd" },
    });
    fireEvent.click(screen.getByTestId("orders-history-apply-filters"));

    await waitFor(() => {
      expect(fetcher).toHaveBeenCalledTimes(2);
    });
    // Symbol is uppercased before being sent.
    expect(fetcher.mock.calls[1]?.[1].symbol).toBe("EURUSD");
    expect(fetcher.mock.calls[1]?.[1].offset).toBe(0);
  });

  it("advances offset by 50 when the next-page button is clicked", async () => {
    const fetcher = vi
      .fn<
        (
          projectId: string,
          opts: FetchOrdersOptions,
        ) => Promise<OrdersListResponse>
      >()
      .mockResolvedValue(makeResp([BASE_ORDER], 200));

    render(<OrdersHistoryTable projectId={PROJECT_ID} fetcher={fetcher} />);
    await waitFor(() => {
      expect(
        screen.getByTestId("orders-history-next-page"),
      ).toBeInTheDocument();
    });
    expect(fetcher.mock.calls[0]?.[1].offset).toBe(0);

    await act(async () => {
      fireEvent.click(screen.getByTestId("orders-history-next-page"));
    });
    await waitFor(() => {
      expect(fetcher).toHaveBeenCalledTimes(2);
    });
    expect(fetcher.mock.calls[1]?.[1].offset).toBe(50);
  });
});
