import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

/**
 * Canonical class-name composition helper used by every shadcn/ui component.
 *
 * - `clsx` resolves conditionals and arrays into a single class string.
 * - `tailwind-merge` deduplicates conflicting Tailwind utilities so the
 *   later one wins (e.g. `px-2 px-4` collapses to `px-4`).
 */
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}
