/**
 * Frontend domain types + zod schemas for the Exchanges surface.
 *
 * Mirrors the Pydantic v2 models in
 * ``apps/api/src/aether_api/routers/exchanges.py``. The Exchange is the top
 * of the accounts-pairs hierarchy ``Exchange → Account (Cuenta) → Pair (Par)
 * → Agents``.
 *
 * IMPORTANT: any change to the backend DTOs must be mirrored here.
 */

import { z } from "zod";

import { apiDelete, apiGet, apiPatch, apiPost } from "@/lib/api";

// ---------------------------------------------------------------------------
// Canonical constants — mirrors `aether_api.models.exchange.EXCHANGE_KINDS`.
// ---------------------------------------------------------------------------
export const EXCHANGE_KINDS = ["broker", "exchange", "prop", "demo"] as const;
export type ExchangeKind = (typeof EXCHANGE_KINDS)[number];

export const EXCHANGE_KIND_LABEL: Record<ExchangeKind, string> = {
  broker: "Bróker",
  exchange: "Exchange",
  prop: "Prop firm",
  demo: "Demo",
};

// ---------------------------------------------------------------------------
// API response shapes.
// ---------------------------------------------------------------------------
export interface Exchange {
  id: string;
  name: string;
  code: string;
  kind: string;
  meta_data: Record<string, unknown>;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface ExchangeListResponse {
  items: Exchange[];
  total: number;
  limit: number;
  offset: number;
}

// ---------------------------------------------------------------------------
// zod schemas — paired 1:1 with the backend Pydantic models.
// ---------------------------------------------------------------------------
export const exchangeCreateSchema = z.object({
  name: z
    .string()
    .min(1, "Nombre requerido")
    .max(100, "Máximo 100 caracteres"),
  code: z
    .string()
    .min(1, "Código requerido")
    .max(40, "Máximo 40 caracteres"),
  kind: z.enum(EXCHANGE_KINDS, {
    errorMap: () => ({ message: "Tipo inválido" }),
  }),
  meta_data: z.record(z.unknown()).optional(),
});

export type ExchangeCreateInput = z.infer<typeof exchangeCreateSchema>;

export const exchangePatchSchema = exchangeCreateSchema.partial();
export type ExchangePatchInput = z.infer<typeof exchangePatchSchema>;

// ---------------------------------------------------------------------------
// API helpers.
// ---------------------------------------------------------------------------
export interface ExchangeListParams {
  limit?: number;
  offset?: number;
}

function buildListPath(params: ExchangeListParams): string {
  const sp = new URLSearchParams();
  if (params.limit !== undefined) sp.set("limit", String(params.limit));
  if (params.offset !== undefined) sp.set("offset", String(params.offset));
  const qs = sp.toString();
  return qs ? `/api/exchanges?${qs}` : "/api/exchanges";
}

export function listExchanges(
  params: ExchangeListParams = {},
): Promise<ExchangeListResponse> {
  return apiGet<ExchangeListResponse>(buildListPath(params));
}

export function getExchange(id: string): Promise<Exchange> {
  return apiGet<Exchange>(`/api/exchanges/${id}`);
}

export function createExchange(
  body: ExchangeCreateInput,
): Promise<Exchange> {
  return apiPost<Exchange>("/api/exchanges", body);
}

export function patchExchange(
  id: string,
  body: ExchangePatchInput,
): Promise<Exchange> {
  return apiPatch<Exchange>(`/api/exchanges/${id}`, body);
}

export function deleteExchange(id: string): Promise<void> {
  return apiDelete<void>(`/api/exchanges/${id}`);
}
