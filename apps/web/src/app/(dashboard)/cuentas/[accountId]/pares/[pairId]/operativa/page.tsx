"use client";

/**
 * Operativa — top-level tab of the project detail screen.
 *
 * The realtime operator surface. Three sections, top to bottom:
 *
 *   ┌────────────────────────────────────────────────────────┐
 *   │  AccountSummaryCard — MCP+DB hybrid health snapshot    │
 *   ├────────────────────────────────────────────────────────┤
 *   │  OpenPositionsTable — live MCP positions feed          │
 *   ├────────────────────────────────────────────────────────┤
 *   │  OrdersHistoryTable — paginated + filtered history     │
 *   └────────────────────────────────────────────────────────┘
 *
 * Live data is driven by `useOperativaWebSocket(pairId)` — a single
 * hook owns the WS lifecycle + REST fallback + transport-state machine.
 * The orders history table fetches independently because its data is
 * query-driven (filters change) and not push-friendly.
 *
 * Page chrome (header, back link, lifecycle controls, tab nav) is
 * supplied by `[id]/layout.tsx`.
 */

import { use } from "react";

import { AccountSummaryCard } from "@/components/operativa/AccountSummaryCard";
import { OpenPositionsTable } from "@/components/operativa/OpenPositionsTable";
import { OrdersHistoryTable } from "@/components/operativa/OrdersHistoryTable";
import { useOperativaWebSocket } from "@/components/operativa/useOperativaWebSocket";
import { Badge } from "@/components/ui/badge";

function TransportBadge({
  state,
}: {
  state: ReturnType<typeof useOperativaWebSocket>["transportState"];
}): React.JSX.Element {
  switch (state) {
    case "live":
      return (
        <Badge variant="success" data-testid="operativa-transport-badge">
          En vivo
        </Badge>
      );
    case "connecting":
      return (
        <Badge variant="muted" data-testid="operativa-transport-badge">
          Conectando…
        </Badge>
      );
    case "reconnecting":
      return (
        <Badge variant="warning" data-testid="operativa-transport-badge">
          Reconectando
        </Badge>
      );
    case "rest":
      return (
        <Badge variant="muted" data-testid="operativa-transport-badge">
          Sólo REST
        </Badge>
      );
    case "error":
      return (
        <Badge variant="danger" data-testid="operativa-transport-badge">
          Sin conexión en vivo
        </Badge>
      );
  }
}

export default function OperativaPage({
  params,
}: {
  params: Promise<{ accountId: string; pairId: string }>;
}): React.JSX.Element {
  const { pairId } = use(params);
  const { accountSummary, positions, mcpStatus, transportState } =
    useOperativaWebSocket(pairId);

  return (
    <section
      data-testid="operativa-page"
      className="flex flex-col gap-6"
    >
      <div className="flex items-center justify-between">
        <h2 className="text-xl font-semibold">Operativa</h2>
        <TransportBadge state={transportState} />
      </div>
      <AccountSummaryCard summary={accountSummary} mcpStatus={mcpStatus} />
      <OpenPositionsTable positions={positions} />
      <OrdersHistoryTable pairId={pairId} />
    </section>
  );
}
