import type { ReactNode } from "react";

/**
 * Layout for unauthenticated routes (currently just `/login`). No sidebar,
 * no chrome — just a centered card area against the GitHub Dark background.
 */
export default function AuthLayout({
  children,
}: {
  children: ReactNode;
}): React.JSX.Element {
  return (
    <main className="flex min-h-screen items-center justify-center bg-[rgb(var(--background))] px-4 py-8">
      <div className="w-full max-w-sm">{children}</div>
    </main>
  );
}
