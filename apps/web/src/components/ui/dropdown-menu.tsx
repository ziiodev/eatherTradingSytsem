"use client";

import * as React from "react";
import { createPortal } from "react-dom";

import { cn } from "@/lib/utils";

/**
 * Minimal dropdown menu — used for the per-row "actions" column on the
 * projects table. Uses native <details>/<summary> for the keyboard +
 * outside-click handling without Radix.
 *
 * Parts:
 *   <DropdownMenu>
 *     <DropdownMenuTrigger>…</DropdownMenuTrigger>
 *     <DropdownMenuContent>
 *       <DropdownMenuItem onSelect={…}>Activate</DropdownMenuItem>
 *       <DropdownMenuSeparator />
 *       <DropdownMenuItem variant="danger" onSelect={…}>Delete</DropdownMenuItem>
 *     </DropdownMenuContent>
 *   </DropdownMenu>
 */

interface DropdownContextValue {
  open: boolean;
  setOpen: (open: boolean) => void;
  triggerRef: React.RefObject<HTMLButtonElement | null>;
  contentRef: React.RefObject<HTMLDivElement | null>;
}

const DropdownContext = React.createContext<DropdownContextValue | null>(null);

function useDropdown(): DropdownContextValue {
  const ctx = React.useContext(DropdownContext);
  if (!ctx) {
    throw new Error("DropdownMenu* must render inside <DropdownMenu>.");
  }
  return ctx;
}

function DropdownMenu({
  children,
}: {
  children?: React.ReactNode;
}): React.JSX.Element {
  const [open, setOpen] = React.useState(false);
  const triggerRef = React.useRef<HTMLButtonElement | null>(null);
  const contentRef = React.useRef<HTMLDivElement | null>(null);

  // Outside-click + Escape cierran el menú. Como el contenido se monta
  // via portal (fuera del subtree del trigger), no podemos usar el
  // `contains` de un único ancestro — comprobamos contra trigger Y
  // content por separado.
  React.useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: MouseEvent): void => {
      const target = event.target;
      if (!(target instanceof Node)) return;
      const inTrigger = triggerRef.current?.contains(target) ?? false;
      const inContent = contentRef.current?.contains(target) ?? false;
      if (!inTrigger && !inContent) setOpen(false);
    };
    const onKey = (event: KeyboardEvent): void => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const ctx = React.useMemo(
    () => ({ open, setOpen, triggerRef, contentRef }),
    [open],
  );
  // Sin ``relative inline-block`` envolvente: el contenido se renderiza
  // via portal, no hace falta un anchor en el árbol del DOM aquí.
  return (
    <DropdownContext.Provider value={ctx}>{children}</DropdownContext.Provider>
  );
}

const DropdownMenuTrigger = React.forwardRef<
  HTMLButtonElement,
  React.ButtonHTMLAttributes<HTMLButtonElement>
>(({ className, onClick, ...props }, forwardedRef) => {
  const { open, setOpen, triggerRef } = useDropdown();
  // Combinar ref interno (para portal positioning) con el opcional del consumidor.
  const setRefs = React.useCallback(
    (node: HTMLButtonElement | null): void => {
      triggerRef.current = node;
      if (typeof forwardedRef === "function") forwardedRef(node);
      else if (forwardedRef)
        (forwardedRef as React.MutableRefObject<HTMLButtonElement | null>).current = node;
    },
    [forwardedRef, triggerRef],
  );
  return (
    <button
      ref={setRefs}
      type="button"
      aria-haspopup="menu"
      aria-expanded={open}
      onClick={(e) => {
        onClick?.(e);
        setOpen(!open);
      }}
      className={cn(
        "inline-flex items-center justify-center rounded-md p-1 text-[rgb(var(--foreground-muted))] transition-colors hover:bg-[rgb(var(--background-elevated))] hover:text-[rgb(var(--foreground))]",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[rgb(var(--accent))]",
        className,
      )}
      {...props}
    />
  );
});
DropdownMenuTrigger.displayName = "DropdownMenuTrigger";

interface DropdownMenuContentProps extends React.HTMLAttributes<HTMLDivElement> {
  align?: "start" | "end";
  /** Gap vertical entre el trigger y el menú, en píxeles. */
  sideOffset?: number;
}

/**
 * El contenido se monta en un portal a ``document.body`` con
 * ``position: fixed``, calculando la posición a partir del rect del
 * trigger. Así escapa de cualquier ``overflow: hidden | auto`` ancestro
 * (tablas, cards, dialogs) que de otra forma clipearía el menú o
 * generaría scrollbars en el padre.
 */
const DropdownMenuContent = React.forwardRef<
  HTMLDivElement,
  DropdownMenuContentProps
>(({ className, align = "end", sideOffset = 4, ...props }, forwardedRef) => {
  const { open, triggerRef, contentRef } = useDropdown();
  const [coords, setCoords] = React.useState<{ top: number; left: number; right: number } | null>(
    null,
  );
  const [mounted, setMounted] = React.useState(false);

  React.useEffect(() => {
    setMounted(true);
  }, []);

  // Recalcula posición al abrir + en scroll/resize mientras esté abierto.
  React.useEffect(() => {
    if (!open) {
      setCoords(null);
      return;
    }
    const update = (): void => {
      const node = triggerRef.current;
      if (!node) return;
      const rect = node.getBoundingClientRect();
      setCoords({
        top: rect.bottom + sideOffset,
        left: rect.left,
        right: window.innerWidth - rect.right,
      });
    };
    update();
    window.addEventListener("scroll", update, true);
    window.addEventListener("resize", update);
    return () => {
      window.removeEventListener("scroll", update, true);
      window.removeEventListener("resize", update);
    };
  }, [open, sideOffset, triggerRef]);

  const setRefs = React.useCallback(
    (node: HTMLDivElement | null): void => {
      contentRef.current = node;
      if (typeof forwardedRef === "function") forwardedRef(node);
      else if (forwardedRef)
        (forwardedRef as React.MutableRefObject<HTMLDivElement | null>).current = node;
    },
    [forwardedRef, contentRef],
  );

  if (!open || !mounted || !coords) return null;

  const positionStyle: React.CSSProperties =
    align === "end"
      ? { position: "fixed", top: coords.top, right: coords.right }
      : { position: "fixed", top: coords.top, left: coords.left };

  return createPortal(
    <div
      ref={setRefs}
      role="menu"
      style={positionStyle}
      className={cn(
        "z-50 min-w-[10rem] rounded-md border border-[rgb(var(--border))] bg-[rgb(var(--card))] p-1 text-sm shadow-lg",
        className,
      )}
      {...props}
    />,
    document.body,
  );
});
DropdownMenuContent.displayName = "DropdownMenuContent";

interface DropdownMenuItemProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  onSelect?: () => void;
  variant?: "default" | "danger";
}

const DropdownMenuItem = React.forwardRef<
  HTMLButtonElement,
  DropdownMenuItemProps
>(({ className, onSelect, onClick, variant = "default", ...props }, ref) => {
  const { setOpen } = useDropdown();
  return (
    <button
      ref={ref}
      type="button"
      role="menuitem"
      onClick={(e) => {
        onClick?.(e);
        onSelect?.();
        setOpen(false);
      }}
      className={cn(
        "flex w-full items-center gap-2 rounded-sm px-2 py-1.5 text-left text-sm transition-colors",
        variant === "danger"
          ? "text-[rgb(var(--danger))] hover:bg-[rgb(var(--danger)/0.15)]"
          : "text-[rgb(var(--foreground))] hover:bg-[rgb(var(--background-elevated))]",
        className,
      )}
      {...props}
    />
  );
});
DropdownMenuItem.displayName = "DropdownMenuItem";

const DropdownMenuSeparator = React.forwardRef<
  HTMLDivElement,
  React.HTMLAttributes<HTMLDivElement>
>(({ className, ...props }, ref) => (
  <div
    ref={ref}
    role="separator"
    className={cn("my-1 h-px bg-[rgb(var(--border))]", className)}
    {...props}
  />
));
DropdownMenuSeparator.displayName = "DropdownMenuSeparator";

export {
  DropdownMenu,
  DropdownMenuTrigger,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
};
