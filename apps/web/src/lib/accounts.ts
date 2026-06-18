/**
 * Frontend domain types + zod schemas for the Accounts (Cuentas) surface.
 *
 * Mirrors the Pydantic v2 models in
 * ``apps/api/src/aether_api/routers/accounts.py``. The Account (Cuenta) is the
 * grouping layer of the accounts-pairs hierarchy
 * ``Exchange → Account (Cuenta) → Pair (Par) → Agents`` and OWNS the broker
 * credential block that used to live on the old ``projects`` row. Every pair
 * under an account inherits those credentials (no per-pair override).
 *
 * NAME-COLLISION NOTE (accounts-pairs-restructure): there is already an
 * ``AccountSummary`` type in ``lib/operativa.ts`` that models the live MT5
 * account snapshot (balance/equity/margin). It is a DIFFERENT concept. To
 * avoid the clash, the entity types here are named ``TradingAccount*`` — the
 * MT5 snapshot keeps the ``AccountSummary`` name.
 *
 * IMPORTANT: any change to the backend DTOs must be mirrored here.
 */

import { z } from "zod";

import { apiDelete, apiGet, apiPatch, apiPost } from "@/lib/api";
import type {
  PairCreateInput,
  PairDetail,
  PairListResponse,
} from "@/lib/pairs";

// ---------------------------------------------------------------------------
// Canonical constants.
// ---------------------------------------------------------------------------
export const ACCOUNT_TYPES = ["demo", "real"] as const;
export type AccountTypeValue = (typeof ACCOUNT_TYPES)[number];

export const ACCOUNT_TYPE_LABEL: Record<AccountTypeValue, string> = {
  demo: "Demo",
  real: "Real",
};

// ---------------------------------------------------------------------------
// API response shapes — the broker-credential block lives here.
// ---------------------------------------------------------------------------
export interface TradingAccount {
  id: string;
  exchange_id: string;
  name: string;
  description: string | null;
  account_login: string | null;
  account_server: string | null;
  broker_name: string | null;
  account_credential_ref: string | null;
  account_currency: string | null;
  account_leverage: number | null;
  account_type: string | null;
  meta_data: Record<string, unknown>;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface TradingAccountListResponse {
  items: TradingAccount[];
  total: number;
  limit: number;
  offset: number;
}

// ---------------------------------------------------------------------------
// zod schemas — paired 1:1 with the backend Pydantic models. The broker
// credential block (account_login/server, broker_name, credential_ref,
// currency, leverage, type) was MOVED here off the pair create surface.
// ---------------------------------------------------------------------------
export const accountCreateSchema = z.object({
  exchange_id: z.string().uuid("Exchange requerido"),
  name: z
    .string()
    .min(1, "Nombre requerido")
    .max(100, "Máximo 100 caracteres"),
  description: z.string().max(2000).optional().nullable(),
  account_login: z.string().max(50).optional().nullable(),
  account_server: z.string().max(100).optional().nullable(),
  broker_name: z.string().max(80).optional().nullable(),
  account_credential_ref: z.string().max(255).optional().nullable(),
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
  meta_data: z.record(z.unknown()).optional(),
});

export type TradingAccountCreateInput = z.infer<typeof accountCreateSchema>;

export const accountPatchSchema = accountCreateSchema.partial();
export type TradingAccountPatchInput = z.infer<typeof accountPatchSchema>;

// ---------------------------------------------------------------------------
// API helpers.
// ---------------------------------------------------------------------------
export interface AccountListParams {
  exchange_id?: string;
  limit?: number;
  offset?: number;
}

function buildListPath(params: AccountListParams): string {
  const sp = new URLSearchParams();
  if (params.exchange_id) sp.set("exchange_id", params.exchange_id);
  if (params.limit !== undefined) sp.set("limit", String(params.limit));
  if (params.offset !== undefined) sp.set("offset", String(params.offset));
  const qs = sp.toString();
  return qs ? `/api/accounts?${qs}` : "/api/accounts";
}

export function listAccounts(
  params: AccountListParams = {},
): Promise<TradingAccountListResponse> {
  return apiGet<TradingAccountListResponse>(buildListPath(params));
}

export function getAccount(id: string): Promise<TradingAccount> {
  return apiGet<TradingAccount>(`/api/accounts/${id}`);
}

export function createAccount(
  body: TradingAccountCreateInput,
): Promise<TradingAccount> {
  return apiPost<TradingAccount>("/api/accounts", body);
}

export function patchAccount(
  id: string,
  body: TradingAccountPatchInput,
): Promise<TradingAccount> {
  return apiPatch<TradingAccount>(`/api/accounts/${id}`, body);
}

export function deleteAccount(id: string): Promise<void> {
  return apiDelete<void>(`/api/accounts/${id}`);
}

// ---------------------------------------------------------------------------
// Nested pair collection under an account.
//   GET  /api/accounts/{accountId}/pairs  — list pairs under the account.
//   POST /api/accounts/{accountId}/pairs  — create a pair under the account.
//
// The pair-create body taken by the nested endpoint omits account_id (it is
// derived from the path), so callers pass the pair-create payload sans
// account_id.
// ---------------------------------------------------------------------------
export interface ListAccountPairsParams {
  status?: string;
  limit?: number;
  offset?: number;
}

export function listAccountPairs(
  accountId: string,
  params: ListAccountPairsParams = {},
): Promise<PairListResponse> {
  const sp = new URLSearchParams();
  if (params.status) sp.set("status", params.status);
  if (params.limit !== undefined) sp.set("limit", String(params.limit));
  if (params.offset !== undefined) sp.set("offset", String(params.offset));
  const qs = sp.toString();
  return apiGet<PairListResponse>(
    qs
      ? `/api/accounts/${accountId}/pairs?${qs}`
      : `/api/accounts/${accountId}/pairs`,
  );
}

/** Body for the nested pair-create endpoint — account_id comes from the path. */
export type NestedPairCreateInput = Omit<PairCreateInput, "account_id">;

export function createAccountPair(
  accountId: string,
  body: NestedPairCreateInput,
): Promise<PairDetail> {
  return apiPost<PairDetail>(`/api/accounts/${accountId}/pairs`, body);
}
