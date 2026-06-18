"use client";

/**
 * Q-Tables page — read-only viewer for the project's learned Q-Table
 * versions.
 *
 * Layout:
 *   ┌─ Version list (left, ~40%) ─┬─ Selected version detail (right) ─┐
 *   │  v3  2026-05-28              │  table_data (collapsed JSON)      │
 *   │  v2  2026-05-27              │  diff vs v(N-1):                  │
 *   │  v1  2026-05-26              │   + 4 added states                │
 *   │                              │   ~ 2 argmax flips                │
 *   └──────────────────────────────┴───────────────────────────────────┘
 *
 * Writes never happen here — Q-Tables are only mutated by the deep-sleep
 * orchestrator transaction (sleep-learning-loop Phase 7).
 */

import { use, useEffect, useMemo, useState } from "react";

import { ApiError } from "@/lib/api";
import {
  diffQTables,
  fetchQTable,
  fetchQTables,
  type QTableListItem,
  type QTableResponse,
} from "@/lib/sleep";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
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

export default function QTablesPage({
  params,
}: {
  params: Promise<{ accountId: string; pairId: string }>;
}): React.JSX.Element {
  const { pairId } = use(params);

  const [versions, setVersions] = useState<QTableListItem[]>([]);
  const [selectedVersion, setSelectedVersion] = useState<number | null>(null);
  const [current, setCurrent] = useState<QTableResponse | null>(null);
  const [previous, setPrevious] = useState<QTableResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notFound, setNotFound] = useState(false);

  // Initial list ---------------------------------------------------------
  useEffect(() => {
    let cancelled = false;
    const load = async (): Promise<void> => {
      try {
        const data = await fetchQTables(pairId, { limit: 100 });
        if (cancelled) return;
        setVersions(data.items);
        const first = data.items[0];
        if (first !== undefined) {
          setSelectedVersion(first.version);
        }
        setError(null);
      } catch (err) {
        if (cancelled) return;
        if (err instanceof ApiError && err.status === 404) {
          setNotFound(true);
        } else {
          setError(
            err instanceof ApiError
              ? `Error al cargar (${err.status})`
              : "Error de red",
          );
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    void load();
    return () => {
      cancelled = true;
    };
  }, [pairId]);

  // Selection — fetch full table_data for current + previous version ----
  useEffect(() => {
    if (selectedVersion === null) return;
    let cancelled = false;
    const load = async (): Promise<void> => {
      try {
        const cur = await fetchQTable(pairId, selectedVersion);
        if (cancelled) return;
        setCurrent(cur);
        const prevVersion = selectedVersion - 1;
        if (prevVersion >= 1) {
          try {
            const prev = await fetchQTable(pairId, prevVersion);
            if (!cancelled) setPrevious(prev);
          } catch {
            if (!cancelled) setPrevious(null);
          }
        } else {
          setPrevious(null);
        }
      } catch (err) {
        if (cancelled) return;
        setError(
          err instanceof ApiError
            ? `Error al cargar versión ${selectedVersion} (${err.status})`
            : "Error de red",
        );
      }
    };
    void load();
    return () => {
      cancelled = true;
    };
  }, [pairId, selectedVersion]);

  const diff = useMemo(() => {
    if (!current) return null;
    return diffQTables(previous?.table_data ?? null, current.table_data);
  }, [current, previous]);

  if (notFound) {
    return (
      <section className="flex flex-col gap-4">
        <p className="text-sm">Proyecto no encontrado.</p>
      </section>
    );
  }

  return (
    <section className="flex flex-col gap-4">
      <header className="flex items-center justify-between gap-3">
        <h2 className="text-2xl font-semibold tracking-tight">Q-Tables</h2>
      </header>

      {loading ? (
        <p className="text-sm text-[rgb(var(--foreground-muted))]">Cargando…</p>
      ) : error ? (
        <p role="alert" className="text-sm text-[rgb(var(--danger))]">
          {error}
        </p>
      ) : versions.length === 0 ? (
        <EmptyState />
      ) : (
        <div className="grid gap-4 lg:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]">
          <VersionList
            versions={versions}
            selected={selectedVersion}
            onSelect={setSelectedVersion}
          />
          <DetailPanel
            current={current}
            previous={previous}
            diff={diff}
          />
        </div>
      )}
    </section>
  );
}

function VersionList({
  versions,
  selected,
  onSelect,
}: {
  versions: QTableListItem[];
  selected: number | null;
  onSelect: (version: number) => void;
}): React.JSX.Element {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Versiones</CardTitle>
        <CardDescription>
          Una fila por versión promovida en sueño profundo.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Versión</TableHead>
              <TableHead>Episodios</TableHead>
              <TableHead>Fecha</TableHead>
              <TableHead></TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {versions.map((v) => {
              const isSelected = v.version === selected;
              return (
                <TableRow
                  key={v.id}
                  data-testid={`q-table-row-${v.version}`}
                  aria-selected={isSelected}
                  className={
                    isSelected
                      ? "bg-[rgb(var(--accent)/0.1)]"
                      : undefined
                  }
                >
                  <TableCell>
                    <Badge variant={isSelected ? "accent" : "default"}>
                      v{v.version}
                    </Badge>
                  </TableCell>
                  <TableCell className="font-mono text-xs">
                    {v.episode_count}
                  </TableCell>
                  <TableCell className="text-xs text-[rgb(var(--foreground-muted))]">
                    {formatDate(v.created_at)}
                  </TableCell>
                  <TableCell>
                    <Button
                      size="sm"
                      variant={isSelected ? "default" : "outline"}
                      onClick={() => onSelect(v.version)}
                      data-testid={`q-table-select-${v.version}`}
                    >
                      Ver
                    </Button>
                  </TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      </CardContent>
    </Card>
  );
}

function DetailPanel({
  current,
  previous,
  diff,
}: {
  current: QTableResponse | null;
  previous: QTableResponse | null;
  diff: ReturnType<typeof diffQTables> | null;
}): React.JSX.Element {
  if (!current) {
    return (
      <Card>
        <CardContent className="p-6 text-sm text-[rgb(var(--foreground-muted))]">
          Selecciona una versión para ver su contenido.
        </CardContent>
      </Card>
    );
  }
  const stateCount = Object.keys(current.table_data).length;
  return (
    <div className="flex flex-col gap-4">
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Badge variant="accent">v{current.version}</Badge>
            <span>Detalle</span>
          </CardTitle>
          <CardDescription>
            α normal = {String(current.alpha_normal)} · α special ={" "}
            {String(current.alpha_special)} · γ = {String(current.gamma)} ·{" "}
            {current.episode_count} episodios ingeridos.
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-2">
          <p className="text-sm text-[rgb(var(--foreground-muted))]">
            {stateCount} estados aprendidos.
          </p>
          <details className="group rounded-md border border-[rgb(var(--border))] bg-[rgb(var(--background))] p-2">
            <summary className="cursor-pointer select-none text-sm text-[rgb(var(--foreground))]">
              Ver <code>table_data</code> (JSON)
            </summary>
            <pre
              data-testid="q-table-json"
              className="mt-2 max-h-96 overflow-auto rounded bg-[rgb(var(--background-elevated))] p-3 text-xs"
            >
              {JSON.stringify(current.table_data, null, 2)}
            </pre>
          </details>
        </CardContent>
      </Card>

      <Card data-testid="q-table-diff-card">
        <CardHeader>
          <CardTitle>
            Diff vs {previous ? `v${previous.version}` : "(no anterior)"}
          </CardTitle>
          <CardDescription>
            Resumen de cambios respecto a la versión anterior.
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          {diff === null ? (
            <p className="text-sm">—</p>
          ) : (
            <>
              <DiffSummary diff={diff} />
              {diff.changedArgmaxStates.length > 0 ? (
                <ArgmaxChangesTable diff={diff} />
              ) : null}
            </>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

function DiffSummary({
  diff,
}: {
  diff: ReturnType<typeof diffQTables>;
}): React.JSX.Element {
  return (
    <div className="flex flex-wrap gap-2 text-xs">
      <Badge variant="success" data-testid="diff-added-count">
        +{diff.addedStates.length} estados nuevos
      </Badge>
      <Badge variant="warning" data-testid="diff-argmax-count">
        ~{diff.changedArgmaxStates.length} argmax cambiados
      </Badge>
      <Badge variant="muted">{diff.totalStates} estados totales</Badge>
    </div>
  );
}

function ArgmaxChangesTable({
  diff,
}: {
  diff: ReturnType<typeof diffQTables>;
}): React.JSX.Element {
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Estado</TableHead>
          <TableHead>Acción anterior</TableHead>
          <TableHead>Acción nueva</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {diff.changedArgmaxStates.slice(0, 50).map((row) => (
          <TableRow key={row.stateKey}>
            <TableCell className="font-mono text-xs">{row.stateKey}</TableCell>
            <TableCell className="text-xs">{row.prevAction ?? "—"}</TableCell>
            <TableCell className="text-xs">{row.nextAction ?? "—"}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

function EmptyState(): React.JSX.Element {
  return (
    <Card>
      <CardContent className="p-6 text-sm text-[rgb(var(--foreground-muted))]">
        Este proyecto no tiene versiones de Q-Table todavía. Aparecerán al
        completar el primer sueño profundo con aprendizaje habilitado.
      </CardContent>
    </Card>
  );
}

function formatDate(iso: string | null): string {
  if (!iso) return "—";
  try {
    // Server returns naive UTC; format as YYYY-MM-DD HH:mm UTC.
    const d = new Date(`${iso}Z`);
    return d.toISOString().slice(0, 16).replace("T", " ") + " UTC";
  } catch {
    return iso;
  }
}
