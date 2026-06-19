"use client";

/**
 * Gestión de EAs — "Mis Expert Advisors", the single management home surface.
 *
 * One concept: "Expert Advisor" (EA), backed by `/api/eas` (cookie-JWT + CSRF
 * via `@/lib/api`). Lists ALL of the user's EAs, creates a new EA and jumps
 * straight into its visual canvas, and renames / soft-archives existing ones
 * inline. Each card opens the editor directly. Wrapped by the route-scoped
 * `EasQueryProvider` (see `../layout.tsx`).
 */
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { Plus, RefreshCw, Pencil, Trash2 } from "lucide-react";
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
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import {
  createEa,
  deleteEa,
  listEas,
  updateEa,
  eaErrorMessage,
  type EaSummary,
} from "./_lib/eas";

const DEFAULT_EA_NAME = "Nuevo Expert Advisor";

function formatTs(iso: string | null): string {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

export default function EasPage() {
  const router = useRouter();
  const queryClient = useQueryClient();

  const [renaming, setRenaming] = useState<EaSummary | null>(null);
  const [renameValue, setRenameValue] = useState("");
  const [confirmingDelete, setConfirmingDelete] = useState<EaSummary | null>(
    null,
  );

  const {
    data: advisors,
    isLoading,
    isError,
    error,
  } = useQuery({
    queryKey: ["eas"],
    queryFn: () => listEas(),
  });

  const invalidate = () =>
    queryClient.invalidateQueries({ queryKey: ["eas"] });

  // Create and open it straight in the editor.
  const createMutation = useMutation({
    mutationFn: () => createEa({ name: DEFAULT_EA_NAME }),
    onSuccess: (created) => {
      invalidate();
      router.push(`/eas/editor/${created.id}`);
    },
  });

  const renameMutation = useMutation({
    // PATCH requires the optimistic-lock precondition `updated_at`.
    mutationFn: (ea: EaSummary) =>
      updateEa(ea.id, {
        name: renameValue.trim(),
        updated_at: ea.updated_at ?? new Date().toISOString(),
      }),
    onSuccess: () => {
      invalidate();
      setRenaming(null);
      setRenameValue("");
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) => deleteEa(id),
    onSuccess: () => {
      invalidate();
      setConfirmingDelete(null);
    },
  });

  const count = advisors?.length ?? 0;

  return (
    <section className="flex flex-col gap-4">
      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Gestión EAs</h1>
          <p className="text-sm text-[rgb(var(--foreground-muted))]">
            Tus Expert Advisors (bots de trading). Abre uno para editarlo en el
            canvas visual y generar su código MQL5 / Python.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={() => invalidate()}
            disabled={isLoading}
            aria-label="Refrescar listado"
          >
            <RefreshCw className="h-4 w-4" /> Refrescar
          </Button>
          <Button
            size="sm"
            onClick={() => createMutation.mutate()}
            disabled={createMutation.isPending}
          >
            <Plus className="h-4 w-4" />
            {createMutation.isPending ? "Creando…" : "Nuevo EA"}
          </Button>
        </div>
      </header>

      <Card>
        <CardHeader>
          <CardTitle>Expert Advisors</CardTitle>
          <CardDescription>
            {count} EA{count === 1 ? "" : "s"} en esta vista
          </CardDescription>
        </CardHeader>
        <CardContent>
          {createMutation.isError && (
            <p role="alert" className="mb-3 text-sm text-[rgb(var(--danger))]">
              {eaErrorMessage(createMutation.error)}
            </p>
          )}
          {isError && (
            <p role="alert" className="mb-3 text-sm text-[rgb(var(--danger))]">
              {eaErrorMessage(error)}
            </p>
          )}
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Nombre</TableHead>
                <TableHead>Versión</TableHead>
                <TableHead>Actualizado</TableHead>
                <TableHead className="text-right">Acciones</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {isLoading && (
                <TableRow>
                  <TableCell
                    colSpan={4}
                    className="text-center text-[rgb(var(--foreground-muted))]"
                  >
                    Cargando…
                  </TableCell>
                </TableRow>
              )}
              {!isLoading && !isError && count === 0 && (
                <TableRow>
                  <TableCell
                    colSpan={4}
                    className="text-center text-[rgb(var(--foreground-muted))]"
                  >
                    Aún no tienes ningún Expert Advisor.
                  </TableCell>
                </TableRow>
              )}
              {advisors?.map((ea: EaSummary) => (
                <TableRow key={ea.id}>
                  <TableCell className="font-medium">
                    <Link
                      className="hover:underline"
                      href={`/eas/editor/${ea.id}`}
                    >
                      {ea.name}
                    </Link>
                  </TableCell>
                  <TableCell>v{ea.version}</TableCell>
                  <TableCell className="text-xs text-[rgb(var(--foreground-muted))]">
                    {formatTs(ea.updated_at)}
                  </TableCell>
                  <TableCell className="text-right">
                    <div className="inline-flex items-center gap-1">
                      <Button
                        variant="ghost"
                        size="icon"
                        aria-label={`Renombrar ${ea.name}`}
                        title="Renombrar"
                        onClick={() => {
                          renameMutation.reset();
                          setRenameValue(ea.name);
                          setRenaming(ea);
                        }}
                      >
                        <Pencil className="h-4 w-4" />
                      </Button>
                      <Button
                        variant="ghost"
                        size="icon"
                        aria-label={`Archivar ${ea.name}`}
                        title="Archivar"
                        onClick={() => {
                          deleteMutation.reset();
                          setConfirmingDelete(ea);
                        }}
                      >
                        <Trash2 className="h-4 w-4" />
                      </Button>
                    </div>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      {/* Rename dialog */}
      <Dialog
        open={renaming !== null}
        onOpenChange={(open) => {
          if (!open) setRenaming(null);
        }}
      >
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>Renombrar Expert Advisor</DialogTitle>
          </DialogHeader>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              if (renaming && renameValue.trim())
                renameMutation.mutate(renaming);
            }}
            className="flex flex-col gap-4"
          >
            <Input
              autoFocus
              value={renameValue}
              onChange={(e) => setRenameValue(e.target.value)}
              placeholder="Nombre del Expert Advisor"
              aria-label="Nombre del Expert Advisor"
            />
            {renameMutation.isError && (
              <p role="alert" className="text-destructive text-sm">
                {eaErrorMessage(renameMutation.error)}
              </p>
            )}
            <div className="flex justify-end gap-2">
              <Button
                type="button"
                variant="ghost"
                onClick={() => setRenaming(null)}
              >
                Cancelar
              </Button>
              <Button
                type="submit"
                disabled={!renameValue.trim() || renameMutation.isPending}
              >
                {renameMutation.isPending ? "Guardando…" : "Guardar"}
              </Button>
            </div>
          </form>
        </DialogContent>
      </Dialog>

      {/* Archive confirmation */}
      <Dialog
        open={confirmingDelete !== null}
        onOpenChange={(open) => {
          if (!open) setConfirmingDelete(null);
        }}
      >
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>Archivar Expert Advisor</DialogTitle>
          </DialogHeader>
          <p className="text-muted-foreground text-sm">
            ¿Archivar{" "}
            <span className="text-foreground font-medium">
              {confirmingDelete?.name}
            </span>
            ? Dejará de aparecer en la lista.
          </p>
          {deleteMutation.isError && (
            <p role="alert" className="text-destructive text-sm">
              {eaErrorMessage(deleteMutation.error)}
            </p>
          )}
          <div className="flex justify-end gap-2">
            <Button
              type="button"
              variant="ghost"
              onClick={() => setConfirmingDelete(null)}
            >
              Cancelar
            </Button>
            <Button
              variant="outline"
              disabled={deleteMutation.isPending}
              onClick={() => {
                if (confirmingDelete) deleteMutation.mutate(confirmingDelete.id);
              }}
            >
              {deleteMutation.isPending ? "Archivando…" : "Archivar"}
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </section>
  );
}
