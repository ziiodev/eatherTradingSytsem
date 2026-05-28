"use client";

import { use, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { ArrowLeft, Trash2 } from "lucide-react";
import { toast } from "sonner";

import { ApiError } from "@/lib/api";
import {
  canTransition,
  deleteProject,
  getProject,
  isDeletable,
  patchProject,
  PROJECT_STATUS_LABEL,
  type ProjectCreateInput,
  type ProjectDetail,
  type LifecycleAction,
} from "@/lib/projects";
import { Button } from "@/components/ui/button";
import {
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
} from "@/components/ui/tabs";
import { StatusBadge } from "@/components/StatusBadge";
import { useProjectLifecycle } from "@/hooks/useProjectLifecycle";
import { ProjectForm } from "@/components/projects/ProjectForm";
import { InfraestructuraPanel } from "@/components/projects/InfraestructuraPanel";
import { OperativaPanel } from "@/components/projects/OperativaPanel";
import { SuenoPanel } from "@/components/projects/SuenoPanel";

const LIFECYCLE_ACTIONS: ReadonlyArray<{
  action: LifecycleAction;
  to: ProjectDetail["status"];
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

export default function ProjectDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}): React.JSX.Element {
  // Next 16: params is a promise — unwrap via use().
  const { id } = use(params);
  const router = useRouter();

  const [project, setProject] = useState<ProjectDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notFound, setNotFound] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const load = async (): Promise<void> => {
      try {
        const data = await getProject(id);
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
  }, [id]);

  // Hook is conditionally configured, but we always call it.
  const lifecycle = useProjectLifecycle(
    project ?? ({
      id,
      status: "inactive",
    } as ProjectDetail),
    (updated) => setProject(updated),
  );

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

  async function handleDelete(): Promise<void> {
    if (!project) return;
    if (!confirm(`Eliminar el proyecto "${project.name}"?`)) return;
    try {
      await deleteProject(project.id);
      toast.success("Proyecto eliminado");
      router.push("/proyectos");
      router.refresh();
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        toast.error("Solo se pueden eliminar proyectos inactivos o detenidos");
      } else {
        toast.error("No se pudo eliminar");
      }
    }
  }

  if (loading) {
    return (
      <section className="flex flex-col gap-4">
        <BackLink />
        <p className="text-sm text-[rgb(var(--foreground-muted))]">
          Cargando…
        </p>
      </section>
    );
  }

  if (notFound) {
    return (
      <section className="flex flex-col gap-4">
        <BackLink />
        <p className="text-sm">Proyecto no encontrado.</p>
      </section>
    );
  }

  if (!project) {
    return (
      <section className="flex flex-col gap-4">
        <BackLink />
        <p role="alert" className="text-sm text-[rgb(var(--danger))]">
          {error ?? "Error desconocido."}
        </p>
      </section>
    );
  }

  return (
    <section className="flex flex-col gap-4">
      <BackLink />

      <header className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <h1 className="text-2xl font-semibold tracking-tight">
            {project.name}
          </h1>
          <StatusBadge status={lifecycle.status} />
        </div>
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
                    ? `Mover a "${PROJECT_STATUS_LABEL[to]}"`
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
                ? "Eliminar este proyecto"
                : "Solo se eliminan proyectos inactivos o detenidos"
            }
          >
            <Trash2 className="h-4 w-4" /> Eliminar
          </Button>
        </div>
      </header>

      <ProjectTabs
        project={project}
        submitting={submitting}
        error={error}
        onSubmit={handlePatch}
        onProjectUpdated={setProject}
      />
    </section>
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
      <TabsList>
        <TabsTrigger value="general">General</TabsTrigger>
        <TabsTrigger value="infraestructura">Infraestructura</TabsTrigger>
        <TabsTrigger value="operativa">Operativa</TabsTrigger>
        <TabsTrigger value="sueno">Sueño</TabsTrigger>
      </TabsList>
      <TabsContent value="general">
        <ProjectForm
          mode="edit"
          initial={project}
          submitting={submitting}
          error={error}
          onSubmit={onSubmit}
        />
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

function BackLink(): React.JSX.Element {
  return (
    <Link
      href="/proyectos"
      className="inline-flex items-center gap-1 text-sm text-[rgb(var(--foreground-muted))] hover:text-[rgb(var(--foreground))]"
    >
      <ArrowLeft className="h-3 w-3" /> Volver al listado
    </Link>
  );
}
