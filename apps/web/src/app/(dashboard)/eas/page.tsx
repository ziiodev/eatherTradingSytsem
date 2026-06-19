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
import { Pencil, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  createEa,
  deleteEa,
  listEas,
  updateEa,
  eaErrorMessage,
  type EaSummary,
} from "./_lib/eas";

const DEFAULT_EA_NAME = "Nuevo Expert Advisor";

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

  return (
    <main className="mx-auto max-w-4xl p-8">
      <div className="mb-1 flex items-center justify-between">
        <h1 className="text-2xl font-bold">Mis Expert Advisors</h1>
        <Button
          onClick={() => createMutation.mutate()}
          disabled={createMutation.isPending}
        >
          {createMutation.isPending ? "Creando…" : "+ Nuevo EA"}
        </Button>
      </div>
      <p className="text-muted-foreground mb-6 text-sm">
        Tus bots de trading. Haz clic en uno para abrir su editor visual.
      </p>

      {createMutation.isError && (
        <p role="alert" className="text-destructive mb-4 text-sm">
          {eaErrorMessage(createMutation.error)}
        </p>
      )}

      {isLoading ? (
        <p className="text-muted-foreground text-sm">
          Cargando tus Expert Advisors…
        </p>
      ) : isError ? (
        <p role="alert" className="text-destructive text-sm">
          {eaErrorMessage(error)}
        </p>
      ) : advisors && advisors.length > 0 ? (
        <ul className="grid gap-3 sm:grid-cols-2">
          {advisors.map((ea: EaSummary) => (
            <li key={ea.id}>
              <div
                role="button"
                tabIndex={0}
                onClick={() => router.push(`/eas/editor/${ea.id}`)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    router.push(`/eas/editor/${ea.id}`);
                  }
                }}
                className="border-border hover:border-primary hover:bg-accent focus-visible:ring-primary group flex cursor-pointer items-start justify-between gap-3 rounded-lg border p-4 transition-colors focus-visible:ring-2 focus-visible:outline-none"
              >
                <div className="min-w-0">
                  <p className="truncate font-medium">{ea.name}</p>
                  <p className="text-muted-foreground mt-0.5 text-xs">
                    v{ea.version}
                  </p>
                </div>
                <div className="flex shrink-0 items-center gap-1">
                  <Button
                    variant="ghost"
                    size="sm"
                    title="Renombrar"
                    onClick={(e) => {
                      e.stopPropagation();
                      renameMutation.reset();
                      setRenameValue(ea.name);
                      setRenaming(ea);
                    }}
                  >
                    <Pencil className="h-4 w-4" />
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    title="Archivar"
                    onClick={(e) => {
                      e.stopPropagation();
                      deleteMutation.reset();
                      setConfirmingDelete(ea);
                    }}
                  >
                    <Trash2 className="h-4 w-4" />
                  </Button>
                </div>
              </div>
            </li>
          ))}
        </ul>
      ) : (
        <div className="border-border rounded-lg border border-dashed p-10 text-center">
          <p className="text-muted-foreground mb-4 text-sm">
            Aún no tienes ningún Expert Advisor.
          </p>
          <Button
            onClick={() => createMutation.mutate()}
            disabled={createMutation.isPending}
          >
            {createMutation.isPending
              ? "Creando…"
              : "Crea tu primer Expert Advisor"}
          </Button>
        </div>
      )}

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
            <input
              autoFocus
              value={renameValue}
              onChange={(e) => setRenameValue(e.target.value)}
              placeholder="Nombre del Expert Advisor"
              aria-label="Nombre del Expert Advisor"
              className="border-border bg-background focus-visible:ring-ring rounded-md border px-3 py-2 text-sm focus-visible:ring-2 focus-visible:outline-none"
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
    </main>
  );
}
