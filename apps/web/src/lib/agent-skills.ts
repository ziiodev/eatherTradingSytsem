/**
 * Frontend types + API helpers for the agent ↔ skill bindings
 * (``/api/agents/{id}/skills``).
 *
 * Mirrors the Pydantic models in
 * ``apps/api/src/aether_api/routers/agents.py`` (AttachedSkill /
 * AttachSkillRequest). Until ``@aether/shared-types`` is regenerated from
 * the live OpenAPI dump we keep the contract local — same pattern as
 * ``lib/agents.ts`` and ``lib/skills.ts``.
 */

import { z } from "zod";

import { apiDelete, apiGet, apiPost } from "@/lib/api";
import { SKILL_RUNTIMES, SKILL_TYPES } from "@/lib/skills";

export const attachedSkillSchema = z.object({
  binding_id: z.string().uuid(),
  skill_id: z.string().uuid(),
  name: z.string(),
  type: z.enum(SKILL_TYPES),
  runtime: z.enum(SKILL_RUNTIMES).default("markdown"),
  is_active: z.boolean(),
  version: z.number().int().nonnegative(),
  notes: z.string().nullable(),
  created_at: z.string(),
});
export type AttachedSkill = z.infer<typeof attachedSkillSchema>;

export const attachSkillRequestSchema = z.object({
  skill_id: z.string().uuid(),
  notes: z.string().max(4000).optional(),
});
export type AttachSkillRequest = z.infer<typeof attachSkillRequestSchema>;

export async function listAgentSkills(
  agentId: string,
): Promise<AttachedSkill[]> {
  const raw = await apiGet<unknown>(`/api/agents/${agentId}/skills`);
  return z.array(attachedSkillSchema).parse(raw);
}

export async function attachSkillToAgent(
  agentId: string,
  input: AttachSkillRequest,
): Promise<AttachedSkill> {
  const body = attachSkillRequestSchema.parse(input);
  const raw = await apiPost<unknown>(`/api/agents/${agentId}/skills`, body);
  return attachedSkillSchema.parse(raw);
}

export async function detachSkillFromAgent(
  agentId: string,
  skillId: string,
): Promise<void> {
  await apiDelete<void>(`/api/agents/${agentId}/skills/${skillId}`);
}
