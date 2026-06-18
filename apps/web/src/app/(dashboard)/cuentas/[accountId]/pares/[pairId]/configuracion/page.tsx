"use client";

/**
 * Configuración — top-level tab of the pair detail screen.
 *
 * Thin shell: unwraps the route params and hands the pair id off to
 * `<ConfiguracionTab>`. All page chrome (BackLink, pair header with
 * lifecycle actions + Eliminar, LearningNav, three-tab nav) lives in the
 * parent `[pairId]/layout.tsx`.
 */

import { use } from "react";

import { ConfiguracionTab } from "@/components/projects/ConfiguracionTab";

export default function ConfiguracionPage({
  params,
}: {
  params: Promise<{ accountId: string; pairId: string }>;
}): React.JSX.Element {
  const { pairId } = use(params);
  return <ConfiguracionTab pairId={pairId} />;
}
