"use client";

import { useCallback, useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { ArrowLeft, Archive, Pencil, Save, Trash2, X } from "lucide-react";
import { toast } from "sonner";

import { ApiError } from "@/lib/api";
import {
  SKILL_RUNTIMES,
  SKILL_RUNTIME_LABEL,
  SKILL_TYPES,
  SKILL_TYPE_LABEL,
  archiveSkill,
  deleteSkill,
  getSkill,
  patchSkill,
  type SkillDetail,
  type SkillRuntime,
  type SkillType,
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
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { CodeMirrorEditor } from "@/components/CodeMirrorEditor";

/**
 * Skill detail view. The page itself is read-only: metadata on the left,
 * code/markdown viewer on the right (always read-only here). The "Editar"
 * pencil opens a small dialog that lets the operator change ONLY the
 * metadata (name, type, runtime, description). The body of the skill
 * (markdown / Python) is NOT edited from this dialog — that surface
 * deserves the full-page editor that ``/skills/new`` already offers.
 *
 * Execution is OUT OF SCOPE for v1; the page shows an inline "No
 * ejecutable" badge that points at the future sandbox change. See
 * ``sdd/skills-catalog/proposal`` and ``sdd/skills-catalog/design``.
 */
export default function SkillDetailPage(): React.JSX.Element {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const id = params?.id;

  const [skill, setSkill] = useState<SkillDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [editOpen, setEditOpen] = useState(false);
  const [refreshNonce, setRefreshNonce] = useState(0);

  const reload = useCallback(() => {
    setLoading(true);
    setRefreshNonce((n) => n + 1);
  }, []);

  useEffect(() => {
    if (!id) return;
    let cancelled = false;
    const load = async (): Promise<void> => {
      try {
        const row = await getSkill(id);
        if (cancelled) return;
        setSkill(row);
        setError(null);
      } catch (err) {
        if (cancelled) return;
        if (err instanceof ApiError && err.status === 404) {
          setError("Skill no encontrada");
        } else {
          setError("Error al cargar la skill");
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    void load();
    return () => {
      cancelled = true;
    };
  }, [id, refreshNonce]);

  async function runArchive(): Promise<void> {
    if (!skill) return;
    try {
      await archiveSkill(skill.id);
      toast.success("Skill archivada");
      reload();
    } catch {
      toast.error("No se pudo archivar la skill");
    }
  }

  async function runDelete(): Promise<void> {
    if (!skill) return;
    if (!confirm(`Eliminar la skill "${skill.name}" definitivamente?`)) return;
    try {
      await deleteSkill(skill.id);
      toast.success("Skill eliminada");
      router.push("/skills");
    } catch {
      toast.error("No se pudo eliminar la skill");
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

  if (error || !skill) {
    return (
      <section className="flex flex-col items-start gap-3">
        <Button
          variant="outline"
          size="sm"
          onClick={() => router.push("/skills")}
        >
          <ArrowLeft className="h-4 w-4" /> Volver
        </Button>
        <p role="alert" className="text-sm text-[rgb(var(--danger))]">
          {error ?? "Skill no disponible"}
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
            onClick={() => router.push("/skills")}
            aria-label="Volver al catálogo"
          >
            <ArrowLeft className="h-4 w-4" />
          </Button>
          <h1 className="text-2xl font-semibold tracking-tight">{skill.name}</h1>
          <Badge variant={skill.is_active ? "success" : "muted"}>
            {skill.is_active ? "Activa" : "Archivada"}
          </Badge>
          <Badge variant="accent">v{skill.version}</Badge>
          <Badge variant="muted">{SKILL_TYPE_LABEL[skill.type]}</Badge>
          <Badge variant="muted" data-testid="skill-runtime-badge">
            {SKILL_RUNTIME_LABEL[skill.runtime]}
          </Badge>
          {skill.used_by_agent_count > 0 && (
            <Badge variant="accent" data-testid="skill-usage-badge">
              {skill.used_by_agent_count} agente
              {skill.used_by_agent_count === 1 ? "" : "s"}
            </Badge>
          )}
          {skill.runtime === "python" && (
            <span
              className="inline-flex items-center gap-1 rounded-md border border-[rgb(var(--warning)/0.4)] bg-[rgb(var(--warning)/0.1)] px-2 py-0.5 text-xs text-[rgb(var(--warning))]"
              title="Activable cuando arranque el sandbox"
              data-testid="non-executable-badge"
            >
              No ejecutable
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          {skill.is_active && (
            <Button variant="outline" size="sm" onClick={() => void runArchive()}>
              <Archive className="h-4 w-4" /> Archivar
            </Button>
          )}
          <Button variant="outline" size="sm" onClick={() => void runDelete()}>
            <Trash2 className="h-4 w-4" /> Eliminar
          </Button>
          <Button
            size="sm"
            onClick={() => setEditOpen(true)}
            data-testid="open-edit-dialog"
          >
            <Pencil className="h-4 w-4" /> Editar
          </Button>
        </div>
      </header>

      {/* Two-column body: left metadata panel (read-only), right code viewer
          (read-only too — body editing is intentionally not on this page). */}
      <div className="grid flex-1 grid-cols-1 gap-4 overflow-hidden lg:grid-cols-[minmax(0,360px)_minmax(0,1fr)]">
        {/* Left: metadata */}
        <div className="flex flex-col gap-3 overflow-y-auto rounded-md border border-[rgb(var(--border))] bg-[rgb(var(--background-elevated))] p-4">
          <h2 className="text-sm font-semibold tracking-tight">Metadatos</h2>
          <dl className="grid grid-cols-2 gap-y-1 text-xs">
            <dt className="text-[rgb(var(--foreground-muted))]">Tipo</dt>
            <dd>{SKILL_TYPE_LABEL[skill.type]}</dd>
            <dt className="text-[rgb(var(--foreground-muted))]">Runtime</dt>
            <dd>{SKILL_RUNTIME_LABEL[skill.runtime]}</dd>
            <dt className="text-[rgb(var(--foreground-muted))]">Versión</dt>
            <dd>v{skill.version}</dd>
            <dt className="text-[rgb(var(--foreground-muted))]">Creada</dt>
            <dd>{skill.created_at ?? "—"}</dd>
            <dt className="text-[rgb(var(--foreground-muted))]">Modificada</dt>
            <dd>{skill.updated_at ?? "—"}</dd>
          </dl>
          <Separator />
          <div>
            <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-[rgb(var(--foreground-muted))]">
              Descripción
            </h3>
            <p className="whitespace-pre-wrap text-sm">
              {skill.description?.trim() ? skill.description : "—"}
            </p>
          </div>
          <Separator />
          <div>
            <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-[rgb(var(--foreground-muted))]">
              Firma tipada — Entradas
            </h3>
            <SignatureSummary fields={skill.input_signature.inputs} />
          </div>
          <div>
            <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-[rgb(var(--foreground-muted))]">
              Firma tipada — Salidas
            </h3>
            <SignatureSummary fields={skill.output_signature.outputs} />
          </div>
        </div>

        {/* Right: code / markdown viewer — always read-only on this page */}
        <div className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-md border border-[rgb(var(--border))] bg-[rgb(var(--background))]">
          <div className="flex items-center justify-between border-b border-[rgb(var(--border))] bg-[rgb(var(--background-elevated))] px-3 py-2 text-xs text-[rgb(var(--foreground-muted))]">
            <span>
              {skill.runtime === "markdown" ? "skill.md" : "skill.py"} — sólo
              lectura
            </span>
            <span>{skill.code.length} chars</span>
          </div>
          <div className="min-h-0 flex-1">
            <CodeMirrorEditor
              value={skill.code}
              onChange={() => {
                /* read-only — required by wrapper contract */
              }}
              language={skill.runtime === "markdown" ? "markdown" : "python"}
              readOnly
              aria-label="Cuerpo de la skill (sólo lectura)"
              data-testid={
                skill.runtime === "markdown"
                  ? "skill-markdown-readonly"
                  : "skill-code-readonly"
              }
            />
          </div>
        </div>
      </div>

      <EditSkillDialog
        open={editOpen}
        skill={skill}
        onOpenChange={setEditOpen}
        onSaved={(updated) => {
          setSkill(updated);
          setEditOpen(false);
          reload();
        }}
      />
    </section>
  );
}

// ---------------------------------------------------------------------------
// Edit dialog — metadata only (name / type / runtime / description).
//
// The skill body (markdown or Python) is NOT edited here on purpose — it
// deserves a full-page editor like ``/skills/new`` and is read-only on the
// detail page until that flow is wired up.
// ---------------------------------------------------------------------------

interface EditSkillDialogProps {
  open: boolean;
  skill: SkillDetail;
  onOpenChange: (open: boolean) => void;
  onSaved: (next: SkillDetail) => void;
}

function EditSkillDialog({
  open,
  skill,
  onOpenChange,
  onSaved,
}: EditSkillDialogProps): React.JSX.Element {
  const [name, setName] = useState(skill.name);
  const [type, setType] = useState<SkillType>(skill.type);
  const [runtime, setRuntime] = useState<SkillRuntime>(skill.runtime);
  const [description, setDescription] = useState(skill.description ?? "");
  const [saving, setSaving] = useState(false);

  // Re-seed local form state whenever the loaded skill changes (e.g. after
  // a successful save the parent reloads and hands a fresh row in).
  useEffect(() => {
    setName(skill.name);
    setType(skill.type);
    setRuntime(skill.runtime);
    setDescription(skill.description ?? "");
  }, [skill]);

  const dirty =
    name !== skill.name ||
    type !== skill.type ||
    runtime !== skill.runtime ||
    (description || "") !== (skill.description || "");

  async function save(): Promise<void> {
    if (!name.trim()) {
      toast.error("El nombre no puede estar vacío");
      return;
    }
    setSaving(true);
    try {
      const next = await patchSkill(skill.id, {
        name: name.trim() !== skill.name ? name.trim() : undefined,
        type: type !== skill.type ? type : undefined,
        runtime: runtime !== skill.runtime ? runtime : undefined,
        description:
          (description || null) !== (skill.description || null)
            ? description || null
            : undefined,
        updated_at: skill.updated_at ?? "",
      });
      toast.success("Skill actualizada");
      onSaved(next);
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        toast.error("La skill cambió desde que la abriste — recarga");
        onOpenChange(false);
      } else if (err instanceof ApiError && err.status === 422) {
        toast.error("Datos inválidos");
      } else if (err instanceof ApiError) {
        toast.error(err.message || `Error (${err.status})`);
      } else {
        toast.error("No se pudo guardar la skill");
      }
    } finally {
      setSaving(false);
    }
  }

  function handleOpenChange(next: boolean): void {
    if (!next && dirty && !confirm("Descartar los cambios sin guardar?")) {
      return;
    }
    onOpenChange(next);
  }

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Editar skill</DialogTitle>
          <DialogDescription>
            Cambia los metadatos de la skill. El cuerpo (markdown / Python) se
            edita desde la página principal cuando habilitemos su flujo
            dedicado.
          </DialogDescription>
        </DialogHeader>

        <div className="grid gap-1.5">
          <Label htmlFor="edit-name">Nombre</Label>
          <Input
            id="edit-name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            maxLength={100}
            required
          />
        </div>
        <div className="grid gap-1.5">
          <Label htmlFor="edit-type">Tipo</Label>
          <Select
            id="edit-type"
            value={type}
            onChange={(e) => setType(e.target.value as SkillType)}
          >
            {SKILL_TYPES.map((kind) => (
              <option key={kind} value={kind}>
                {SKILL_TYPE_LABEL[kind]}
              </option>
            ))}
          </Select>
        </div>
        <div className="grid gap-1.5">
          <Label>Runtime</Label>
          <Tabs
            value={runtime}
            onValueChange={(v) => setRuntime(v as SkillRuntime)}
          >
            <TabsList>
              {SKILL_RUNTIMES.map((rt) => (
                <TabsTrigger
                  key={rt}
                  value={rt}
                  data-testid={`edit-runtime-${rt}`}
                >
                  {SKILL_RUNTIME_LABEL[rt]}
                </TabsTrigger>
              ))}
            </TabsList>
          </Tabs>
        </div>
        <div className="grid gap-1.5">
          <Label htmlFor="edit-description">Descripción</Label>
          <Textarea
            id="edit-description"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            maxLength={4000}
            rows={3}
          />
        </div>

        <DialogFooter>
          <Button
            variant="outline"
            onClick={() => handleOpenChange(false)}
            disabled={saving}
            data-testid="cancel-edit-button"
          >
            <X className="h-4 w-4" /> Cancelar
          </Button>
          <Button
            onClick={() => void save()}
            disabled={!dirty || saving || !name.trim()}
            data-testid="save-edit-button"
          >
            <Save className="h-4 w-4" />
            {saving ? "Guardando…" : "Guardar"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

interface SignatureSummaryProps {
  fields: { name: string; type: string }[];
}

function SignatureSummary({
  fields,
}: SignatureSummaryProps): React.JSX.Element {
  if (fields.length === 0) {
    return <p className="text-xs text-[rgb(var(--foreground-muted))]">—</p>;
  }
  return (
    <ul className="flex flex-col gap-1 text-xs">
      {fields.map((f, i) => (
        <li key={`${f.name}-${i}`}>
          <code className="font-mono">
            {f.name}: {f.type}
          </code>
        </li>
      ))}
    </ul>
  );
}
