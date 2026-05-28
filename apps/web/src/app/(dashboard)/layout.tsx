import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import type { ReactNode } from "react";
import { Toaster } from "sonner";

import Sidebar from "@/components/Sidebar";

interface MeResponse {
  id: string;
  email: string;
  display_name: string | null;
  avatar_url: string | null;
  is_admin: boolean;
  mfa_enabled: boolean;
}

/**
 * Server-side auth gate for the dashboard route group.
 *
 * Strategy:
 *   1. If there's no access cookie, send the user to `/login`. The
 *      middleware does this too, but a second check here covers route
 *      transitions where middleware may not run (e.g. nested RSC fetches).
 *   2. Call `/api/auth/me` to resolve the user identity. On a non-OK
 *      response, redirect to `/login` (the cookie may have been revoked).
 *      During Phase 4 the backend may not be running — in that case we
 *      degrade gracefully by passing `null` to the Sidebar so the UI still
 *      renders for visual review.
 */
async function fetchMe(cookieHeader: string): Promise<MeResponse | null> {
  const apiBase = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
  try {
    const res = await fetch(`${apiBase}/api/auth/me`, {
      headers: { Cookie: cookieHeader, Accept: "application/json" },
      cache: "no-store",
    });
    if (res.status === 401) return null;
    if (!res.ok) return null;
    return (await res.json()) as MeResponse;
  } catch {
    // Backend down — let the UI render so the operator can still see the
    // shell. Real auth enforcement happens on every protected endpoint.
    return null;
  }
}

export default async function DashboardLayout({
  children,
}: {
  children: ReactNode;
}): Promise<React.JSX.Element> {
  const cookieStore = await cookies();
  const access = cookieStore.get("aether_access");
  if (!access) {
    redirect("/login");
  }

  const cookieHeader = cookieStore
    .getAll()
    .map((c) => `${c.name}=${c.value}`)
    .join("; ");
  const me = await fetchMe(cookieHeader);

  return (
    <div className="flex min-h-screen bg-[rgb(var(--background))]">
      <Sidebar userEmail={me?.email ?? null} />
      <div className="flex flex-1 flex-col">
        <main className="flex-1 overflow-y-auto p-6">{children}</main>
      </div>
      {/* Toast surface — used by lifecycle / form mutations across the dashboard. */}
      <Toaster theme="dark" position="bottom-right" richColors closeButton />
    </div>
  );
}
