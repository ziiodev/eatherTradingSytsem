"use client";

/**
 * Editor toolbar: Save (serialize graph -> PATCH backend) and Generate code.
 *
 * Save persists the current graph via PATCH /api/eas/:id using a TanStack Query
 * mutation, and surfaces saving / saved / error states. The backend requires the
 * `updated_at` optimistic-locking precondition, so we read it from the cached EA
 * (populated by StrategyHydrator) and send it with the patch; on success it
 * refreshes the cached EA so a remount re-hydrates the latest graph. Generate
 * POSTs the IN-MEMORY graph to the preview codegen endpoints (no forced save)
 * and shows the result in a lazy, client-only modal.
 *
 * The AI copilot is OUT OF SCOPE for this change (deferred to v2) — there is no
 * Copilot button here.
 */
import { useState } from "react";
import Link from "next/link";
import { ArrowLeft } from "lucide-react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Button, buttonVariants } from "@/components/ui/button";
import { useGraphStore } from "../_stores/graphStore";
import { updateEa, eaErrorMessage, type EaDetail } from "../_lib/eas";
import {
  generateMql5,
  generatePython,
  codegenErrorMessage,
} from "../_lib/codegen";
import { GeneratedCodeModal } from "./GeneratedCodeModal";
import { EaSwitcher } from "./EaSwitcher";
import { GroupButton } from "./GroupButton";

export function EditorToolbar({ eaId }: { eaId: string }) {
  const toJSON = useGraphStore((s) => s.toJSON);
  const undo = useGraphStore((s) => s.undo);
  const redo = useGraphStore((s) => s.redo);
  const canUndo = useGraphStore((s) => s.canUndo);
  const canRedo = useGraphStore((s) => s.canRedo);
  const queryClient = useQueryClient();
  const [modalOpen, setModalOpen] = useState(false);

  const cached = () => queryClient.getQueryData<EaDetail>(["ea", eaId]);

  const saveMutation = useMutation({
    // Send the graph VERBATIM — no normalization of the custom-node shape. The
    // backend requires `updated_at` for optimistic locking; read it from cache.
    mutationFn: () => {
      const updatedAt = cached()?.updated_at ?? new Date().toISOString();
      return updateEa(eaId, { graph: toJSON(), updated_at: updatedAt });
    },
    onSuccess: (updated) => {
      // Keep the cached EA in sync with what we just persisted (and absorb the
      // new updated_at so the next save's precondition is current).
      queryClient.setQueryData(["ea", eaId], updated);
    },
  });

  // EA name comes from the cached EA (populated by StrategyHydrator), falling
  // back to a default. Read once per generate so both languages agree.
  const eaName = () => cached()?.name ?? "GeneratedEA";

  // Two INDEPENDENT mutations so MQL5 and Python generate concurrently and each
  // pane has its own loading + error state (partial failure: one pane can show
  // code while the other shows its error).
  const mql5Mutation = useMutation({
    mutationFn: () => generateMql5(toJSON(), eaName()),
  });
  const pythonMutation = useMutation({
    mutationFn: () => generatePython(toJSON(), eaName()),
  });

  // Fire both at once and open the modal immediately so panes show per-pane
  // loading while the requests are in flight.
  const handleGenerate = () => {
    setModalOpen(true);
    mql5Mutation.mutate();
    pythonMutation.mutate();
  };

  const generating = mql5Mutation.isPending || pythonMutation.isPending;
  const cachedName = cached()?.name;

  const saveLabel = saveMutation.isPending
    ? "Guardando…"
    : saveMutation.isSuccess
      ? "Guardado"
      : "Guardar";

  return (
    <div className="border-border bg-background-elevated flex h-14 items-center gap-2 border-b px-4">
      <div className="mr-auto flex items-center gap-2">
        <Link
          href="/eas"
          title="Volver a Gestión EAs"
          className={buttonVariants({ variant: "ghost", size: "sm" })}
        >
          <ArrowLeft className="h-4 w-4" /> Volver
        </Link>
        <span
          aria-hidden
          className="bg-border mx-1 h-5 w-px shrink-0"
        />
        <EaSwitcher eaId={eaId} />
      </div>
      {saveMutation.isError && (
        <span role="alert" className="text-destructive text-sm">
          {eaErrorMessage(saveMutation.error)}
        </span>
      )}
      <Button
        size="sm"
        variant="outline"
        onClick={() => undo()}
        disabled={!canUndo}
        title="Deshacer (Ctrl/Cmd+Z)"
      >
        Deshacer
      </Button>
      <Button
        size="sm"
        variant="outline"
        onClick={() => redo()}
        disabled={!canRedo}
        title="Rehacer (Ctrl+Shift+Z / Ctrl+Y)"
      >
        Rehacer
      </Button>
      <GroupButton />
      <Button
        size="sm"
        variant="outline"
        onClick={() => saveMutation.mutate()}
        disabled={saveMutation.isPending}
      >
        {saveLabel}
      </Button>
      <Button size="sm" onClick={handleGenerate} disabled={generating}>
        {generating ? "Generando…" : "Generar código"}
      </Button>
      <GeneratedCodeModal
        open={modalOpen}
        onOpenChange={setModalOpen}
        eaName={cachedName ?? "GeneratedEA"}
        mql5Code={mql5Mutation.data?.code ?? ""}
        mql5Loading={mql5Mutation.isPending}
        mql5Error={
          mql5Mutation.isError ? codegenErrorMessage(mql5Mutation.error) : null
        }
        pythonCode={pythonMutation.data?.code ?? ""}
        pythonLoading={pythonMutation.isPending}
        pythonError={
          pythonMutation.isError
            ? codegenErrorMessage(pythonMutation.error)
            : null
        }
      />
    </div>
  );
}
