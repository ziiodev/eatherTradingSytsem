/**
 * `/proyectos/[id]` — default landing redirect.
 *
 * The project detail screen now has three top-level tabs (Operativa, Chat,
 * Configuración) backed by their own route segments. We funnel the bare
 * project URL to Configuración because — until the sibling SDD changes
 * `project-operativa` and `project-chat` ship — those tabs are intentional
 * placeholders, and landing on a "próximamente" card would feel broken.
 *
 * Server-side redirect avoids the brief flash a client-side one would
 * cause and keeps the URL canonical for bookmarks / back-button.
 */

import { redirect } from "next/navigation";

export default async function ProjectDetailIndexPage({
  params,
}: {
  params: Promise<{ id: string }>;
}): Promise<never> {
  const { id } = await params;
  redirect(`/proyectos/${id}/configuracion`);
}
