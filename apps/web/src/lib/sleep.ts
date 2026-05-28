/**
 * Typed client for the Sleep Phase HTTP surface.
 *
 * Endpoints:
 *   POST   /api/projects/{id}/sleep/trigger
 *   GET    /api/projects/{id}/sleep/runs
 *   GET    /api/projects/{id}/sleep/runs/{run_id}
 *   POST   /api/config-versions/{id}/approve
 *   POST   /api/config-versions/{id}/reject
 *   POST   /api/config-versions/{id}/revert
 *
 * The shapes here mirror the Pydantic DTOs in
 * `apps/api/src/aether_api/sleep/routes.py`.
 */

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
  project_id: string;
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
  project_id: string;
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
  projectId: string,
  phase: SleepPhaseType,
): Promise<TriggerSleepResponse> {
  return apiPost<TriggerSleepResponse>(
    `/api/projects/${projectId}/sleep/trigger`,
    { phase_type: phase },
  );
}

export function listSleepRuns(
  projectId: string,
  params: { limit?: number; offset?: number } = {},
): Promise<SleepRunListResponse> {
  const search = new URLSearchParams();
  if (params.limit !== undefined) search.set("limit", String(params.limit));
  if (params.offset !== undefined) search.set("offset", String(params.offset));
  const qs = search.toString();
  return apiGet<SleepRunListResponse>(
    `/api/projects/${projectId}/sleep/runs${qs ? `?${qs}` : ""}`,
  );
}

export function getSleepRun(
  projectId: string,
  runId: string,
): Promise<SleepRunDetailResponse> {
  return apiGet<SleepRunDetailResponse>(
    `/api/projects/${projectId}/sleep/runs/${runId}`,
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
