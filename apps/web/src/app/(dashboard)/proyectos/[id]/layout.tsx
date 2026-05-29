/**
 * Project detail layout — shared chrome for `/proyectos/[id]/**`.
 *
 * Server component that wraps every page under the `[id]` route segment:
 *
 *   ┌──────────────────────────────────────────────────────────┐
 *   │  BackLink                                                │
 *   │  Project name  StatusBadge  [Activar][Pausar]…[Eliminar] │  ← ProjectHeader (client)
 *   │  LearningNav (flag-gated)                                │  ← LearningNav (client)
 *   │  Operativa | Chat | Configuración                        │  ← ProjectTabsNav (client)
 *   │ ──────────────────────────────────────────────────────── │
 *   │  {children}                                              │  ← per-route page
 *   └──────────────────────────────────────────────────────────┘
 *
 * Sub-routes (memoria, q-tables, sleep-runs) reach this same layout and
 * therefore inherit the chrome — they shouldn't re-render their own
 * BackLink/header/LearningNav (handled in T6).
 */

import { ProjectHeader } from "@/components/projects/ProjectHeader";
import { ProjectTabsNav } from "@/components/projects/ProjectTabsNav";
import { LearningNav } from "@/components/projects/LearningNav";

export default async function ProjectDetailLayout({
  children,
  params,
}: {
  children: React.ReactNode;
  params: Promise<{ id: string }>;
}): Promise<React.JSX.Element> {
  // Next 16: params is async — await directly in server components.
  const { id: projectId } = await params;
  return (
    <section className="flex flex-col gap-4">
      <ProjectHeader projectId={projectId} />
      <LearningNav projectId={projectId} />
      <ProjectTabsNav projectId={projectId} />
      <div>{children}</div>
    </section>
  );
}
