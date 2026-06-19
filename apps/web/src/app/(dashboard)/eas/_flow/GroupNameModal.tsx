"use client";

/**
 * Accessible modal for naming a group on CREATE and RENAME (replaces the old
 * window.prompt). Built on the shared Radix Dialog primitive (no new dependency),
 * consistent with GeneratedCodeModal.
 *
 * Behaviour:
 * - Opens prefilled with `initialName`; the input is auto-focused + selected.
 * - Enter submits (when non-empty); Esc / Cancel / overlay click closes.
 * - The submit button is disabled while the trimmed name is empty.
 * - `onSubmit` receives the trimmed name; the caller decides create vs rename.
 *
 * The form lives in an inner component mounted only while `open`, so its local
 * draft initializes from `initialName` on every open WITHOUT a setState-in-effect
 * (the effect only does imperative focus, never a synchronous setState).
 */
import { useEffect, useRef, useState } from "react";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";

interface GroupNameModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Prefilled value (e.g. "Grupo 3" for create, the current name for rename). */
  initialName: string;
  /** Dialog title (e.g. "Nuevo grupo" / "Renombrar grupo"). */
  title: string;
  /** Submit button label (e.g. "Crear" / "Guardar"). */
  submitLabel: string;
  /** Called with the trimmed, non-empty name; the modal then closes. */
  onSubmit: (name: string) => void;
}

/** The form body. Remounted on each open so `useState(initialName)` re-seeds. */
function GroupNameForm({
  initialName,
  submitLabel,
  onSubmit,
  onClose,
}: {
  initialName: string;
  submitLabel: string;
  onSubmit: (name: string) => void;
  onClose: () => void;
}) {
  const [value, setValue] = useState(initialName);
  const inputRef = useRef<HTMLInputElement>(null);

  // Imperative focus + select on mount (after the dialog content is in the DOM).
  // No setState here, so this never triggers cascading renders.
  useEffect(() => {
    const id = window.setTimeout(() => {
      inputRef.current?.focus();
      inputRef.current?.select();
    }, 0);
    return () => window.clearTimeout(id);
  }, []);

  const trimmed = value.trim();
  const canSubmit = trimmed.length > 0;

  const submit = () => {
    if (!canSubmit) return;
    onSubmit(trimmed);
    onClose();
  };

  return (
    <>
      <input
        ref={inputRef}
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            e.preventDefault();
            submit();
          }
        }}
        placeholder="Nombre del grupo"
        aria-label="Nombre del grupo"
        className="bg-gl-gray-1000 border-border text-foreground focus-visible:ring-ring w-full rounded-md border px-3 py-2 text-sm focus-visible:ring-2 focus-visible:outline-none"
      />

      <div className="flex justify-end gap-2">
        <Button size="sm" variant="outline" onClick={onClose}>
          Cancelar
        </Button>
        <Button size="sm" onClick={submit} disabled={!canSubmit}>
          {submitLabel}
        </Button>
      </div>
    </>
  );
}

export function GroupNameModal({
  open,
  onOpenChange,
  initialName,
  title,
  submitLabel,
  onSubmit,
}: GroupNameModalProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-sm">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
        </DialogHeader>
        {/* Mount the form only while open so its draft re-seeds from initialName. */}
        {open && (
          <GroupNameForm
            initialName={initialName}
            submitLabel={submitLabel}
            onSubmit={onSubmit}
            onClose={() => onOpenChange(false)}
          />
        )}
      </DialogContent>
    </Dialog>
  );
}
