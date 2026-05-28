import * as React from "react";

import { cn } from "@/lib/utils";

/**
 * Multi-line input. Matches the shadcn/ui contract so it is drop-in
 * replaceable with the upstream component if we later run
 * `pnpm dlx shadcn@latest add textarea`.
 */
const Textarea = React.forwardRef<
  HTMLTextAreaElement,
  React.TextareaHTMLAttributes<HTMLTextAreaElement>
>(({ className, ...props }, ref) => {
  return (
    <textarea
      ref={ref}
      className={cn(
        "flex min-h-[80px] w-full rounded-md border border-[rgb(var(--border))] bg-[rgb(var(--background-elevated))] px-3 py-2 text-sm text-[rgb(var(--foreground))] shadow-sm transition-colors",
        "placeholder:text-[rgb(var(--foreground-muted))]",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[rgb(var(--accent))] focus-visible:ring-offset-1 focus-visible:ring-offset-[rgb(var(--background))]",
        "disabled:cursor-not-allowed disabled:opacity-50",
        className,
      )}
      {...props}
    />
  );
});
Textarea.displayName = "Textarea";

export { Textarea };
