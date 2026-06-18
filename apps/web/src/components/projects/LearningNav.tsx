"use client";

/**
 * LearningNav — three sub-nav links surfaced on the project detail view
 * and on every learning sub-page (Q-Tables, Memoria, Sleep Runs).
 *
 * Visibility is gated by `NEXT_PUBLIC_LEARNING_UI_ENABLED`. When the flag
 * is not exactly `"true"` the component renders nothing — but the pages
 * themselves still resolve so admins with a direct URL can hit them.
 *
 * **Flag contract — important**: `NEXT_PUBLIC_*` env vars are inlined at
 * BUILD time in Next.js, not read at runtime. A flag flip therefore
 * requires a rebuild. This is acceptable for v1; if we need runtime
 * toggling later we'll move the check to a server-rendered config
 * endpoint.
 */

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Brain, Layers, MoonStar } from "lucide-react";

import { cn } from "@/lib/utils";

export interface LearningNavProps {
  accountId: string;
  pairId: string;
  className?: string;
}

interface NavEntry {
  href: (base: string) => string;
  label: string;
  match: (pathname: string, base: string) => boolean;
  Icon: typeof Brain;
}

const ENTRIES: ReadonlyArray<NavEntry> = [
  {
    href: (base) => `${base}/q-tables`,
    label: "Q-Tables",
    match: (p, base) => p.startsWith(`${base}/q-tables`),
    Icon: Layers,
  },
  {
    href: (base) => `${base}/memoria`,
    label: "Memoria",
    match: (p, base) => p.startsWith(`${base}/memoria`),
    Icon: Brain,
  },
  {
    href: (base) => `${base}/sleep-runs`,
    label: "Sleep Runs",
    match: (p, base) => p.startsWith(`${base}/sleep-runs`),
    Icon: MoonStar,
  },
];

/**
 * Pure flag-check (export so the page components can decide whether to
 * surface the back-nav as well).
 */
export function isLearningUiEnabled(): boolean {
  return process.env.NEXT_PUBLIC_LEARNING_UI_ENABLED === "true";
}

export function LearningNav({
  accountId,
  pairId,
  className,
}: LearningNavProps): React.JSX.Element | null {
  const pathname = usePathname() ?? "";
  const base = `/cuentas/${accountId}/pares/${pairId}`;
  if (!isLearningUiEnabled()) return null;
  return (
    <nav
      aria-label="Aprendizaje del par"
      className={cn(
        "flex flex-wrap items-center gap-1 rounded-md border border-[rgb(var(--border))] bg-[rgb(var(--background-elevated))] p-1",
        className,
      )}
    >
      {ENTRIES.map(({ href, label, match, Icon }) => {
        const active = match(pathname, base);
        return (
          <Link
            key={label}
            href={href(base)}
            aria-current={active ? "page" : undefined}
            className={cn(
              "inline-flex items-center gap-2 rounded-sm px-3 py-1 text-sm transition-colors",
              active
                ? "bg-[rgb(var(--background))] text-[rgb(var(--foreground))] shadow-sm"
                : "text-[rgb(var(--foreground-muted))] hover:text-[rgb(var(--foreground))]",
            )}
          >
            <Icon className="h-3.5 w-3.5" aria-hidden />
            <span>{label}</span>
          </Link>
        );
      })}
    </nav>
  );
}

export default LearningNav;
