"use client";

/**
 * Expert Advisor switcher for the editor header.
 *
 * Shows the CURRENT EA's friendly name as a dropdown trigger (the name comes
 * from the shared `["ea", id]` cache that StrategyHydrator populates) and, when
 * opened, lists ALL of the user's EAs so they can jump straight to another one
 * without leaving the canvas. Selecting an EA navigates to its editor route; the
 * editor shell re-hydrates the graph for the new id.
 *
 * The full list is only fetched when the dropdown opens (`enabled: open`) to
 * keep the editor's first paint cheap.
 */
import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { ChevronDown } from "lucide-react";
import { cn } from "@/lib/utils";
import { getEa, listEas, type EaSummary } from "../_lib/eas";

export function EaSwitcher({ eaId }: { eaId: string }) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  // Shares the cache key StrategyHydrator fills, so the name is usually instant.
  const { data: current } = useQuery({
    queryKey: ["ea", eaId],
    queryFn: () => getEa(eaId),
  });

  // Only load the full EA list once the dropdown is actually opened.
  const { data: all } = useQuery({
    queryKey: ["eas"],
    queryFn: () => listEas(),
    enabled: open,
  });

  // Close on outside click while the dropdown is open.
  useEffect(() => {
    if (!open) return;
    const onMouseDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    window.addEventListener("mousedown", onMouseDown);
    return () => window.removeEventListener("mousedown", onMouseDown);
  }, [open]);

  const name = current?.name ?? "Cargando…";

  const goTo = (id: string) => {
    setOpen(false);
    if (id !== eaId) router.push(`/eas/editor/${id}`);
  };

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        title="Cambiar de Expert Advisor"
        className="hover:bg-accent flex items-center gap-1.5 rounded-md px-2 py-1 text-sm font-semibold"
      >
        <span className="max-w-[18rem] truncate">{name}</span>
        <ChevronDown className="text-muted-foreground h-4 w-4 shrink-0" />
      </button>

      {open && (
        <div className="border-border bg-background absolute top-full left-0 z-20 mt-1 max-h-80 w-72 overflow-auto rounded-md border p-1 shadow-lg">
          <p className="text-muted-foreground px-2 py-1 text-xs font-medium">
            Cambiar a otro Expert Advisor
          </p>
          {all && all.length > 0 ? (
            all.map((s: EaSummary) => (
              <button
                key={s.id}
                type="button"
                onClick={() => goTo(s.id)}
                className={cn(
                  "hover:bg-accent flex w-full items-center justify-between gap-2 rounded px-2 py-1.5 text-left text-sm",
                  s.id === eaId && "text-muted-foreground",
                )}
              >
                <span className="truncate">{s.name}</span>
                {s.id === eaId && (
                  <span className="text-muted-foreground shrink-0 text-xs">
                    actual
                  </span>
                )}
              </button>
            ))
          ) : (
            <p className="text-muted-foreground px-2 py-1.5 text-sm">
              Cargando…
            </p>
          )}
        </div>
      )}
    </div>
  );
}
