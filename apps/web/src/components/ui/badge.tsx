import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";

import { cn } from "@/lib/utils";

/**
 * Status pill. The lifecycle UI uses one badge per status with a colour
 * tied to its semantic meaning.
 */
const badgeVariants = cva(
  "inline-flex items-center gap-1 rounded-md px-2 py-0.5 text-xs font-medium transition-colors",
  {
    variants: {
      variant: {
        default:
          "bg-[rgb(var(--background-elevated))] text-[rgb(var(--foreground))] border border-[rgb(var(--border))]",
        success:
          "bg-[rgb(var(--success)/0.15)] text-[rgb(var(--success))] border border-[rgb(var(--success)/0.4)]",
        warning:
          "bg-[rgb(var(--warning)/0.15)] text-[rgb(var(--warning))] border border-[rgb(var(--warning)/0.4)]",
        danger:
          "bg-[rgb(var(--danger)/0.15)] text-[rgb(var(--danger))] border border-[rgb(var(--danger)/0.4)]",
        muted:
          "bg-[rgb(var(--background-elevated))] text-[rgb(var(--foreground-muted))] border border-[rgb(var(--border))]",
        accent:
          "bg-[rgb(var(--accent)/0.15)] text-[rgb(var(--accent))] border border-[rgb(var(--accent)/0.4)]",
      },
    },
    defaultVariants: { variant: "default" },
  },
);

export interface BadgeProps
  extends React.HTMLAttributes<HTMLSpanElement>,
    VariantProps<typeof badgeVariants> {}

function Badge({ className, variant, ...props }: BadgeProps): React.JSX.Element {
  return (
    <span className={cn(badgeVariants({ variant, className }))} {...props} />
  );
}

export { Badge, badgeVariants };
