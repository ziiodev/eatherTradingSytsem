/**
 * Pair detail layout — shared chrome for
 * `/cuentas/[accountId]/pares/[pairId]/**`.
 *
 * Server component that wraps every page under the `[pairId]` route segment:
 *
 *   ┌──────────────────────────────────────────────────────────┐
 *   │  BackLink                                                │
 *   │  Pair name  StatusBadge  [Activar][Pausar]…[Eliminar]    │  ← PairHeader (client)
 *   │  LearningNav (flag-gated)                                │  ← LearningNav (client)
 *   │  Operativa | Chat | Configuración                        │  ← PairTabsNav (client)
 *   │ ──────────────────────────────────────────────────────── │
 *   │  {children}                                              │  ← per-route page
 *   └──────────────────────────────────────────────────────────┘
 *
 * Sub-routes (memoria, q-tables, sleep-runs) reach this same layout and
 * therefore inherit the chrome.
 */

import { PairHeader } from "@/components/projects/PairHeader";
import { PairTabsNav } from "@/components/projects/PairTabsNav";
import { LearningNav } from "@/components/projects/LearningNav";

export default async function PairDetailLayout({
  children,
  params,
}: {
  children: React.ReactNode;
  params: Promise<{ accountId: string; pairId: string }>;
}): Promise<React.JSX.Element> {
  // Next 16: params is async — await directly in server components.
  const { accountId, pairId } = await params;
  return (
    <section className="flex flex-col gap-4">
      <PairHeader accountId={accountId} pairId={pairId} />
      <LearningNav accountId={accountId} pairId={pairId} />
      <PairTabsNav accountId={accountId} pairId={pairId} />
      <div>{children}</div>
    </section>
  );
}
