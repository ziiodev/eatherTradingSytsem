import * as React from "react";

import { cn } from "@/lib/utils";

/**
 * Plain controlled checkbox styled to match the GitHub Dark palette.
 *
 * Native <input type="checkbox"> is enough for the trading-sessions
 * multi-select; Radix is overkill until we need keyboard menus.
 */
const Checkbox = React.forwardRef<
  HTMLInputElement,
  Omit<React.InputHTMLAttributes<HTMLInputElement>, "type">
>(({ className, ...props }, ref) => (
  <input
    ref={ref}
    type="checkbox"
    className={cn(
      "h-4 w-4 cursor-pointer rounded-sm border border-[rgb(var(--border))] bg-[rgb(var(--background-elevated))] text-[rgb(var(--accent))] accent-[rgb(var(--accent))]",
      "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[rgb(var(--accent))] focus-visible:ring-offset-1 focus-visible:ring-offset-[rgb(var(--background))]",
      "disabled:cursor-not-allowed disabled:opacity-50",
      className,
    )}
    {...props}
  />
));
Checkbox.displayName = "Checkbox";

export { Checkbox };
