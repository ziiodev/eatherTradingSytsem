/**
 * Frontend domain types + API helpers for the Skills surface.
 *
 * Mirrors the Pydantic v2 models in
 * ``apps/api/src/aether_api/routers/skills.py``. Until ``@aether/shared-types``
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
// Canonical constants — mirrors ``aether_api.models.skill.SKILL_TYPES`` /
// ``SKILL_RUNTIMES``.
// ---------------------------------------------------------------------------
export const SKILL_TYPES = [
  "indicator",
  "data_source",
  "analytic",
  "executor",
  "risk",
] as const;
export type SkillType = (typeof SKILL_TYPES)[number];

/**
 * Canonical skill runtimes.
 *
 * - ``markdown`` (default) — knowledge artifact (prompts, decision
 *   frameworks, entry/exit rules). Rendered with the shared
 *   ``MarkdownView``.
 * - ``python`` — computational/algorithmic body (indicators, correlation
 *   calculators, risk math). Edited with CodeMirror; validated server-
 *   side via ``ast.parse``.
 */
export const SKILL_RUNTIMES = ["markdown", "python"] as const;
export type SkillRuntime = (typeof SKILL_RUNTIMES)[number];

export const SKILL_RUNTIME_LABEL: Record<SkillRuntime, string> = {
  markdown: "Markdown",
  python: "Python",
};

/**
 * UI label (Spanish, charter copy: "trading skills" / "habilidades de
 * trading"). The DB ``type`` column is the canonical English string.
 */
export const SKILL_TYPE_LABEL: Record<SkillType, string> = {
  indicator: "Indicador",
  data_source: "Fuente de datos",
  analytic: "Analítico",
  executor: "Ejecutor",
  risk: "Riesgo",
};

export const SKILL_TYPE_DESCRIPTION: Record<SkillType, string> = {
  indicator: "Cálculo derivado de series (RSI, EMA, etc.).",
  data_source: "Provee datos externos (noticias, fundamentales, etc.).",
  analytic: "Combina datos para extraer una conclusión cuantitativa.",
  executor: "Envía órdenes al broker — sólo se ejecuta con sandbox activo.",
  risk: "Verifica budgets de riesgo y devuelve violaciones.",
};

/**
 * Per-type **markdown** template. Used by the create form when the
 * runtime is ``markdown`` (the default). Each template seeds the body
 * with a short structured prompt the operator can edit.
 */
export const SKILL_TYPE_TEMPLATE_MARKDOWN: Record<SkillType, string> = {
  indicator: [
    "# Indicator skill",
    "",
    "Describe **what** the indicator measures and **when** it fires.",
    "",
    "## Inputs",
    "- price series (timeframe: …)",
    "- period: 14",
    "",
    "## Signal",
    "Explain in plain language when the indicator is bullish / bearish.",
    "",
  ].join("\n"),
  data_source: [
    "# Data source skill",
    "",
    "Describe the external data this skill provides (news, fundamentals,",
    "sentiment, calendar events, …).",
    "",
    "## Endpoint / origin",
    "- where the data comes from",
    "- update frequency",
    "",
    "## Payload",
    "Shape returned to consumers.",
    "",
  ].join("\n"),
  analytic: [
    "# Analytic skill",
    "",
    "Combine inputs into a quantitative finding or score.",
    "",
    "## Decision framework",
    "- if X and Y → bias long",
    "- if Z → flat",
    "",
    "## Output",
    "`{ score, rationale }`",
    "",
  ].join("\n"),
  executor: [
    "# Executor skill",
    "",
    "Describe **how** an order is built and the **guardrails** before sending it.",
    "",
    "## Entry rules",
    "- always include a stop-loss",
    "- size = risk_per_trade × equity",
    "",
    "## Exit rules",
    "- TP at 2R",
    "- trail after 1R in favour",
    "",
  ].join("\n"),
  risk: [
    "# Risk skill",
    "",
    "Encode the risk checks this skill performs before every order.",
    "",
    "## Budgets",
    "- max daily DD: 3%",
    "- max total DD: 8%",
    "- max exposure: 10%",
    "",
    "## Violations",
    "List the conditions that trigger an alert / abort.",
    "",
  ].join("\n"),
};

/**
 * Per-type Python code template. The create form seeds the body with the
 * matching template; the operator edits from there in the detail view.
 */
export const SKILL_TYPE_TEMPLATE: Record<SkillType, string> = {
  indicator: [
    "# Indicator skill — returns a derived series from market data.",
    "def compute(series, period=14):",
    '    """Return a list[float] aligned to ``series``."""',
    "    return series",
    "",
  ].join("\n"),
  data_source: [
    "# Data source skill — fetches external data on demand.",
    "def fetch(ctx):",
    '    """Return a dict of named payloads (e.g. {"news": [...]})."""',
    "    return {}",
    "",
  ].join("\n"),
  analytic: [
    "# Analytic skill — combines inputs into a quantitative score/finding.",
    "def analyze(inputs):",
    '    """Return a dict like {"score": 0.0, "rationale": ""}."""',
    "    return {}",
    "",
  ].join("\n"),
  executor: [
    "# Executor skill — emits an order to be placed by the future sandbox.",
    "# Storage-only in v1 (sandbox still in-flight).",
    "def execute(ctx, order):",
    '    """Return the executor receipt or raise."""',
    "    return None",
    "",
  ].join("\n"),
  risk: [
    "# Risk skill — checks budgets and returns a list of violations.",
    "def evaluate(ctx):",
    '    """Return a list[dict] of violations (may be empty)."""',
    "    return []",
    "",
  ].join("\n"),
};

// ---------------------------------------------------------------------------
// Schemas (zod) — front-end validation. The server is the authority but we
// gate POST/PATCH bodies here so the operator gets immediate feedback.
// ---------------------------------------------------------------------------

const signatureFieldSchema = z.object({
  name: z.string().min(1).max(80),
  type: z.string().min(1).max(80),
});
export type SignatureField = z.infer<typeof signatureFieldSchema>;

const skillSignatureSchema = z.object({
  inputs: z.array(signatureFieldSchema).default([]),
  outputs: z.array(signatureFieldSchema).default([]),
});
export type SkillSignature = z.infer<typeof skillSignatureSchema>;

/**
 * The backend persists the signature as JSONB and tolerates an empty `{}`
 * literal for skills that pre-date the signature shape (default at the DB
 * layer). At parse time we accept either an empty object or the
 * `{inputs, outputs}` shape — anything else is coerced to empty so the
 * dashboard never crashes on a malformed legacy row.
 */
const tolerantSignatureSchema = z.preprocess(
  (raw): SkillSignature => {
    if (raw && typeof raw === "object" && !Array.isArray(raw)) {
      const obj = raw as Record<string, unknown>;
      if (Array.isArray(obj.inputs) || Array.isArray(obj.outputs)) {
        return {
          inputs: Array.isArray(obj.inputs)
            ? (obj.inputs as SignatureField[])
            : [],
          outputs: Array.isArray(obj.outputs)
            ? (obj.outputs as SignatureField[])
            : [],
        };
      }
    }
    return { inputs: [], outputs: [] };
  },
  skillSignatureSchema,
);

export const skillSummarySchema = z.object({
  id: z.string().uuid(),
  name: z.string(),
  type: z.enum(SKILL_TYPES),
  runtime: z.enum(SKILL_RUNTIMES).default("markdown"),
  is_active: z.boolean(),
  version: z.number().int().nonnegative(),
  updated_at: z.string().nullable(),
  used_by_agent_count: z.number().int().nonnegative().default(0),
});
export type SkillSummary = z.infer<typeof skillSummarySchema>;

export const skillDetailSchema = z.object({
  id: z.string().uuid(),
  name: z.string(),
  type: z.enum(SKILL_TYPES),
  runtime: z.enum(SKILL_RUNTIMES).default("markdown"),
  is_active: z.boolean(),
  version: z.number().int().nonnegative(),
  description: z.string().nullable(),
  code: z.string(),
  input_signature: tolerantSignatureSchema,
  output_signature: tolerantSignatureSchema,
  created_at: z.string().nullable(),
  updated_at: z.string().nullable(),
  used_by_agent_count: z.number().int().nonnegative().default(0),
});
export type SkillDetail = z.infer<typeof skillDetailSchema>;

export const skillCreateSchema = z.object({
  name: z.string().trim().min(1).max(100),
  type: z.enum(SKILL_TYPES),
  runtime: z.enum(SKILL_RUNTIMES).optional(),
  code: z.string().min(1),
  description: z.string().max(4000).optional(),
  input_signature: skillSignatureSchema.optional(),
  output_signature: skillSignatureSchema.optional(),
});
export type SkillCreateInput = z.infer<typeof skillCreateSchema>;

export const skillPatchSchema = z.object({
  name: z.string().trim().min(1).max(100).optional(),
  description: z.string().max(4000).nullable().optional(),
  code: z.string().min(1).optional(),
  type: z.enum(SKILL_TYPES).optional(),
  runtime: z.enum(SKILL_RUNTIMES).optional(),
  input_signature: skillSignatureSchema.optional(),
  output_signature: skillSignatureSchema.optional(),
  // Optimistic locking precondition — server requires this.
  updated_at: z.string(),
});
export type SkillPatchInput = z.infer<typeof skillPatchSchema>;

// ---------------------------------------------------------------------------
// API helpers
// ---------------------------------------------------------------------------

export interface ListSkillsParams {
  type?: SkillType;
  is_active?: boolean;
}

export async function listSkills(
  params: ListSkillsParams = {},
): Promise<SkillSummary[]> {
  const search = new URLSearchParams();
  if (params.type) search.set("type", params.type);
  if (params.is_active !== undefined) {
    search.set("is_active", String(params.is_active));
  }
  const qs = search.toString();
  const raw = await apiGet<unknown>(`/api/skills${qs ? `?${qs}` : ""}`);
  return z.array(skillSummarySchema).parse(raw);
}

export async function getSkill(id: string): Promise<SkillDetail> {
  const raw = await apiGet<unknown>(`/api/skills/${id}`);
  return skillDetailSchema.parse(raw);
}

export async function createSkill(
  input: SkillCreateInput,
): Promise<SkillDetail> {
  const body = skillCreateSchema.parse(input);
  const raw = await apiPost<unknown>("/api/skills", body);
  return skillDetailSchema.parse(raw);
}

export async function patchSkill(
  id: string,
  input: SkillPatchInput,
): Promise<SkillDetail> {
  const body = skillPatchSchema.parse(input);
  const raw = await apiPatch<unknown>(`/api/skills/${id}`, body);
  return skillDetailSchema.parse(raw);
}

export interface SkillArchiveResponse {
  id: string;
  is_active: boolean;
  version: number;
}

export async function archiveSkill(id: string): Promise<SkillArchiveResponse> {
  return apiPost<SkillArchiveResponse>(`/api/skills/${id}/archive`);
}

export async function deleteSkill(id: string): Promise<void> {
  await apiDelete<void>(`/api/skills/${id}`);
}
