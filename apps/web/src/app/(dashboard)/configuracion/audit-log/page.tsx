"use client";

import { useCallback, useEffect, useState } from "react";

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
import { listAuditLog, type AuditLogItem } from "@/lib/audit-log";

/**
 * /configuracion/audit-log — user-scoped, server-paginated audit history.
 *
 * Data is read-only and pagination is offset-based (matches the API's
 * AuditLogPage contract). The page is intentionally minimal — the audit
 * trail is meant to be *trustworthy*, not pretty.
 */

const PAGE_SIZE = 20;

function formatTimestamp(value: string | null): string {
  if (!value) return "—";
  try {
    return new Date(value).toLocaleString("es-ES", {
      year: "numeric",
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    });
  } catch {
    return value;
  }
}

function summariseChange(item: AuditLogItem): string {
  // Compact human summary: action verb + target type. The full JSON
  // payload is hidden behind a details/summary so the row stays
  // scannable.
  const target = item.target_id
    ? `${item.target_type}:${item.target_id.slice(0, 8)}`
    : item.target_type;
  return `${item.action} → ${target}`;
}

export default function AuditLogPage(): React.JSX.Element {
  const [items, setItems] = useState<AuditLogItem[]>([]);
  const [total, setTotal] = useState<number>(0);
  const [offset, setOffset] = useState<number>(0);
  const [loading, setLoading] = useState<boolean>(false);
  const [hasError, setHasError] = useState<boolean>(false);

  const load = useCallback(async (nextOffset: number) => {
    setLoading(true);
    setHasError(false);
    try {
      const page = await listAuditLog({
        limit: PAGE_SIZE,
        offset: nextOffset,
      });
      setItems(page.items);
      setTotal(page.total);
      setOffset(page.offset);
    } catch {
      setHasError(true);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load(0);
  }, [load]);

  const canPrev = offset > 0;
  const canNext = offset + items.length < total;

  return (
    <section className="flex flex-col gap-6">
      <header className="flex flex-col gap-2">
        <h1 className="text-2xl font-semibold tracking-tight">
          Registro de auditoría
        </h1>
        <p className="text-sm text-[rgb(var(--foreground-muted))]">
          Historial inmutable de las acciones realizadas en tu cuenta. Los
          registros son sólo de lectura.
        </p>
      </header>

      <Card>
        <CardHeader>
          <CardTitle>Eventos recientes</CardTitle>
          <CardDescription>
            Mostrando {items.length} de {total} eventos.
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          {hasError ? (
            <div
              role="alert"
              className="rounded-md border border-[rgb(var(--danger))] bg-[rgb(var(--danger)/0.1)] px-3 py-2 text-sm text-[rgb(var(--danger))]"
            >
              No se pudo cargar el registro de auditoría.
            </div>
          ) : null}

          {loading && items.length === 0 ? (
            <p className="text-sm text-[rgb(var(--foreground-muted))]">
              Cargando eventos...
            </p>
          ) : null}

          {!loading && items.length === 0 && !hasError ? (
            <p className="text-sm text-[rgb(var(--foreground-muted))]">
              No hay eventos auditados todavía.
            </p>
          ) : null}

          {items.length > 0 ? (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Fecha</TableHead>
                  <TableHead>Acción</TableHead>
                  <TableHead>IP</TableHead>
                  <TableHead className="hidden md:table-cell">
                    Detalle
                  </TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {items.map((item) => (
                  <TableRow key={item.id}>
                    <TableCell className="whitespace-nowrap font-mono text-xs">
                      {formatTimestamp(item.created_at)}
                    </TableCell>
                    <TableCell className="font-mono text-xs">
                      {summariseChange(item)}
                    </TableCell>
                    <TableCell className="font-mono text-xs">
                      {item.ip_address ?? "—"}
                    </TableCell>
                    <TableCell className="hidden md:table-cell">
                      <details>
                        <summary className="cursor-pointer text-xs text-[rgb(var(--foreground-muted))]">
                          Ver
                        </summary>
                        <pre className="mt-2 max-h-64 overflow-auto rounded-md border border-[rgb(var(--border))] bg-[rgb(var(--background-elevated))] p-2 text-xs">
                          {JSON.stringify(
                            {
                              before: item.before_state,
                              after: item.after_state,
                              user_agent: item.user_agent,
                            },
                            null,
                            2,
                          )}
                        </pre>
                      </details>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          ) : null}

          <div className="flex items-center justify-between gap-3">
            <Button
              variant="outline"
              size="sm"
              disabled={!canPrev || loading}
              onClick={() => {
                void load(Math.max(0, offset - PAGE_SIZE));
              }}
            >
              Anterior
            </Button>
            <span className="text-xs text-[rgb(var(--foreground-muted))]">
              Página {Math.floor(offset / PAGE_SIZE) + 1} de{" "}
              {Math.max(1, Math.ceil(total / PAGE_SIZE))}
            </span>
            <Button
              variant="outline"
              size="sm"
              disabled={!canNext || loading}
              onClick={() => {
                void load(offset + PAGE_SIZE);
              }}
            >
              Siguiente
            </Button>
          </div>
        </CardContent>
      </Card>
    </section>
  );
}
