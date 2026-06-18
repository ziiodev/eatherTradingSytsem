"use client";

import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";

import { ApiError } from "@/lib/api";
import {
  buildPairImage,
  createPairContainer,
  getPairContainerLogs,
  listPairContainerEvents,
  pausePairContainer,
  previewDockerfile,
  recreatePairContainer,
  removePairContainer,
  startPairContainer,
  stopPairContainer,
  type ContainerEventRow,
  type PairDetail,
} from "@/lib/pairs";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

/**
 * Infrastructure tab inside /cuentas/[accountId]/pares/[pairId].
 *
 * Surfaces:
 * - Status panel (container_id, image, mcp_url, last reconcile result).
 * - "Generar Dockerfile" → preview modal with the rendered text.
 * - Lifecycle button group (state-machine aware enable/disable).
 * - Logs viewer (auto-refresh every 5 s, tail=200).
 * - Recent container_events feed.
 *
 * Every mutation goes through ``apiPost`` which attaches the CSRF
 * header automatically. The backend rejects cross-tenant requests with
 * 404 (never 403), and surfaces docker-proxy errors as 502 with a
 * structured ``{op, cause}`` detail.
 */
export interface InfraestructuraPanelProps {
  pair: PairDetail;
  onPairUpdated?: (next: PairDetail) => void;
}

const LOGS_REFRESH_MS = 5_000;
const EVENTS_REFRESH_MS = 10_000;

type Pending =
  | null
  | "build"
  | "create"
  | "start"
  | "pause"
  | "stop"
  | "recreate"
  | "remove";

export function InfraestructuraPanel({
  pair,
  onPairUpdated,
}: InfraestructuraPanelProps): React.JSX.Element {
  const [previewOpen, setPreviewOpen] = useState(false);
  const [previewText, setPreviewText] = useState<string | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewError, setPreviewError] = useState<string | null>(null);

  const [logs, setLogs] = useState<string>("");
  const [logsError, setLogsError] = useState<string | null>(null);

  const [events, setEvents] = useState<ContainerEventRow[]>([]);
  const [eventsError, setEventsError] = useState<string | null>(null);

  const [pending, setPending] = useState<Pending>(null);

  // ------------------------------------------------------------------
  // Logs poller — 5 s tick when the container exists.
  // ------------------------------------------------------------------
  useEffect(() => {
    if (!pair.container_id) {
      setLogs("");
      return;
    }
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;

    const tick = async () => {
      try {
        const body = await getPairContainerLogs(pair.id, 200);
        if (!cancelled) {
          setLogs(body);
          setLogsError(null);
        }
      } catch (err) {
        if (!cancelled) {
          setLogsError(
            err instanceof ApiError ? `Logs: HTTP ${err.status}` : "Logs: red",
          );
        }
      } finally {
        if (!cancelled) {
          timer = setTimeout(tick, LOGS_REFRESH_MS);
        }
      }
    };
    void tick();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [pair.id, pair.container_id]);

  // ------------------------------------------------------------------
  // Events feed poller — slower tick.
  // ------------------------------------------------------------------
  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;

    const tick = async () => {
      try {
        const resp = await listPairContainerEvents(pair.id, { limit: 20 });
        if (!cancelled) {
          setEvents(resp.items);
          setEventsError(null);
        }
      } catch (err) {
        if (!cancelled) {
          setEventsError(
            err instanceof ApiError
              ? `Eventos: HTTP ${err.status}`
              : "Eventos: red",
          );
        }
      } finally {
        if (!cancelled) {
          timer = setTimeout(tick, EVENTS_REFRESH_MS);
        }
      }
    };
    void tick();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [pair.id]);

  // ------------------------------------------------------------------
  // Dockerfile preview
  // ------------------------------------------------------------------
  const openPreview = useCallback(async () => {
    setPreviewOpen(true);
    setPreviewLoading(true);
    setPreviewError(null);
    setPreviewText(null);
    try {
      const text = await previewDockerfile(pair.id);
      setPreviewText(text);
    } catch (err) {
      if (err instanceof ApiError && err.status === 422) {
        const detail =
          typeof err.body === "object" && err.body !== null
            ? (err.body as { detail?: { field?: string; value?: string } }).detail
            : null;
        setPreviewError(
          detail?.field
            ? `Campo "${detail.field}" contiene caracteres no permitidos`
            : "Validación 422",
        );
      } else if (err instanceof ApiError) {
        setPreviewError(`Error HTTP ${err.status}`);
      } else {
        setPreviewError("Error de red");
      }
    } finally {
      setPreviewLoading(false);
    }
  }, [pair.id]);

  // ------------------------------------------------------------------
  // Lifecycle button handlers — each one wraps a docker_control call
  // and surfaces toast errors. Enable/disable is computed inline below.
  // ------------------------------------------------------------------
  async function runOp<T>(
    op: Pending,
    fn: () => Promise<T>,
    okMessage: string,
  ): Promise<void> {
    setPending(op);
    try {
      await fn();
      toast.success(okMessage);
      // Refresh events feed so the new audit row appears immediately.
      try {
        const resp = await listPairContainerEvents(pair.id, { limit: 20 });
        setEvents(resp.items);
      } catch {
        // best-effort — the poller will catch up.
      }
      onPairUpdated?.(pair);
    } catch (err) {
      if (err instanceof ApiError) {
        const detail =
          typeof err.body === "object" && err.body !== null
            ? (err.body as { detail?: unknown }).detail
            : null;
        const code =
          detail && typeof detail === "object" && "code" in detail
            ? String((detail as { code: unknown }).code)
            : null;
        if (code === "invalid_transition") {
          toast.error("Transición no permitida en el estado actual");
        } else if (code === "docker_error") {
          toast.error("Docker rechazó la operación");
        } else if (code === "unsafe_value") {
          toast.error("Valor con caracteres no permitidos");
        } else {
          toast.error(`Error HTTP ${err.status}`);
        }
      } else {
        toast.error("Error de red");
      }
    } finally {
      setPending(null);
    }
  }

  const hasContainer = Boolean(pair.container_id);
  const isActive = pair.status === "active";
  const isPaused = pair.status === "paused";
  const disabled = pending !== null;

  return (
    <section className="flex flex-col gap-6">
      {/* Status panel ------------------------------------------------- */}
      <div className="rounded-md border border-[rgb(var(--border))] bg-[rgb(var(--background-elevated))] p-4">
        <h3 className="text-sm font-semibold">Infraestructura</h3>
        <dl className="mt-3 grid grid-cols-1 gap-2 text-sm sm:grid-cols-2">
          <Row label="Container ID" value={pair.container_id ?? "—"} mono />
          <Row label="Container Name" value={pair.container_name ?? "—"} mono />
          <Row label="Imagen Docker" value={pair.docker_image ?? "—"} mono />
          <Row label="MCP URL" value={pair.mcp_url} mono />
          <Row
            label="MCP Port"
            value={pair.mcp_port !== null ? String(pair.mcp_port) : "—"}
          />
          <Row label="Estado" value={pair.status} />
        </dl>
      </div>

      {/* Actions ----------------------------------------------------- */}
      <div className="flex flex-wrap items-center gap-2">
        <Button
          size="sm"
          variant="outline"
          onClick={() => void openPreview()}
          disabled={disabled}
        >
          Generar Dockerfile
        </Button>

        <Button
          size="sm"
          onClick={() =>
            void runOp("build", () => buildPairImage(pair.id), "Imagen construida")
          }
          disabled={disabled}
        >
          {pending === "build" ? "Construyendo…" : "Build"}
        </Button>

        <Button
          size="sm"
          variant="outline"
          onClick={() =>
            void runOp(
              "create",
              () => createPairContainer(pair.id),
              "Container creado",
            )
          }
          disabled={disabled || hasContainer}
          title={
            hasContainer
              ? "Container ya existe; usa Recrear para reemplazarlo"
              : "Crear container desde la imagen construida"
          }
        >
          Crear container
        </Button>

        <Button
          size="sm"
          onClick={() =>
            void runOp(
              "start",
              () => startPairContainer(pair.id),
              "Container iniciado",
            )
          }
          disabled={disabled || !hasContainer || isActive}
        >
          Iniciar
        </Button>

        <Button
          size="sm"
          variant="outline"
          onClick={() =>
            void runOp(
              "pause",
              () => pausePairContainer(pair.id),
              "Container pausado",
            )
          }
          disabled={disabled || !isActive}
        >
          Pausar
        </Button>

        <Button
          size="sm"
          variant="outline"
          onClick={() =>
            void runOp(
              "stop",
              () => stopPairContainer(pair.id),
              "Container detenido",
            )
          }
          disabled={disabled || (!isActive && !isPaused)}
        >
          Detener
        </Button>

        <Button
          size="sm"
          variant="outline"
          onClick={() =>
            void runOp(
              "recreate",
              () => recreatePairContainer(pair.id),
              "Container recreado",
            )
          }
          disabled={disabled || !hasContainer}
        >
          Recrear
        </Button>

        <Button
          size="sm"
          variant="destructive"
          onClick={() =>
            void runOp(
              "remove",
              () => removePairContainer(pair.id),
              "Container eliminado",
            )
          }
          disabled={disabled || !hasContainer}
        >
          Eliminar container
        </Button>
      </div>

      {/* Logs -------------------------------------------------------- */}
      <div className="rounded-md border border-[rgb(var(--border))] bg-[rgb(var(--background-elevated))] p-4">
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-semibold">Logs (tail=200)</h3>
          {logsError && (
            <span className="text-xs text-[rgb(var(--danger))]">{logsError}</span>
          )}
        </div>
        <pre className="mt-3 max-h-64 overflow-auto whitespace-pre-wrap rounded bg-[rgb(var(--background))] p-3 text-xs">
          {hasContainer
            ? logs || "Esperando…"
            : "El proyecto aún no tiene un container."}
        </pre>
      </div>

      {/* Events feed ------------------------------------------------- */}
      <div className="rounded-md border border-[rgb(var(--border))] bg-[rgb(var(--background-elevated))] p-4">
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-semibold">Eventos recientes</h3>
          {eventsError && (
            <span className="text-xs text-[rgb(var(--danger))]">{eventsError}</span>
          )}
        </div>
        <ul className="mt-3 flex flex-col gap-2 text-xs">
          {events.length === 0 && (
            <li className="text-[rgb(var(--foreground-muted))]">
              Sin eventos registrados.
            </li>
          )}
          {events.map((row) => (
            <li
              key={row.id}
              className="flex flex-col gap-1 rounded border border-[rgb(var(--border))] p-2"
            >
              <span className="font-mono">
                {row.created_at?.slice(0, 19).replace("T", " ") ?? "—"} ·{" "}
                {row.action} · {row.status}
              </span>
              {row.error && (
                <span className="text-[rgb(var(--danger))]">{row.error}</span>
              )}
            </li>
          ))}
        </ul>
      </div>

      {/* Dockerfile preview modal ----------------------------------- */}
      <Dialog open={previewOpen} onOpenChange={setPreviewOpen}>
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle>Dockerfile generado</DialogTitle>
          </DialogHeader>
          {previewLoading && <p className="text-sm">Renderizando…</p>}
          {previewError && (
            <p className="text-sm text-[rgb(var(--danger))]">{previewError}</p>
          )}
          {previewText && (
            <pre className="max-h-96 overflow-auto whitespace-pre rounded bg-[rgb(var(--background))] p-3 text-xs">
              {previewText}
            </pre>
          )}
        </DialogContent>
      </Dialog>
    </section>
  );
}

function Row({
  label,
  value,
  mono,
}: {
  label: string;
  value: string;
  mono?: boolean;
}): React.JSX.Element {
  return (
    <div className="flex flex-col">
      <dt className="text-xs uppercase tracking-wider text-[rgb(var(--foreground-muted))]">
        {label}
      </dt>
      <dd className={mono ? "font-mono text-xs" : "text-sm"}>{value}</dd>
    </div>
  );
}
