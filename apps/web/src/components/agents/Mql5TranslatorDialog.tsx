"use client";

/**
 * MQL5 → Python translator dialog used on the agent edit page.
 *
 * Charter alignment: the MQL5 input lives ONLY in this component's
 * local state. It's posted to ``POST /api/tools/mql5-to-python`` (which
 * discards it after the upstream call) and is wiped from local state
 * the moment the dialog closes — Cancelar AND Aplicar both clear
 * ``mql5`` so a stray re-open never shows previous content. The
 * Python output is handed back to the parent via ``onApply`` and the
 * dialog never persists it either.
 *
 * The component is controlled (``open`` / ``onOpenChange``). The
 * parent owns the open state because the trigger button lives outside
 * the dialog (next to the ``logica`` editor header).
 */

import * as React from "react";
import { toast } from "sonner";
import { ChevronsRight } from "lucide-react";

import { ApiError } from "@/lib/api";
import { translateMql5ToPython } from "@/lib/translator";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { CodeMirrorEditor } from "@/components/CodeMirrorEditor";

export interface Mql5TranslatorDialogProps {
  /** Whether the dialog is open. Controlled by the parent. */
  open: boolean;
  /** Open-state setter (parent-owned). */
  onOpenChange: (open: boolean) => void;
  /**
   * Optional override for the entrypoint the translator should bake
   * into the generated Python. Defaults to ``on_tick`` (Worker
   * convention). The agent edit page passes the current entrypoint
   * value here so the translation matches whatever the operator typed.
   */
  targetEntrypoint?: string;
  /**
   * Called when the operator clicks Aplicar with a successful
   * translation in hand. Receives the Python source — the parent is
   * responsible for setting it on the agent's ``logica`` editor.
   */
  onApply: (python: string) => void;
}

export function Mql5TranslatorDialog({
  open,
  onOpenChange,
  targetEntrypoint,
  onApply,
}: Mql5TranslatorDialogProps): React.JSX.Element {
  const [mql5, setMql5] = React.useState("");
  const [python, setPython] = React.useState<string | null>(null);
  const [loading, setLoading] = React.useState(false);
  // Token counts surfaced for operator visibility — the backend also
  // records these in the audit log (size-only, no content).
  const [tokens, setTokens] = React.useState<{
    input: number;
    output: number;
    model: string;
  } | null>(null);

  // Wipe local state whenever the dialog closes — defence in depth
  // for the "no MQL5 ever persists" invariant, and also avoids
  // leaking a prior translation into a fresh open. We wrap the
  // parent's ``onOpenChange`` so the reset is a side-effect of the
  // close event itself, not a synchronous useEffect setState (which
  // the project's react-hooks lint rule forbids).
  const wipe = React.useCallback(() => {
    setMql5("");
    setPython(null);
    setTokens(null);
    setLoading(false);
  }, []);
  const handleOpenChange = React.useCallback(
    (next: boolean) => {
      if (!next) wipe();
      onOpenChange(next);
    },
    [onOpenChange, wipe],
  );

  async function convert(): Promise<void> {
    if (!mql5.trim()) {
      toast.error("Pega código MQL5 antes de convertir");
      return;
    }
    setLoading(true);
    setPython(null);
    setTokens(null);
    try {
      const result = await translateMql5ToPython({
        mql5,
        target_entrypoint: targetEntrypoint,
      });
      setPython(result.python);
      setTokens({
        input: result.input_tokens,
        output: result.output_tokens,
        model: result.model,
      });
    } catch (err) {
      if (err instanceof ApiError) {
        if (err.status === 413) {
          toast.error("El código MQL5 excede el tamaño máximo permitido.");
        } else if (err.status === 503) {
          toast.error(
            "El traductor MQL5→Py no está habilitado en este entorno.",
          );
        } else if (err.status === 502) {
          toast.error("El traductor falló al contactar el proveedor remoto.");
        } else if (err.status === 401 || err.status === 403) {
          toast.error("Sesión no autorizada. Vuelve a iniciar sesión.");
        } else {
          toast.error("No se pudo traducir el código MQL5.");
        }
      } else {
        toast.error("No se pudo traducir el código MQL5.");
      }
    } finally {
      setLoading(false);
    }
  }

  function cancel(): void {
    // ``handleOpenChange`` wipes local state on the way down.
    handleOpenChange(false);
  }

  function apply(): void {
    if (python === null) return;
    onApply(python);
    handleOpenChange(false);
  }

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent
        // Full-screen modal: traductor maneja mucho texto a la vez (EA completo
        // a la izquierda + Python traducido a la derecha). Ocupamos casi todo
        // el viewport, dejando ~0.5rem de margen para que se sigan viendo los
        // bordes del modal sobre el backdrop.
        className="w-[calc(100vw-1rem)] max-w-none h-[calc(100vh-1rem)] max-h-none"
        data-testid="mql5-translator-dialog"
      >
        <DialogHeader>
          <DialogTitle>Convertir MQL5 → Python</DialogTitle>
          <DialogDescription>
            Pega tu código MQL5/MQL4. La traducción usa el cliente MCP del
            proyecto y no crea EAs. El sistema nunca guarda ni ejecuta MQL5
            — sólo el Python resultante se conserva si pulsas Aplicar.
          </DialogDescription>
        </DialogHeader>

        {/*
          Outer grid takes ALL remaining vertical space between header and
          footer. `min-h-0` is essential — without it, the textarea/editor
          inside refuses to shrink and pushes the grid past the viewport.

          Three columns on md+: input · convert-button (intrinsic width,
          vertically centered) · output. Falls back to single column on
          mobile where the button sits between the two panes naturally.
        */}
        <div className="grid min-h-0 flex-1 grid-cols-1 gap-3 md:grid-cols-[1fr_auto_1fr]">
          {/* Left pane: MQL5 input wrapped in the same "code panel" style
              as the Python side (border + filename header + chars + body). */}
          <div className="flex min-h-0 flex-col">
            <div className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-md border border-[rgb(var(--border))] bg-[rgb(var(--background))]">
              <div className="flex items-center justify-between border-b border-[rgb(var(--border))] bg-[rgb(var(--background-elevated))] px-3 py-2 text-xs text-[rgb(var(--foreground-muted))]">
                <span>mql5.mq5</span>
                <span>{mql5.length} chars</span>
              </div>
              <Textarea
                id="mql5-input"
                value={mql5}
                onChange={(e) => setMql5(e.target.value)}
                placeholder={
                  "// pega aquí tu Expert Advisor o script MQL5/MQL4\n" +
                  "void OnTick() { ... }"
                }
                className="min-h-0 flex-1 resize-none rounded-none border-0 bg-transparent font-mono text-xs focus-visible:ring-0 focus-visible:ring-offset-0"
                aria-label="Código MQL5 o MQL4"
                data-testid="mql5-input"
              />
            </div>
          </div>

          {/* Middle column: Convert button (vertically centered). The button
              is the action that bridges left → right; `>>` reinforces the
              direction visually. */}
          <div className="flex min-h-0 flex-col items-center justify-center gap-2 md:w-32">
            <Button
              onClick={() => void convert()}
              disabled={loading || mql5.trim().length === 0}
              aria-label="Convertir MQL5 a Python"
              data-testid="convert-button"
              className="w-full"
            >
              <span>{loading ? "Convirtiendo…" : "Convertir"}</span>
              <ChevronsRight className="h-4 w-4" aria-hidden />
            </Button>
            {tokens ? (
              <span className="text-center text-[10px] leading-tight text-[rgb(var(--foreground-muted))]">
                {tokens.input} in / {tokens.output} out
                <br />
                <span className="opacity-70">{tokens.model}</span>
              </span>
            ) : null}
          </div>

          {/* Right pane: Python output — misma estructura que `/agentes/[id]`
              (border + header con `logica.py` y chars + editor abajo). */}
          <div className="flex min-h-0 flex-col">
            <div className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-md border border-[rgb(var(--border))] bg-[rgb(var(--background))]">
              <div className="flex items-center justify-between border-b border-[rgb(var(--border))] bg-[rgb(var(--background-elevated))] px-3 py-2 text-xs text-[rgb(var(--foreground-muted))]">
                <span>logica.py</span>
                <span>{python !== null ? `${python.length} chars` : "—"}</span>
              </div>
              <div id="python-output" className="min-h-0 flex-1">
                {python === null ? (
                  <div className="flex h-full items-center justify-center p-6 text-center text-xs text-[rgb(var(--foreground-muted))]">
                    La traducción aparecerá aquí tras pulsar Convertir.
                  </div>
                ) : (
                  <CodeMirrorEditor
                    value={python}
                    onChange={() => {
                      /* read-only — ignore */
                    }}
                    language="python"
                    readOnly
                    aria-label="Python traducido"
                    data-testid="python-output"
                  />
                )}
              </div>
            </div>
          </div>
        </div>

        <DialogFooter>
          <Button
            variant="outline"
            onClick={cancel}
            disabled={loading}
            data-testid="cancel-button"
          >
            Cancelar
          </Button>
          <Button
            onClick={apply}
            disabled={python === null || loading}
            data-testid="apply-button"
          >
            Aplicar
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export default Mql5TranslatorDialog;
