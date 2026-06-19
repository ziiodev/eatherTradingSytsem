/**
 * First-party Expert Advisor (EA) client for Aether's FastAPI backend.
 *
 * Wraps the shared `@/lib/api` helpers (cookie-JWT + automatic CSRF header on
 * mutations, `credentials: "include"`, 401-refresh-retry). NEVER uses
 * header-bearer tokens — auth is the httpOnly `aether_access` cookie only.
 *
 * Endpoints live under `/api/eas` (see `apps/api/src/aether_api/routers/eas.py`):
 *   GET    /api/eas                  — list (optional ?is_active=)
 *   GET    /api/eas/{id}             — detail (includes the full graph)
 *   POST   /api/eas                  — create (201)
 *   PATCH  /api/eas/{id}             — partial update (optimistic-lock via updated_at)
 *   DELETE /api/eas/{id}             — soft-archive (204)
 *
 * The `graph` payload is the editor's `SerializedGraph` and MUST round-trip
 * verbatim — React-Flow `node.type:"custom"` + domain `data.type` are never
 * normalized here.
 */
import { apiDelete, apiGet, apiPatch, apiPost, ApiError } from "@/lib/api";
import type { SerializedGraph } from "../_types/graph";

/** Summary row returned by the list endpoint (no graph). */
export interface EaSummary {
  id: string;
  name: string;
  description: string | null;
  is_active: boolean;
  version: number;
  created_at: string | null;
  updated_at: string | null;
}

/** Full EA detail, including the serialized React Flow graph. */
export interface EaDetail {
  id: string;
  name: string;
  description: string | null;
  graph: SerializedGraph;
  is_active: boolean;
  version: number;
  created_at: string | null;
  updated_at: string | null;
}

/** Body for POST /api/eas. `graph` is optional (defaults to the empty envelope). */
export interface CreateEaInput {
  name: string;
  description?: string;
  graph?: SerializedGraph;
}

/**
 * Body for PATCH /api/eas/{id}. `updated_at` is REQUIRED by the backend as the
 * optimistic-locking precondition (missing → 428, stale → 409).
 */
export interface UpdateEaInput {
  name?: string;
  description?: string | null;
  graph?: SerializedGraph;
  updated_at: string;
}

/** GET /api/eas — the current user's EAs. */
export function listEas(isActive?: boolean): Promise<EaSummary[]> {
  const qs = isActive === undefined ? "" : `?is_active=${String(isActive)}`;
  return apiGet<EaSummary[]>(`/api/eas${qs}`);
}

/** GET /api/eas/{id} — a single owned EA (404 if not owned). */
export function getEa(id: string): Promise<EaDetail> {
  return apiGet<EaDetail>(`/api/eas/${id}`);
}

/** POST /api/eas — creates and returns the new EA (201). */
export function createEa(payload: CreateEaInput): Promise<EaDetail> {
  return apiPost<EaDetail>("/api/eas", payload);
}

/** PATCH /api/eas/{id} — partial update; returns the updated row. */
export function updateEa(id: string, patch: UpdateEaInput): Promise<EaDetail> {
  return apiPatch<EaDetail>(`/api/eas/${id}`, patch);
}

/** DELETE /api/eas/{id} — soft-archive an owned EA (204). */
export function deleteEa(id: string): Promise<void> {
  return apiDelete<void>(`/api/eas/${id}`);
}

/** Map an API error to a friendly Spanish message for EA UI surfaces. */
export function eaErrorMessage(err: unknown): string {
  if (err instanceof ApiError) {
    switch (err.status) {
      case 401:
        return "Tu sesión ha expirado. Vuelve a iniciar sesión.";
      case 404:
        return "Este Expert Advisor ya no existe.";
      case 409:
        return "Este Expert Advisor se modificó desde que lo cargaste. Recárgalo e inténtalo de nuevo.";
      case 428:
        return "Falta la marca temporal para guardar. Recarga el editor.";
      default:
        return "Algo salió mal. Inténtalo de nuevo.";
    }
  }
  return "No se pudo contactar con el servidor. Inténtalo de nuevo.";
}
