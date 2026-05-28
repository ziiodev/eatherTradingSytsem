/**
 * Frontend domain types + API helpers for the live MT5 (operativa) surface.
 *
 * Mirrors the routes added in
 * ``apps/api/src/aether_api/routers/projects_live.py``. Until the OpenAPI
 * dump regenerates ``@aether/shared-types`` we keep a local copy of the
 * contract here so the dashboard compiles without a network round-trip.
 */

import { z } from "zod";

import { apiGet, apiPost } from "@/lib/api";

// ---------------------------------------------------------------------------
// Schemas — the API returns the raw MT5 payload after Decimal→string coercion.
// ---------------------------------------------------------------------------
export const AccountSchema = z.object({
  balance: z.coerce.number(),
  equity: z.coerce.number(),
  margin: z.coerce.number(),
  free_margin: z.coerce.number(),
  leverage: z.number().int(),
  currency: z.string(),
  login: z.number().int(),
});
export type Account = z.infer<typeof AccountSchema>;

export const PositionSchema = z.object({
  ticket: z.number().int(),
  symbol: z.string(),
  side: z.enum(["buy", "sell"]),
  volume: z.coerce.number(),
  price_open: z.coerce.number(),
  sl: z.coerce.number().nullable().optional(),
  tp: z.coerce.number().nullable().optional(),
  profit: z.coerce.number(),
  time: z.string(),
});
export type Position = z.infer<typeof PositionSchema>;

export const PositionsResponseSchema = z.object({
  positions: z.array(PositionSchema),
});

export const OrderSchema = z.object({
  id: z.string().uuid(),
  project_id: z.string().uuid(),
  agent_id: z.string().uuid().nullable().optional(),
  symbol: z.string(),
  side: z.enum(["buy", "sell"]),
  volume: z.coerce.number(),
  sl: z.coerce.number(),
  tp: z.coerce.number().nullable().optional(),
  mt5_ticket: z.number().int().nullable().optional(),
  status: z.string(),
  comment: z.string().nullable().optional(),
  magic: z.number().int().nullable().optional(),
  created_at: z.string().nullable().optional(),
  filled_at: z.string().nullable().optional(),
});
export type OrderRecord = z.infer<typeof OrderSchema>;

export const OrdersListSchema = z.object({
  items: z.array(OrderSchema),
  total: z.number().int(),
  limit: z.number().int(),
  offset: z.number().int(),
});

export const DealSchema = z.object({
  ticket: z.number().int(),
  symbol: z.string(),
  type: z.number().int(),
  volume: z.coerce.number(),
  price: z.coerce.number(),
  profit: z.coerce.number(),
  time: z.string(),
});
export const HistoryResponseSchema = z.object({
  deals: z.array(DealSchema),
});

export const ApprovalSchema = z.object({
  id: z.string().uuid(),
  payload: z.record(z.string(), z.unknown()),
  requested_at: z.string().nullable().optional(),
  expires_at: z.string().nullable().optional(),
  status: z.string(),
});
export const ApprovalsListSchema = z.object({
  items: z.array(ApprovalSchema),
});
export type Approval = z.infer<typeof ApprovalSchema>;

// ---------------------------------------------------------------------------
// API helpers
// ---------------------------------------------------------------------------

export async function getAccount(projectId: string): Promise<Account> {
  const raw = await apiGet(`/api/projects/${projectId}/account`);
  return AccountSchema.parse(raw);
}

export async function getPositions(projectId: string): Promise<Position[]> {
  const raw = await apiGet(`/api/projects/${projectId}/positions`);
  return PositionsResponseSchema.parse(raw).positions;
}

export async function listOrders(
  projectId: string,
  { limit = 50, offset = 0 }: { limit?: number; offset?: number } = {},
): Promise<{ items: OrderRecord[]; total: number }> {
  const raw = await apiGet(
    `/api/projects/${projectId}/orders?limit=${limit}&offset=${offset}`,
  );
  const parsed = OrdersListSchema.parse(raw);
  return { items: parsed.items, total: parsed.total };
}

export async function getHistory(
  projectId: string,
  params: { date_from: string; date_to: string; symbol?: string },
): Promise<z.infer<typeof DealSchema>[]> {
  const qs = new URLSearchParams({
    date_from: params.date_from,
    date_to: params.date_to,
  });
  if (params.symbol) qs.set("symbol", params.symbol);
  const raw = await apiGet(`/api/projects/${projectId}/history?${qs}`);
  return HistoryResponseSchema.parse(raw).deals;
}

export async function listApprovals(projectId: string): Promise<Approval[]> {
  const raw = await apiGet(`/api/projects/${projectId}/approvals`);
  return ApprovalsListSchema.parse(raw).items;
}

export async function approveApproval(
  projectId: string,
  approvalId: string,
): Promise<{ id: string; status: string }> {
  return (await apiPost(
    `/api/projects/${projectId}/approvals/${approvalId}/approve`,
    {},
  )) as { id: string; status: string };
}

export async function rejectApproval(
  projectId: string,
  approvalId: string,
): Promise<{ id: string; status: string }> {
  return (await apiPost(
    `/api/projects/${projectId}/approvals/${approvalId}/reject`,
    {},
  )) as { id: string; status: string };
}
