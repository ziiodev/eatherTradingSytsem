import * as React from "react";

import { cn } from "@/lib/utils";

const Input = React.forwardRef<
  HTMLInputElement,
  React.InputHTMLAttributes<HTMLInputElement>
>(({ className, type, ...props }, ref) => {
  return (
    <input
      type={type}
      ref={ref}
      className={cn(
        "flex h-9 w-full rounded-md border border-[rgb(var(--border))] bg-[rgb(var(--background-elevated))] px-3 py-1 text-sm text-[rgb(var(--foreground))] shadow-sm transition-colors",
        "placeholder:text-[rgb(var(--foreground-muted))]",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[rgb(var(--accent))] focus-visible:ring-offset-1 focus-visible:ring-offset-[rgb(var(--background))]",
        "disabled:cursor-not-allowed disabled:opacity-50",
        "file:border-0 file:bg-transparent file:text-sm file:font-medium",
        className,
      )}
      {...props}
    />
  );
});
Input.displayName = "Input";

export { Input };
