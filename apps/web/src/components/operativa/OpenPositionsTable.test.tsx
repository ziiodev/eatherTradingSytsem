/**
 * OpenPositionsTable — Phase 6.2 tests.
 *
 * - Renders one row per position with the full column set.
 * - Empty state shows "Sin posiciones abiertas" copy.
 * - Clicking a sortable header re-orders the rows (asc → desc toggle).
 */

import { describe, expect, it } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";

import { OpenPositionsTable } from "./OpenPositionsTable";
import type { Position } from "@/lib/operativa";

const P1: Position = {
  ticket: 1001,
  symbol: "EURUSD",
  side: "buy",
  volume: 0.1,
  price_open: 1.085,
  sl: 1.08,
  tp: 1.09,
  profit: 12.5,
  time: "2026-05-29T17:00:00Z",
};
const P2: Position = {
  ticket: 1002,
  symbol: "USDJPY",
  side: "sell",
  volume: 0.5,
  price_open: 156.5,
  sl: 157,
  tp: 156,
  profit: -5,
  time: "2026-05-29T17:30:00Z",
};

describe("OpenPositionsTable", () => {
  it("renders the empty state when there are no positions", () => {
    render(<OpenPositionsTable positions={[]} />);
    expect(screen.getByTestId("open-positions-empty")).toHaveTextContent(
      /sin posiciones abiertas/i,
    );
    expect(
      screen.queryByTestId("open-positions-table"),
    ).not.toBeInTheDocument();
  });

  it("renders one row per position with the canonical columns", () => {
    render(<OpenPositionsTable positions={[P1, P2]} />);
    expect(screen.getByTestId("open-positions-table")).toBeInTheDocument();
    expect(
      screen.getByTestId("open-positions-row-1001"),
    ).toBeInTheDocument();
    expect(
      screen.getByTestId("open-positions-row-1002"),
    ).toBeInTheDocument();

    const r1 = screen.getByTestId("open-positions-row-1001");
    expect(within(r1).getByText("EURUSD")).toBeInTheDocument();
    expect(within(r1).getByText("buy")).toBeInTheDocument();
  });

  it("toggles sort direction when the same header is clicked twice", () => {
    render(<OpenPositionsTable positions={[P1, P2]} />);

    // Default sort by ticket asc → 1001 first.
    let rows = screen.getAllByRole("row");
    // First row is the header so check second.
    expect(rows[1]).toHaveAttribute(
      "data-testid",
      "open-positions-row-1001",
    );

    // Click profit header — asc → P2 (-5) first.
    fireEvent.click(screen.getByTestId("positions-sort-profit"));
    rows = screen.getAllByRole("row");
    expect(rows[1]).toHaveAttribute(
      "data-testid",
      "open-positions-row-1002",
    );

    // Click again — desc → P1 (12.5) first.
    fireEvent.click(screen.getByTestId("positions-sort-profit"));
    rows = screen.getAllByRole("row");
    expect(rows[1]).toHaveAttribute(
      "data-testid",
      "open-positions-row-1001",
    );
  });

  it("color-codes the floating P&L cell", () => {
    render(<OpenPositionsTable positions={[P1, P2]} />);
    const winCell = screen.getByTestId("open-positions-pnl-1001");
    const lossCell = screen.getByTestId("open-positions-pnl-1002");
    expect(winCell.className).toContain("rgb(var(--success))");
    expect(lossCell.className).toContain("rgb(var(--danger))");
  });
});
