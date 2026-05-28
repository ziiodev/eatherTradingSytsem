"use client";

import { useState } from "react";
import { toast } from "sonner";

import { ApiError } from "@/lib/api";
import {
  lifecycleAction,
  PROJECT_STATUS_LABEL,
  type LifecycleAction,
  type ProjectDetail,
  type ProjectStatus,
} from "@/lib/projects";

/**
 * Optimistic lifecycle transition hook.
 *
 * Usage:
 *   const { run, status, pending } = useProjectLifecycle(initial, (updated) =>
 *     setProject(updated)
 *   );
 *   run("activate");
 *
 * Strategy:
 *   1. Apply the predicted ``to`` status to local state immediately.
 *   2. Fire the POST. On 200 → keep the server's payload.
 *   3. On 409 → revert to the original status and surface a toast with
 *      the backend's reason ("invalid_transition", "already moved", ...).
 *   4. On 4xx/5xx other than 409 → revert + generic toast.
 */
const ACTION_TO_STATUS: Record<LifecycleAction, ProjectStatus> = {
  activate: "active",
  pause: "paused",
  stop: "stopped",
  "mark-error": "error",
  maintenance: "maintenance",
};

export interface ProjectLifecycleHook {
  status: ProjectStatus;
  pending: boolean;
  run: (action: LifecycleAction) => Promise<void>;
}

export function useProjectLifecycle(
  project: ProjectDetail,
  onChange: (updated: ProjectDetail) => void,
): ProjectLifecycleHook {
  const [status, setStatus] = useState<ProjectStatus>(project.status);
  const [pending, setPending] = useState(false);

  async function run(action: LifecycleAction): Promise<void> {
    const previous = status;
    const optimistic = ACTION_TO_STATUS[action];
    setStatus(optimistic);
    setPending(true);
    try {
      const updated = await lifecycleAction(project.id, action);
      onChange(updated);
      setStatus(updated.status);
      toast.success(
        `Proyecto movido a "${PROJECT_STATUS_LABEL[updated.status]}"`,
      );
    } catch (err) {
      setStatus(previous);
      if (err instanceof ApiError) {
        if (err.status === 409) {
          const body = err.body as { detail?: unknown };
          const detail = body?.detail;
          const reason =
            typeof detail === "string"
              ? detail
              : typeof detail === "object" && detail !== null
                ? "Transición inválida en este estado"
                : "Conflicto al cambiar de estado";
          toast.error(reason);
        } else if (err.status === 404) {
          toast.error("Proyecto no encontrado");
        } else {
          toast.error("No se pudo cambiar el estado");
        }
      } else {
        toast.error("Error de red");
      }
    } finally {
      setPending(false);
    }
  }

  return { status, pending, run };
}
