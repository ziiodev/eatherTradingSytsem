"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  Bot,
  LayoutGrid,
  LogOut,
  Settings,
  Sparkles,
  Workflow,
  type LucideIcon,
} from "lucide-react";

import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { logout } from "@/lib/auth";

/**
 * Charter-mandated fixed sidebar. The four entries, their order, and their
 * spelling (including the accent in `Configuración`) are PRODUCT-FIXED.
 * Do not add, rename, or reorder these without explicit user approval.
 *
 * accounts-pairs-restructure: the first entry was renamed `Proyectos` →
 * `Cuentas` (and its href `/proyectos` → `/cuentas`) per the explicit
 * user approval recorded in the change decisions. The other three entries
 * are untouched.
 *
 * ea-management: a FIFTH entry "Gestión EAs" (`/eas`) was added as a SANCTIONED
 * exception to the four-entry lock (user-approved 2026-06-19). It is gated by
 * `NEXT_PUBLIC_AETHER_EAS_ENABLED` (frontend mirror of the backend
 * `AETHER_EAS_ENABLED` flag, default OFF): the entry is hidden unless the flag
 * is explicitly set truthy.
 */
interface SidebarEntry {
  href: string;
  label: string;
  Icon: LucideIcon;
}

/**
 * Whether the EA-management surface is enabled on the frontend. Mirrors the
 * backend `AETHER_EAS_ENABLED` flag; OFF by default. `NEXT_PUBLIC_*` env vars
 * are inlined at build time, so this is a static boolean per build.
 */
const EAS_ENABLED =
  process.env.NEXT_PUBLIC_AETHER_EAS_ENABLED === "true" ||
  process.env.NEXT_PUBLIC_AETHER_EAS_ENABLED === "1";

const ENTRIES: ReadonlyArray<SidebarEntry> = [
  { href: "/cuentas", label: "Cuentas", Icon: LayoutGrid },
  { href: "/agentes", label: "Agentes", Icon: Bot },
  { href: "/skills", label: "Skills", Icon: Sparkles },
  ...(EAS_ENABLED
    ? [{ href: "/eas", label: "Gestión EAs", Icon: Workflow }]
    : []),
  { href: "/configuracion", label: "Configuración", Icon: Settings },
];

export interface SidebarProps {
  userEmail?: string | null;
}

export function Sidebar({ userEmail }: SidebarProps): React.JSX.Element {
  const pathname = usePathname();
  const initials = (userEmail ?? "??").slice(0, 2).toUpperCase();

  return (
    <aside
      aria-label="Navegación principal"
      className="flex h-screen w-[240px] shrink-0 flex-col border-r border-[rgb(var(--border))] bg-[rgb(var(--background-elevated))]"
    >
      {/* Brand — clickable: returns to the main dashboard view (`/`) */}
      <Link
        href="/"
        aria-label="Ir al dashboard principal"
        className="flex h-14 items-center gap-2 px-4 transition-colors hover:bg-[rgb(var(--background))]"
      >
        <div
          aria-hidden
          className="flex h-8 w-8 items-center justify-center rounded-md bg-[rgb(var(--accent))] text-[rgb(var(--accent-foreground))]"
        >
          <span className="text-sm font-bold">A</span>
        </div>
        <span className="text-base font-semibold tracking-tight">Aether</span>
      </Link>
      <Separator />

      {/* Navigation */}
      <nav className="flex flex-1 flex-col gap-1 p-2" aria-label="Secciones">
        {ENTRIES.map(({ href, label, Icon }) => {
          const isActive =
            pathname === href || pathname.startsWith(`${href}/`);
          return (
            <Link
              key={href}
              href={href}
              aria-current={isActive ? "page" : undefined}
              className={cn(
                "flex items-center gap-3 rounded-md px-3 py-2 text-sm transition-colors",
                isActive
                  ? "bg-[rgb(var(--accent)/0.15)] text-[rgb(var(--accent))]"
                  : "text-[rgb(var(--foreground))] hover:bg-[rgb(var(--background))]",
              )}
            >
              <Icon className="h-4 w-4" aria-hidden />
              <span>{label}</span>
            </Link>
          );
        })}
      </nav>

      {/* User dropdown stub */}
      <Separator />
      <div className="flex items-center gap-2 p-3">
        <Avatar>
          <AvatarFallback>{initials}</AvatarFallback>
        </Avatar>
        <div className="flex min-w-0 flex-1 flex-col">
          <span className="truncate text-xs font-medium">
            {userEmail ?? "Invitado"}
          </span>
          <span className="text-[10px] text-[rgb(var(--foreground-muted))]">
            Sesión activa
          </span>
        </div>
        <Button
          variant="ghost"
          size="icon"
          aria-label="Cerrar sesión"
          onClick={() => {
            void logout();
          }}
        >
          <LogOut className="h-4 w-4" />
        </Button>
      </div>
    </aside>
  );
}

export default Sidebar;
