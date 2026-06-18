"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import {
  ArrowLeft,
  Save,
  Archive,
  Trash2,
  Plus,
  X,
  Link2,
  Wand2,
} from "lucide-react";
import { toast } from "sonner";

import { ApiError } from "@/lib/api";
import {
  archiveAgent,
  deleteAgent,
  getAgent,
  patchAgent,
  AGENT_TYPES,
  AGENT_TYPE_LABEL,
  AGENT_TYPE_DEFAULT_ENTRYPOINT,
  type AgentDetail,
  type AgentType,
} from "@/lib/agents";
import {
  attachSkillToAgent,
  detachSkillFromAgent,
  listAgentSkills,
  type AttachedSkill,
} from "@/lib/agent-skills";
import {
  SKILL_RUNTIME_LABEL,
  SKILL_TYPE_LABEL,
  listSkills,
  type SkillSummary,
} from "@/lib/skills";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { CodeMirrorEditor } from "@/components/CodeMirrorEditor";
import { Mql5TranslatorDialog } from "@/components/agents/Mql5TranslatorDialog";

export default function AgentDetailPage(): React.JSX.Element {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const id = params?.id;

  const [agent, setAgent] = useState<AgentDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  // Mutable form fields. We keep them separate from `agent` so the dirty
  // detection is a simple equality check.
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [entrypoint, setEntrypoint] = useState("");
  const [type, setType] = useState<AgentType>("worker");
  const [logica, setLogica] = useState("");
  const [saving, setSaving] = useState(false);
  // MQL5→Py translator modal — local UI state only. The modal owns
  // the MQL5 input; this page only cares about the resulting Python.
  const [translatorOpen, setTranslatorOpen] = useState(false);

  const hydrate = useCallback((row: AgentDetail) => {
    setAgent(row);
    setName(row.name);
    setDescription(row.description ?? "");
    setEntrypoint(row.entrypoint ?? "");
    setType(row.type);
    setLogica(row.logica);
  }, []);

  // ``refreshNonce`` is bumped to force a re-fetch without calling
  // setState from inside the effect body (lint: set-state-in-effect).
  const [refreshNonce, setRefreshNonce] = useState(0);

  useEffect(() => {
    if (!id) return;
    let cancelled = false;
    const load = async (): Promise<void> => {
      try {
        const row = await getAgent(id);
        if (cancelled) return;
        hydrate(row);
        setError(null);
      } catch (err) {
        if (cancelled) return;
        if (err instanceof ApiError && err.status === 404) {
          setError("Agente no encontrado");
        } else {
          setError("Error al cargar el agente");
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    void load();
    return () => {
      cancelled = true;
    };
  }, [id, hydrate, refreshNonce]);

  function refresh(): void {
    setLoading(true);
    setRefreshNonce((n) => n + 1);
  }

  const dirty = Boolean(
    agent &&
      (name !== agent.name ||
        (description || null) !== (agent.description || null) ||
        (entrypoint || null) !== (agent.entrypoint || null) ||
        type !== agent.type ||
        logica !== agent.logica),
  );

  // Warn on navigation while dirty — operator must save or discard.
  useEffect(() => {
    if (!dirty) return;
    function onBeforeUnload(e: BeforeUnloadEvent): void {
      e.preventDefault();
      e.returnValue = "";
    }
    window.addEventListener("beforeunload", onBeforeUnload);
    return () => window.removeEventListener("beforeunload", onBeforeUnload);
  }, [dirty]);

  async function save(): Promise<void> {
    if (!agent || !id) return;
    setSaving(true);
    try {
      const updated = await patchAgent(id, {
        name: name.trim() !== agent.name ? name.trim() : undefined,
        description:
          (description || null) !== (agent.description || null)
            ? description || null
            : undefined,
        entrypoint:
          (entrypoint || null) !== (agent.entrypoint || null)
            ? entrypoint || null
            : undefined,
        type: type !== agent.type ? type : undefined,
        logica: logica !== agent.logica ? logica : undefined,
        updated_at: agent.updated_at ?? "",
      });
      hydrate(updated);
      const warningMsg =
        updated.warnings && updated.warnings.length > 0
          ? ` (${updated.warnings.length} aviso${updated.warnings.length === 1 ? "" : "s"})`
          : "";
      toast.success(`Guardado v${updated.version}${warningMsg}`);
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        toast.error("Otro cliente modificó el agente. Recarga para continuar.");
      } else if (err instanceof ApiError && err.status === 422) {
        const detail = (err.body as { detail?: { message?: string; line?: number; col?: number } })
          ?.detail;
        if (detail?.message) {
          toast.error(
            `Error de sintaxis en línea ${detail.line ?? "?"}: ${detail.message}`,
          );
        } else {
          toast.error("Datos inválidos");
        }
      } else {
        toast.error("No se pudo guardar el agente");
      }
    } finally {
      setSaving(false);
    }
  }

  async function runArchive(): Promise<void> {
    if (!agent) return;
    try {
      await archiveAgent(agent.id);
      toast.success("Agente archivado");
      refresh();
    } catch {
      toast.error("No se pudo archivar el agente");
    }
  }

  async function runDelete(): Promise<void> {
    if (!agent) return;
    if (!confirm(`Eliminar el agente "${agent.name}" definitivamente?`)) return;
    try {
      await deleteAgent(agent.id);
      toast.success("Agente eliminado");
      router.push("/agentes");
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        toast.error("El agente está referenciado por pares.");
      } else {
        toast.error("No se pudo eliminar el agente");
      }
    }
  }

  if (loading) {
    return (
      <section className="flex h-full items-center justify-center">
        <span className="text-sm text-[rgb(var(--foreground-muted))]">
          Cargando…
        </span>
      </section>
    );
  }

  if (error || !agent) {
    return (
      <section className="flex flex-col items-start gap-3">
        <Button variant="outline" size="sm" onClick={() => router.push("/agentes")}>
          <ArrowLeft className="h-4 w-4" /> Volver
        </Button>
        <p role="alert" className="text-sm text-[rgb(var(--danger))]">
          {error ?? "Agente no disponible"}
        </p>
      </section>
    );
  }

  return (
    <section className="flex h-full flex-col gap-4">
      <header className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={() => router.push("/agentes")}
            aria-label="Volver a la lista"
          >
            <ArrowLeft className="h-4 w-4" />
          </Button>
          <h1 className="text-2xl font-semibold tracking-tight">{agent.name}</h1>
          <Badge variant={agent.is_active ? "success" : "muted"}>
            {agent.is_active ? "Activo" : "Archivado"}
          </Badge>
          <Badge variant="accent">v{agent.version}</Badge>
        </div>
        <div className="flex items-center gap-2">
          {agent.is_active && (
            <Button variant="outline" size="sm" onClick={() => void runArchive()}>
              <Archive className="h-4 w-4" /> Archivar
            </Button>
          )}
          <Button variant="outline" size="sm" onClick={() => void runDelete()}>
            <Trash2 className="h-4 w-4" /> Eliminar
          </Button>
          <Button
            size="sm"
            onClick={() => void save()}
            disabled={!dirty || saving}
            data-testid="save-button"
          >
            <Save className="h-4 w-4" />
            {saving ? "Guardando…" : "Guardar"}
          </Button>
        </div>
      </header>

      {agent.warnings.length > 0 && (
        <div
          role="status"
          className="rounded-md border border-[rgb(var(--warning)/0.4)] bg-[rgb(var(--warning)/0.1)] p-3 text-sm text-[rgb(var(--warning))]"
        >
          <p className="font-medium">Avisos</p>
          <ul className="mt-1 list-disc pl-5">
            {agent.warnings.map((w, i) => (
              <li key={i}>{w}</li>
            ))}
          </ul>
        </div>
      )}

      <div className="grid flex-1 grid-cols-1 gap-4 lg:grid-cols-[minmax(0,360px)_minmax(0,1fr)]">
        {/* Left: metadata form */}
        <div className="flex flex-col gap-3 rounded-md border border-[rgb(var(--border))] bg-[rgb(var(--background-elevated))] p-4">
          <div className="grid gap-1.5">
            <Label htmlFor="agent-name">Nombre</Label>
            <Input
              id="agent-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              maxLength={100}
              required
            />
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="agent-type">Tipo</Label>
            <Select
              id="agent-type"
              value={type}
              onChange={(e) => setType(e.target.value as AgentType)}
            >
              {AGENT_TYPES.map((kind) => (
                <option key={kind} value={kind}>
                  {AGENT_TYPE_LABEL[kind]}
                </option>
              ))}
            </Select>
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="agent-entrypoint">Entrypoint</Label>
            <Input
              id="agent-entrypoint"
              value={entrypoint}
              onChange={(e) => setEntrypoint(e.target.value)}
              placeholder={AGENT_TYPE_DEFAULT_ENTRYPOINT[type]}
              maxLength={120}
            />
            <p className="text-xs text-[rgb(var(--foreground-muted))]">
              Convención para {AGENT_TYPE_LABEL[type]}:{" "}
              <code>{AGENT_TYPE_DEFAULT_ENTRYPOINT[type]}</code>
            </p>
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="agent-description">Descripción</Label>
            <Textarea
              id="agent-description"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              maxLength={4000}
              rows={4}
            />
          </div>
          <Separator />
          <dl className="grid grid-cols-2 gap-y-1 text-xs">
            <dt className="text-[rgb(var(--foreground-muted))]">Creado</dt>
            <dd>{agent.created_at ?? "—"}</dd>
            <dt className="text-[rgb(var(--foreground-muted))]">Modificado</dt>
            <dd>{agent.updated_at ?? "—"}</dd>
          </dl>
        </div>

        {/* Right: code editor, full height */}
        <div className="flex flex-col overflow-hidden rounded-md border border-[rgb(var(--border))] bg-[rgb(var(--background))]">
          <div className="flex items-center justify-between border-b border-[rgb(var(--border))] bg-[rgb(var(--background-elevated))] px-3 py-2 text-xs text-[rgb(var(--foreground-muted))]">
            <span>logica.py</span>
            <div className="flex items-center gap-3">
              <span>{logica.length} chars</span>
              <Button
                variant="outline"
                size="sm"
                onClick={() => setTranslatorOpen(true)}
                data-testid="open-mql5-translator-button"
                aria-label="Abrir traductor MQL5 → Python"
              >
                <Wand2 className="h-4 w-4" /> MQL5 → Py
              </Button>
            </div>
          </div>
          <div className="min-h-[420px] flex-1">
            <CodeMirrorEditor
              value={logica}
              onChange={setLogica}
              language="python"
              aria-label="Editor de lógica del agente"
              data-testid="logica-editor"
            />
          </div>
        </div>
      </div>

      {/* Skills binding panel */}
      <AgentSkillsPanel agentId={agent.id} />

      {/* MQL5 → Python translator modal. The MQL5 input lives inside
          the dialog only; on Aplicar the resulting Python replaces
          the local `logica` state (it still requires Guardar to land
          in the DB — no MQL5 is ever sent to the agents endpoint). */}
      <Mql5TranslatorDialog
        open={translatorOpen}
        onOpenChange={setTranslatorOpen}
        targetEntrypoint={entrypoint || AGENT_TYPE_DEFAULT_ENTRYPOINT[type]}
        onApply={(python) => {
          setLogica(python);
          toast.success("Traducción aplicada. Revisa antes de guardar.");
        }}
      />
    </section>
  );
}

// ---------------------------------------------------------------------------
// Skills panel — list + attach/detach against /api/agents/{id}/skills.
// ---------------------------------------------------------------------------

interface AgentSkillsPanelProps {
  agentId: string;
}

function AgentSkillsPanel({
  agentId,
}: AgentSkillsPanelProps): React.JSX.Element {
  const [attached, setAttached] = useState<AttachedSkill[]>([]);
  const [loading, setLoading] = useState(true);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [refreshNonce, setRefreshNonce] = useState(0);
  // Confirm modal antes de desvincular — el botón X de cada pill no
  // ejecuta la acción directamente; abre este modal con el nombre de la
  // skill para evitar clicks accidentales.
  const [confirmingDetach, setConfirmingDetach] = useState<{
    skillId: string;
    skillName: string;
  } | null>(null);

  useEffect(() => {
    let cancelled = false;
    const load = async (): Promise<void> => {
      try {
        const rows = await listAgentSkills(agentId);
        if (cancelled) return;
        setAttached(rows);
      } catch (err) {
        if (cancelled) return;
        if (err instanceof ApiError && err.status === 404) {
          // Agent went away — UI parent will handle the empty state.
          setAttached([]);
        } else {
          toast.error("Error al cargar skills del agente");
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    void load();
    return () => {
      cancelled = true;
    };
  }, [agentId, refreshNonce]);

  function refresh(): void {
    setLoading(true);
    setRefreshNonce((n) => n + 1);
  }

  async function detach(skillId: string): Promise<void> {
    try {
      await detachSkillFromAgent(agentId, skillId);
      toast.success("Skill desvinculada");
      refresh();
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) {
        toast.error("La skill ya no estaba vinculada");
        refresh();
      } else {
        toast.error("No se pudo desvincular la skill");
      }
    }
  }

  return (
    <div className="flex flex-col gap-3 rounded-md border border-[rgb(var(--border))] bg-[rgb(var(--background-elevated))] p-4">
      <header className="flex items-center justify-between">
        <div>
          <h2 className="text-base font-semibold tracking-tight">Skills</h2>
          <p className="text-xs text-[rgb(var(--foreground-muted))]">
            Skills vinculadas a este agente vía la tabla{" "}
            <code>agent_skills</code>.
          </p>
        </div>
        <Button
          size="sm"
          onClick={() => setPickerOpen(true)}
          data-testid="attach-skill-button"
        >
          <Plus className="h-4 w-4" /> Adjuntar skill
        </Button>
      </header>

      {loading ? (
        <p className="text-xs text-[rgb(var(--foreground-muted))]">Cargando…</p>
      ) : attached.length === 0 ? (
        <p className="text-xs text-[rgb(var(--foreground-muted))]">
          Aún no hay skills vinculadas a este agente.
        </p>
      ) : (
        // Tarjeta-pill compacta por skill: flujo horizontal con wrap, así
        // cada skill ocupa solo lo que necesita y caben varias por línea.
        // Las notes (si existen) se muestran como tooltip nativo.
        <ul
          className="flex flex-wrap gap-2"
          data-testid="attached-skills-list"
        >
          {attached.map((row) => (
            <li
              key={row.binding_id}
              title={row.notes ?? undefined}
              className="inline-flex items-center gap-2 rounded-md border border-[rgb(var(--border))] bg-[rgb(var(--background))] py-1 pl-2.5 pr-1"
            >
              <Link2 className="h-3.5 w-3.5 text-[rgb(var(--foreground-muted))]" />
              <span className="text-sm font-medium">{row.name}</span>
              <Badge variant="muted">{SKILL_TYPE_LABEL[row.type]}</Badge>
              <Badge variant="muted">{SKILL_RUNTIME_LABEL[row.runtime]}</Badge>
              {!row.is_active && <Badge variant="muted">Archivada</Badge>}
              {row.notes && (
                <span
                  aria-hidden
                  className="text-[10px] uppercase tracking-wide text-[rgb(var(--foreground-muted))]"
                >
                  ··
                </span>
              )}
              <button
                type="button"
                onClick={() =>
                  setConfirmingDetach({
                    skillId: row.skill_id,
                    skillName: row.name,
                  })
                }
                aria-label={`Quitar skill ${row.name}`}
                data-testid="detach-skill-button"
                className="rounded-md p-1 text-[rgb(var(--foreground-muted))] transition-colors hover:bg-[rgb(var(--danger)/0.12)] hover:text-[rgb(var(--danger))] focus:outline-none focus-visible:ring-2 focus-visible:ring-[rgb(var(--danger))]"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            </li>
          ))}
        </ul>
      )}

      <AttachSkillDialog
        open={pickerOpen}
        onOpenChange={setPickerOpen}
        agentId={agentId}
        attachedSkillIds={attached.map((r) => r.skill_id)}
        onAttached={() => {
          refresh();
        }}
      />

      {/* Modal de confirmación al desvincular una skill. Patrón usado
          en EditSkillDialog/ProjectAgentsPanel — coherencia visual. */}
      <Dialog
        open={confirmingDetach !== null}
        onOpenChange={(next) => {
          if (!next) setConfirmingDetach(null);
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>¿Quitar la skill del agente?</DialogTitle>
            <DialogDescription>
              {confirmingDetach
                ? `Vas a desvincular "${confirmingDetach.skillName}" de este agente. La skill no se elimina del catálogo, solo se quita esta asignación.`
                : ""}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setConfirmingDetach(null)}
              data-testid="cancel-detach-skill"
            >
              Cancelar
            </Button>
            <Button
              onClick={() => {
                if (confirmingDetach === null) return;
                const id = confirmingDetach.skillId;
                setConfirmingDetach(null);
                void detach(id);
              }}
              data-testid="confirm-detach-skill"
            >
              <X className="h-4 w-4" /> Quitar
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

interface AttachSkillDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  agentId: string;
  attachedSkillIds: string[];
  onAttached: () => void;
}

function AttachSkillDialog({
  open,
  onOpenChange,
  agentId,
  attachedSkillIds,
  onAttached,
}: AttachSkillDialogProps): React.JSX.Element {
  const [allSkills, setAllSkills] = useState<SkillSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedSkillId, setSelectedSkillId] = useState<string>("");
  const [notes, setNotes] = useState("");
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    const load = async (): Promise<void> => {
      setLoading(true);
      try {
        const rows = await listSkills();
        if (cancelled) return;
        setAllSkills(rows);
      } catch {
        if (cancelled) return;
        toast.error("Error al cargar el catálogo de skills");
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    void load();
    return () => {
      cancelled = true;
    };
  }, [open]);

  const available = useMemo(
    () =>
      allSkills.filter(
        (s) => s.is_active && !attachedSkillIds.includes(s.id),
      ),
    [allSkills, attachedSkillIds],
  );

  async function submit(): Promise<void> {
    if (!selectedSkillId) return;
    setSubmitting(true);
    try {
      await attachSkillToAgent(agentId, {
        skill_id: selectedSkillId,
        notes: notes.trim() || undefined,
      });
      toast.success("Skill vinculada");
      onAttached();
      setSelectedSkillId("");
      setNotes("");
      onOpenChange(false);
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        toast.error("La skill ya está vinculada a este agente.");
      } else if (err instanceof ApiError && err.status === 404) {
        toast.error("Skill o agente no disponible.");
      } else {
        toast.error("No se pudo vincular la skill");
      }
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>Adjuntar skill al agente</DialogTitle>
          <DialogDescription>
            Selecciona una skill del catálogo y, opcionalmente, añade una nota
            con el contexto en el que el agente la usará.
          </DialogDescription>
        </DialogHeader>
        <div className="grid gap-3">
          <div className="grid gap-1.5">
            <Label htmlFor="picker-skill">Skill</Label>
            {loading ? (
              <p className="text-xs text-[rgb(var(--foreground-muted))]">
                Cargando catálogo…
              </p>
            ) : available.length === 0 ? (
              <p className="text-xs text-[rgb(var(--foreground-muted))]">
                No quedan skills disponibles para vincular.
              </p>
            ) : (
              <Select
                id="picker-skill"
                value={selectedSkillId}
                onChange={(e) => setSelectedSkillId(e.target.value)}
                data-testid="skill-picker"
              >
                <option value="">Selecciona una skill…</option>
                {available.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.name} — {SKILL_TYPE_LABEL[s.type]} ·{" "}
                    {SKILL_RUNTIME_LABEL[s.runtime]}
                  </option>
                ))}
              </Select>
            )}
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="picker-notes">Notas (opcional)</Label>
            <Textarea
              id="picker-notes"
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              maxLength={4000}
              rows={3}
              placeholder="ej. usar en ventanas de baja volatilidad"
            />
          </div>
        </div>
        <DialogFooter>
          <Button
            variant="outline"
            onClick={() => onOpenChange(false)}
            disabled={submitting}
          >
            Cancelar
          </Button>
          <Button
            onClick={() => void submit()}
            disabled={!selectedSkillId || submitting}
            data-testid="confirm-attach-button"
          >
            {submitting ? "Vinculando…" : "Adjuntar"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
