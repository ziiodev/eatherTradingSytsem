"use client";

/**
 * ProjectHeader — top of the project detail layout.
 *
 * Owns:
 *   - BackLink to /proyectos
 *   - Project name + StatusBadge
 *   - Five lifecycle action buttons (Activar, Pausar, Detener, Marcar error,
 *     Mantenimiento) + Eliminar
 *
 * Fetches the project itself so the layout (a server component) doesn't
 * need to do any network work. Owns its loading / error / not-found states
 * inline because the layout renders its `children` regardless — we don't
 * want one tab's failure to blank the whole shell.
 */

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { ArrowLeft, Trash2 } from "lucide-react";
import { toast } from "sonner";

import { ApiError } from "@/lib/api";
import {
  canTransition,
  deleteProject,
  getProject,
  isDeletable,
  PROJECT_STATUS_LABEL,
  type ProjectDetail,
  type LifecycleAction,
} from "@/lib/projects";
import { Button } from "@/components/ui/button";
import { StatusBadge } from "@/components/StatusBadge";
import { useProjectLifecycle } from "@/hooks/useProjectLifecycle";

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

export interface ProjectHeaderProps {
  projectId: string;
}

export function ProjectHeader({
  projectId,
}: ProjectHeaderProps): React.JSX.Element {
  const router = useRouter();
  const [project, setProject] = useState<ProjectDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [errored, setErrored] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const load = async (): Promise<void> => {
      try {
        const data = await getProject(projectId);
        if (cancelled) return;
        setProject(data);
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
  }, [projectId]);

  // Hook is always called; falls back to a synthetic placeholder while the
  // real project is loading. Lifecycle actions are disabled in that window.
  const lifecycle = useProjectLifecycle(
    project ?? ({ id: projectId, status: "inactive" } as ProjectDetail),
    (updated) => setProject(updated),
  );

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

  return (
    <div className="flex flex-col gap-3">
      <BackLink />
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <h1 className="text-2xl font-semibold tracking-tight">
            {loading ? "Cargando…" : (project?.name ?? "Proyecto")}
          </h1>
          {project ? <StatusBadge status={lifecycle.status} /> : null}
        </div>
        {project ? (
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
        ) : null}
      </header>
      {errored && !project ? (
        <p role="alert" className="text-sm text-[rgb(var(--danger))]">
          No se pudo cargar el proyecto.
        </p>
      ) : null}
    </div>
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

export default ProjectHeader;
