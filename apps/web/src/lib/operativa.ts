/**
 * Frontend domain types + API helpers for the live MT5 (operativa) surface.
 *
 * Mirrors the routes added in
 * ``apps/api/src/aether_api/routers/pairs_live.py``. Until the OpenAPI
 * dump regenerates ``@aether/shared-types`` we keep a local copy of the
 * contract here so the dashboard compiles without a network round-trip.
 *
 * Phase 5 (`project-operativa`) extends this file with:
 * - Account summary schema for the realtime ``GET .../operativa/account-summary``.
 * - Metrics + extended order schema for ``GET .../operativa/orders``.
 * - The WebSocket event discriminated union pushed by ``LiveBus``.
 * - Fetchers ``fetchAccountSummary`` / ``fetchOrders`` with filter opts.
 *
 * Worker P&L authority: backend serialises ``profit_factor`` as the string
 * literal ``"Infinity"`` when there are wins but zero losses (strict JSON
 * cannot encode ``math.inf``). The schema MUST accept both numeric and
 * the literal string and render ``∞`` in the UI.
 */

import { z } from "zod";

import { apiGet, apiPost } from "@/lib/api";

// ---------------------------------------------------------------------------
// Legacy schemas — kept for the existing /account, /positions, /history,
// /orders (basic) and /approvals endpoints that the page still consumes.
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
  pair_id: z.string().uuid(),
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
// Operativa surface — schemas mirroring pairs_live.py operativa endpoints.
// ---------------------------------------------------------------------------

/**
 * Numeric or the literal string ``"Infinity"``.
 *
 * Backend serialises ``profit_factor`` as ``"Infinity"`` when there are
 * wins but zero losses (Python ``math.inf`` would crash strict JSON
 * encoders). The schema accepts both shapes; renderers should map the
 * literal to ``∞``.
 */
export const NumberOrInfinitySchema = z.union([
  z.number(),
  z.literal("Infinity"),
]);
export type NumberOrInfinity = z.infer<typeof NumberOrInfinitySchema>;

/** Coerces Decimal-as-string from FastAPI into a JS number, allowing null. */
const nullableNumber = z.union([z.coerce.number(), z.null()]).nullable();

/** GET /api/pairs/{id}/operativa/account-summary */
export const accountSummarySchema = z.object({
  equity: nullableNumber.optional().default(null),
  balance: nullableNumber.optional().default(null),
  margin_used: nullableNumber.optional().default(null),
  margin_free: nullableNumber.optional().default(null),
  current_drawdown: nullableNumber.optional().default(null),
  pnl_day: z.coerce.number(),
  pnl_week: z.coerce.number(),
  pnl_month: z.coerce.number(),
  mcp_status: z.enum(["available", "unavailable"]),
  source_at: z.string(),
});
export type AccountSummary = z.infer<typeof accountSummarySchema>;

/** Metrics block embedded in the orders response and broadcast over WS. */
export const metricsSchema = z.object({
  trades_total: z.number().int(),
  win_rate: z.number(),
  profit_factor: NumberOrInfinitySchema,
  avg_rr: z.number().nullable(),
  total_pnl: z.coerce.number(),
});
export type OperativaMetrics = z.infer<typeof metricsSchema>;

/** Extended order row exposed by the operativa endpoint. */
export const operativaOrderSchema = z.object({
  id: z.string().uuid(),
  pair_id: z.string().uuid(),
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
  open_time: z.string().nullable().optional(),
  open_price: z.coerce.number().nullable().optional(),
  close_time: z.string().nullable().optional(),
  close_price: z.coerce.number().nullable().optional(),
  commission: z.coerce.number().nullable().optional(),
  swap: z.coerce.number().nullable().optional(),
  profit_gross: z.coerce.number().nullable().optional(),
  profit_net: z.coerce.number().nullable().optional(),
  meta_data: z.record(z.string(), z.unknown()).default({}),
});
export type OperativaOrderRecord = z.infer<typeof operativaOrderSchema>;

/** GET /api/pairs/{id}/operativa/orders */
export const ordersListResponseSchema = z.object({
  items: z.array(operativaOrderSchema),
  total: z.number().int(),
  metrics: metricsSchema,
});
export type OrdersListResponse = z.infer<typeof ordersListResponseSchema>;

// ---------------------------------------------------------------------------
// WebSocket event protocol — discriminated union over `type`.
// ---------------------------------------------------------------------------

/** Live MCP snapshot of the account (5s cadence). */
export const wsAccountSnapshotSchema = z.object({
  type: z.literal("account_snapshot"),
  ts: z.string(),
  data: accountSummarySchema,
});

/**
 * Live MCP snapshot of the positions list (5s cadence).
 *
 * Re-uses {@link PositionSchema} so component logic does not need to
 * branch between WS-derived and REST-derived shapes.
 */
export const wsPositionSnapshotSchema = z.object({
  type: z.literal("position_snapshot"),
  ts: z.string(),
  data: z.object({
    positions: z.array(PositionSchema),
  }),
});

/** Reconciler / Worker pushed order lifecycle event. */
export const wsOrderEventSchema = z.object({
  type: z.literal("order_event"),
  ts: z.string(),
  data: z.object({
    event: z.string(),
    order: operativaOrderSchema,
  }),
});

/** Bus-level transport state for the per-project MCP leg. */
export const wsMcpStatusSchema = z.object({
  type: z.literal("mcp_status"),
  ts: z.string(),
  data: z.object({
    status: z.enum(["available", "unavailable"]),
    reason: z.string().nullable().optional(),
  }),
});

/** Heartbeat — used to short-circuit idle-timeout proxies. */
export const wsPingSchema = z.object({
  type: z.literal("ping"),
  ts: z.string(),
});

export const wsEventSchema = z.discriminatedUnion("type", [
  wsAccountSnapshotSchema,
  wsPositionSnapshotSchema,
  wsOrderEventSchema,
  wsMcpStatusSchema,
  wsPingSchema,
]);
export type WsEvent = z.infer<typeof wsEventSchema>;

// ---------------------------------------------------------------------------
// API helpers
// ---------------------------------------------------------------------------

export async function getAccount(pairId: string): Promise<Account> {
  const raw = await apiGet(`/api/pairs/${pairId}/account`);
  return AccountSchema.parse(raw);
}

export async function getPositions(pairId: string): Promise<Position[]> {
  const raw = await apiGet(`/api/pairs/${pairId}/positions`);
  return PositionsResponseSchema.parse(raw).positions;
}

export async function listOrders(
  pairId: string,
  { limit = 50, offset = 0 }: { limit?: number; offset?: number } = {},
): Promise<{ items: OrderRecord[]; total: number }> {
  const raw = await apiGet(
    `/api/pairs/${pairId}/orders?limit=${limit}&offset=${offset}`,
  );
  const parsed = OrdersListSchema.parse(raw);
  return { items: parsed.items, total: parsed.total };
}

export async function getHistory(
  pairId: string,
  params: { date_from: string; date_to: string; symbol?: string },
): Promise<z.infer<typeof DealSchema>[]> {
  const qs = new URLSearchParams({
    date_from: params.date_from,
    date_to: params.date_to,
  });
  if (params.symbol) qs.set("symbol", params.symbol);
  const raw = await apiGet(`/api/pairs/${pairId}/history?${qs}`);
  return HistoryResponseSchema.parse(raw).deals;
}

export async function listApprovals(pairId: string): Promise<Approval[]> {
  const raw = await apiGet(`/api/pairs/${pairId}/approvals`);
  return ApprovalsListSchema.parse(raw).items;
}

export async function approveApproval(
  pairId: string,
  approvalId: string,
): Promise<{ id: string; status: string }> {
  return (await apiPost(
    `/api/pairs/${pairId}/approvals/${approvalId}/approve`,
    {},
  )) as { id: string; status: string };
}

export async function rejectApproval(
  pairId: string,
  approvalId: string,
): Promise<{ id: string; status: string }> {
  return (await apiPost(
    `/api/pairs/${pairId}/approvals/${approvalId}/reject`,
    {},
  )) as { id: string; status: string };
}

// ---------------------------------------------------------------------------
// Operativa fetchers (Phase 5.1)
// ---------------------------------------------------------------------------

/** GET /api/pairs/{id}/operativa/account-summary */
export async function fetchAccountSummary(
  pairId: string,
): Promise<AccountSummary> {
  const raw = await apiGet(
    `/api/pairs/${pairId}/operativa/account-summary`,
  );
  return accountSummarySchema.parse(raw);
}

export interface FetchOrdersOptions {
  /** ISO datetime lower bound on `open_time`. */
  from?: string;
  /** ISO datetime upper bound on `open_time`. */
  to?: string;
  symbol?: string;
  side?: "buy" | "sell";
  result?: "win" | "loss";
  magic?: number;
  status?: string;
  limit?: number;
  offset?: number;
}

/**
 * GET /api/pairs/{id}/operativa/orders with the seven supported filters
 * plus pagination. ``from`` is the canonical query string parameter — the
 * backend reads it via the ``alias="from"`` Pydantic field.
 */
export async function fetchOrders(
  pairId: string,
  opts: FetchOrdersOptions = {},
): Promise<OrdersListResponse> {
  const qs = new URLSearchParams();
  if (opts.from) qs.set("from", opts.from);
  if (opts.to) qs.set("to", opts.to);
  if (opts.symbol) qs.set("symbol", opts.symbol);
  if (opts.side) qs.set("side", opts.side);
  if (opts.result) qs.set("result", opts.result);
  if (opts.magic !== undefined) qs.set("magic", String(opts.magic));
  if (opts.status) qs.set("status", opts.status);
  if (opts.limit !== undefined) qs.set("limit", String(opts.limit));
  if (opts.offset !== undefined) qs.set("offset", String(opts.offset));

  const query = qs.toString();
  const path =
    `/api/pairs/${pairId}/operativa/orders` +
    (query ? `?${query}` : "");
  const raw = await apiGet(path);
  return ordersListResponseSchema.parse(raw);
}
