import type { ReactNode } from "react";

import { EasQueryProvider } from "./_components/query-provider";

/**
 * Segment layout for the "Gestión EAs" route tree.
 *
 * This layout exists to mount the route-scoped TanStack Query boundary
 * (`EasQueryProvider`) around the EA editor + management surface ONLY — it
 * does not leak Query into the rest of the dashboard, which stays on RSC
 * data fetching. The editor UI itself is built in Phase 5.
 */
export default function EasLayout({ children }: { children: ReactNode }) {
  return <EasQueryProvider>{children}</EasQueryProvider>;
}
