"use client";

/**
 * ConfiguracionTab — pair configuration surface (three sub-tabs).
 *
 * Hosts the inner shadcn-Tabs (`general` / `infraestructura` / `sueno`)
 * and the pair PATCH handling that previously lived inline in
 * `[id]/page.tsx`.
 *
 * Lifted into a dedicated component so it can be consumed verbatim by the
 * new `/configuracion` route segment introduced by `project-tabs-shell`.
 *
 * History: the prior `operativa` sub-tab was REMOVED in `project-operativa`
 * (Phase 7.2). The realtime Operativa surface now lives as its own
 * top-level tab at `/cuentas/[accountId]/pares/[pairId]/operativa`, so Configuración no
 * longer duplicates it.
 *
 * The outer chrome (BackLink, header with pair name + status, lifecycle
 * action buttons + Eliminar, LearningNav, top-level tab navigation) lives
 * in the parent `layout.tsx` — this component is the *contents* of the
 * Configuración tab, not the page chrome.
 */

import { useEffect, useState } from "react";

import { ApiError } from "@/lib/api";
import {
  getPair,
  patchPair,
  type PairCreateInput,
  type PairDetail,
} from "@/lib/pairs";
import { toast } from "sonner";
import {
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
} from "@/components/ui/tabs";
import { PairForm } from "@/components/projects/PairForm";
import { PairAgentsPanel } from "@/components/projects/PairAgentsPanel";
import { InfraestructuraPanel } from "@/components/projects/InfraestructuraPanel";
import { SuenoPanel } from "@/components/projects/SuenoPanel";

export interface ConfiguracionTabProps {
  pairId: string;
}

export function ConfiguracionTab({
  pairId,
}: ConfiguracionTabProps): React.JSX.Element {
  const [pair, setPair] = useState<PairDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notFound, setNotFound] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const load = async (): Promise<void> => {
      try {
        const data = await getPair(pairId);
        if (cancelled) return;
        setPair(data);
      } catch (err) {
        if (cancelled) return;
        if (err instanceof ApiError && err.status === 404) {
          setNotFound(true);
        } else {
          setError(
            err instanceof ApiError
              ? `Error al cargar (${err.status})`
              : "Error de red",
          );
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    void load();
    return () => {
      cancelled = true;
    };
  }, [pairId]);

  async function handlePatch(values: PairCreateInput): Promise<void> {
    if (!pair) return;
    setSubmitting(true);
    setError(null);
    try {
      const updated = await patchPair(pair.id, values);
      setPair(updated);
      toast.success("Cambios guardados");
    } catch (err) {
      if (err instanceof ApiError) {
        if (err.status === 409) {
          setError("Nombre duplicado o conflicto de estado.");
        } else if (err.status === 400 || err.status === 422) {
          setError(
            "Validación fallida en el servidor. Revisa los campos del formulario.",
          );
        } else if (err.status === 404) {
          setNotFound(true);
        } else {
          setError(`Error inesperado (${err.status})`);
        }
      } else {
        setError("Error de red.");
      }
    } finally {
      setSubmitting(false);
    }
  }

  if (loading) {
    return (
      <p className="text-sm text-[rgb(var(--foreground-muted))]">Cargando…</p>
    );
  }

  if (notFound) {
    return <p className="text-sm">Par no encontrado.</p>;
  }

  if (!pair) {
    return (
      <p role="alert" className="text-sm text-[rgb(var(--danger))]">
        {error ?? "Error desconocido."}
      </p>
    );
  }

  return (
    <ConfigSubTabs
      pair={pair}
      submitting={submitting}
      error={error}
      onSubmit={handlePatch}
      onPairUpdated={setPair}
    />
  );
}

function ConfigSubTabs({
  pair,
  submitting,
  error,
  onSubmit,
  onPairUpdated,
}: {
  pair: PairDetail;
  submitting: boolean;
  error: string | null;
  onSubmit: (values: PairCreateInput) => Promise<void>;
  onPairUpdated: (next: PairDetail) => void;
}): React.JSX.Element {
  const [tab, setTab] = useState<
    "general" | "infraestructura" | "sueno"
  >("general");
  return (
    <Tabs value={tab} onValueChange={(v) => setTab(v as typeof tab)}>
      <TabsList data-testid="config-subtabs-list">
        <TabsTrigger value="general" data-testid="config-subtab-general">
          General
        </TabsTrigger>
        <TabsTrigger
          value="infraestructura"
          data-testid="config-subtab-infraestructura"
        >
          Infraestructura
        </TabsTrigger>
        <TabsTrigger value="sueno" data-testid="config-subtab-sueno">
          Sueño
        </TabsTrigger>
      </TabsList>
      <TabsContent value="general">
        <div className="flex flex-col gap-4">
          <PairAgentsPanel
            pair={pair}
            onPairUpdated={onPairUpdated}
          />
          <PairForm
            mode="edit"
            initial={pair}
            submitting={submitting}
            error={error}
            onSubmit={onSubmit}
          />
        </div>
      </TabsContent>
      <TabsContent value="infraestructura">
        <InfraestructuraPanel
          pair={pair}
          onPairUpdated={onPairUpdated}
        />
      </TabsContent>
      <TabsContent value="sueno">
        <SuenoPanel pairId={pair.id} />
      </TabsContent>
    </Tabs>
  );
}

export default ConfiguracionTab;
