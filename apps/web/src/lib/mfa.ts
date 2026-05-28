/**
 * Typed client for the ``/api/me/mfa`` surface (mfa-totp).
 *
 * The shapes mirror the FastAPI Pydantic models in
 * ``apps/api/src/aether_api/routers/me_mfa.py`` 1:1 — keep them in
 * lockstep. The ``recovery_codes`` arrays are returned by the backend
 * exactly ONCE (verify / regenerate); the consuming UI must force the
 * user to save them before allowing dismissal.
 */

import { apiPost } from "@/lib/api";

export interface MfaSetupResponse {
  provisioning_uri: string;
  secret_b32: string;
  qr_data_url: string;
}

export interface MfaVerifyResponse {
  mfa_enabled: boolean;
  recovery_codes: string[];
}

export interface MfaDisableResponse {
  mfa_enabled: boolean;
  recovery_codes_deleted: number;
}

export interface MfaRegenerateResponse {
  recovery_codes: string[];
}

export interface MfaLoginResponse {
  user: { id: string; email: string; is_admin: boolean } | null;
  requires_mfa: boolean;
}

export function mfaSetup(): Promise<MfaSetupResponse> {
  // No body required — the caller is already authenticated and the
  // server generates a fresh per-user TOTP secret.
  return apiPost<MfaSetupResponse>("/api/me/mfa/setup");
}

export function mfaVerify(totp_code: string): Promise<MfaVerifyResponse> {
  return apiPost<MfaVerifyResponse>("/api/me/mfa/verify", { totp_code });
}

export function mfaDisable(body: {
  current_password: string;
  totp_code: string;
}): Promise<MfaDisableResponse> {
  return apiPost<MfaDisableResponse>("/api/me/mfa/disable", body);
}

export function mfaRegenerateRecoveryCodes(
  current_password: string,
): Promise<MfaRegenerateResponse> {
  return apiPost<MfaRegenerateResponse>(
    "/api/me/mfa/recovery-codes/regenerate",
    { current_password },
  );
}

export function loginWithMfa(
  body: { totp_code: string } | { recovery_code: string },
): Promise<MfaLoginResponse> {
  // POST /api/auth/login/mfa. Exactly one of totp_code / recovery_code
  // — the server validates the XOR.
  return apiPost<MfaLoginResponse>("/api/auth/login/mfa", body);
}
