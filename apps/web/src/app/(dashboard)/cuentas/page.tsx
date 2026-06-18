"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { MoreHorizontal, Plus, RefreshCw } from "lucide-react";
import { toast } from "sonner";

import { ApiError } from "@/lib/api";
import {
  deleteAccount,
  listAccounts,
  type TradingAccount,
} from "@/lib/accounts";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
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
} from "@/components/ui/dropdown-menu";

const PAGE_SIZE = 25;

/**
 * `/cuentas` — Accounts (Cuentas) list. Top of the hierarchy surface in the
 * sidebar. Each row links to its pairs list.
 */
export default function CuentasPage(): React.JSX.Element {
  const [items, setItems] = useState<TradingAccount[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [offset, setOffset] = useState(0);
  const [refreshNonce, setRefreshNonce] = useState(0);

  useEffect(() => {
    let cancelled = false;
    const load = async (): Promise<void> => {
      try {
        const data = await listAccounts({ limit: PAGE_SIZE, offset });
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
  }, [offset, refreshNonce]);

  function refresh(): void {
    setLoading(true);
    setRefreshNonce((n) => n + 1);
  }

  async function runDelete(account: TradingAccount): Promise<void> {
    if (!confirm(`Eliminar la cuenta "${account.name}"?`)) return;
    try {
      await deleteAccount(account.id);
      setItems((prev) => prev.filter((a) => a.id !== account.id));
      setTotal((t) => Math.max(0, t - 1));
      toast.success("Cuenta eliminada");
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        toast.error("La cuenta tiene pares; elimínalos primero.");
      } else {
        toast.error("No se pudo eliminar la cuenta");
      }
    }
  }

  return (
    <section className="flex flex-col gap-4">
      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Cuentas</h1>
          <p className="text-sm text-[rgb(var(--foreground-muted))]">
            Cada cuenta agrupa las credenciales del broker y sus pares de
            trading.
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
          <Link href="/cuentas/new">
            <Button size="sm">
              <Plus className="h-4 w-4" /> Nueva cuenta
            </Button>
          </Link>
        </div>
      </header>

      <Card>
        <CardHeader>
          <CardTitle>Listado</CardTitle>
          <CardDescription>
            {total} cuenta{total === 1 ? "" : "s"} en total
          </CardDescription>
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
                <TableHead>Broker</TableHead>
                <TableHead>Tipo</TableHead>
                <TableHead className="text-right">Acciones</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {loading && items.length === 0 && (
                <TableRow>
                  <TableCell
                    colSpan={4}
                    className="text-center text-[rgb(var(--foreground-muted))]"
                  >
                    Cargando…
                  </TableCell>
                </TableRow>
              )}
              {!loading && items.length === 0 && (
                <TableRow>
                  <TableCell
                    colSpan={4}
                    className="text-center text-[rgb(var(--foreground-muted))]"
                  >
                    No hay cuentas.
                  </TableCell>
                </TableRow>
              )}
              {items.map((a) => (
                <TableRow key={a.id}>
                  <TableCell className="font-medium">
                    <Link
                      className="hover:underline"
                      href={`/cuentas/${a.id}/pares`}
                    >
                      {a.name}
                    </Link>
                  </TableCell>
                  <TableCell>{a.broker_name ?? "—"}</TableCell>
                  <TableCell>
                    {a.account_type ? (
                      <Badge
                        variant={
                          a.account_type === "real" ? "warning" : "muted"
                        }
                      >
                        {a.account_type}
                      </Badge>
                    ) : (
                      "—"
                    )}
                  </TableCell>
                  <TableCell className="text-right">
                    <DropdownMenu>
                      <DropdownMenuTrigger
                        aria-label={`Acciones de ${a.name}`}
                      >
                        <MoreHorizontal className="h-4 w-4" />
                      </DropdownMenuTrigger>
                      <DropdownMenuContent>
                        <DropdownMenuItem
                          variant="danger"
                          onSelect={() => void runDelete(a)}
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
