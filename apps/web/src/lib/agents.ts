/**
 * Frontend domain types + API helpers for the Agents surface.
 *
 * Mirrors the Pydantic v2 models in
 * ``apps/api/src/aether_api/routers/agents.py``. Until ``@aether/shared-types``
 * gets regenerated from the live OpenAPI dump we keep a local copy of the
 * contract here — that way the dashboard compiles without a network
 * round-trip and the types stay aligned with the live backend.
 *
 * IMPORTANT: any change to backend DTOs MUST be mirrored here AND a future
 * ``make gen.types`` MUST be run before relying on `@aether/shared-types`.
 */

import { z } from "zod";

import { apiDelete, apiGet, apiPatch, apiPost } from "@/lib/api";

// ---------------------------------------------------------------------------
// Canonical constants — mirrors ``aether_api.models.agent.AGENT_TYPES``.
// ---------------------------------------------------------------------------
export const AGENT_TYPES = ["worker", "investigator", "auditor"] as const;
export type AgentType = (typeof AGENT_TYPES)[number];

export const AGENT_TYPE_LABEL: Record<AgentType, string> = {
  worker: "Worker",
  investigator: "Investigator",
  auditor: "Auditor",
};

export const AGENT_TYPE_DESCRIPTION: Record<AgentType, string> = {
  worker: "Ejecuta señales sobre velas/tick — entrada/salida del mercado.",
  investigator: "Analiza estructuras, eventos y noticias — sin abrir órdenes.",
  auditor: "Verifica riesgo, salud del entorno y desviaciones del plan.",
};

/**
 * Per-type code template. The detail editor seeds the body with the
 * matching template on creation; the entrypoint name matches the
 * convention surfaced by the backend warnings.
 */
export const AGENT_TYPE_TEMPLATE: Record<AgentType, string> = {
  worker: [
    "# Worker agent — runs once per tick.",
    "# Receives `ctx` with market/account state, returns a decision.",
    "def on_tick(ctx):",
    '    """Return a dict like {"action": "buy", "size": 0.1} or None."""',
    "    return None",
    "",
  ].join("\n"),
  investigator: [
    "# Investigator agent — runs on schedule or event.",
    "# Reads context and emits findings/insights for the Worker to consume.",
    "def investigate(ctx):",
    '    """Return a dict of findings or None."""',
    "    return None",
    "",
  ].join("\n"),
  auditor: [
    "# Auditor agent — checks risk budgets and plan adherence.",
    "# Returns warnings/violations; never opens trades.",
    "def audit(ctx):",
    '    """Return a list of violations (may be empty)."""',
    "    return []",
    "",
  ].join("\n"),
};

/**
 * Canonical entrypoint name per agent type. Matches the backend
 * ``_DEFAULT_ENTRYPOINTS`` map.
 */
export const AGENT_TYPE_DEFAULT_ENTRYPOINT: Record<AgentType, string> = {
  worker: "on_tick",
  investigator: "investigate",
  auditor: "audit",
};

// ---------------------------------------------------------------------------
// Schemas (zod) — front-end validation. The server is the authority but we
// gate POST/PATCH bodies here so the operator gets immediate feedback.
// ---------------------------------------------------------------------------

const ENTRYPOINT_REGEX = /^[A-Za-z_][A-Za-z0-9_]{0,119}$/;

export const agentSummarySchema = z.object({
  id: z.string().uuid(),
  name: z.string(),
  type: z.enum(AGENT_TYPES),
  is_active: z.boolean(),
  version: z.number().int().nonnegative(),
  updated_at: z.string().nullable(),
  projects_using: z.number().int().nonnegative(),
});
export type AgentSummary = z.infer<typeof agentSummarySchema>;

export const agentDetailSchema = z.object({
  id: z.string().uuid(),
  name: z.string(),
  type: z.enum(AGENT_TYPES),
  is_active: z.boolean(),
  version: z.number().int().nonnegative(),
  description: z.string().nullable(),
  entrypoint: z.string().nullable(),
  logica: z.string(),
  created_at: z.string().nullable(),
  updated_at: z.string().nullable(),
  warnings: z.array(z.string()).default([]),
});
export type AgentDetail = z.infer<typeof agentDetailSchema>;

export const agentCreateSchema = z.object({
  name: z.string().trim().min(1).max(100),
  type: z.enum(AGENT_TYPES),
  logica: z.string().min(1),
  description: z.string().max(4000).optional(),
  entrypoint: z
    .string()
    .regex(ENTRYPOINT_REGEX, "Nombre de entrypoint inválido")
    .max(120)
    .optional(),
});
export type AgentCreateInput = z.infer<typeof agentCreateSchema>;

export const agentPatchSchema = z.object({
  name: z.string().trim().min(1).max(100).optional(),
  description: z.string().max(4000).nullable().optional(),
  logica: z.string().min(1).optional(),
  entrypoint: z
    .string()
    .regex(ENTRYPOINT_REGEX, "Nombre de entrypoint inválido")
    .max(120)
    .nullable()
    .optional(),
  type: z.enum(AGENT_TYPES).optional(),
  // Optimistic locking precondition — server requires this.
  updated_at: z.string(),
});
export type AgentPatchInput = z.infer<typeof agentPatchSchema>;

// ---------------------------------------------------------------------------
// API helpers
// ---------------------------------------------------------------------------

export interface ListAgentsParams {
  type?: AgentType;
  is_active?: boolean;
}

export async function listAgents(
  params: ListAgentsParams = {},
): Promise<AgentSummary[]> {
  const search = new URLSearchParams();
  if (params.type) search.set("type", params.type);
  if (params.is_active !== undefined) {
    search.set("is_active", String(params.is_active));
  }
  const qs = search.toString();
  const raw = await apiGet<unknown>(`/api/agents${qs ? `?${qs}` : ""}`);
  return z.array(agentSummarySchema).parse(raw);
}

export async function getAgent(id: string): Promise<AgentDetail> {
  const raw = await apiGet<unknown>(`/api/agents/${id}`);
  return agentDetailSchema.parse(raw);
}

export async function createAgent(input: AgentCreateInput): Promise<AgentDetail> {
  const body = agentCreateSchema.parse(input);
  const raw = await apiPost<unknown>("/api/agents", body);
  return agentDetailSchema.parse(raw);
}

export async function patchAgent(
  id: string,
  input: AgentPatchInput,
): Promise<AgentDetail> {
  const body = agentPatchSchema.parse(input);
  const raw = await apiPatch<unknown>(`/api/agents/${id}`, body);
  return agentDetailSchema.parse(raw);
}

export interface AgentArchiveResponse {
  id: string;
  is_active: boolean;
  version: number;
}

export async function archiveAgent(id: string): Promise<AgentArchiveResponse> {
  return apiPost<AgentArchiveResponse>(`/api/agents/${id}/archive`);
}

export async function deleteAgent(id: string): Promise<void> {
  await apiDelete<void>(`/api/agents/${id}`);
}
