"use client";

import { use, useEffect, useState } from "react";
import Link from "next/link";
import { ArrowLeft, MoreHorizontal, Plus, RefreshCw } from "lucide-react";
import { toast } from "sonner";

import { ApiError } from "@/lib/api";
import {
  deletePair,
  isDeletable,
  lifecycleAction,
  PAIR_STATUSES,
  PAIR_STATUS_LABEL,
  type LifecycleAction,
  type PairStatus,
  type PairSummary,
} from "@/lib/pairs";
import { listAccountPairs } from "@/lib/accounts";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Select } from "@/components/ui/select";
import { StatusBadge } from "@/components/StatusBadge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  DropdownMenu,
  DropdownMenuTrigger,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
} from "@/components/ui/dropdown-menu";

type Filter = PairStatus | "all";

const PAGE_SIZE = 25;

/**
 * `/cuentas/[accountId]/pares` — pairs (Pares) belonging to one account.
 */
export default function ParesPage({
  params,
}: {
  params: Promise<{ accountId: string }>;
}): React.JSX.Element {
  const { accountId } = use(params);
  const [items, setItems] = useState<PairSummary[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<Filter>("all");
  const [offset, setOffset] = useState(0);
  const [refreshNonce, setRefreshNonce] = useState(0);

  useEffect(() => {
    let cancelled = false;
    const load = async (): Promise<void> => {
      try {
        const data = await listAccountPairs(accountId, {
          status: filter === "all" ? undefined : filter,
          limit: PAGE_SIZE,
          offset,
        });
        if (cancelled) return;
        setItems(data.items);
        setTotal(data.total);
        setError(null);
      } catch (err) {
        if (cancelled) return;
        setError(
          err instanceof ApiError
            ? `Error al cargar (${err.status})`
            : "Error de red",
        );
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    void load();
    return () => {
      cancelled = true;
    };
  }, [accountId, filter, offset, refreshNonce]);

  function refresh(): void {
    setLoading(true);
    setRefreshNonce((n) => n + 1);
  }

  async function runLifecycle(
    id: string,
    action: LifecycleAction,
  ): Promise<void> {
    try {
      const updated = await lifecycleAction(id, action);
      setItems((prev) =>
        prev.map((p) => (p.id === id ? { ...p, status: updated.status } : p)),
      );
      toast.success(`Estado: ${PAIR_STATUS_LABEL[updated.status]}`);
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        toast.error("Transición no permitida");
      } else if (err instanceof ApiError && err.status === 404) {
        toast.error("Par no encontrado");
        refresh();
      } else {
        toast.error("No se pudo cambiar el estado");
      }
    }
  }

  async function runDelete(pair: PairSummary): Promise<void> {
    if (!confirm(`Eliminar el par "${pair.name}"?`)) return;
    try {
      await deletePair(pair.id);
      setItems((prev) => prev.filter((p) => p.id !== pair.id));
      setTotal((t) => Math.max(0, t - 1));
      toast.success("Par eliminado");
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        toast.error("Solo se pueden eliminar pares inactivos o detenidos");
      } else if (err instanceof ApiError && err.status === 404) {
        toast.error("Par no encontrado");
      } else {
        toast.error("No se pudo eliminar el par");
      }
    }
  }

  return (
    <section className="flex flex-col gap-4">
      <Link
        href="/cuentas"
        className="inline-flex items-center gap-1 text-sm text-[rgb(var(--foreground-muted))] hover:text-[rgb(var(--foreground))]"
      >
        <ArrowLeft className="h-3 w-3" /> Volver a las cuentas
      </Link>
      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Pares</h1>
          <p className="text-sm text-[rgb(var(--foreground-muted))]">
            Pares de trading de esta cuenta.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={() => refresh()}
            disabled={loading}
            aria-label="Refrescar listado"
          >
            <RefreshCw className="h-4 w-4" /> Refrescar
          </Button>
          <Link href={`/cuentas/${accountId}/pares/new`}>
            <Button size="sm">
              <Plus className="h-4 w-4" /> Nuevo par
            </Button>
          </Link>
        </div>
      </header>

      <Card>
        <CardHeader className="flex-row items-center justify-between gap-4">
          <div>
            <CardTitle>Listado</CardTitle>
            <CardDescription>
              {total} par{total === 1 ? "" : "es"} en total
            </CardDescription>
          </div>
          <div className="flex items-center gap-2">
            <label
              htmlFor="status-filter"
              className="text-xs text-[rgb(var(--foreground-muted))]"
            >
              Estado:
            </label>
            <Select
              id="status-filter"
              value={filter}
              onChange={(e) => {
                setOffset(0);
                setFilter(e.target.value as Filter);
              }}
              className="w-40"
            >
              <option value="all">Todos</option>
              {PAIR_STATUSES.map((s) => (
                <option key={s} value={s}>
                  {PAIR_STATUS_LABEL[s]}
                </option>
              ))}
            </Select>
          </div>
        </CardHeader>
        <CardContent>
          {error && (
            <p role="alert" className="mb-3 text-sm text-[rgb(var(--danger))]">
              {error}
            </p>
          )}
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Nombre</TableHead>
                <TableHead>Símbolo</TableHead>
                <TableHead>Marco</TableHead>
                <TableHead>Estado</TableHead>
                <TableHead className="text-right">Acciones</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {loading && items.length === 0 && (
                <TableRow>
                  <TableCell
                    colSpan={5}
                    className="text-center text-[rgb(var(--foreground-muted))]"
                  >
                    Cargando…
                  </TableCell>
                </TableRow>
              )}
              {!loading && items.length === 0 && (
                <TableRow>
                  <TableCell
                    colSpan={5}
                    className="text-center text-[rgb(var(--foreground-muted))]"
                  >
                    No hay pares.
                  </TableCell>
                </TableRow>
              )}
              {items.map((p) => (
                <TableRow key={p.id}>
                  <TableCell className="font-medium">
                    <Link
                      className="hover:underline"
                      href={`/cuentas/${accountId}/pares/${p.id}`}
                    >
                      {p.name}
                    </Link>
                  </TableCell>
                  <TableCell>{p.symbol}</TableCell>
                  <TableCell>{p.timeframe}</TableCell>
                  <TableCell>
                    <StatusBadge status={p.status} />
                  </TableCell>
                  <TableCell className="text-right">
                    <DropdownMenu>
                      <DropdownMenuTrigger
                        aria-label={`Acciones de ${p.name}`}
                      >
                        <MoreHorizontal className="h-4 w-4" />
                      </DropdownMenuTrigger>
                      <DropdownMenuContent>
                        <DropdownMenuItem
                          onSelect={() => void runLifecycle(p.id, "activate")}
                        >
                          Activar
                        </DropdownMenuItem>
                        <DropdownMenuItem
                          onSelect={() => void runLifecycle(p.id, "pause")}
                        >
                          Pausar
                        </DropdownMenuItem>
                        <DropdownMenuItem
                          onSelect={() => void runLifecycle(p.id, "stop")}
                        >
                          Detener
                        </DropdownMenuItem>
                        <DropdownMenuItem
                          onSelect={() =>
                            void runLifecycle(p.id, "maintenance")
                          }
                        >
                          Mantenimiento
                        </DropdownMenuItem>
                        <DropdownMenuSeparator />
                        <DropdownMenuItem
                          variant="danger"
                          disabled={!isDeletable(p.status)}
                          onSelect={() => void runDelete(p)}
                        >
                          Eliminar
                        </DropdownMenuItem>
                      </DropdownMenuContent>
                    </DropdownMenu>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>

          {total > PAGE_SIZE && (
            <div className="mt-3 flex items-center justify-between text-xs text-[rgb(var(--foreground-muted))]">
              <span>
                Mostrando {offset + 1}-{Math.min(offset + PAGE_SIZE, total)} de{" "}
                {total}
              </span>
              <div className="flex gap-2">
                <Button
                  variant="outline"
                  size="sm"
                  disabled={offset === 0 || loading}
                  onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
                >
                  Anterior
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  disabled={offset + PAGE_SIZE >= total || loading}
                  onClick={() => setOffset(offset + PAGE_SIZE)}
                >
                  Siguiente
                </Button>
              </div>
            </div>
          )}
        </CardContent>
      </Card>
    </section>
  );
}
