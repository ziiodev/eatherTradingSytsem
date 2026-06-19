"use client";

/**
 * Loads a strategy by id and hydrates the editor's graph store.
 *
 * Mounted by the editor Server-Component shell. Fetches the strategy via
 * TanStack Query and, once the data arrives, pushes its graph into the
 * Zustand store VERBATIM (React-Flow `node.type:"custom"` + domain `data.type`
 * must round-trip unchanged — no normalization here). On 404 it renders a
 * Not Found surface and skips hydration.
 */
import { useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { ApiError } from "@/lib/api";
import { getEa, eaErrorMessage } from "../_lib/eas";
import { useGraphStore } from "../_stores/graphStore";
import { useNodeUiStore } from "../_stores/nodeUiStore";
import { usePaletteUiStore } from "../_stores/paletteUiStore";
import { buttonVariants } from "@/components/ui/button";

export function StrategyHydrator({ id }: { id: string }) {
  // Use hydrate (not setGraph) so the loaded state becomes the history floor:
  // it sets the graph AND resets undo/redo history. This also runs after a Save
  // refreshes the cached strategy, re-hydrating to the just-persisted state.
  const hydrate = useGraphStore((s) => s.hydrate);
  // Ephemeral expand state must NOT survive a (re)load: a freshly loaded or
  // just-saved strategy always starts all-compact.
  const resetNodeUi = useNodeUiStore((s) => s.reset);
  // Palette expand state must also NOT survive a (re)load: a freshly loaded
  // or just-saved strategy always starts with all groups/subgroups collapsed.
  const resetPaletteUi = usePaletteUiStore((s) => s.reset);

  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["ea", id],
    queryFn: () => getEa(id),
    // A missing/unowned strategy is a terminal 404; don't hammer the backend.
    retry: (failureCount, err) =>
      !(err instanceof ApiError && err.status === 404) && failureCount < 2,
  });

  useEffect(() => {
    if (data) {
      // Hydrate the store verbatim — preserve the custom-node graph shape — and
      // reset history so the loaded/saved state is the undo floor.
      hydrate(data.graph);
      // Drop any stale expanded-node ids so the load starts all-compact.
      resetNodeUi();
      // Drop any stale expanded palette ids so groups/subgroups start collapsed.
      resetPaletteUi();
    }
  }, [data, hydrate, resetNodeUi, resetPaletteUi]);

  const isNotFound = error instanceof ApiError && error.status === 404;

  // Overlay the canvas while loading or on error so the editing surface stays
  // mounted underneath; render nothing once the store is hydrated.
  if (isError && isNotFound) {
    return (
      <Overlay>
        <p className="text-muted-foreground text-sm">
          Este Expert Advisor no se encontró. Puede que se haya eliminado.
        </p>
        <Link
          href="/eas"
          className={buttonVariants({ variant: "outline", size: "sm" })}
        >
          Volver a Mis Expert Advisors
        </Link>
      </Overlay>
    );
  }

  if (isError) {
    return (
      <Overlay>
        <p role="alert" className="text-destructive text-sm">
          {eaErrorMessage(error)}
        </p>
      </Overlay>
    );
  }

  if (isLoading) {
    return (
      <Overlay>
        <p className="text-muted-foreground text-sm">Cargando Expert Advisor…</p>
      </Overlay>
    );
  }

  return null;
}

/** Full-bleed centered overlay above the canvas for status/error surfaces. */
function Overlay({ children }: { children: React.ReactNode }) {
  return (
    <div className="bg-background/80 absolute inset-0 z-10 flex flex-col items-center justify-center gap-4 p-8 text-center backdrop-blur-sm">
      {children}
    </div>
  );
}
