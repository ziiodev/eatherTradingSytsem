"use client";

/**
 * OrdersHistoryTable — bottom section of the Operativa page.
 *
 * Owns:
 *   - The collapsible filter bar (from / to / symbol / side / result /
 *     magic / status).
 *   - Pagination (50 per page, server-side via `limit` + `offset`).
 *   - The inline metrics card (Trades / Win Rate / Profit Factor /
 *     Avg R / Total PnL). Profit Factor renders as "∞" when the API
 *     returned the literal string "Infinity".
 *   - The detail rows: Ticket / Símbolo / Tipo / Volumen / Apertura /
 *     Cierre / P&L bruto / P&L neto / Comisión / Swap / Comentario.
 *
 * The fetcher (`fetchOrders`) lives in `lib/operativa.ts`. This component
 * is responsible for the filter state ↔ URL/query-param shape, debouncing
 * symbol input, and triggering re-fetches.
 */

import { useCallback, useEffect, useMemo, useState } from "react";

import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Button } from "@/components/ui/button";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  fetchOrders,
  type FetchOrdersOptions,
  type OperativaMetrics,
  type OperativaOrderRecord,
  type OrdersListResponse,
} from "@/lib/operativa";

const PAGE_SIZE = 50;

export interface OrdersHistoryTableProps {
  pairId: string;
  /**
   * Injected for tests so we don't need to round-trip through `fetch`.
   * Production callers leave undefined and the real `fetchOrders` is
   * used.
   */
  fetcher?: (
    pairId: string,
    opts: FetchOrdersOptions,
  ) => Promise<OrdersListResponse>;
}

interface FilterState {
  from: string;
  to: string;
  symbol: string;
  side: "" | "buy" | "sell";
  result: "" | "win" | "loss";
  magic: string;
  status: string;
}

const EMPTY_FILTERS: FilterState = {
  from: "",
  to: "",
  symbol: "",
  side: "",
  result: "",
  magic: "",
  status: "",
};

function formatPnl(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return value.toFixed(2);
}

function pnlColor(value: number | null | undefined): string {
  if (value === null || value === undefined) {
    return "text-[rgb(var(--foreground-muted))]";
  }
  if (value > 0) return "text-[rgb(var(--success))]";
  if (value < 0) return "text-[rgb(var(--danger))]";
  return "text-[rgb(var(--foreground-muted))]";
}

function formatProfitFactor(pf: number | "Infinity"): string {
  if (pf === "Infinity") return "∞";
  return pf.toFixed(2);
}

function formatTimestamp(iso: string | null | undefined): string {
  if (!iso) return "—";
  return iso.slice(0, 19).replace("T", " ");
}

function MetricsBar({
  metrics,
}: {
  metrics: OperativaMetrics;
}): React.JSX.Element {
  return (
    <div
      data-testid="orders-history-metrics"
      className="grid grid-cols-2 gap-4 md:grid-cols-5"
    >
      <Metric label="Trades" value={String(metrics.trades_total)} />
      <Metric
        label="Win Rate"
        value={`${(metrics.win_rate * 100).toFixed(1)} %`}
      />
      <Metric
        label="Profit Factor"
        value={formatProfitFactor(metrics.profit_factor)}
        testId="orders-history-metric-pf"
      />
      <Metric
        label="Avg R"
        value={metrics.avg_rr === null ? "—" : metrics.avg_rr.toFixed(2)}
      />
      <Metric
        label="Total PnL"
        value={formatPnl(metrics.total_pnl)}
        className={pnlColor(metrics.total_pnl)}
      />
    </div>
  );
}

function Metric({
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

export function OrdersHistoryTable({
  pairId,
  fetcher = fetchOrders,
}: OrdersHistoryTableProps): React.JSX.Element {
  const [filters, setFilters] = useState<FilterState>(EMPTY_FILTERS);
  const [committedFilters, setCommittedFilters] =
    useState<FilterState>(EMPTY_FILTERS);
  const [offset, setOffset] = useState(0);
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [data, setData] = useState<OrdersListResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchOpts = useMemo<FetchOrdersOptions>(() => {
    const opts: FetchOrdersOptions = {
      limit: PAGE_SIZE,
      offset,
    };
    if (committedFilters.from) opts.from = committedFilters.from;
    if (committedFilters.to) opts.to = committedFilters.to;
    if (committedFilters.symbol)
      opts.symbol = committedFilters.symbol.toUpperCase();
    if (committedFilters.side) opts.side = committedFilters.side;
    if (committedFilters.result) opts.result = committedFilters.result;
    if (committedFilters.magic !== "") {
      const m = Number(committedFilters.magic);
      if (!Number.isNaN(m)) opts.magic = m;
    }
    if (committedFilters.status) opts.status = committedFilters.status;
    return opts;
  }, [committedFilters, offset]);

  const reload = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await fetcher(pairId, fetchOpts);
      setData(result);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      setError(`No se pudo cargar el historial: ${msg}`);
    } finally {
      setLoading(false);
    }
  }, [fetcher, pairId, fetchOpts]);

  useEffect(() => {
    // The fetch sets state on completion — accepted pattern in this
    // codebase (mirrors agentes/page.tsx, configuracion/page.tsx).
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void reload();
  }, [reload]);

  const applyFilters = (): void => {
    setOffset(0);
    setCommittedFilters(filters);
  };

  const resetFilters = (): void => {
    setFilters(EMPTY_FILTERS);
    setCommittedFilters(EMPTY_FILTERS);
    setOffset(0);
  };

  const totalPages = data ? Math.max(1, Math.ceil(data.total / PAGE_SIZE)) : 1;
  const currentPage = Math.floor(offset / PAGE_SIZE) + 1;

  return (
    <Card data-testid="orders-history-card">
      <CardHeader className="flex flex-row items-start justify-between space-y-0">
        <div>
          <CardTitle>Historial de órdenes</CardTitle>
          <CardDescription>
            {data
              ? `${data.total} órden${data.total === 1 ? "" : "es"} bajo los filtros actuales`
              : "Cargando…"}
          </CardDescription>
        </div>
        <Button
          variant="outline"
          size="sm"
          data-testid="orders-history-toggle-filters"
          onClick={() => setFiltersOpen((v) => !v)}
        >
          {filtersOpen ? "Ocultar filtros" : "Mostrar filtros"}
        </Button>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        {filtersOpen ? (
          <div
            data-testid="orders-history-filters"
            className="grid grid-cols-2 gap-3 rounded-md border border-[rgb(var(--border))] p-3 md:grid-cols-4"
          >
            <div className="flex flex-col gap-1">
              <label className="text-xs text-[rgb(var(--foreground-muted))]">
                Desde
              </label>
              <Input
                type="datetime-local"
                value={filters.from}
                onChange={(e) =>
                  setFilters({ ...filters, from: e.target.value })
                }
                data-testid="orders-history-filter-from"
              />
            </div>
            <div className="flex flex-col gap-1">
              <label className="text-xs text-[rgb(var(--foreground-muted))]">
                Hasta
              </label>
              <Input
                type="datetime-local"
                value={filters.to}
                onChange={(e) =>
                  setFilters({ ...filters, to: e.target.value })
                }
                data-testid="orders-history-filter-to"
              />
            </div>
            <div className="flex flex-col gap-1">
              <label className="text-xs text-[rgb(var(--foreground-muted))]">
                Símbolo
              </label>
              <Input
                value={filters.symbol}
                onChange={(e) =>
                  setFilters({ ...filters, symbol: e.target.value })
                }
                placeholder="EURUSD"
                data-testid="orders-history-filter-symbol"
              />
            </div>
            <div className="flex flex-col gap-1">
              <label className="text-xs text-[rgb(var(--foreground-muted))]">
                Tipo
              </label>
              <Select
                value={filters.side}
                onChange={(e) =>
                  setFilters({
                    ...filters,
                    side: e.target.value as FilterState["side"],
                  })
                }
                data-testid="orders-history-filter-side"
              >
                <option value="">Todos</option>
                <option value="buy">Compra</option>
                <option value="sell">Venta</option>
              </Select>
            </div>
            <div className="flex flex-col gap-1">
              <label className="text-xs text-[rgb(var(--foreground-muted))]">
                Resultado
              </label>
              <Select
                value={filters.result}
                onChange={(e) =>
                  setFilters({
                    ...filters,
                    result: e.target.value as FilterState["result"],
                  })
                }
                data-testid="orders-history-filter-result"
              >
                <option value="">Todos</option>
                <option value="win">Ganadora</option>
                <option value="loss">Perdedora</option>
              </Select>
            </div>
            <div className="flex flex-col gap-1">
              <label className="text-xs text-[rgb(var(--foreground-muted))]">
                Magic
              </label>
              <Input
                type="number"
                value={filters.magic}
                onChange={(e) =>
                  setFilters({ ...filters, magic: e.target.value })
                }
                data-testid="orders-history-filter-magic"
              />
            </div>
            <div className="flex flex-col gap-1">
              <label className="text-xs text-[rgb(var(--foreground-muted))]">
                Estado
              </label>
              <Select
                value={filters.status}
                onChange={(e) =>
                  setFilters({ ...filters, status: e.target.value })
                }
                data-testid="orders-history-filter-status"
              >
                <option value="">Todos</option>
                <option value="filled">Llenada</option>
                <option value="closed">Cerrada</option>
                <option value="cancelled">Cancelada</option>
                <option value="rejected">Rechazada</option>
                <option value="failed">Fallida</option>
                <option value="expired">Expirada</option>
              </Select>
            </div>
            <div className="flex items-end gap-2">
              <Button
                size="sm"
                data-testid="orders-history-apply-filters"
                onClick={applyFilters}
              >
                Aplicar
              </Button>
              <Button
                size="sm"
                variant="ghost"
                data-testid="orders-history-reset-filters"
                onClick={resetFilters}
              >
                Limpiar
              </Button>
            </div>
          </div>
        ) : null}

        {data?.metrics ? <MetricsBar metrics={data.metrics} /> : null}

        {error ? (
          <p
            role="alert"
            className="text-sm text-[rgb(var(--danger))]"
            data-testid="orders-history-error"
          >
            {error}
          </p>
        ) : null}

        {loading && !data ? (
          <p className="text-sm text-[rgb(var(--foreground-muted))]">
            Cargando órdenes…
          </p>
        ) : data && data.items.length === 0 ? (
          <p
            data-testid="orders-history-empty"
            className="text-sm text-[rgb(var(--foreground-muted))]"
          >
            Sin órdenes en la ventana seleccionada.
          </p>
        ) : data ? (
          <Table data-testid="orders-history-table">
            <TableHeader>
              <TableRow>
                <TableHead>Ticket</TableHead>
                <TableHead>Símbolo</TableHead>
                <TableHead>Tipo</TableHead>
                <TableHead>Volumen</TableHead>
                <TableHead>Apertura</TableHead>
                <TableHead>Cierre</TableHead>
                <TableHead>P&L bruto</TableHead>
                <TableHead>P&L neto</TableHead>
                <TableHead>Comisión</TableHead>
                <TableHead>Swap</TableHead>
                <TableHead>Comentario</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {data.items.map((o: OperativaOrderRecord) => (
                <TableRow
                  key={o.id}
                  data-testid={`orders-history-row-${o.id}`}
                >
                  <TableCell className="font-mono">
                    {o.mt5_ticket ?? "—"}
                  </TableCell>
                  <TableCell>{o.symbol}</TableCell>
                  <TableCell className="capitalize">{o.side}</TableCell>
                  <TableCell className="font-mono">
                    {o.volume.toFixed(2)}
                  </TableCell>
                  <TableCell className="font-mono text-xs">
                    {formatTimestamp(o.open_time ?? o.created_at)}
                  </TableCell>
                  <TableCell className="font-mono text-xs">
                    {formatTimestamp(o.close_time)}
                  </TableCell>
                  <TableCell
                    className={`font-mono ${pnlColor(o.profit_gross)}`}
                  >
                    {formatPnl(o.profit_gross)}
                  </TableCell>
                  <TableCell
                    className={`font-mono ${pnlColor(o.profit_net)}`}
                  >
                    {formatPnl(o.profit_net)}
                  </TableCell>
                  <TableCell className="font-mono">
                    {formatPnl(o.commission)}
                  </TableCell>
                  <TableCell className="font-mono">
                    {formatPnl(o.swap)}
                  </TableCell>
                  <TableCell className="text-xs text-[rgb(var(--foreground-muted))]">
                    {o.comment ?? "—"}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        ) : null}

        {data && data.total > PAGE_SIZE ? (
          <div className="flex items-center justify-between text-sm">
            <span className="text-[rgb(var(--foreground-muted))]">
              Página {currentPage} de {totalPages}
            </span>
            <div className="flex gap-2">
              <Button
                size="sm"
                variant="outline"
                disabled={offset === 0}
                data-testid="orders-history-prev-page"
                onClick={() =>
                  setOffset((o) => Math.max(0, o - PAGE_SIZE))
                }
              >
                Anterior
              </Button>
              <Button
                size="sm"
                variant="outline"
                disabled={currentPage >= totalPages}
                data-testid="orders-history-next-page"
                onClick={() => setOffset((o) => o + PAGE_SIZE)}
              >
                Siguiente
              </Button>
            </div>
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}
