"use client";

/**
 * One language pane inside the split GeneratedCodeModal: title, highlighted code,
 * and its OWN copy + download buttons. Handles three independent states driven by
 * props from the parent (one mutation per language):
 *  - loading  → "Generando…" placeholder (generation in flight)
 *  - error    → friendly message, no actions (this language failed; the other
 *               pane may still show code — partial failure)
 *  - success  → Shiki-highlighted code (falls back to a plain <pre> on highlight
 *               failure) with active copy/download buttons
 *
 * Shiki is loaded on demand via a dynamic `import("shiki")` INSIDE an effect (not
 * a top-level import, not next/dynamic) so the highlighter never ships in the
 * initial bundle and never runs during SSR/hydration — see frontend/AGENTS.md.
 */
import { useEffect, useState } from "react";
import { Check, Copy, Download } from "lucide-react";
import { Button } from "@/components/ui/button";
import { downloadCode } from "./generatedCodeFile";

interface CodePaneProps {
  /** Display label, e.g. "MQL5" or "Python". */
  label: string;
  /** Generated source; empty while loading or on error. */
  code: string;
  /** Shiki grammar id, e.g. "cpp" or "python". */
  shikiLang: string;
  /** Target download filename, e.g. "MyEA.mq5". */
  filename: string;
  /** True while this language's request is still in flight. */
  loading: boolean;
  /** Friendly error message for this language, or null on success. */
  error: string | null;
}

export function CodePane({
  label,
  code,
  shikiLang,
  filename,
  loading,
  error,
}: CodePaneProps) {
  const [html, setHtml] = useState<string | null>(null);
  const [highlighting, setHighlighting] = useState(false);
  const [copied, setCopied] = useState(false);

  // Highlight whenever we have code to show. All setState happens inside the
  // async task (after the import boundary) so we never call it synchronously in
  // the effect body — see frontend/AGENTS.md and react-hooks/set-state-in-effect.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      setHtml(null);
      // Nothing to highlight while loading / errored / empty; the render branch
      // already shows the right placeholder, so just clear stale markup above.
      if (loading || error || !code) return;
      setHighlighting(true);
      try {
        const { codeToHtml } = await import("shiki");
        const out = await codeToHtml(code, {
          lang: shikiLang,
          theme: "github-dark",
        });
        if (!cancelled) setHtml(out);
      } catch {
        // Fall back to the raw <pre> rendered below when html stays null.
        if (!cancelled) setHtml(null);
      } finally {
        if (!cancelled) setHighlighting(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [code, shikiLang, loading, error]);

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(code);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // Clipboard may be unavailable (insecure context); ignore silently.
    }
  };

  return (
    <div className="flex min-h-0 flex-col gap-2">
      <h3 className="text-foreground text-sm font-semibold">{label}</h3>

      <div className="border-border bg-gl-gray-950 min-h-0 flex-1 overflow-auto rounded-md border text-sm">
        {loading ? (
          <p className="text-muted-foreground p-4">Generando…</p>
        ) : error ? (
          <p role="alert" className="text-destructive p-4">
            {error}
          </p>
        ) : highlighting ? (
          <p className="text-muted-foreground p-4">Resaltando código…</p>
        ) : html ? (
          <div
            className="[&_pre]:m-0 [&_pre]:overflow-auto [&_pre]:p-4"
            // Shiki output is trusted, self-generated highlight markup.
            dangerouslySetInnerHTML={{ __html: html }}
          />
        ) : (
          <pre className="text-foreground m-0 overflow-auto p-4 font-mono">
            {code}
          </pre>
        )}
      </div>

      <div className="flex justify-end gap-2">
        <Button
          size="sm"
          variant="outline"
          onClick={handleCopy}
          disabled={loading || !!error || !code}
        >
          {copied ? (
            <Check className="h-4 w-4" />
          ) : (
            <Copy className="h-4 w-4" />
          )}
          {copied ? "Copiado" : "Copiar"}
        </Button>
        <Button
          size="sm"
          onClick={() => downloadCode(code, filename)}
          disabled={loading || !!error || !code}
        >
          <Download className="h-4 w-4" />
          Descargar
        </Button>
      </div>
    </div>
  );
}
