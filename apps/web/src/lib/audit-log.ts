/**
 * Typed client for ``/api/me/audit-log``.
 *
 * Shapes mirror :class:`aether_api.routers.audit_log.AuditLogPage` /
 * :class:`AuditLogItem` 1:1. When the OpenAPI schema is regenerated these
 * will be swapped for ``ApiResponse<"/api/me/audit-log", ...>`` from
 * ``@aether/shared-types``.
 */

import { apiGet } from "@/lib/api";

export interface AuditLogItem {
  id: string;
  action: string;
  target_type: string;
  target_id: string | null;
  before_state: Record<string, unknown> | null;
  after_state: Record<string, unknown> | null;
  ip_address: string | null;
  user_agent: string | null;
  created_at: string | null;
}

export interface AuditLogPage {
  items: AuditLogItem[];
  total: number;
  limit: number;
  offset: number;
}

export function listAuditLog(params: {
  limit?: number;
  offset?: number;
}): Promise<AuditLogPage> {
  const query = new URLSearchParams();
  if (params.limit) query.set("limit", String(params.limit));
  if (params.offset) query.set("offset", String(params.offset));
  const qs = query.toString();
  return apiGet<AuditLogPage>(`/api/me/audit-log${qs ? `?${qs}` : ""}`);
}
