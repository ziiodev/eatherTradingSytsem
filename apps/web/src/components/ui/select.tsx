import * as React from "react";
import { ChevronDown } from "lucide-react";

import { cn } from "@/lib/utils";

/**
 * Native <select> wrapped with shadcn-styled chrome.
 *
 * This is intentionally NOT a Radix-based combobox — for the project
 * filters and the lifecycle picker we need keyboard semantics that work
 * inside react-hook-form via ``{...register("status")}``. A native
 * <select> covers both at zero JS cost.
 */
const Select = React.forwardRef<
  HTMLSelectElement,
  React.SelectHTMLAttributes<HTMLSelectElement>
>(({ className, children, ...props }, ref) => (
  <div className="relative">
    <select
      ref={ref}
      className={cn(
        "flex h-9 w-full appearance-none rounded-md border border-[rgb(var(--border))] bg-[rgb(var(--background-elevated))] px-3 py-1 pr-8 text-sm text-[rgb(var(--foreground))] shadow-sm transition-colors",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[rgb(var(--accent))] focus-visible:ring-offset-1 focus-visible:ring-offset-[rgb(var(--background))]",
        "disabled:cursor-not-allowed disabled:opacity-50",
        className,
      )}
      {...props}
    >
      {children}
    </select>
    <ChevronDown
      aria-hidden
      className="pointer-events-none absolute right-2 top-1/2 h-4 w-4 -translate-y-1/2 text-[rgb(var(--foreground-muted))]"
    />
  </div>
));
Select.displayName = "Select";

export { Select };
