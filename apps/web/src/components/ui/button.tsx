import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";

import { cn } from "@/lib/utils";

/**
 * Button component (shadcn/ui flavor, adapted for Tailwind v4 + GitHub Dark).
 *
 * Theme colors come from CSS vars in `globals.css` and are consumed via
 * `bg-[rgb(var(--accent))]` style utilities — Tailwind v4 evaluates those at
 * build time and produces real classes. This avoids relying on a
 * `tailwind.config.js` (which we intentionally do not have).
 */
const buttonVariants = cva(
  "inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-md text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[rgb(var(--accent))] focus-visible:ring-offset-2 focus-visible:ring-offset-[rgb(var(--background))] disabled:pointer-events-none disabled:opacity-50",
  {
    variants: {
      variant: {
        default:
          "bg-[rgb(var(--accent))] text-[rgb(var(--accent-foreground))] hover:bg-[rgb(var(--accent)/0.9)]",
        destructive:
          "bg-[rgb(var(--danger))] text-white hover:bg-[rgb(var(--danger)/0.9)]",
        outline:
          "border border-[rgb(var(--border))] bg-transparent text-[rgb(var(--foreground))] hover:bg-[rgb(var(--background-elevated))]",
        secondary:
          "bg-[rgb(var(--background-elevated))] text-[rgb(var(--foreground))] hover:bg-[rgb(var(--background-elevated)/0.8)]",
        ghost:
          "text-[rgb(var(--foreground))] hover:bg-[rgb(var(--background-elevated))]",
        link: "text-[rgb(var(--accent))] underline-offset-4 hover:underline",
      },
      size: {
        default: "h-9 px-4 py-2",
        sm: "h-8 rounded-md px-3 text-xs",
        lg: "h-10 rounded-md px-6",
        icon: "h-9 w-9",
      },
    },
    defaultVariants: {
      variant: "default",
      size: "default",
    },
  },
);

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {}

const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, type = "button", ...props }, ref) => {
    return (
      <button
        ref={ref}
        type={type}
        className={cn(buttonVariants({ variant, size, className }))}
        {...props}
      />
    );
  },
);
Button.displayName = "Button";

export { Button, buttonVariants };
