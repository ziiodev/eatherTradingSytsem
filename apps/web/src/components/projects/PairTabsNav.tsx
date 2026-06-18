"use client";

/**
 * PairTabsNav — three top-level tabs of the pair (Par) detail view.
 *
 * Renders `<Operativa>`, `<Chat>`, `<Configuración>` as `next/link` anchors.
 * The active tab is derived from `usePathname()` and visually accented with
 * the existing GitHub blue token (`--accent`); no new colors introduced.
 *
 * Sub-routes (memoria, q-tables, sleep-runs) sit OUTSIDE these three top
 * tabs — they are deep links surfaced separately via `<LearningNav>` and
 * intentionally don't light up any of the three top tabs while active.
 */

import Link from "next/link";
import { usePathname } from "next/navigation";

import { cn } from "@/lib/utils";

export interface PairTabsNavProps {
  accountId: string;
  pairId: string;
}

interface TabEntry {
  segment: string;
  label: string;
}

const TABS: ReadonlyArray<TabEntry> = [
  { segment: "operativa", label: "Operativa" },
  { segment: "chat", label: "Chat" },
  { segment: "configuracion", label: "Configuración" },
];

export function PairTabsNav({
  accountId,
  pairId,
}: PairTabsNavProps): React.JSX.Element {
  const pathname = usePathname() ?? "";
  const base = `/cuentas/${accountId}/pares/${pairId}`;
  return (
    <nav
      aria-label="Secciones del par"
      className="flex flex-wrap items-center gap-1 border-b border-[rgb(var(--border))]"
    >
      {TABS.map(({ segment, label }) => {
        const href = `${base}/${segment}`;
        const active = pathname.startsWith(href);
        return (
          <Link
            key={segment}
            href={href}
            aria-current={active ? "page" : undefined}
            data-testid={`pair-tab-${segment}`}
            className={cn(
              "inline-flex items-center gap-2 border-b-2 px-3 py-2 text-sm transition-colors -mb-px",
              active
                ? "border-[rgb(var(--accent))] text-[rgb(var(--foreground))] font-medium"
                : "border-transparent text-[rgb(var(--foreground-muted))] hover:text-[rgb(var(--foreground))]",
            )}
          >
            {label}
          </Link>
        );
      })}
    </nav>
  );
}

export default PairTabsNav;
