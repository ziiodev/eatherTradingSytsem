"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import {
  Bot,
  Crown,
  GraduationCap,
  Pencil,
  Search,
  Shield,
  Target,
} from "lucide-react";
import { toast } from "sonner";

import { ApiError } from "@/lib/api";
import {
  listAgents,
  type AgentSummary,
  type AgentType,
} from "@/lib/agents";
import {
  patchProject,
  type ProjectDetail,
} from "@/lib/projects";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

/**
 * ProjectAgentsPanel — visible on the project detail page. Renders the
 * six slot bindings (Orquestador / Investigador / Marker / Worker /
 * Tutor / Auditor) and a "Cambiar" action that opens a Dialog with six
 * native selects to update them.
 *
 * Charter corrections:
 *   * Migration 0010 — the Orquestador is a first-class agent slot
 *     like the others.
 *   * Migration 0012 — added Marker (market-signal) and Tutor (Sleep
 *     Phase) as first-class slots.
 *
 * Order convention mirrors the charter prose "supervisor → research
 * news → market signal → execute → sleep/teach → audit".
 *
 * Multi-tenancy: the backend filters /api/agents by current_user.id, so
 * the picker never sees other tenants' rows. We do NOT layer a client
 * filter on top of that.
 *
 * Charter: a project has at most one agent of each type. The backend
 * doesn't enforce "no duplicate id across slots" — the dialog flags it
 * client-side via a warning.
 */

export interface ProjectAgentsPanelProps {
  project: ProjectDetail;
  onProjectUpdated: (next: ProjectDetail) => void;
}

interface SlotConfig {
  key:
    | "orchestrator_agent_id"
    | "investigator_agent_id"
    | "marker_agent_id"
    | "worker_agent_id"
    | "tutor_agent_id"
    | "auditor_agent_id";
  type: AgentType;
  label: string;
  description: string;
  icon: React.ComponentType<{ className?: string }>;
}

// Icon choices (migration 0012):
//   * Marker → Target — bullseye = "give the signal".
//   * Tutor  → GraduationCap — teaching/learning, the Sleep Phase role.
const SLOTS: ReadonlyArray<SlotConfig> = [
  {
    key: "orchestrator_agent_id",
    type: "orchestrator",
    label: "Orquestador",
    description: "Supervisor del proyecto — decide qué agente dispara.",
    icon: Crown,
  },
  {
    key: "investigator_agent_id",
    type: "investigator",
    label: "Investigador",
    description: "Lee y resume todas las noticias relevantes.",
    icon: Search,
  },
  {
    key: "marker_agent_id",
    type: "marker",
    label: "Marker",
    description: "Da la señal del mercado y la opción a poner en marcha.",
    icon: Target,
  },
  {
    key: "worker_agent_id",
    type: "worker",
    label: "Worker",
    description: "Ejecuta órdenes contra MT5 vía MCP.",
    icon: Bot,
  },
  {
    key: "tutor_agent_id",
    type: "tutor",
    label: "Tutor",
    description: "Conduce la Fase de Sueño y orquesta el aprendizaje.",
    icon: GraduationCap,
  },
  {
    key: "auditor_agent_id",
    type: "auditor",
    label: "Auditor",
    description: "Analiza la operativa, q-table y los informes de MT5.",
    icon: Shield,
  },
];

export function ProjectAgentsPanel({
  project,
  onProjectUpdated,
}: ProjectAgentsPanelProps): React.JSX.Element {
  const [dialogOpen, setDialogOpen] = useState(false);

  // Per-slot agent maps for label lookup. The picker re-fetches lists
  // when the dialog opens; here we just want id → name resolution for
  // the row display.
  const [agentLookup, setAgentLookup] = useState<
    Record<string, AgentSummary | undefined>
  >({});

  useEffect(() => {
    let cancelled = false;
    const load = async (): Promise<void> => {
      const ids = [
        project.orchestrator_agent_id,
        project.investigator_agent_id,
        project.marker_agent_id,
        project.worker_agent_id,
        project.tutor_agent_id,
        project.auditor_agent_id,
      ].filter((x): x is string => Boolean(x));
      if (ids.length === 0) {
        if (!cancelled) setAgentLookup({});
        return;
      }
      // We can't query by id list (no endpoint), so we fetch by type and
      // merge. One fetch per type that's referenced.
      const types = new Set<AgentType>();
      if (project.orchestrator_agent_id) types.add("orchestrator");
      if (project.investigator_agent_id) types.add("investigator");
      if (project.marker_agent_id) types.add("marker");
      if (project.worker_agent_id) types.add("worker");
      if (project.tutor_agent_id) types.add("tutor");
      if (project.auditor_agent_id) types.add("auditor");
      try {
        const lists = await Promise.all(
          [...types].map((t) => listAgents({ type: t })),
        );
        if (cancelled) return;
        const map: Record<string, AgentSummary> = {};
        for (const list of lists) {
          for (const a of list) {
            map[a.id] = a;
          }
        }
        setAgentLookup(map);
      } catch {
        // Non-fatal — the row will fall back to showing the bare UUID.
        if (!cancelled) setAgentLookup({});
      }
    };
    void load();
    return () => {
      cancelled = true;
    };
  }, [
    project.orchestrator_agent_id,
    project.investigator_agent_id,
    project.marker_agent_id,
    project.worker_agent_id,
    project.tutor_agent_id,
    project.auditor_agent_id,
  ]);

  return (
    <div
      className="flex flex-col gap-3 rounded-md border border-[rgb(var(--border))] bg-[rgb(var(--background-elevated))] p-4"
      data-testid="project-agents-panel"
    >
      <header className="flex items-center justify-between">
        <div>
          <h2 className="text-base font-semibold tracking-tight">Agentes</h2>
          <p className="text-xs text-[rgb(var(--foreground-muted))]">
            Vínculos del proyecto a las definiciones de agente
            (Orquestador / Investigador / Marker / Worker / Tutor /
            Auditor).
          </p>
        </div>
        <Button
          size="sm"
          onClick={() => setDialogOpen(true)}
          data-testid="open-agent-binding-dialog"
        >
          <Pencil className="h-4 w-4" /> Editar asignaciones
        </Button>
      </header>

      {/* Six slot cards. Breakpoint choice (migration 0012):
            * mobile           → 1 column
            * md (≥ 768px)     → 2 columns
            * xl (≥ 1280px)    → 3 columns
            * 2xl (≥ 1536px)   → 6 columns (single row on wide displays)
          Three columns at ``xl`` keeps the cards readable on a typical
          14-16" laptop; six-up only kicks in on truly wide screens. */}
      <ul
        className="grid grid-cols-1 gap-2 md:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-6"
        data-testid="project-agents-list"
      >
        {SLOTS.map((slot) => {
          const agentId = project[slot.key];
          const agent = agentId ? agentLookup[agentId] : undefined;
          const Icon = slot.icon;
          return (
            <li key={slot.key}>
              <button
                type="button"
                onClick={() => setDialogOpen(true)}
                className="group flex h-full w-full flex-col gap-2 rounded-md border border-[rgb(var(--border))] bg-[rgb(var(--background))] p-3 text-left transition-colors hover:border-[rgb(var(--accent))] hover:bg-[rgb(var(--background-elevated))] focus:outline-none focus-visible:ring-2 focus-visible:ring-[rgb(var(--accent))]"
                data-testid={`project-agents-row-${slot.type}`}
                aria-label={`Editar asignación de ${slot.label}`}
              >
                <div className="flex items-center gap-2">
                  <Icon className="h-4 w-4 text-[rgb(var(--foreground-muted))] group-hover:text-[rgb(var(--accent))]" />
                  <span className="text-sm font-medium">{slot.label}</span>
                </div>
                {agentId ? (
                  agent ? (
                    <div className="flex flex-wrap items-center gap-1.5">
                      <span className="truncate text-sm font-medium text-[rgb(var(--foreground))]">
                        {agent.name}
                      </span>
                      <Badge variant="accent">v{agent.version}</Badge>
                      {!agent.is_active && (
                        <Badge variant="muted">Archivado</Badge>
                      )}
                    </div>
                  ) : (
                    <span className="text-xs text-[rgb(var(--foreground-muted))]">
                      {agentId.slice(0, 8)}…
                    </span>
                  )
                ) : (
                  <span className="text-xs text-[rgb(var(--foreground-muted))]">
                    No asignado
                  </span>
                )}
                <span className="mt-auto text-xs leading-snug text-[rgb(var(--foreground-muted))]">
                  {slot.description}
                </span>
              </button>
            </li>
          );
        })}
      </ul>

      {/* `key` forces a remount on every (re-)open so the dialog's
          internal useState initializers re-read fresh project values
          without needing a sync-from-props effect (which the lint rule
          `react-hooks/set-state-in-effect` rejects). The key includes
          the three slot ids so a successful save (which mutates the
          parent project) also re-seeds the dialog state if the user
          re-opens it. */}
      <EditAgentBindingsDialog
        key={`${dialogOpen ? "open" : "closed"}:${project.orchestrator_agent_id ?? ""}:${project.investigator_agent_id ?? ""}:${project.marker_agent_id ?? ""}:${project.worker_agent_id ?? ""}:${project.tutor_agent_id ?? ""}:${project.auditor_agent_id ?? ""}`}
        project={project}
        open={dialogOpen}
        onOpenChange={setDialogOpen}
        onSaved={onProjectUpdated}
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Dialog — six selects, prefilled with current project values. Saves
// via PATCH /api/projects/{id} with the six *_agent_id fields. Empty
// string maps to null on the way out.
// ---------------------------------------------------------------------------

interface EditAgentBindingsDialogProps {
  project: ProjectDetail;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSaved: (project: ProjectDetail) => void;
}

function EditAgentBindingsDialog({
  project,
  open,
  onOpenChange,
  onSaved,
}: EditAgentBindingsDialogProps): React.JSX.Element {
  const [orchestratorId, setOrchestratorId] = useState<string>(
    project.orchestrator_agent_id ?? "",
  );
  const [investigatorId, setInvestigatorId] = useState<string>(
    project.investigator_agent_id ?? "",
  );
  const [markerId, setMarkerId] = useState<string>(
    project.marker_agent_id ?? "",
  );
  const [workerId, setWorkerId] = useState<string>(
    project.worker_agent_id ?? "",
  );
  const [tutorId, setTutorId] = useState<string>(
    project.tutor_agent_id ?? "",
  );
  const [auditorId, setAuditorId] = useState<string>(
    project.auditor_agent_id ?? "",
  );

  const [orchestrators, setOrchestrators] = useState<AgentSummary[]>([]);
  const [investigators, setInvestigators] = useState<AgentSummary[]>([]);
  const [markers, setMarkers] = useState<AgentSummary[]>([]);
  const [workers, setWorkers] = useState<AgentSummary[]>([]);
  const [tutors, setTutors] = useState<AgentSummary[]>([]);
  const [auditors, setAuditors] = useState<AgentSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [discardOpen, setDiscardOpen] = useState(false);

  // NOTE: we don't run a sync-from-props effect here. Instead the
  // parent passes a `key` that forces a fresh mount whenever the
  // dialog opens or the underlying project slot ids change — which
  // means the useState initializers above always see fresh values.

  // Load six lists when the dialog opens.
  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    const load = async (): Promise<void> => {
      setLoading(true);
      try {
        const [o, i, m, w, t, a] = await Promise.all([
          listAgents({ type: "orchestrator" }),
          listAgents({ type: "investigator" }),
          listAgents({ type: "marker" }),
          listAgents({ type: "worker" }),
          listAgents({ type: "tutor" }),
          listAgents({ type: "auditor" }),
        ]);
        if (cancelled) return;
        setOrchestrators(o);
        setInvestigators(i);
        setMarkers(m);
        setWorkers(w);
        setTutors(t);
        setAuditors(a);
      } catch {
        if (cancelled) return;
        toast.error("Error al cargar agentes");
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    void load();
    return () => {
      cancelled = true;
    };
  }, [open]);

  const dirty =
    orchestratorId !== (project.orchestrator_agent_id ?? "") ||
    investigatorId !== (project.investigator_agent_id ?? "") ||
    markerId !== (project.marker_agent_id ?? "") ||
    workerId !== (project.worker_agent_id ?? "") ||
    tutorId !== (project.tutor_agent_id ?? "") ||
    auditorId !== (project.auditor_agent_id ?? "");

  const duplicateWarning = useMemo(
    () =>
      duplicateAgentWarning(
        orchestratorId,
        investigatorId,
        markerId,
        workerId,
        tutorId,
        auditorId,
      ),
    [orchestratorId, investigatorId, markerId, workerId, tutorId, auditorId],
  );

  function requestClose(): void {
    if (dirty) {
      setDiscardOpen(true);
      return;
    }
    onOpenChange(false);
  }

  function confirmDiscard(): void {
    setDiscardOpen(false);
    onOpenChange(false);
  }

  async function save(): Promise<void> {
    setSubmitting(true);
    try {
      const updated = await patchProject(project.id, {
        orchestrator_agent_id: orchestratorId || null,
        investigator_agent_id: investigatorId || null,
        marker_agent_id: markerId || null,
        worker_agent_id: workerId || null,
        tutor_agent_id: tutorId || null,
        auditor_agent_id: auditorId || null,
      });
      onSaved(updated);
      toast.success("Asignaciones guardadas");
      onOpenChange(false);
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        toast.error("Conflicto al guardar las asignaciones.");
      } else if (err instanceof ApiError && err.status === 422) {
        toast.error("Alguno de los agentes seleccionados ya no es válido.");
      } else {
        toast.error("No se pudieron guardar las asignaciones");
      }
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <>
      <Dialog open={open} onOpenChange={(next) => (next ? onOpenChange(true) : requestClose())}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle>Editar asignaciones de agentes</DialogTitle>
            <DialogDescription>
              Vincula el proyecto con una definición de Orquestador,
              Investigador, Marker, Worker, Tutor y/o Auditor.
            </DialogDescription>
          </DialogHeader>
          <div className="grid gap-3">
            <AgentDialogPicker
              id="dlg-orchestrator-agent"
              label="Orquestador"
              loading={loading}
              agents={orchestrators}
              value={orchestratorId}
              onChange={setOrchestratorId}
            />
            <AgentDialogPicker
              id="dlg-investigator-agent"
              label="Investigador"
              loading={loading}
              agents={investigators}
              value={investigatorId}
              onChange={setInvestigatorId}
            />
            <AgentDialogPicker
              id="dlg-marker-agent"
              label="Marker"
              loading={loading}
              agents={markers}
              value={markerId}
              onChange={setMarkerId}
            />
            <AgentDialogPicker
              id="dlg-worker-agent"
              label="Worker"
              loading={loading}
              agents={workers}
              value={workerId}
              onChange={setWorkerId}
            />
            <AgentDialogPicker
              id="dlg-tutor-agent"
              label="Tutor"
              loading={loading}
              agents={tutors}
              value={tutorId}
              onChange={setTutorId}
            />
            <AgentDialogPicker
              id="dlg-auditor-agent"
              label="Auditor"
              loading={loading}
              agents={auditors}
              value={auditorId}
              onChange={setAuditorId}
            />
            {duplicateWarning && (
              <p
                role="alert"
                className="rounded-md border border-[rgb(var(--warning)/0.4)] bg-[rgb(var(--warning)/0.1)] p-2 text-xs text-[rgb(var(--warning))]"
              >
                {duplicateWarning}
              </p>
            )}
          </div>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={requestClose}
              disabled={submitting}
            >
              Cancelar
            </Button>
            <Button
              onClick={() => void save()}
              disabled={!dirty || submitting}
              data-testid="confirm-save-agent-bindings"
            >
              {submitting ? "Guardando…" : "Guardar"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Discard-changes confirm modal. Mirrors the pattern used by
          EditSkillDialog: do not let the operator lose unsaved selections
          by accident. */}
      <Dialog open={discardOpen} onOpenChange={setDiscardOpen}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>¿Descartar cambios?</DialogTitle>
            <DialogDescription>
              Hay asignaciones modificadas sin guardar. Si cierras, se
              perderán.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDiscardOpen(false)}>
              Seguir editando
            </Button>
            <Button variant="destructive" onClick={confirmDiscard}>
              Descartar
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}

interface AgentDialogPickerProps {
  id: string;
  label: string;
  agents: AgentSummary[];
  value: string;
  loading: boolean;
  onChange: (value: string) => void;
}

function AgentDialogPicker({
  id,
  label,
  agents,
  value,
  loading,
  onChange,
}: AgentDialogPickerProps): React.JSX.Element {
  const activeCount = agents.filter((a) => a.is_active).length;
  return (
    <div className="grid gap-1.5">
      <Label htmlFor={id}>{label}</Label>
      {loading ? (
        <p className="text-xs text-[rgb(var(--foreground-muted))]">
          Cargando…
        </p>
      ) : (
        <Select
          id={id}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          data-testid={`${id}-select`}
        >
          <option value="">(sin asignar)</option>
          {agents.map((agent) => (
            <option key={agent.id} value={agent.id}>
              {agent.name} v{agent.version}
              {!agent.is_active ? " · Archivado" : ""}
            </option>
          ))}
        </Select>
      )}
      {!loading && activeCount === 0 && (
        <p className="text-xs text-[rgb(var(--foreground-muted))]">
          No tienes agentes {label.toLowerCase()} activos.{" "}
          <Link
            href="/agentes"
            className="text-[rgb(var(--accent))] underline-offset-2 hover:underline"
          >
            Crea uno en Agentes
          </Link>
          .
        </p>
      )}
    </div>
  );
}

/**
 * Shared helper — flag if the operator picked the same agent.id in
 * more than one slot. Returns null if all picks are distinct or empty.
 */
function duplicateAgentWarning(
  orchestratorId: string,
  investigatorId: string,
  markerId: string,
  workerId: string,
  tutorId: string,
  auditorId: string,
): string | null {
  const labels: Record<string, string[]> = {};
  const push = (id: string, label: string): void => {
    if (!id) return;
    labels[id] = labels[id] ?? [];
    labels[id].push(label);
  };
  push(orchestratorId, "Orquestador");
  push(investigatorId, "Investigador");
  push(markerId, "Marker");
  push(workerId, "Worker");
  push(tutorId, "Tutor");
  push(auditorId, "Auditor");
  for (const slots of Object.values(labels)) {
    if (slots.length > 1) {
      return `El mismo agente está asignado como ${slots.join(" y ")}. Revisa la asignación.`;
    }
  }
  return null;
}
