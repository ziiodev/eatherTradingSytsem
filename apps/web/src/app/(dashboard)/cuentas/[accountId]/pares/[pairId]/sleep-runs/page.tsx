"use client";

/**
 * Sleep Runs index — paginated list of runs for the project. Each row
 * links to the per-run report viewer.
 *
 * Mirrors the SuenoPanel data fetch but in a dedicated full-page surface
 * so operators can deep-link to reports without going through the tabs.
 */

import Link from "next/link";
import { use, useEffect, useState } from "react";

import { ApiError } from "@/lib/api";
import {
  listSleepRuns,
  SLEEP_PHASE_LABEL,
  SLEEP_RUN_STATUS_LABEL,
  type SleepRunSummary,
} from "@/lib/sleep";
import { Badge } from "@/components/ui/badge";
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

export default function SleepRunsListPage({
  params,
}: {
  params: Promise<{ accountId: string; pairId: string }>;
}): React.JSX.Element {
  const { accountId, pairId } = use(params);
  const [runs, setRuns] = useState<SleepRunSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const load = async (): Promise<void> => {
      try {
        const data = await listSleepRuns(pairId, { limit: 100 });
        if (cancelled) return;
        setRuns(data.items);
        setError(null);
      } catch (err) {
        if (cancelled) return;
        setError(
          err instanceof ApiError ? `Error (${err.status})` : "Error de red",
        );
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    void load();
    return () => {
      cancelled = true;
    };
  }, [pairId]);

  return (
    <section className="flex flex-col gap-4">
      <h2 className="text-2xl font-semibold tracking-tight">Sleep Runs</h2>

      <Card>
        <CardHeader>
          <CardTitle>Historial</CardTitle>
          <CardDescription>
            Selecciona un run para ver su informe completo.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {loading ? (
            <p className="text-sm text-[rgb(var(--foreground-muted))]">
              Cargando…
            </p>
          ) : error ? (
            <p role="alert" className="text-sm text-[rgb(var(--danger))]">
              {error}
            </p>
          ) : runs.length === 0 ? (
            <p className="text-sm text-[rgb(var(--foreground-muted))]">
              Aún no hay sleep runs para este proyecto.
            </p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Inicio</TableHead>
                  <TableHead>Tipo</TableHead>
                  <TableHead>Estado</TableHead>
                  <TableHead>Resumen</TableHead>
                  <TableHead></TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {runs.map((r) => (
                  <TableRow key={r.id}>
                    <TableCell className="text-xs text-[rgb(var(--foreground-muted))]">
                      {r.started_at ?? "—"}
                    </TableCell>
                    <TableCell>
                      <Badge variant="accent">
                        {SLEEP_PHASE_LABEL[r.phase_type]}
                      </Badge>
                    </TableCell>
                    <TableCell>
                      <Badge variant={statusVariant(r.status)}>
                        {SLEEP_RUN_STATUS_LABEL[r.status]}
                      </Badge>
                    </TableCell>
                    <TableCell className="text-xs">
                      {r.summary ?? r.error ?? "—"}
                    </TableCell>
                    <TableCell>
                      <Link
                        href={`/cuentas/${accountId}/pares/${pairId}/sleep-runs/${r.id}`}
                        className="text-xs text-[rgb(var(--accent))] hover:underline"
                      >
                        Ver informe
                      </Link>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
    </section>
  );
}

function statusVariant(
  status: SleepRunSummary["status"],
): "success" | "warning" | "danger" | "muted" | "accent" {
  switch (status) {
    case "succeeded":
      return "success";
    case "running":
      return "accent";
    case "skipped":
    case "partial":
      return "warning";
    case "failed":
    case "crashed":
      return "danger";
    default:
      return "muted";
  }
}
