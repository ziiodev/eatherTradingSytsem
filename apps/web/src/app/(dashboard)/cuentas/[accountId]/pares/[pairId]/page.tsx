/**
 * `/cuentas/[accountId]/pares/[pairId]` — default landing redirect.
 *
 * The pair detail screen has three top-level tabs (Operativa, Chat,
 * Configuración) backed by their own route segments. We funnel the bare
 * pair URL to Configuración.
 *
 * Server-side redirect avoids the brief flash a client-side one would
 * cause and keeps the URL canonical for bookmarks / back-button.
 */

import { redirect } from "next/navigation";

export default async function PairDetailIndexPage({
  params,
}: {
  params: Promise<{ accountId: string; pairId: string }>;
}): Promise<never> {
  const { accountId, pairId } = await params;
  redirect(`/cuentas/${accountId}/pares/${pairId}/configuracion`);
}
