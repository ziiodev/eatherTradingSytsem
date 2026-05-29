"use client";

/**
 * OpenPositionsTable — middle section of the Operativa page.
 *
 * Lists currently-open positions reported by MCP (live feed via
 * `position_snapshot` events). Empty state: "Sin posiciones abiertas".
 *
 * Columns: Ticket / Símbolo / Tipo / Volumen / Apertura / Precio actual /
 *          SL / TP / P&L flotante / Tiempo.
 *
 * The table supports column-sort by clicking the header. Sort state is
 * intentionally component-local — closing the page resets it because
 * the realtime nature of the data makes any global persistence brittle.
 */

import { useMemo, useState } from "react";

import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import type { Position } from "@/lib/operativa";

type SortKey =
  | "ticket"
  | "symbol"
  | "side"
  | "volume"
  | "price_open"
  | "profit"
  | "time";
type SortDirection = "asc" | "desc";

interface SortState {
  key: SortKey;
  direction: SortDirection;
}

export interface OpenPositionsTableProps {
  positions: Position[];
  /**
   * Optional latest price map keyed by ticket. Useful when the parent
   * also subscribes to a per-symbol tick stream. Falls back to
   * `price_open` when missing.
   */
  currentPrices?: Record<number, number>;
}

function pnlColor(value: number): string {
  if (value > 0) return "text-[rgb(var(--success))]";
  if (value < 0) return "text-[rgb(var(--danger))]";
  return "text-[rgb(var(--foreground-muted))]";
}

function compareBy(a: Position, b: Position, key: SortKey): number {
  switch (key) {
    case "ticket":
      return a.ticket - b.ticket;
    case "symbol":
      return a.symbol.localeCompare(b.symbol);
    case "side":
      return a.side.localeCompare(b.side);
    case "volume":
      return a.volume - b.volume;
    case "price_open":
      return a.price_open - b.price_open;
    case "profit":
      return a.profit - b.profit;
    case "time":
      return a.time.localeCompare(b.time);
  }
}

function HeaderCell({
  label,
  sortKey,
  sort,
  setSort,
}: {
  label: string;
  sortKey: SortKey;
  sort: SortState;
  setSort: (s: SortState) => void;
}): React.JSX.Element {
  const isActive = sort.key === sortKey;
  const arrow = !isActive ? "" : sort.direction === "asc" ? " ▲" : " ▼";
  return (
    <TableHead>
      <button
        type="button"
        data-testid={`positions-sort-${sortKey}`}
        className="flex items-center gap-1 text-left text-xs font-medium uppercase tracking-wide text-[rgb(var(--foreground-muted))] hover:text-[rgb(var(--foreground))]"
        onClick={() => {
          if (isActive) {
            setSort({
              key: sortKey,
              direction: sort.direction === "asc" ? "desc" : "asc",
            });
          } else {
            setSort({ key: sortKey, direction: "asc" });
          }
        }}
      >
        {label}
        <span>{arrow}</span>
      </button>
    </TableHead>
  );
}

export function OpenPositionsTable({
  positions,
  currentPrices,
}: OpenPositionsTableProps): React.JSX.Element {
  const [sort, setSort] = useState<SortState>({
    key: "ticket",
    direction: "asc",
  });

  const sorted = useMemo(() => {
    const copy = positions.slice();
    copy.sort((a, b) => {
      const cmp = compareBy(a, b, sort.key);
      return sort.direction === "asc" ? cmp : -cmp;
    });
    return copy;
  }, [positions, sort]);

  return (
    <Card data-testid="open-positions-card">
      <CardHeader>
        <CardTitle>Posiciones abiertas</CardTitle>
        <CardDescription>
          {positions.length === 0
            ? "Sin posiciones abiertas"
            : `${positions.length} posición${positions.length === 1 ? "" : "es"} en curso`}
        </CardDescription>
      </CardHeader>
      <CardContent>
        {positions.length === 0 ? (
          <p
            data-testid="open-positions-empty"
            className="text-sm text-[rgb(var(--foreground-muted))]"
          >
            Sin posiciones abiertas.
          </p>
        ) : (
          <Table data-testid="open-positions-table">
            <TableHeader>
              <TableRow>
                <HeaderCell
                  label="Ticket"
                  sortKey="ticket"
                  sort={sort}
                  setSort={setSort}
                />
                <HeaderCell
                  label="Símbolo"
                  sortKey="symbol"
                  sort={sort}
                  setSort={setSort}
                />
                <HeaderCell
                  label="Tipo"
                  sortKey="side"
                  sort={sort}
                  setSort={setSort}
                />
                <HeaderCell
                  label="Volumen"
                  sortKey="volume"
                  sort={sort}
                  setSort={setSort}
                />
                <HeaderCell
                  label="Apertura"
                  sortKey="price_open"
                  sort={sort}
                  setSort={setSort}
                />
                <TableHead>Precio actual</TableHead>
                <TableHead>SL</TableHead>
                <TableHead>TP</TableHead>
                <HeaderCell
                  label="P&L flotante"
                  sortKey="profit"
                  sort={sort}
                  setSort={setSort}
                />
                <HeaderCell
                  label="Tiempo"
                  sortKey="time"
                  sort={sort}
                  setSort={setSort}
                />
              </TableRow>
            </TableHeader>
            <TableBody>
              {sorted.map((p) => {
                const livePrice =
                  currentPrices?.[p.ticket] ?? p.price_open;
                return (
                  <TableRow
                    key={p.ticket}
                    data-testid={`open-positions-row-${p.ticket}`}
                  >
                    <TableCell className="font-mono">{p.ticket}</TableCell>
                    <TableCell>{p.symbol}</TableCell>
                    <TableCell className="capitalize">{p.side}</TableCell>
                    <TableCell className="font-mono">
                      {p.volume.toFixed(2)}
                    </TableCell>
                    <TableCell className="font-mono">
                      {p.price_open.toFixed(5)}
                    </TableCell>
                    <TableCell className="font-mono">
                      {livePrice.toFixed(5)}
                    </TableCell>
                    <TableCell className="font-mono">
                      {p.sl != null ? p.sl.toFixed(5) : "—"}
                    </TableCell>
                    <TableCell className="font-mono">
                      {p.tp != null ? p.tp.toFixed(5) : "—"}
                    </TableCell>
                    <TableCell
                      className={`font-mono ${pnlColor(p.profit)}`}
                      data-testid={`open-positions-pnl-${p.ticket}`}
                    >
                      {p.profit.toFixed(2)}
                    </TableCell>
                    <TableCell className="font-mono text-xs text-[rgb(var(--foreground-muted))]">
                      {p.time.slice(0, 19).replace("T", " ")}
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        )}
      </CardContent>
    </Card>
  );
}
