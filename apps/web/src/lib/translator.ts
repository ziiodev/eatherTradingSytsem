/**
 * Frontend client for ``POST /api/tools/mql5-to-python``.
 *
 * Mirrors the Pydantic shape in ``apps/api/src/aether_api/routers/tools.py``.
 * Keep this file in lockstep with the backend until the OpenAPI dump is
 * wired up via @aether/shared-types.
 *
 * Charter alignment: the MQL5 input goes out in the request, the Python
 * comes back in the response, and neither side persists the source
 * across the call boundary. The audit log on the server records sizes
 * only.
 */

import { z } from "zod";

import { apiPost } from "@/lib/api";

// ---------------------------------------------------------------------------
// Schemas
// ---------------------------------------------------------------------------

/** Request body. ``target_entrypoint`` defaults to ``on_tick`` (Worker convention). */
export const mql5TranslateRequestSchema = z.object({
  mql5: z.string().min(1, "Pega código MQL5 antes de convertir"),
  target_entrypoint: z
    .string()
    .regex(
      /^[A-Za-z_][A-Za-z0-9_]{0,119}$/,
      "Nombre de entrypoint inválido",
    )
    .max(120)
    .optional(),
});
export type Mql5TranslateRequest = z.infer<typeof mql5TranslateRequestSchema>;

/** Success envelope. */
export const mql5TranslateResponseSchema = z.object({
  python: z.string(),
  model: z.string(),
  input_tokens: z.number().int().nonnegative(),
  output_tokens: z.number().int().nonnegative(),
});
export type Mql5TranslateResponse = z.infer<typeof mql5TranslateResponseSchema>;

// ---------------------------------------------------------------------------
// API helper
// ---------------------------------------------------------------------------

/**
 * Call the translator. Errors come back as {@link ApiError} from the
 * underlying fetch helper; callers should map by ``.status``:
 *
 *  * 413 → input too large.
 *  * 503 → translator disabled OR API key missing on the server.
 *  * 502 → upstream Anthropic error (``code: "translator_upstream_error"``).
 */
export async function translateMql5ToPython(
  input: Mql5TranslateRequest,
): Promise<Mql5TranslateResponse> {
  const body = mql5TranslateRequestSchema.parse(input);
  const raw = await apiPost<unknown>("/api/tools/mql5-to-python", body);
  return mql5TranslateResponseSchema.parse(raw);
}
