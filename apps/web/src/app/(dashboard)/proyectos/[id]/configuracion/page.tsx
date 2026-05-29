"use client";

/**
 * Configuración — top-level tab of the project detail screen.
 *
 * Thin shell: unwraps the route param and hands the project id off to
 * `<ConfiguracionTab>`. All page chrome (BackLink, project header with
 * lifecycle actions + Eliminar, LearningNav, three-tab nav) lives in the
 * parent `[id]/layout.tsx`.
 */

import { use } from "react";

import { ConfiguracionTab } from "@/components/projects/ConfiguracionTab";

export default function ConfiguracionPage({
  params,
}: {
  params: Promise<{ id: string }>;
}): React.JSX.Element {
  const { id: projectId } = use(params);
  return <ConfiguracionTab projectId={projectId} />;
}
