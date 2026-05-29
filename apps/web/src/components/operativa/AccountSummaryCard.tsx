"use client";

/**
 * AccountSummaryCard — top section of the Operativa page.
 *
 * Six MCP-derived metrics + three DB-derived rolling P&L windows. Each
 * MCP field renders as "—" when null (the canonical degraded state when
 * `mcp_status === "unavailable"`); the DB-derived `pnl_*` rows always
 * render with the live numbers since the backend can compute them even
 * with MCP down.
 *
 * Visual conventions:
 *   - GitHub Dark palette via CSS variables (see ``globals.css``).
 *   - P&L numbers colour-coded: positive = success/green, negative =
 *     danger/red, zero = muted.
 *   - "MCP no disponible" badge (`variant=warning`) renders top-right
 *     when the live feed is degraded; the badge is the only place the
 *     UI surfaces MCP transport state on this card.
 */

import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import type { AccountSummary } from "@/lib/operativa";

export interface AccountSummaryCardProps {
  summary: AccountSummary | null;
  /**
   * When non-null and != summary.mcp_status, the latter wins — the WS
   * mcp_status event is authoritative once received.
   */
  mcpStatus?: "available" | "unavailable" | null;
}

const NBSP = " ";

function formatMoney(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return value.toLocaleString("es-ES", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

function pnlColor(value: number | null | undefined): string {
  if (value === null || value === undefined) {
    return "text-[rgb(var(--foreground-muted))]";
  }
  if (value > 0) return "text-[rgb(var(--success))]";
  if (value < 0) return "text-[rgb(var(--danger))]";
  return "text-[rgb(var(--foreground-muted))]";
}

function Field({
  label,
  value,
  className = "",
  testId,
}: {
  label: string;
  value: string;
  className?: string;
  testId?: string;
}): React.JSX.Element {
  return (
    <div className="flex flex-col" data-testid={testId}>
      <dt className="text-xs uppercase tracking-wide text-[rgb(var(--foreground-muted))]">
        {label}
      </dt>
      <dd className={`font-mono text-base ${className}`}>{value}</dd>
    </div>
  );
}

export function AccountSummaryCard({
  summary,
  mcpStatus,
}: AccountSummaryCardProps): React.JSX.Element {
  const effectiveStatus =
    mcpStatus ?? summary?.mcp_status ?? null;
  const mcpDown = effectiveStatus === "unavailable";

  // When loading (no summary yet), render skeleton-ish placeholders.
  const equity = summary?.equity ?? null;
  const balance = summary?.balance ?? null;
  const marginUsed = summary?.margin_used ?? null;
  const marginFree = summary?.margin_free ?? null;
  const drawdown = summary?.current_drawdown ?? null;
  const pnlDay = summary?.pnl_day ?? null;
  const pnlWeek = summary?.pnl_week ?? null;
  const pnlMonth = summary?.pnl_month ?? null;

  // MCP fields are nulled (greyed) when degraded; the formatter already
  // renders "—" for null so we just stop forcing a number.
  const equityDisplay = mcpDown ? null : equity;
  const balanceDisplay = mcpDown ? null : balance;
  const marginUsedDisplay = mcpDown ? null : marginUsed;
  const marginFreeDisplay = mcpDown ? null : marginFree;
  const drawdownDisplay = mcpDown ? null : drawdown;

  const greyedClass = mcpDown ? "opacity-50" : "";

  return (
    <Card data-testid="account-summary-card">
      <CardHeader className="flex flex-row items-start justify-between space-y-0">
        <div>
          <CardTitle>Resumen de cuenta</CardTitle>
          <CardDescription>
            {summary
              ? `Última actualización: ${summary.source_at.slice(0, 19).replace("T", " ")} UTC`
              : "Cargando…"}
          </CardDescription>
        </div>
        {mcpDown ? (
          <Badge
            variant="warning"
            data-testid="account-summary-mcp-down-badge"
          >
            MCP no disponible
          </Badge>
        ) : (
          <Badge variant="success" data-testid="account-summary-mcp-up-badge">
            MCP en vivo
          </Badge>
        )}
      </CardHeader>
      <CardContent>
        <dl className="grid grid-cols-2 gap-4 md:grid-cols-3">
          <Field
            label="Equity"
            value={formatMoney(equityDisplay)}
            className={greyedClass}
            testId="account-summary-equity"
          />
          <Field
            label="Balance"
            value={formatMoney(balanceDisplay)}
            className={greyedClass}
            testId="account-summary-balance"
          />
          <Field
            label="Margen usado"
            value={formatMoney(marginUsedDisplay)}
            className={greyedClass}
            testId="account-summary-margin-used"
          />
          <Field
            label="Margen libre"
            value={formatMoney(marginFreeDisplay)}
            className={greyedClass}
            testId="account-summary-margin-free"
          />
          <Field
            label="Drawdown actual"
            value={formatMoney(drawdownDisplay)}
            className={greyedClass}
            testId="account-summary-drawdown"
          />
          <Field
            label={`P&L${NBSP}hoy`}
            value={formatMoney(pnlDay)}
            className={pnlColor(pnlDay)}
            testId="account-summary-pnl-day"
          />
        </dl>
        <dl className="mt-4 grid grid-cols-2 gap-4 md:grid-cols-3">
          <Field
            label={`P&L${NBSP}semana`}
            value={formatMoney(pnlWeek)}
            className={pnlColor(pnlWeek)}
            testId="account-summary-pnl-week"
          />
          <Field
            label={`P&L${NBSP}mes`}
            value={formatMoney(pnlMonth)}
            className={pnlColor(pnlMonth)}
            testId="account-summary-pnl-month"
          />
        </dl>
      </CardContent>
    </Card>
  );
}
