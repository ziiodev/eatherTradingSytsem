/**
 * Frontend domain types + zod schemas for the Projects surface.
 *
 * These mirror the Pydantic v2 models in
 * ``apps/api/src/aether_api/routers/projects.py``. Until the OpenAPI
 * dump regenerates ``@aether/shared-types`` we keep a local copy of the
 * contract here so the dashboard compiles without a network round-trip.
 *
 * IMPORTANT: any change to the backend DTOs must be mirrored here AND
 * re-generated for ``@aether/shared-types`` (``make gen.types``). Drift
 * is checked at build time once typed call sites adopt the generated
 * types (see lib/api.ts).
 */

import { z } from "zod";

import { apiDelete, apiGet, apiPatch, apiPost } from "@/lib/api";

// ---------------------------------------------------------------------------
// Canonical constants — mirrors `aether_api.services.project_lifecycle`.
// ---------------------------------------------------------------------------
export const PROJECT_STATUSES = [
  "inactive",
  "active",
  "paused",
  "stopped",
  "error",
  "maintenance",
] as const;

export type ProjectStatus = (typeof PROJECT_STATUSES)[number];

export const PROJECT_STATUS_LABEL: Record<ProjectStatus, string> = {
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
// ``aether_api.services.project_lifecycle.VALID_TRANSITIONS``.
// ---------------------------------------------------------------------------
const TRANSITIONS: Record<ProjectStatus, ReadonlyArray<ProjectStatus>> = {
  inactive: ["active", "maintenance"],
  active: ["paused", "stopped", "error", "maintenance"],
  paused: ["active", "stopped"],
  stopped: ["inactive", "active"],
  error: ["stopped", "maintenance"],
  maintenance: ["inactive", "active"],
};

export function canTransition(
  from: ProjectStatus,
  to: ProjectStatus,
): boolean {
  return TRANSITIONS[from].includes(to);
}

export function isDeletable(status: ProjectStatus): boolean {
  return status === "inactive" || status === "stopped";
}

// ---------------------------------------------------------------------------
// API response shapes (lite — only the fields the UI actually reads).
// ---------------------------------------------------------------------------
export interface ProjectSummary {
  id: string;
  name: string;
  symbol: string;
  timeframe: string;
  status: ProjectStatus;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface ProjectDetail extends ProjectSummary {
  description: string | null;
  mcp_url: string;
  mcp_port: number | null;
  docker_image: string | null;
  container_id: string | null;
  container_name: string | null;
  account_login: string | null;
  account_server: string | null;
  broker_name: string | null;
  account_credential_ref: string | null;
  account_currency: string | null;
  account_leverage: number | null;
  account_type: string | null;
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
  worker_agent_id: string | null;
  investigator_agent_id: string | null;
  auditor_agent_id: string | null;
  trading_sessions: TradingSession[];
  auditor_params: Record<string, unknown>;
  investigator_params: Record<string, unknown>;
  worker_params: Record<string, unknown>;
  tags: string[] | null;
  notes: string | null;
  error_count: number | null;
  last_error: string | null;
}

export interface ProjectListResponse {
  items: ProjectSummary[];
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

export const projectCreateSchema = z.object({
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
  account_login: z.string().max(50).optional().nullable(),
  account_server: z.string().max(100).optional().nullable(),
  broker_name: z.string().max(80).optional().nullable(),
  account_currency: z.string().max(10).optional().nullable(),
  account_leverage: z
    .union([z.string(), z.number()])
    .optional()
    .nullable()
    .transform((v) => {
      if (v === undefined || v === null || v === "") return undefined;
      const n = typeof v === "number" ? v : Number.parseInt(v, 10);
      return Number.isNaN(n) ? undefined : n;
    }),
  account_type: z.string().max(20).optional().nullable(),
  capital_asignado: optionalDecimalString,
  commission_per_lot: optionalDecimalString,
  commission_currency: z.string().max(10).optional().nullable(),
  risk_per_trade: optionalDecimalString,
  max_daily_dd: optionalDecimalString,
  max_total_dd: optionalDecimalString,
  max_exposure: optionalDecimalString,
  strategy_description: z.string().max(4000).optional().nullable(),
  base_logic: z.string().max(20000).optional().nullable(),
  // Agent bindings — each project can reference at most one Worker /
  // Investigator / Auditor definition (the Orchestrator is system-level
  // and not user-bound). The IDs are validated as UUIDs when present;
  // empty string or null clears the binding server-side.
  worker_agent_id: z.string().uuid().optional().nullable(),
  investigator_agent_id: z.string().uuid().optional().nullable(),
  auditor_agent_id: z.string().uuid().optional().nullable(),
  trading_sessions: z.array(z.enum(TRADING_SESSIONS)).max(10).default([]),
  tags: z.array(z.string().min(1).max(40)).max(20).optional().nullable(),
  notes: z.string().max(4000).optional().nullable(),
});

export type ProjectCreateInput = z.infer<typeof projectCreateSchema>;

// PATCH allows any subset; we represent it as all-optional.
export const projectPatchSchema = projectCreateSchema.partial();
export type ProjectPatchInput = z.infer<typeof projectPatchSchema>;

// ---------------------------------------------------------------------------
// API helpers.
// ---------------------------------------------------------------------------
export interface ListParams {
  status?: ProjectStatus;
  limit?: number;
  offset?: number;
}

function buildListPath(params: ListParams): string {
  const sp = new URLSearchParams();
  if (params.status) sp.set("status", params.status);
  if (params.limit !== undefined) sp.set("limit", String(params.limit));
  if (params.offset !== undefined) sp.set("offset", String(params.offset));
  const qs = sp.toString();
  return qs ? `/api/projects?${qs}` : "/api/projects";
}

export function listProjects(
  params: ListParams = {},
): Promise<ProjectListResponse> {
  return apiGet<ProjectListResponse>(buildListPath(params));
}

export function getProject(id: string): Promise<ProjectDetail> {
  return apiGet<ProjectDetail>(`/api/projects/${id}`);
}

export function createProject(
  body: ProjectCreateInput,
): Promise<ProjectDetail> {
  return apiPost<ProjectDetail>("/api/projects", body);
}

export function patchProject(
  id: string,
  body: ProjectPatchInput,
): Promise<ProjectDetail> {
  return apiPatch<ProjectDetail>(`/api/projects/${id}`, body);
}

export function deleteProject(id: string): Promise<void> {
  return apiDelete<void>(`/api/projects/${id}`);
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
): Promise<ProjectDetail> {
  return apiPost<ProjectDetail>(`/api/projects/${id}/${action}`);
}

// ---------------------------------------------------------------------------
// Per-project Docker orchestration — surface for the
// /proyectos/[id]/infraestructura tab. Every call goes through the same
// /api/projects/{id} prefix; the backend routes them to docker_control
// which speaks to the docker-socket-proxy sidecar (no raw socket access).
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
export async function previewDockerfile(projectId: string): Promise<string> {
  return apiPost<string>(`/api/projects/${projectId}/dockerfile/preview`);
}

export function buildProjectImage(projectId: string): Promise<BuildResult> {
  return apiPost<BuildResult>(`/api/projects/${projectId}/build`);
}

export function createProjectContainer(
  projectId: string,
): Promise<ContainerOpResult> {
  return apiPost<ContainerOpResult>(
    `/api/projects/${projectId}/container/create`,
  );
}

export function startProjectContainer(
  projectId: string,
): Promise<ContainerOpResult> {
  return apiPost<ContainerOpResult>(
    `/api/projects/${projectId}/container/start`,
  );
}

export function pauseProjectContainer(
  projectId: string,
): Promise<ContainerOpResult> {
  return apiPost<ContainerOpResult>(
    `/api/projects/${projectId}/container/pause`,
  );
}

export function stopProjectContainer(
  projectId: string,
): Promise<ContainerOpResult> {
  return apiPost<ContainerOpResult>(
    `/api/projects/${projectId}/container/stop`,
  );
}

export function recreateProjectContainer(
  projectId: string,
): Promise<ContainerOpResult> {
  return apiPost<ContainerOpResult>(
    `/api/projects/${projectId}/container/recreate`,
  );
}

export function removeProjectContainer(
  projectId: string,
): Promise<{ removed_container_id?: string; status?: string }> {
  return apiDelete(`/api/projects/${projectId}/container`);
}

export function getProjectContainerLogs(
  projectId: string,
  tail: number = 200,
): Promise<string> {
  return apiGet<string>(
    `/api/projects/${projectId}/container/logs?tail=${tail}`,
  );
}

export function listProjectContainerEvents(
  projectId: string,
  params: { limit?: number; offset?: number } = {},
): Promise<ContainerEventsResponse> {
  const sp = new URLSearchParams();
  if (params.limit !== undefined) sp.set("limit", String(params.limit));
  if (params.offset !== undefined) sp.set("offset", String(params.offset));
  const qs = sp.toString();
  return apiGet<ContainerEventsResponse>(
    qs
      ? `/api/projects/${projectId}/container/events?${qs}`
      : `/api/projects/${projectId}/container/events`,
  );
}
