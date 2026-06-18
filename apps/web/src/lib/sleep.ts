/**
 * Typed client for the Sleep Phase + Learning HTTP surface.
 *
 * Endpoints:
 *   POST   /api/pairs/{id}/sleep/trigger
 *   GET    /api/pairs/{id}/sleep/runs
 *   GET    /api/pairs/{id}/sleep/runs/{run_id}
 *   GET    /api/pairs/{id}/sleep-runs/{run_id}/report
 *   POST   /api/config-versions/{id}/approve
 *   POST   /api/config-versions/{id}/reject
 *   POST   /api/config-versions/{id}/revert
 *   GET    /api/pairs/{id}/q-tables
 *   GET    /api/pairs/{id}/q-tables/{version}
 *   GET    /api/pairs/{id}/episodic-memory
 *   GET    /api/pairs/{id}/semantic-memory
 *
 * The shapes here mirror the Pydantic DTOs in
 * `apps/api/src/aether_api/sleep/routes.py` and
 * `apps/api/src/aether_api/routers/learning.py`.
 */

import { z } from "zod";

import { apiGet, apiPost } from "@/lib/api";

export type SleepPhaseType = "micro" | "profundo" | "critico";

export type SleepRunStatus =
  | "running"
  | "succeeded"
  | "failed"
  | "crashed"
  | "skipped"
  | "partial";

export type SleepAgentType = "worker" | "investigator" | "auditor";

export type ConfigVersionRiskClass = "bajo" | "medio" | "alto";

export type ConfigVersionStatus =
  | "pending"
  | "approved"
  | "rejected"
  | "applied"
  | "reverted";

export interface SleepRunSummary {
  id: string;
  pair_id: string;
  phase_type: SleepPhaseType;
  status: SleepRunStatus;
  started_at: string | null;
  ended_at: string | null;
  summary: string | null;
  error: string | null;
}

export interface SleepReflectionDetail {
  id: string;
  agent_type: SleepAgentType;
  reflection_md: string | null;
  suggested_changes: Record<string, unknown>;
  created_at: string | null;
}

export interface ConfigVersionDetail {
  id: string;
  pair_id: string;
  parent_version_id: string | null;
  sleep_run_id: string | null;
  snapshot: Record<string, unknown>;
  risk_class: ConfigVersionRiskClass;
  status: ConfigVersionStatus;
  proposed_at: string | null;
  decided_at: string | null;
  decided_by: string | null;
  applied_at: string | null;
}

export interface SleepRunDetailResponse {
  run: SleepRunSummary;
  reflections: SleepReflectionDetail[];
  config_versions: ConfigVersionDetail[];
}

export interface SleepRunListResponse {
  items: SleepRunSummary[];
  total: number;
  limit: number;
  offset: number;
}

export interface TriggerSleepResponse {
  sleep_run_id: string;
  status: SleepRunStatus;
  summary: string | null;
  error: string | null;
  config_version_id: string | null;
}

export const SLEEP_PHASE_LABEL: Record<SleepPhaseType, string> = {
  micro: "Micro-sueño",
  profundo: "Sueño Profundo",
  critico: "Sueño Crítico",
};

export const SLEEP_RUN_STATUS_LABEL: Record<SleepRunStatus, string> = {
  running: "En curso",
  succeeded: "Completado",
  failed: "Fallido",
  crashed: "Crashed (boot sweep)",
  skipped: "Omitido",
  partial: "Parcial",
};

export const RISK_CLASS_LABEL: Record<ConfigVersionRiskClass, string> = {
  bajo: "Bajo",
  medio: "Medio",
  alto: "Alto",
};

export function triggerSleepRun(
  pairId: string,
  phase: SleepPhaseType,
): Promise<TriggerSleepResponse> {
  return apiPost<TriggerSleepResponse>(
    `/api/pairs/${pairId}/sleep/trigger`,
    { phase_type: phase },
  );
}

export function listSleepRuns(
  pairId: string,
  params: { limit?: number; offset?: number } = {},
): Promise<SleepRunListResponse> {
  const search = new URLSearchParams();
  if (params.limit !== undefined) search.set("limit", String(params.limit));
  if (params.offset !== undefined) search.set("offset", String(params.offset));
  const qs = search.toString();
  return apiGet<SleepRunListResponse>(
    `/api/pairs/${pairId}/sleep/runs${qs ? `?${qs}` : ""}`,
  );
}

export function getSleepRun(
  pairId: string,
  runId: string,
): Promise<SleepRunDetailResponse> {
  return apiGet<SleepRunDetailResponse>(
    `/api/pairs/${pairId}/sleep/runs/${runId}`,
  );
}

export function approveConfigVersion(
  versionId: string,
): Promise<ConfigVersionDetail> {
  return apiPost<ConfigVersionDetail>(
    `/api/config-versions/${versionId}/approve`,
  );
}

export function rejectConfigVersion(
  versionId: string,
): Promise<ConfigVersionDetail> {
  return apiPost<ConfigVersionDetail>(
    `/api/config-versions/${versionId}/reject`,
  );
}

export function revertConfigVersion(
  versionId: string,
): Promise<ConfigVersionDetail> {
  return apiPost<ConfigVersionDetail>(
    `/api/config-versions/${versionId}/revert`,
  );
}

// ---------------------------------------------------------------------------
// Learning surface (sleep-learning-loop Phase 10)
//
// Read-only views over the four learning tables. Writes happen ONLY via
// the Sleep Phase orchestrator (server-side) and the sandboxed Worker
// ctx proxies — never via HTTP.
//
// Backend reference: ``apps/api/src/aether_api/routers/learning.py``.
// Tenancy is enforced server-side; cross-tenant returns 404 (existence
// non-disclosure), matching the rest of /api/pairs.
// ---------------------------------------------------------------------------

// Q-Tables --------------------------------------------------------------

export const qTableListItemSchema = z.object({
  id: z.string().uuid(),
  pair_id: z.string().uuid(),
  version: z.number().int().positive(),
  // Decimals come over the wire as strings to preserve precision.
  alpha_normal: z.union([z.string(), z.number()]),
  alpha_special: z.union([z.string(), z.number()]),
  gamma: z.union([z.string(), z.number()]),
  episode_count: z.number().int().nonnegative(),
  created_by_sleep_run_id: z.string().uuid().nullable(),
  created_at: z.string().nullable(),
});
export type QTableListItem = z.infer<typeof qTableListItemSchema>;

export const qTableListResponseSchema = z.object({
  items: z.array(qTableListItemSchema),
  total: z.number().int().nonnegative(),
});
export type QTableListResponse = z.infer<typeof qTableListResponseSchema>;

export const qTableResponseSchema = qTableListItemSchema.extend({
  table_data: z.record(z.string(), z.unknown()).default({}),
});
export type QTableResponse = z.infer<typeof qTableResponseSchema>;

export interface ListQTablesParams {
  limit?: number;
  offset?: number;
}

export async function fetchQTables(
  pairId: string,
  params: ListQTablesParams = {},
): Promise<QTableListResponse> {
  const search = new URLSearchParams();
  if (params.limit !== undefined) search.set("limit", String(params.limit));
  if (params.offset !== undefined) search.set("offset", String(params.offset));
  const qs = search.toString();
  const raw = await apiGet<unknown>(
    `/api/pairs/${pairId}/q-tables${qs ? `?${qs}` : ""}`,
  );
  return qTableListResponseSchema.parse(raw);
}

export async function fetchQTable(
  pairId: string,
  version: string | number,
): Promise<QTableResponse> {
  const raw = await apiGet<unknown>(
    `/api/pairs/${pairId}/q-tables/${version}`,
  );
  return qTableResponseSchema.parse(raw);
}

// Episodic memory -------------------------------------------------------

export const episodicMemorySchema = z.object({
  id: z.string().uuid(),
  pair_id: z.string().uuid(),
  state_key: z.string(),
  action: z.string(),
  reward: z.union([z.string(), z.number()]),
  next_state_key: z.string().nullable(),
  order_id: z.string().uuid().nullable(),
  consumed_by_sleep_run_id: z.string().uuid().nullable(),
  meta_data: z.record(z.string(), z.unknown()).default({}),
  created_at: z.string().nullable(),
});
export type EpisodicMemory = z.infer<typeof episodicMemorySchema>;

export const episodicMemoryListResponseSchema = z.object({
  items: z.array(episodicMemorySchema),
  total: z.number().int().nonnegative(),
});
export type EpisodicMemoryListResponse = z.infer<
  typeof episodicMemoryListResponseSchema
>;

export interface ListEpisodicMemoryParams {
  since?: string; // ISO datetime
  until?: string; // ISO datetime
  stateKey?: string;
  limit?: number;
  offset?: number;
}

export async function fetchEpisodicMemory(
  pairId: string,
  params: ListEpisodicMemoryParams = {},
): Promise<EpisodicMemoryListResponse> {
  const search = new URLSearchParams();
  if (params.since) search.set("since", params.since);
  if (params.until) search.set("until", params.until);
  if (params.stateKey) search.set("state_key", params.stateKey);
  if (params.limit !== undefined) search.set("limit", String(params.limit));
  if (params.offset !== undefined) search.set("offset", String(params.offset));
  const qs = search.toString();
  const raw = await apiGet<unknown>(
    `/api/pairs/${pairId}/episodic-memory${qs ? `?${qs}` : ""}`,
  );
  return episodicMemoryListResponseSchema.parse(raw);
}

// Semantic memory -------------------------------------------------------

export const semanticMemorySchema = z.object({
  id: z.string().uuid(),
  pair_id: z.string().uuid(),
  rule_type: z.string(),
  body: z.string(),
  payload: z.record(z.string(), z.unknown()).default({}),
  superseded_by: z.string().uuid().nullable(),
  active: z.boolean(),
  created_by_sleep_run_id: z.string().uuid().nullable(),
  created_at: z.string().nullable(),
  updated_at: z.string().nullable(),
});
export type SemanticMemory = z.infer<typeof semanticMemorySchema>;

export const semanticMemoryListResponseSchema = z.object({
  items: z.array(semanticMemorySchema),
  total: z.number().int().nonnegative(),
});
export type SemanticMemoryListResponse = z.infer<
  typeof semanticMemoryListResponseSchema
>;

export interface ListSemanticMemoryParams {
  ruleType?: string;
  active?: boolean;
}

export async function fetchSemanticMemory(
  pairId: string,
  params: ListSemanticMemoryParams = {},
): Promise<SemanticMemoryListResponse> {
  const search = new URLSearchParams();
  if (params.ruleType) search.set("rule_type", params.ruleType);
  if (params.active !== undefined) search.set("active", String(params.active));
  const qs = search.toString();
  const raw = await apiGet<unknown>(
    `/api/pairs/${pairId}/semantic-memory${qs ? `?${qs}` : ""}`,
  );
  return semanticMemoryListResponseSchema.parse(raw);
}

// Sleep report ----------------------------------------------------------

export const sleepReportSchema = z.object({
  id: z.string().uuid(),
  sleep_run_id: z.string().uuid(),
  payload: z.record(z.string(), z.unknown()).default({}),
  summary_md: z.string().nullable(),
  created_at: z.string().nullable(),
});
export type SleepReport = z.infer<typeof sleepReportSchema>;

export async function fetchSleepReport(
  pairId: string,
  runId: string,
): Promise<SleepReport> {
  const raw = await apiGet<unknown>(
    `/api/pairs/${pairId}/sleep-runs/${runId}/report`,
  );
  return sleepReportSchema.parse(raw);
}

// ---------------------------------------------------------------------------
// Q-Table diff helpers (used by the version comparison view)
//
// `table_data` shape is `{ [state_key]: { [action]: q_value } }`.
// ---------------------------------------------------------------------------

export interface QTableDiffSummary {
  /** States that appear in `next` but not in `prev`. */
  addedStates: string[];
  /** States where argmax(action) differs between the two versions. */
  changedArgmaxStates: Array<{
    stateKey: string;
    prevAction: string | null;
    nextAction: string | null;
  }>;
  /** Total unique states across both versions. */
  totalStates: number;
}

/**
 * Compute a lightweight diff between two `table_data` blobs. Pure +
 * dependency-free so it's safe to call from both the page and tests.
 */
export function diffQTables(
  prev: Record<string, unknown> | null,
  next: Record<string, unknown>,
): QTableDiffSummary {
  const prevStates = new Set(Object.keys(prev ?? {}));
  const nextStates = new Set(Object.keys(next));
  const addedStates: string[] = [];
  for (const s of nextStates) {
    if (!prevStates.has(s)) addedStates.push(s);
  }
  const changedArgmaxStates: QTableDiffSummary["changedArgmaxStates"] = [];
  for (const s of nextStates) {
    const prevActions = (prev?.[s] as Record<string, number> | undefined) ?? null;
    const nextActions = next[s] as Record<string, number>;
    const prevArgmax = argmax(prevActions);
    const nextArgmax = argmax(nextActions);
    if (prevActions !== null && prevArgmax !== nextArgmax) {
      changedArgmaxStates.push({
        stateKey: s,
        prevAction: prevArgmax,
        nextAction: nextArgmax,
      });
    }
  }
  const totalStates = new Set([...prevStates, ...nextStates]).size;
  return { addedStates, changedArgmaxStates, totalStates };
}

function argmax(
  actions: Record<string, number> | null | undefined,
): string | null {
  if (!actions) return null;
  let best: string | null = null;
  let bestVal = -Infinity;
  for (const [action, value] of Object.entries(actions)) {
    const n = typeof value === "number" ? value : Number(value);
    if (Number.isFinite(n) && n > bestVal) {
      bestVal = n;
      best = action;
    }
  }
  return best;
}
