/**
 * Typed client for the ``/api/me`` surface (settings / configuracion).
 *
 * The shapes here mirror the FastAPI Pydantic models in
 * ``apps/api/src/aether_api/routers/me.py`` 1:1 — keep them in lockstep.
 * Once ``apps/api`` regenerates the OpenAPI schema, these types should
 * be replaced with `ApiResponse<"/api/me", ...>` from `@aether/shared-types`.
 */

import { apiGet, apiPatch, apiPost } from "@/lib/api";

export interface Me {
  id: string;
  email: string;
  display_name: string | null;
  avatar_url: string | null;
  email_verified_at: string | null;
  is_admin: boolean;
  created_at: string | null;
  // Populated by ``GET /api/auth/me`` once the mfa-totp change lands. The
  // field is optional only to keep older API builds backwards-compatible
  // through the rolling deploy; new code should treat ``undefined`` as
  // ``false`` (charter default).
  mfa_enabled?: boolean;
}

export interface SessionItem {
  id: string;
  ip_address: string | null;
  user_agent: string | null;
  issued_at: string;
  last_used_at: string;
  expires_at: string;
  revoked_at: string | null;
  is_current: boolean;
}

export interface SessionsPage {
  items: SessionItem[];
  next_cursor: string | null;
}

export interface PatchProfileRequest {
  display_name?: string | null;
  avatar_url?: string | null;
}

export interface ChangeEmailRequest {
  new_email: string;
  current_password: string;
}

export interface ChangePasswordRequest {
  current_password: string;
  new_password: string;
  sign_out_others?: boolean;
}

export interface ChangePasswordResponse {
  ok: boolean;
  revoked_other_sessions: number;
}

export interface RevokeOthersResponse {
  revoked: number;
}

export function fetchMe(): Promise<Me> {
  // GET /api/auth/me — the identity probe. Kept here next to the
  // settings client so the configuracion page only imports from one
  // module.
  return apiGet<Me>("/api/auth/me");
}

export function patchMe(body: PatchProfileRequest): Promise<Me> {
  return apiPatch<Me>("/api/me", body);
}

export function changeEmail(body: ChangeEmailRequest): Promise<Me> {
  return apiPost<Me>("/api/me/email/change", body);
}

export function changePassword(
  body: ChangePasswordRequest,
): Promise<ChangePasswordResponse> {
  return apiPost<ChangePasswordResponse>("/api/me/password/change", body);
}

export function listSessions(params: {
  limit?: number;
  cursor?: string | null;
}): Promise<SessionsPage> {
  const query = new URLSearchParams();
  if (params.limit) query.set("limit", String(params.limit));
  if (params.cursor) query.set("cursor", params.cursor);
  const qs = query.toString();
  return apiGet<SessionsPage>(`/api/me/sessions${qs ? `?${qs}` : ""}`);
}

export function revokeSession(sessionId: string): Promise<void> {
  return apiPost<void>(`/api/me/sessions/${sessionId}/revoke`);
}

export function revokeOtherSessions(): Promise<RevokeOthersResponse> {
  return apiPost<RevokeOthersResponse>("/api/me/sessions/revoke-others");
}
