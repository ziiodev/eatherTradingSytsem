"use client";

/**
 * Route-scoped TanStack Query boundary for the "Gestión EAs" route tree.
 *
 * IMPORTANT — scoping rationale (ea-management, Phase 4):
 * Aether fetches data via React Server Components everywhere else. TanStack
 * Query is introduced ONLY for the visual EA editor (React Flow + client-side
 * graph mutation), so this provider is deliberately NOT app-wide. It lives at
 * the `(dashboard)/eas` segment layout and wraps only that subtree.
 *
 * The QueryClient is created lazily via useState so it is stable across
 * re-renders and never shared between requests on the server.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";

export function EasQueryProvider({ children }: { children: ReactNode }) {
  const [client] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: 60_000, // 1 min: avoid refetch storms during editing
            refetchOnWindowFocus: false,
          },
        },
      }),
  );

  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}
