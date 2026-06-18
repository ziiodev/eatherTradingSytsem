"use client";

/**
 * PairHeader — top of the pair (Par) detail layout.
 *
 * Owns:
 *   - BackLink to the owning account's pares list
 *   - Pair name + StatusBadge
 *   - Five lifecycle action buttons (Activar, Pausar, Detener, Marcar error,
 *     Mantenimiento) + Eliminar
 *
 * Fetches the pair itself so the layout (a server component) doesn't need to
 * do any network work. Owns its loading / error / not-found states inline.
 */

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { ArrowLeft, Trash2 } from "lucide-react";
import { toast } from "sonner";

import { ApiError } from "@/lib/api";
import {
  canTransition,
  deletePair,
  getPair,
  isDeletable,
  PAIR_STATUS_LABEL,
  type PairDetail,
  type LifecycleAction,
} from "@/lib/pairs";
import { Button } from "@/components/ui/button";
import { StatusBadge } from "@/components/StatusBadge";
import { usePairLifecycle } from "@/hooks/usePairLifecycle";

const LIFECYCLE_ACTIONS: ReadonlyArray<{
  action: LifecycleAction;
  to: PairDetail["status"];
  label: string;
  variant?: "default" | "outline" | "destructive";
}> = [
  { action: "activate", to: "active", label: "Activar" },
  { action: "pause", to: "paused", label: "Pausar", variant: "outline" },
  { action: "stop", to: "stopped", label: "Detener", variant: "outline" },
  {
    action: "mark-error",
    to: "error",
    label: "Marcar error",
    variant: "destructive",
  },
  {
    action: "maintenance",
    to: "maintenance",
    label: "Mantenimiento",
    variant: "outline",
  },
];

export interface PairHeaderProps {
  accountId: string;
  pairId: string;
}

export function PairHeader({
  accountId,
  pairId,
}: PairHeaderProps): React.JSX.Element {
  const router = useRouter();
  const [pair, setPair] = useState<PairDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [errored, setErrored] = useState(false);
  const paresHref = `/cuentas/${accountId}/pares`;

  useEffect(() => {
    let cancelled = false;
    const load = async (): Promise<void> => {
      try {
        const data = await getPair(pairId);
        if (cancelled) return;
        setPair(data);
        setErrored(false);
      } catch {
        if (cancelled) return;
        setErrored(true);
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    void load();
    return () => {
      cancelled = true;
    };
  }, [pairId]);

  // Hook is always called; falls back to a synthetic placeholder while the
  // real pair is loading. Lifecycle actions are disabled in that window.
  const lifecycle = usePairLifecycle(
    pair ?? ({ id: pairId, status: "inactive" } as PairDetail),
    (updated) => setPair(updated),
  );

  async function handleDelete(): Promise<void> {
    if (!pair) return;
    if (!confirm(`Eliminar el par "${pair.name}"?`)) return;
    try {
      await deletePair(pair.id);
      toast.success("Par eliminado");
      router.push(paresHref);
      router.refresh();
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        toast.error("Solo se pueden eliminar pares inactivos o detenidos");
      } else {
        toast.error("No se pudo eliminar");
      }
    }
  }

  return (
    <div className="flex flex-col gap-3">
      <BackLink href={paresHref} />
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <h1 className="text-2xl font-semibold tracking-tight">
            {loading ? "Cargando…" : (pair?.name ?? "Par")}
          </h1>
          {pair ? <StatusBadge status={lifecycle.status} /> : null}
        </div>
        {pair ? (
          <div className="flex flex-wrap items-center gap-2">
            {LIFECYCLE_ACTIONS.map(({ action, to, label, variant }) => {
              const enabled =
                canTransition(lifecycle.status, to) && !lifecycle.pending;
              return (
                <Button
                  key={action}
                  size="sm"
                  variant={variant ?? "default"}
                  disabled={!enabled}
                  title={
                    enabled
                      ? `Mover a "${PAIR_STATUS_LABEL[to]}"`
                      : "Transición no permitida en el estado actual"
                  }
                  onClick={() => void lifecycle.run(action)}
                >
                  {label}
                </Button>
              );
            })}
            <Button
              size="sm"
              variant="destructive"
              disabled={!isDeletable(lifecycle.status)}
              onClick={() => void handleDelete()}
              title={
                isDeletable(lifecycle.status)
                  ? "Eliminar este par"
                  : "Solo se eliminan pares inactivos o detenidos"
              }
            >
              <Trash2 className="h-4 w-4" /> Eliminar
            </Button>
          </div>
        ) : null}
      </header>
      {errored && !pair ? (
        <p role="alert" className="text-sm text-[rgb(var(--danger))]">
          No se pudo cargar el par.
        </p>
      ) : null}
    </div>
  );
}

function BackLink({ href }: { href: string }): React.JSX.Element {
  return (
    <Link
      href={href}
      className="inline-flex items-center gap-1 text-sm text-[rgb(var(--foreground-muted))] hover:text-[rgb(var(--foreground))]"
    >
      <ArrowLeft className="h-3 w-3" /> Volver a los pares
    </Link>
  );
}

export default PairHeader;
