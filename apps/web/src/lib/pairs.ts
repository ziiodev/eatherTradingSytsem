/**
 * Frontend domain types + zod schemas for the Pairs (Pares) surface.
 *
 * These mirror the Pydantic v2 models in
 * ``apps/api/src/aether_api/routers/pairs.py``. The Pair (Par) is the leaf
 * runtime of the accounts-pairs hierarchy ``Exchange → Account (Cuenta) →
 * Pair (Par) → Agents`` — it owns the Docker/MT5/MCP container, the risk
 * config, the strategy and the agent bindings.
 *
 * accounts-pairs-restructure: this file was renamed from ``lib/projects.ts``
 * and the broker-credential block was MOVED to ``lib/accounts.ts`` (it now
 * lives on the Account, inherited by every pair). The pair-create surface no
 * longer accepts credential fields and REQUIRES ``account_id``.
 *
 * IMPORTANT: any change to the backend DTOs must be mirrored here AND
 * re-generated for ``@aether/shared-types`` (``make gen.types``).
 */

import { z } from "zod";

import { apiDelete, apiGet, apiPatch, apiPost } from "@/lib/api";

// ---------------------------------------------------------------------------
// Canonical constants — mirrors `aether_api.services.pair_lifecycle`.
// ---------------------------------------------------------------------------
export const PAIR_STATUSES = [
  "inactive",
  "active",
  "paused",
  "stopped",
  "error",
  "maintenance",
] as const;

export type PairStatus = (typeof PAIR_STATUSES)[number];

export const PAIR_STATUS_LABEL: Record<PairStatus, string> = {
  inactive: "Inactivo",
  active: "Activo",
  paused: "Pausado",
  stopped: "Detenido",
  error: "Error",
  maintenance: "Mantenimiento",
};

export const TRADING_SESSIONS = [
  "sydney",
  "shanghai",
  "tokyo",
  "europe",
  "new_york",
] as const;

export type TradingSession = (typeof TRADING_SESSIONS)[number];

export const TRADING_SESSION_LABEL: Record<TradingSession, string> = {
  sydney: "Sydney",
  shanghai: "Shanghai",
  tokyo: "Tokio",
  europe: "Europa",
  new_york: "Nueva York",
};

export const TIMEFRAMES = ["M1", "M5", "M15", "M30", "H1", "H4", "D1", "W1", "MN1"] as const;
export type Timeframe = (typeof TIMEFRAMES)[number];

// ---------------------------------------------------------------------------
// Lifecycle matrix — duplicated client-side so the UI can render disabled
// actions without a round-trip. Source of truth lives in
// ``aether_api.services.pair_lifecycle.VALID_TRANSITIONS``.
// ---------------------------------------------------------------------------
const TRANSITIONS: Record<PairStatus, ReadonlyArray<PairStatus>> = {
  inactive: ["active", "maintenance"],
  active: ["paused", "stopped", "error", "maintenance"],
  paused: ["active", "stopped"],
  stopped: ["inactive", "active"],
  error: ["stopped", "maintenance"],
  maintenance: ["inactive", "active"],
};

export function canTransition(
  from: PairStatus,
  to: PairStatus,
): boolean {
  return TRANSITIONS[from].includes(to);
}

export function isDeletable(status: PairStatus): boolean {
  return status === "inactive" || status === "stopped";
}

// ---------------------------------------------------------------------------
// API response shapes (lite — only the fields the UI actually reads).
// ---------------------------------------------------------------------------
export interface PairSummary {
  id: string;
  name: string;
  symbol: string;
  timeframe: string;
  status: PairStatus;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface PairDetail extends PairSummary {
  // Reparent: every pair belongs to exactly one account (Cuenta).
  account_id: string;
  description: string | null;
  mcp_url: string;
  mcp_port: number | null;
  docker_image: string | null;
  container_id: string | null;
  container_name: string | null;
  // NOTE: the broker-credential block (account_login/server, broker_name,
  // account_credential_ref, account_currency/leverage/type) is NO LONGER on
  // the pair — it lives on the Account (see lib/accounts.ts).
  commission_per_lot: string | null;
  commission_currency: string | null;
  swap_long: string | null;
  swap_short: string | null;
  spread_typical: string | null;
  capital_asignado: string | null;
  risk_per_trade: string | null;
  max_daily_dd: string | null;
  max_total_dd: string | null;
  max_exposure: string | null;
  strategy_version: number | null;
  strategy_description: string | null;
  base_logic: string | null;
  orchestrator_agent_id: string | null;
  investigator_agent_id: string | null;
  marker_agent_id: string | null;
  worker_agent_id: string | null;
  tutor_agent_id: string | null;
  auditor_agent_id: string | null;
  trading_sessions: TradingSession[];
  orchestrator_params: Record<string, unknown>;
  investigator_params: Record<string, unknown>;
  marker_params: Record<string, unknown>;
  worker_params: Record<string, unknown>;
  tutor_params: Record<string, unknown>;
  auditor_params: Record<string, unknown>;
  tags: string[] | null;
  notes: string | null;
  error_count: number | null;
  last_error: string | null;
}

export interface PairListResponse {
  items: PairSummary[];
  total: number;
  limit: number;
  offset: number;
}

// ---------------------------------------------------------------------------
// zod schemas — paired 1:1 with the backend Pydantic models.
// ---------------------------------------------------------------------------

// Symbols are stored uppercase server-side.
const symbolSchema = z
  .string()
  .min(1, "Símbolo requerido")
  .max(20, "Máximo 20 caracteres")
  .regex(/^[A-Z0-9._\-]+$/, "Solo A-Z, 0-9, . _ -")
  .transform((s) => s.toUpperCase());

const timeframeSchema = z.enum(TIMEFRAMES, {
  errorMap: () => ({ message: "Marco temporal inválido" }),
});

const optionalDecimalString = z
  .union([z.string(), z.number()])
  .optional()
  .nullable()
  .transform((v) => {
    if (v === undefined || v === null || v === "") return undefined;
    return String(v);
  });

export const pairCreateSchema = z.object({
  // Reparent: the owning account is REQUIRED. The broker-credential block
  // lives on the Account (Cuenta), never on the pair.
  account_id: z.string().uuid("Cuenta requerida"),
  name: z
    .string()
    .min(1, "Nombre requerido")
    .max(100, "Máximo 100 caracteres")
    .regex(/^[\w\- .,()/]+$/, "Caracteres no permitidos"),
  description: z.string().max(2000).optional().nullable(),
  symbol: symbolSchema,
  timeframe: timeframeSchema,
  mcp_url: z
    .string()
    .min(1, "MCP URL requerido")
    .max(255, "Máximo 255 caracteres"),
  mcp_port: z
    .union([z.string(), z.number()])
    .optional()
    .nullable()
    .transform((v) => {
      if (v === undefined || v === null || v === "") return undefined;
      const n = typeof v === "number" ? v : Number.parseInt(v, 10);
      if (Number.isNaN(n)) return undefined;
      return n;
    })
    .refine(
      (n) => n === undefined || (n >= 1 && n <= 65535),
      "Puerto fuera de rango",
    ),
  capital_asignado: optionalDecimalString,
  commission_per_lot: optionalDecimalString,
  commission_currency: z.string().max(10).optional().nullable(),
  risk_per_trade: optionalDecimalString,
  max_daily_dd: optionalDecimalString,
  max_total_dd: optionalDecimalString,
  max_exposure: optionalDecimalString,
  strategy_description: z.string().max(4000).optional().nullable(),
  base_logic: z.string().max(20000).optional().nullable(),
  // Agent bindings — each pair can reference at most one
  // Orquestador / Investigador / Marker / Worker / Tutor / Auditor
  // definition. The IDs are validated as UUIDs when present; empty string
  // or null clears the binding server-side.
  orchestrator_agent_id: z.string().uuid().optional().nullable(),
  investigator_agent_id: z.string().uuid().optional().nullable(),
  marker_agent_id: z.string().uuid().optional().nullable(),
  worker_agent_id: z.string().uuid().optional().nullable(),
  tutor_agent_id: z.string().uuid().optional().nullable(),
  auditor_agent_id: z.string().uuid().optional().nullable(),
  trading_sessions: z.array(z.enum(TRADING_SESSIONS)).max(10).default([]),
  // Free-form per-agent params (mirrors backend JSONB columns).
  orchestrator_params: z.record(z.unknown()).optional(),
  investigator_params: z.record(z.unknown()).optional(),
  marker_params: z.record(z.unknown()).optional(),
  worker_params: z.record(z.unknown()).optional(),
  tutor_params: z.record(z.unknown()).optional(),
  auditor_params: z.record(z.unknown()).optional(),
  tags: z.array(z.string().min(1).max(40)).max(20).optional().nullable(),
  notes: z.string().max(4000).optional().nullable(),
});

export type PairCreateInput = z.infer<typeof pairCreateSchema>;

// PATCH allows any subset; we represent it as all-optional.
export const pairPatchSchema = pairCreateSchema.partial();
export type PairPatchInput = z.infer<typeof pairPatchSchema>;

// ---------------------------------------------------------------------------
// API helpers.
// ---------------------------------------------------------------------------
export interface ListParams {
  status?: PairStatus;
  limit?: number;
  offset?: number;
}

function buildListPath(params: ListParams): string {
  const sp = new URLSearchParams();
  if (params.status) sp.set("status", params.status);
  if (params.limit !== undefined) sp.set("limit", String(params.limit));
  if (params.offset !== undefined) sp.set("offset", String(params.offset));
  const qs = sp.toString();
  return qs ? `/api/pairs?${qs}` : "/api/pairs";
}

export function listPairs(
  params: ListParams = {},
): Promise<PairListResponse> {
  return apiGet<PairListResponse>(buildListPath(params));
}

export function getPair(id: string): Promise<PairDetail> {
  return apiGet<PairDetail>(`/api/pairs/${id}`);
}

export function createPair(
  body: PairCreateInput,
): Promise<PairDetail> {
  return apiPost<PairDetail>("/api/pairs", body);
}

export function patchPair(
  id: string,
  body: PairPatchInput,
): Promise<PairDetail> {
  return apiPatch<PairDetail>(`/api/pairs/${id}`, body);
}

export function deletePair(id: string): Promise<void> {
  return apiDelete<void>(`/api/pairs/${id}`);
}

export type LifecycleAction =
  | "activate"
  | "pause"
  | "stop"
  | "mark-error"
  | "maintenance";

export function lifecycleAction(
  id: string,
  action: LifecycleAction,
): Promise<PairDetail> {
  return apiPost<PairDetail>(`/api/pairs/${id}/${action}`);
}

// ---------------------------------------------------------------------------
// Per-pair Docker orchestration — surface for the
// /cuentas/[accountId]/pares/[pairId]/infraestructura tab. Every call goes
// through the same /api/pairs/{id} prefix; the backend routes them to
// docker_control which speaks to the docker-socket-proxy sidecar (no raw
// socket access).
// ---------------------------------------------------------------------------

export interface ContainerEventRow {
  id: string;
  action: string;
  status: string;
  payload: Record<string, unknown>;
  error: string | null;
  created_at: string | null;
}

export interface ContainerEventsResponse {
  items: ContainerEventRow[];
  total: number;
  limit: number;
  offset: number;
}

export interface BuildResult {
  image_tag: string;
  log_lines: number;
}

export interface ContainerOpResult {
  container_id?: string;
  container_name?: string;
  status?: string;
}

/** Render-only Dockerfile preview. Returns text/plain. */
export async function previewDockerfile(pairId: string): Promise<string> {
  return apiPost<string>(`/api/pairs/${pairId}/dockerfile/preview`);
}

export function buildPairImage(pairId: string): Promise<BuildResult> {
  return apiPost<BuildResult>(`/api/pairs/${pairId}/build`);
}

export function createPairContainer(
  pairId: string,
): Promise<ContainerOpResult> {
  return apiPost<ContainerOpResult>(`/api/pairs/${pairId}/container/create`);
}

export function startPairContainer(
  pairId: string,
): Promise<ContainerOpResult> {
  return apiPost<ContainerOpResult>(`/api/pairs/${pairId}/container/start`);
}

export function pausePairContainer(
  pairId: string,
): Promise<ContainerOpResult> {
  return apiPost<ContainerOpResult>(`/api/pairs/${pairId}/container/pause`);
}

export function stopPairContainer(
  pairId: string,
): Promise<ContainerOpResult> {
  return apiPost<ContainerOpResult>(`/api/pairs/${pairId}/container/stop`);
}

export function recreatePairContainer(
  pairId: string,
): Promise<ContainerOpResult> {
  return apiPost<ContainerOpResult>(`/api/pairs/${pairId}/container/recreate`);
}

export function removePairContainer(
  pairId: string,
): Promise<{ removed_container_id?: string; status?: string }> {
  return apiDelete(`/api/pairs/${pairId}/container`);
}

export function getPairContainerLogs(
  pairId: string,
  tail: number = 200,
): Promise<string> {
  return apiGet<string>(`/api/pairs/${pairId}/container/logs?tail=${tail}`);
}

export function listPairContainerEvents(
  pairId: string,
  params: { limit?: number; offset?: number } = {},
): Promise<ContainerEventsResponse> {
  const sp = new URLSearchParams();
  if (params.limit !== undefined) sp.set("limit", String(params.limit));
  if (params.offset !== undefined) sp.set("offset", String(params.offset));
  const qs = sp.toString();
  return apiGet<ContainerEventsResponse>(
    qs
      ? `/api/pairs/${pairId}/container/events?${qs}`
      : `/api/pairs/${pairId}/container/events`,
  );
}
