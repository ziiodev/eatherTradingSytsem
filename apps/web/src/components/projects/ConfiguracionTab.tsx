"use client";

/**
 * ConfiguracionTab — the existing four-panel project configuration surface.
 *
 * Hosts the inner shadcn-Tabs (`general` / `infraestructura` / `operativa` /
 * `sueno`) and the project PATCH handling that previously lived inline in
 * `[id]/page.tsx`.
 *
 * Lifted into a dedicated component so it can be consumed verbatim by the
 * new `/configuracion` route segment introduced by `project-tabs-shell`.
 * Behavior is intentionally unchanged from the pre-refactor page.
 *
 * The outer chrome (BackLink, header with project name + status, lifecycle
 * action buttons + Eliminar, LearningNav, top-level tab navigation) lives
 * in the parent `layout.tsx` once the refactor lands — this component is
 * the *contents* of the Configuración tab, not the page chrome.
 */

import { useEffect, useState } from "react";

import { ApiError } from "@/lib/api";
import {
  getProject,
  patchProject,
  type ProjectCreateInput,
  type ProjectDetail,
} from "@/lib/projects";
import { toast } from "sonner";
import {
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
} from "@/components/ui/tabs";
import { ProjectForm } from "@/components/projects/ProjectForm";
import { ProjectAgentsPanel } from "@/components/projects/ProjectAgentsPanel";
import { InfraestructuraPanel } from "@/components/projects/InfraestructuraPanel";
import { OperativaPanel } from "@/components/projects/OperativaPanel";
import { SuenoPanel } from "@/components/projects/SuenoPanel";

export interface ConfiguracionTabProps {
  projectId: string;
}

export function ConfiguracionTab({
  projectId,
}: ConfiguracionTabProps): React.JSX.Element {
  const [project, setProject] = useState<ProjectDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notFound, setNotFound] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const load = async (): Promise<void> => {
      try {
        const data = await getProject(projectId);
        if (cancelled) return;
        setProject(data);
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
  }, [projectId]);

  async function handlePatch(values: ProjectCreateInput): Promise<void> {
    if (!project) return;
    setSubmitting(true);
    setError(null);
    try {
      const updated = await patchProject(project.id, values);
      setProject(updated);
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
    return <p className="text-sm">Proyecto no encontrado.</p>;
  }

  if (!project) {
    return (
      <p role="alert" className="text-sm text-[rgb(var(--danger))]">
        {error ?? "Error desconocido."}
      </p>
    );
  }

  return (
    <ProjectTabs
      project={project}
      submitting={submitting}
      error={error}
      onSubmit={handlePatch}
      onProjectUpdated={setProject}
    />
  );
}

function ProjectTabs({
  project,
  submitting,
  error,
  onSubmit,
  onProjectUpdated,
}: {
  project: ProjectDetail;
  submitting: boolean;
  error: string | null;
  onSubmit: (values: ProjectCreateInput) => Promise<void>;
  onProjectUpdated: (next: ProjectDetail) => void;
}): React.JSX.Element {
  const [tab, setTab] = useState<
    "general" | "infraestructura" | "operativa" | "sueno"
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
        <TabsTrigger value="operativa" data-testid="config-subtab-operativa">
          Operativa
        </TabsTrigger>
        <TabsTrigger value="sueno" data-testid="config-subtab-sueno">
          Sueño
        </TabsTrigger>
      </TabsList>
      <TabsContent value="general">
        <div className="flex flex-col gap-4">
          <ProjectAgentsPanel
            project={project}
            onProjectUpdated={onProjectUpdated}
          />
          <ProjectForm
            mode="edit"
            initial={project}
            submitting={submitting}
            error={error}
            onSubmit={onSubmit}
          />
        </div>
      </TabsContent>
      <TabsContent value="infraestructura">
        <InfraestructuraPanel
          project={project}
          onProjectUpdated={onProjectUpdated}
        />
      </TabsContent>
      <TabsContent value="operativa">
        <OperativaPanel project={project} />
      </TabsContent>
      <TabsContent value="sueno">
        <SuenoPanel projectId={project.id} />
      </TabsContent>
    </Tabs>
  );
}

export default ConfiguracionTab;
