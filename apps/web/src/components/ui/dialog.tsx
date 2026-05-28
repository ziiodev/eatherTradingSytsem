"use client";

import * as React from "react";
import { X } from "lucide-react";

import { cn } from "@/lib/utils";

/**
 * Lightweight modal dialog. Uses the native <dialog> element under the
 * hood plus a controlled-state wrapper that mimics the shadcn part API.
 *
 * Why not Radix:
 *   - We don't render this on a portal across many routes; one dialog
 *     per page is enough.
 *   - Native <dialog>.showModal() gives us the backdrop and focus trap
 *     for free.
 *
 * Parts:
 *   <Dialog open={...} onOpenChange={...}>
 *     <DialogContent>
 *       <DialogHeader>
 *         <DialogTitle>…</DialogTitle>
 *         <DialogDescription>…</DialogDescription>
 *       </DialogHeader>
 *       …
 *       <DialogFooter>…</DialogFooter>
 *     </DialogContent>
 *   </Dialog>
 */

interface DialogContextValue {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  dialogRef: React.RefObject<HTMLDialogElement | null>;
}

const DialogContext = React.createContext<DialogContextValue | null>(null);

function useDialog(): DialogContextValue {
  const ctx = React.useContext(DialogContext);
  if (!ctx) {
    throw new Error("Dialog sub-components must render inside <Dialog>.");
  }
  return ctx;
}

export interface DialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  children?: React.ReactNode;
}

function Dialog({
  open,
  onOpenChange,
  children,
}: DialogProps): React.JSX.Element {
  const dialogRef = React.useRef<HTMLDialogElement | null>(null);
  const ctx = React.useMemo(
    () => ({ open, onOpenChange, dialogRef }),
    [open, onOpenChange],
  );
  return <DialogContext.Provider value={ctx}>{children}</DialogContext.Provider>;
}

const DialogContent = React.forwardRef<
  HTMLDivElement,
  React.HTMLAttributes<HTMLDivElement>
>(({ className, children, ...props }, ref) => {
  const { open, onOpenChange, dialogRef } = useDialog();

  React.useEffect(() => {
    const node = dialogRef.current;
    if (!node) return;
    if (open && !node.open) {
      try {
        node.showModal();
      } catch {
        // happy-dom may not implement showModal; fall back to .open=true.
        node.setAttribute("open", "");
      }
    }
    if (!open && node.open) {
      node.close();
    }
  }, [open, dialogRef]);

  // Esc / backdrop click closes the dialog.
  const handleCancel = React.useCallback(
    (e: React.SyntheticEvent<HTMLDialogElement>) => {
      e.preventDefault();
      onOpenChange(false);
    },
    [onOpenChange],
  );
  const handleClick = React.useCallback(
    (e: React.MouseEvent<HTMLDialogElement>) => {
      // Close on backdrop click — the dialog element itself receives the
      // click when the user clicks the backdrop because the inner content
      // stops propagation.
      if (e.target === e.currentTarget) {
        onOpenChange(false);
      }
    },
    [onOpenChange],
  );

  return (
    <dialog
      ref={dialogRef}
      onCancel={handleCancel}
      onClick={handleClick}
      className={cn(
        // Chromium's UA stylesheet for `<dialog>:modal` ONLY sets
        // `inset-block-{start,end}: 0` — there is no `margin: auto` nor
        // `inset-inline: 0`, so the modal anchors to `left: 0` by default.
        // We pin top-left to viewport center then shift the box back by
        // half its own size with translate. This centers regardless of
        // content width / height.
        "fixed left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2",
        "w-[32rem] max-w-[calc(100vw-2rem)] max-h-[calc(100vh-2rem)] overflow-auto",
        "rounded-lg border border-[rgb(var(--border))] bg-[rgb(var(--card))] p-0 text-[rgb(var(--foreground))] shadow-xl backdrop:bg-black/60",
        // The class prop on <DialogContent> can override sizing
        // (e.g. `max-w-xl`, `max-w-4xl`, `w-[48rem]`) — Tailwind's
        // class ordering means the last w-/max-w- in the merged string wins.
        className,
      )}
    >
      <div
        ref={ref}
        onClick={(e) => e.stopPropagation()}
        // `h-full` is critical: when the parent <dialog> is sized
        // explicitly (e.g. the MQL5 translator's `h-[calc(100vh-1rem)]`),
        // this wrapper MUST fill that height so descendant `flex-1` chains
        // (textarea, code editor) actually grow. For intrinsically-sized
        // dialogs (most cases), `h-full` of an `auto`-height parent
        // collapses back to auto — so it's safe everywhere.
        className={cn("relative flex h-full flex-col gap-4 p-6")}
        {...props}
      >
        <button
          type="button"
          aria-label="Cerrar diálogo"
          className="absolute right-3 top-3 rounded-md p-1 text-[rgb(var(--foreground-muted))] transition-colors hover:bg-[rgb(var(--background-elevated))] hover:text-[rgb(var(--foreground))]"
          onClick={() => onOpenChange(false)}
        >
          <X className="h-4 w-4" />
        </button>
        {children}
      </div>
    </dialog>
  );
});
DialogContent.displayName = "DialogContent";

const DialogHeader = ({
  className,
  ...props
}: React.HTMLAttributes<HTMLDivElement>): React.JSX.Element => (
  <div className={cn("flex flex-col gap-1.5", className)} {...props} />
);
DialogHeader.displayName = "DialogHeader";

const DialogFooter = ({
  className,
  ...props
}: React.HTMLAttributes<HTMLDivElement>): React.JSX.Element => (
  <div
    className={cn(
      "flex flex-col-reverse gap-2 sm:flex-row sm:justify-end",
      className,
    )}
    {...props}
  />
);
DialogFooter.displayName = "DialogFooter";

const DialogTitle = React.forwardRef<
  HTMLHeadingElement,
  React.HTMLAttributes<HTMLHeadingElement>
>(({ className, ...props }, ref) => (
  <h2
    ref={ref}
    className={cn(
      "text-lg font-semibold leading-none tracking-tight",
      className,
    )}
    {...props}
  />
));
DialogTitle.displayName = "DialogTitle";

const DialogDescription = React.forwardRef<
  HTMLParagraphElement,
  React.HTMLAttributes<HTMLParagraphElement>
>(({ className, ...props }, ref) => (
  <p
    ref={ref}
    className={cn("text-sm text-[rgb(var(--foreground-muted))]", className)}
    {...props}
  />
));
DialogDescription.displayName = "DialogDescription";

export {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogFooter,
  DialogTitle,
  DialogDescription,
};
