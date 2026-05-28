"use client";

/**
 * Sleep Phase panel — embedded as a tab on `/proyectos/[id]`.
 *
 * Responsibilities:
 *
 * 1. Trigger a Micro / Profundo / Crítico sleep run from the dashboard.
 * 2. List recent `sleep_runs` for the project (polled every 5 s).
 * 3. Expand a run to reveal per-agent reflections + any proposed
 *    `config_versions`. Each pending version exposes Approve / Reject
 *    buttons; applied versions expose Revert.
 *
 * Polling instead of WebSockets in v1 — see the design doc decision.
 */

import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";

import { ApiError } from "@/lib/api";
import {
  approveConfigVersion,
  ConfigVersionDetail,
  ConfigVersionRiskClass,
  getSleepRun,
  listSleepRuns,
  RISK_CLASS_LABEL,
  rejectConfigVersion,
  revertConfigVersion,
  SLEEP_PHASE_LABEL,
  SLEEP_RUN_STATUS_LABEL,
  SleepPhaseType,
  SleepRunDetailResponse,
  SleepRunSummary,
  triggerSleepRun,
} from "@/lib/sleep";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";

const POLL_MS = 5000;

interface Props {
  projectId: string;
}

export function SuenoPanel({ projectId }: Props): React.JSX.Element {
  const [runs, setRuns] = useState<SleepRunSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [triggering, setTriggering] = useState(false);
  const [expandedRunId, setExpandedRunId] = useState<string | null>(null);

  const refresh = useCallback(async (): Promise<void> => {
    try {
      const data = await listSleepRuns(projectId, { limit: 50 });
      setRuns(data.items);
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        // Bubble up to global auth handler — surface a quiet toast.
        toast.error("Sesión expirada");
      }
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    void refresh();
    const interval = setInterval(() => void refresh(), POLL_MS);
    return () => clearInterval(interval);
  }, [refresh]);

  const handleTrigger = async (phase: SleepPhaseType): Promise<void> => {
    setTriggering(true);
    try {
      const result = await triggerSleepRun(projectId, phase);
      if (result.status === "failed") {
        toast.error(`Sleep run falló: ${result.error ?? "sin detalle"}`);
      } else if (result.status === "skipped") {
        toast.message(`Run omitido: ${result.summary ?? ""}`);
      } else {
        toast.success(`Sleep run completado (${result.status})`);
      }
      await refresh();
    } catch (err) {
      const detail = err instanceof ApiError ? `(${err.status})` : "";
      toast.error(`Trigger falló ${detail}`);
    } finally {
      setTriggering(false);
    }
  };

  return (
    <div className="flex flex-col gap-4">
      <header className="flex flex-wrap items-center gap-2">
        <h2 className="text-lg font-semibold tracking-tight">Fases del Sueño</h2>
        <div className="ml-auto flex gap-2">
          <Button
            size="sm"
            variant="outline"
            disabled={triggering}
            onClick={() => void handleTrigger("micro")}
          >
            Trigger Micro
          </Button>
          <Button
            size="sm"
            variant="outline"
            disabled={triggering}
            onClick={() => void handleTrigger("profundo")}
          >
            Trigger Profundo
          </Button>
          <Button
            size="sm"
            variant="destructive"
            disabled={triggering}
            onClick={() => void handleTrigger("critico")}
          >
            Trigger Crítico
          </Button>
        </div>
      </header>

      {loading ? (
        <p className="text-sm text-[rgb(var(--foreground-muted))]">Cargando…</p>
      ) : runs.length === 0 ? (
        <p className="text-sm text-[rgb(var(--foreground-muted))]">
          Sin runs todavía. Lanza un Micro-sueño manual para empezar.
        </p>
      ) : (
        <ul className="flex flex-col gap-2">
          {runs.map((run) => (
            <SleepRunRow
              key={run.id}
              projectId={projectId}
              run={run}
              expanded={expandedRunId === run.id}
              onToggle={() =>
                setExpandedRunId((current) => (current === run.id ? null : run.id))
              }
              onDecisionMade={() => void refresh()}
            />
          ))}
        </ul>
      )}
    </div>
  );
}

interface RowProps {
  projectId: string;
  run: SleepRunSummary;
  expanded: boolean;
  onToggle: () => void;
  onDecisionMade: () => void;
}

function SleepRunRow({
  projectId,
  run,
  expanded,
  onToggle,
  onDecisionMade,
}: RowProps): React.JSX.Element {
  const [detail, setDetail] = useState<SleepRunDetailResponse | null>(null);
  const [loadingDetail, setLoadingDetail] = useState(false);

  useEffect(() => {
    if (!expanded) return;
    let cancelled = false;
    const load = async (): Promise<void> => {
      setLoadingDetail(true);
      try {
        const data = await getSleepRun(projectId, run.id);
        if (!cancelled) setDetail(data);
      } catch {
        // Toast already handled by global handler; keep the panel quiet.
      } finally {
        if (!cancelled) setLoadingDetail(false);
      }
    };
    void load();
    return () => {
      cancelled = true;
    };
  }, [expanded, projectId, run.id]);

  return (
    <li className="rounded-md border border-[rgb(var(--border))] bg-[rgb(var(--background-elevated))] p-3">
      <button
        type="button"
        onClick={onToggle}
        className="flex w-full items-center justify-between gap-3 text-left"
      >
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <Badge variant="muted">{SLEEP_PHASE_LABEL[run.phase_type]}</Badge>
          <StatusBadgeForRun status={run.status} />
          <span className="text-[rgb(var(--foreground-muted))]">
            {run.started_at ? new Date(run.started_at).toLocaleString() : "—"}
          </span>
        </div>
        <span className="text-xs text-[rgb(var(--foreground-muted))]">
          {expanded ? "Cerrar" : "Detalles"}
        </span>
      </button>

      {expanded && (
        <div className="mt-3 flex flex-col gap-3 border-t border-[rgb(var(--border))] pt-3">
          {run.summary && (
            <p className="text-sm text-[rgb(var(--foreground-muted))]">
              {run.summary}
            </p>
          )}
          {run.error && (
            <p
              role="alert"
              className="text-sm text-[rgb(var(--danger))]"
            >
              {run.error}
            </p>
          )}

          {loadingDetail ? (
            <p className="text-sm">Cargando reflexiones…</p>
          ) : detail ? (
            <RunDetailBody
              detail={detail}
              onDecisionMade={onDecisionMade}
            />
          ) : null}
        </div>
      )}
    </li>
  );
}

function RunDetailBody({
  detail,
  onDecisionMade,
}: {
  detail: SleepRunDetailResponse;
  onDecisionMade: () => void;
}): React.JSX.Element {
  return (
    <div className="flex flex-col gap-4">
      <div>
        <h3 className="text-sm font-semibold">Reflexiones por agente</h3>
        {detail.reflections.length === 0 ? (
          <p className="text-sm text-[rgb(var(--foreground-muted))]">
            Sin reflexiones registradas.
          </p>
        ) : (
          <ul className="mt-2 flex flex-col gap-2">
            {detail.reflections.map((reflection) => (
              <li
                key={reflection.id}
                className="rounded border border-[rgb(var(--border))] p-2 text-sm"
              >
                <div className="flex items-center gap-2">
                  <Badge variant="muted">{reflection.agent_type}</Badge>
                </div>
                {reflection.reflection_md && (
                  <pre className="mt-1 whitespace-pre-wrap text-xs text-[rgb(var(--foreground-muted))]">
                    {reflection.reflection_md}
                  </pre>
                )}
                {Object.keys(reflection.suggested_changes).length > 0 && (
                  <pre className="mt-1 overflow-x-auto rounded bg-[rgb(var(--background))] p-2 text-xs">
                    {JSON.stringify(reflection.suggested_changes, null, 2)}
                  </pre>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>

      <div>
        <h3 className="text-sm font-semibold">
          Propuestas de configuración
        </h3>
        {detail.config_versions.length === 0 ? (
          <p className="text-sm text-[rgb(var(--foreground-muted))]">
            Sin cambios propuestos.
          </p>
        ) : (
          <ul className="mt-2 flex flex-col gap-2">
            {detail.config_versions.map((cv) => (
              <ConfigVersionRow
                key={cv.id}
                cv={cv}
                onDecisionMade={onDecisionMade}
              />
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

function ConfigVersionRow({
  cv,
  onDecisionMade,
}: {
  cv: ConfigVersionDetail;
  onDecisionMade: () => void;
}): React.JSX.Element {
  const [pending, setPending] = useState(false);

  const handle = async (
    action: "approve" | "reject" | "revert",
  ): Promise<void> => {
    setPending(true);
    try {
      const fn = {
        approve: approveConfigVersion,
        reject: rejectConfigVersion,
        revert: revertConfigVersion,
      }[action];
      await fn(cv.id);
      toast.success(
        action === "approve"
          ? "Cambios aplicados"
          : action === "reject"
            ? "Propuesta rechazada"
            : "Revert aplicado",
      );
      onDecisionMade();
    } catch (err) {
      const detail = err instanceof ApiError ? `(${err.status})` : "";
      toast.error(`Acción falló ${detail}`);
    } finally {
      setPending(false);
    }
  };

  return (
    <li className="rounded border border-[rgb(var(--border))] p-2 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <RiskBadge risk={cv.risk_class} />
        <Badge variant="muted">{cv.status}</Badge>
        <span className="text-xs text-[rgb(var(--foreground-muted))]">
          {cv.proposed_at ? new Date(cv.proposed_at).toLocaleString() : "—"}
        </span>
        <div className="ml-auto flex gap-2">
          {cv.status === "pending" && (
            <>
              <Button
                size="sm"
                disabled={pending}
                onClick={() => void handle("approve")}
              >
                Aprobar
              </Button>
              <Button
                size="sm"
                variant="outline"
                disabled={pending}
                onClick={() => void handle("reject")}
              >
                Rechazar
              </Button>
            </>
          )}
          {cv.status === "applied" && (
            <Button
              size="sm"
              variant="outline"
              disabled={pending}
              onClick={() => void handle("revert")}
            >
              Revertir
            </Button>
          )}
        </div>
      </div>
      <pre className="mt-2 overflow-x-auto rounded bg-[rgb(var(--background))] p-2 text-xs">
        {JSON.stringify(cv.snapshot, null, 2)}
      </pre>
    </li>
  );
}

function StatusBadgeForRun({
  status,
}: {
  status: SleepRunSummary["status"];
}): React.JSX.Element {
  const variant: "success" | "danger" | "muted" =
    status === "succeeded"
      ? "success"
      : status === "failed" || status === "crashed"
        ? "danger"
        : "muted";
  return <Badge variant={variant}>{SLEEP_RUN_STATUS_LABEL[status]}</Badge>;
}

function RiskBadge({
  risk,
}: {
  risk: ConfigVersionRiskClass;
}): React.JSX.Element {
  const variant: "success" | "warning" | "danger" =
    risk === "alto" ? "danger" : risk === "medio" ? "warning" : "success";
  return <Badge variant={variant}>{RISK_CLASS_LABEL[risk]}</Badge>;
}
