/**
 * AccountSummaryCard — Phase 6.1 tests.
 *
 * - Renders the six MCP fields + three rolling P&L windows when MCP is up.
 * - When MCP is down, the warning badge surfaces AND the five MCP-derived
 *   fields render as "—" (DB-derived P&L still renders normally).
 * - P&L color-coding flips on the sign of the value.
 */

import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import { AccountSummaryCard } from "./AccountSummaryCard";
import type { AccountSummary } from "@/lib/operativa";

const HEALTHY: AccountSummary = {
  equity: 10_250.5,
  balance: 10_000,
  margin_used: 500,
  margin_free: 9_500,
  current_drawdown: 0,
  pnl_day: 12.34,
  pnl_week: -45.67,
  pnl_month: 0,
  mcp_status: "available",
  source_at: "2026-05-29T18:00:00Z",
};

describe("AccountSummaryCard", () => {
  it("renders six MCP fields + three rolling P&L windows when MCP is available", () => {
    render(<AccountSummaryCard summary={HEALTHY} />);

    expect(screen.getByTestId("account-summary-card")).toBeInTheDocument();
    // Locale formatting in happy-dom varies by node version (sometimes
    // omits thousand separators), so we assert digit-level membership
    // rather than a strict separator pattern.
    expect(screen.getByTestId("account-summary-equity")).toHaveTextContent(
      /10.?250/,
    );
    expect(screen.getByTestId("account-summary-balance")).toHaveTextContent(
      /10.?000/,
    );
    expect(screen.getByTestId("account-summary-margin-used")).toHaveTextContent(
      /500/,
    );
    expect(screen.getByTestId("account-summary-margin-free")).toHaveTextContent(
      /9.?500/,
    );
    expect(screen.getByTestId("account-summary-drawdown")).toHaveTextContent(
      /0[.,]00/,
    );
    expect(screen.getByTestId("account-summary-pnl-day")).toBeInTheDocument();
    expect(screen.getByTestId("account-summary-pnl-week")).toBeInTheDocument();
    expect(screen.getByTestId("account-summary-pnl-month")).toBeInTheDocument();

    // The "MCP en vivo" success badge is rendered (the "no disponible" one
    // is NOT).
    expect(
      screen.getByTestId("account-summary-mcp-up-badge"),
    ).toBeInTheDocument();
    expect(
      screen.queryByTestId("account-summary-mcp-down-badge"),
    ).not.toBeInTheDocument();
  });

  it("renders the MCP-down badge and nulls the MCP-derived fields when MCP is unavailable", () => {
    const downed: AccountSummary = {
      ...HEALTHY,
      equity: null,
      balance: null,
      margin_used: null,
      margin_free: null,
      current_drawdown: null,
      mcp_status: "unavailable",
    };
    render(<AccountSummaryCard summary={downed} />);

    expect(
      screen.getByTestId("account-summary-mcp-down-badge"),
    ).toHaveTextContent(/MCP no disponible/i);

    // MCP-derived fields render the em-dash sentinel.
    expect(screen.getByTestId("account-summary-equity")).toHaveTextContent(
      "—",
    );
    expect(screen.getByTestId("account-summary-balance")).toHaveTextContent(
      "—",
    );
    expect(screen.getByTestId("account-summary-margin-used")).toHaveTextContent(
      "—",
    );
    expect(screen.getByTestId("account-summary-margin-free")).toHaveTextContent(
      "—",
    );
    expect(screen.getByTestId("account-summary-drawdown")).toHaveTextContent(
      "—",
    );

    // DB-derived P&L still renders (Worker authority — values came from DB).
    expect(screen.getByTestId("account-summary-pnl-day")).not.toHaveTextContent(
      "—",
    );
  });

  it("color-codes positive vs negative vs zero P&L values", () => {
    render(<AccountSummaryCard summary={HEALTHY} />);

    const day = screen.getByTestId("account-summary-pnl-day").querySelector("dd");
    const week = screen
      .getByTestId("account-summary-pnl-week")
      .querySelector("dd");
    const month = screen
      .getByTestId("account-summary-pnl-month")
      .querySelector("dd");

    expect(day?.className).toContain("rgb(var(--success))");
    expect(week?.className).toContain("rgb(var(--danger))");
    expect(month?.className).toContain("rgb(var(--foreground-muted))");
  });

  it("falls back to a loading caption when summary is null", () => {
    render(<AccountSummaryCard summary={null} />);
    expect(screen.getByText(/cargando/i)).toBeInTheDocument();
  });
});
