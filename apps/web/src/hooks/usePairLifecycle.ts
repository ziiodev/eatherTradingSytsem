"use client";

import { useState } from "react";
import { toast } from "sonner";

import { ApiError } from "@/lib/api";
import {
  lifecycleAction,
  PAIR_STATUS_LABEL,
  type LifecycleAction,
  type PairDetail,
  type PairStatus,
} from "@/lib/pairs";

/**
 * Optimistic lifecycle transition hook.
 *
 * Usage:
 *   const { run, status, pending } = usePairLifecycle(initial, (updated) =>
 *     setPair(updated)
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
const ACTION_TO_STATUS: Record<LifecycleAction, PairStatus> = {
  activate: "active",
  pause: "paused",
  stop: "stopped",
  "mark-error": "error",
  maintenance: "maintenance",
};

export interface PairLifecycleHook {
  status: PairStatus;
  pending: boolean;
  run: (action: LifecycleAction) => Promise<void>;
}

export function usePairLifecycle(
  pair: PairDetail,
  onChange: (updated: PairDetail) => void,
): PairLifecycleHook {
  const [status, setStatus] = useState<PairStatus>(pair.status);
  const [pending, setPending] = useState(false);

  async function run(action: LifecycleAction): Promise<void> {
    const previous = status;
    const optimistic = ACTION_TO_STATUS[action];
    setStatus(optimistic);
    setPending(true);
    try {
      const updated = await lifecycleAction(pair.id, action);
      onChange(updated);
      setStatus(updated.status);
      toast.success(
        `Par movido a "${PAIR_STATUS_LABEL[updated.status]}"`,
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
          toast.error("Par no encontrado");
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
