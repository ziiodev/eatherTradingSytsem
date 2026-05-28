"use client";

import * as React from "react";

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
  const containerRef = React.useRef<HTMLDivElement>(null);

  // Outside-click closes the menu.
  React.useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: MouseEvent): void => {
      if (
        containerRef.current &&
        event.target instanceof Node &&
        !containerRef.current.contains(event.target)
      ) {
        setOpen(false);
      }
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

  const ctx = React.useMemo(() => ({ open, setOpen }), [open]);
  return (
    <DropdownContext.Provider value={ctx}>
      <div ref={containerRef} className="relative inline-block">
        {children}
      </div>
    </DropdownContext.Provider>
  );
}

const DropdownMenuTrigger = React.forwardRef<
  HTMLButtonElement,
  React.ButtonHTMLAttributes<HTMLButtonElement>
>(({ className, onClick, ...props }, ref) => {
  const { open, setOpen } = useDropdown();
  return (
    <button
      ref={ref}
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
}

const DropdownMenuContent = React.forwardRef<
  HTMLDivElement,
  DropdownMenuContentProps
>(({ className, align = "end", ...props }, ref) => {
  const { open } = useDropdown();
  if (!open) return null;
  return (
    <div
      ref={ref}
      role="menu"
      className={cn(
        "absolute z-50 mt-1 min-w-[10rem] rounded-md border border-[rgb(var(--border))] bg-[rgb(var(--card))] p-1 text-sm shadow-lg",
        align === "end" ? "right-0" : "left-0",
        className,
      )}
      {...props}
    />
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
