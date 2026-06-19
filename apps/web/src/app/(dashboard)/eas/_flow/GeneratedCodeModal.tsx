"use client";

/**
 * Split result modal for dual-language codegen (Radix Dialog).
 *
 * Renders TWO independent {@link CodePane}s — MQL5 (Shiki "cpp", .mq5) and Python
 * (Shiki "python", .py) — side by side on desktop (md:grid-cols-2) and stacked on
 * narrow screens. Each pane owns its highlight, copy and download, and receives
 * its language's code + independent loading + error state so PARTIAL FAILURE works
 * (one pane shows code while the other shows its error). Shiki stays lazy and
 * client-only via the dynamic import inside CodePane — see frontend/AGENTS.md.
 */
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { CodePane } from "./CodePane";
import { mq5Filename, pyFilename } from "./generatedCodeFile";

interface GeneratedCodeModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  eaName?: string;
  /** MQL5 pane state. */
  mql5Code: string;
  mql5Loading: boolean;
  mql5Error: string | null;
  /** Python pane state. */
  pythonCode: string;
  pythonLoading: boolean;
  pythonError: string | null;
}

export function GeneratedCodeModal({
  open,
  onOpenChange,
  eaName,
  mql5Code,
  mql5Loading,
  mql5Error,
  pythonCode,
  pythonLoading,
  pythonError,
}: GeneratedCodeModalProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-6xl">
        <DialogHeader>
          <DialogTitle>Código generado</DialogTitle>
        </DialogHeader>

        <div className="grid min-h-0 flex-1 gap-4 md:grid-cols-2">
          <CodePane
            label="MQL5"
            code={mql5Code}
            shikiLang="cpp"
            filename={mq5Filename(eaName)}
            loading={mql5Loading}
            error={mql5Error}
          />
          <CodePane
            label="Python"
            code={pythonCode}
            shikiLang="python"
            filename={pyFilename(eaName)}
            loading={pythonLoading}
            error={pythonError}
          />
        </div>
      </DialogContent>
    </Dialog>
  );
}
