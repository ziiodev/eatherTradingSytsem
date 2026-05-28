"use client";

import { useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { ArrowLeft, Save } from "lucide-react";
import { toast } from "sonner";

import { ApiError } from "@/lib/api";
import {
  SKILL_RUNTIMES,
  SKILL_RUNTIME_LABEL,
  SKILL_TYPES,
  SKILL_TYPE_DESCRIPTION,
  SKILL_TYPE_LABEL,
  SKILL_TYPE_TEMPLATE,
  SKILL_TYPE_TEMPLATE_MARKDOWN,
  createSkill,
  type SkillRuntime,
  type SkillType,
} from "@/lib/skills";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { Separator } from "@/components/ui/separator";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { CodeMirrorEditor } from "@/components/CodeMirrorEditor";

/**
 * Create a new skill. Markdown is the default runtime — skills are
 * knowledge artifacts; Python is reserved for computational skills.
 *
 * The right-hand editor pane swaps with ``runtime``:
 * - markdown → split textarea + live ``MarkdownView`` preview
 * - python   → CodeMirror with Python grammar
 */
export default function NewSkillPage(): React.JSX.Element {
  const router = useRouter();
  const [type, setType] = useState<SkillType>("indicator");
  const [runtime, setRuntime] = useState<SkillRuntime>("markdown");
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [code, setCode] = useState<string>(
    SKILL_TYPE_TEMPLATE_MARKDOWN.indicator,
  );
  // Track whether the operator has edited the body; if not, swapping the
  // type or runtime re-seeds the template.
  const [codeTouched, setCodeTouched] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  const canSubmit = useMemo(
    () => !!name.trim() && !!code.trim() && !submitting,
    [name, code, submitting],
  );

  function templateFor(t: SkillType, r: SkillRuntime): string {
    return r === "markdown"
      ? SKILL_TYPE_TEMPLATE_MARKDOWN[t]
      : SKILL_TYPE_TEMPLATE[t];
  }

  function handleTypeChange(next: SkillType): void {
    setType(next);
    if (!codeTouched) {
      setCode(templateFor(next, runtime));
    }
  }

  function handleRuntimeChange(next: SkillRuntime): void {
    setRuntime(next);
    if (!codeTouched) {
      setCode(templateFor(type, next));
    }
  }

  async function submit(): Promise<void> {
    setSubmitting(true);
    try {
      const detail = await createSkill({
        name: name.trim(),
        type,
        runtime,
        code,
        description: description.trim() || undefined,
      });
      toast.success(`Skill "${detail.name}" creada`);
      router.push(`/skills/${detail.id}`);
    } catch (err) {
      if (err instanceof ApiError) {
        const body = err.body as
          | { detail?: { code?: string; message?: string; line?: number } }
          | undefined;
        const detail = body?.detail;
        if (detail?.code === "python_syntax_error") {
          toast.error(
            `Error de sintaxis en línea ${detail.line ?? "?"}: ${detail.message ?? "inválido"}`,
          );
        } else if (detail?.code === "markdown_too_large") {
          toast.error("El contenido markdown excede el tamaño máximo.");
        } else {
          toast.error(`Error (${err.status})`);
        }
      } else {
        toast.error("No se pudo crear la skill");
      }
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <section className="flex h-full flex-col gap-4">
      <header className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={() => router.push("/skills")}
            aria-label="Volver al catálogo"
          >
            <ArrowLeft className="h-4 w-4" />
          </Button>
          <h1 className="text-2xl font-semibold tracking-tight">Nueva skill</h1>
        </div>
        <Button
          size="sm"
          onClick={() => void submit()}
          disabled={!canSubmit}
          data-testid="create-skill-button"
        >
          <Save className="h-4 w-4" />
          {submitting ? "Creando…" : "Crear skill"}
        </Button>
      </header>

      <p
        role="note"
        className="rounded-md border border-[rgb(var(--warning)/0.4)] bg-[rgb(var(--warning)/0.1)] p-3 text-xs text-[rgb(var(--warning))]"
      >
        Las skills se almacenan, versionan y se muestran. La ejecución llegará
        con el sandbox de agentes (cambio posterior).
      </p>

      <div className="grid flex-1 grid-cols-1 gap-4 lg:grid-cols-[minmax(0,360px)_minmax(0,1fr)]">
        {/* Left: metadata */}
        <div className="flex flex-col gap-3 rounded-md border border-[rgb(var(--border))] bg-[rgb(var(--background-elevated))] p-4">
          <div className="grid gap-1.5">
            <Label htmlFor="skill-name">Nombre</Label>
            <Input
              id="skill-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="ej. Entrada por RSI"
              maxLength={100}
              required
            />
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="skill-type">Tipo</Label>
            <Select
              id="skill-type"
              value={type}
              onChange={(e) => handleTypeChange(e.target.value as SkillType)}
            >
              {SKILL_TYPES.map((kind) => (
                <option key={kind} value={kind}>
                  {SKILL_TYPE_LABEL[kind]}
                </option>
              ))}
            </Select>
            <p className="text-xs text-[rgb(var(--foreground-muted))]">
              {SKILL_TYPE_DESCRIPTION[type]}
            </p>
          </div>
          <div className="grid gap-1.5">
            <Label>Runtime</Label>
            <Tabs
              value={runtime}
              onValueChange={(v) => handleRuntimeChange(v as SkillRuntime)}
            >
              <TabsList>
                {SKILL_RUNTIMES.map((rt) => (
                  <TabsTrigger key={rt} value={rt} data-testid={`runtime-${rt}`}>
                    {SKILL_RUNTIME_LABEL[rt]}
                  </TabsTrigger>
                ))}
              </TabsList>
            </Tabs>
            <p className="text-xs text-[rgb(var(--foreground-muted))]">
              {runtime === "markdown"
                ? "Artefacto de conocimiento: prompts, reglas de entrada/salida, marcos de decisión."
                : "Lógica computacional: indicadores, cálculos de riesgo, correlaciones."}
            </p>
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="skill-description">Descripción (opcional)</Label>
            <Textarea
              id="skill-description"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              maxLength={4000}
              rows={4}
            />
          </div>
          <Separator />
          <p className="text-xs text-[rgb(var(--foreground-muted))]">
            La firma tipada (input/output) podrá editarse desde la vista de
            detalle una vez creada la skill.
          </p>
        </div>

        {/* Right: editor — swaps with runtime */}
        <div className="flex flex-col overflow-hidden rounded-md border border-[rgb(var(--border))] bg-[rgb(var(--background))]">
          <div className="flex items-center justify-between border-b border-[rgb(var(--border))] bg-[rgb(var(--background-elevated))] px-3 py-2 text-xs text-[rgb(var(--foreground-muted))]">
            <span>{runtime === "markdown" ? "skill.md" : "skill.py"}</span>
            <span>{code.length} chars</span>
          </div>
          <div className="min-h-[420px] flex-1">
            <CodeMirrorEditor
              value={code}
              onChange={(next) => {
                setCode(next);
                setCodeTouched(true);
              }}
              language={runtime === "markdown" ? "markdown" : "python"}
              aria-label={
                runtime === "markdown"
                  ? "Editor markdown de la skill"
                  : "Editor de código de la skill"
              }
              data-testid={
                runtime === "markdown"
                  ? "skill-markdown-editor"
                  : "skill-code-editor"
              }
            />
          </div>
        </div>
      </div>
    </section>
  );
}
