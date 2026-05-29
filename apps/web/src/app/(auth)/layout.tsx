import type { ReactNode } from "react";

import { NumberFieldBackground } from "@/components/auth/NumberFieldBackground";

/**
 * Layout for unauthenticated routes (currently just `/login`). No sidebar,
 * no chrome — just a centered card area against the GitHub Dark background.
 * Behind the card sits a discreet interactive numeric grid that brightens
 * around the cursor (decorativo, sin datos).
 */
export default function AuthLayout({
  children,
}: {
  children: ReactNode;
}): React.JSX.Element {
  return (
    <main className="relative flex min-h-screen items-center justify-center overflow-hidden bg-[rgb(var(--background))] px-4 py-8">
      <NumberFieldBackground />
      <div className="relative z-10 w-full max-w-sm">{children}</div>
    </main>
  );
}
