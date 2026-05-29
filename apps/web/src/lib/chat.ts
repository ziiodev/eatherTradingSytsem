/**
 * Frontend domain types + zod schemas for the project Chat surface.
 *
 * Mirrors the backend Pydantic v2 DTOs in
 * ``apps/api/src/aether_api/routers/chat.py`` and the SSE event protocol
 * defined in ``apps/api/src/aether_api/services/chat/stream.py``.
 *
 * Wire format note: the SSE stream uses ``event: <type>`` + ``data: <json>``
 * frames separated by a blank line, so a single ``EventSource`` cannot be
 * used here — ``EventSource`` doesn't support POST + body, and the chat
 * turn endpoint is POSTed. We consume the stream with ``fetch`` plus
 * ``ReadableStream.getReader()`` and a small state machine that buffers
 * partial frames until ``\n\n`` lands.
 *
 * IMPORTANT: model selection is locked to ``MODEL_WHITELIST``. The
 * backend validates this at the DTO layer and refuses unknown ids.
 */

import { z } from "zod";

import { apiDelete as _unused_apiDelete, apiGet, apiPatch, apiPost } from "@/lib/api";
import { withCsrfHeader } from "@/lib/auth";

// Suppress unused-import lint without removing it — apiDelete is re-exported
// from this module for symmetry with `projects.ts`. Kept under an underscored
// alias to make the intent obvious.
void _unused_apiDelete;

// ---------------------------------------------------------------------------
// Whitelisted models — must stay in sync with
// ``services/chat/anthropic_client.py::MODEL_WHITELIST``.
// ---------------------------------------------------------------------------
export const CHAT_MODEL_WHITELIST = [
  "claude-sonnet-4-5",
  "claude-haiku-4-5",
] as const;
export type ChatModel = (typeof CHAT_MODEL_WHITELIST)[number];

export const CHAT_MODEL_LABEL: Record<ChatModel, string> = {
  "claude-sonnet-4-5": "Claude Sonnet 4.5",
  "claude-haiku-4-5": "Claude Haiku 4.5",
};

// ---------------------------------------------------------------------------
// Conversation + message schemas
// ---------------------------------------------------------------------------

export const conversationSchema = z.object({
  id: z.string().uuid(),
  project_id: z.string().uuid(),
  title: z.string(),
  created_at: z.string().nullable().optional(),
  updated_at: z.string().nullable().optional(),
  archived_at: z.string().nullable().optional(),
  tokens_in_total: z.number().int().nonnegative().default(0),
  usd_estimated_total: z
    .union([z.string(), z.number()])
    .transform((v) => (typeof v === "string" ? Number.parseFloat(v) : v))
    .default(0),
  meta_data: z.record(z.unknown()).default({}),
});

export type ChatConversation = z.infer<typeof conversationSchema>;

const messageRoleSchema = z.enum(["user", "assistant", "tool", "system"]);
export type ChatMessageRole = z.infer<typeof messageRoleSchema>;

export const messageSchema = z.object({
  id: z.string().uuid(),
  conversation_id: z.string().uuid(),
  role: messageRoleSchema,
  content: z.string(),
  tool_calls: z.array(z.record(z.unknown())).nullable().optional(),
  tool_results: z.array(z.record(z.unknown())).nullable().optional(),
  tokens_in: z.number().int().nullable().optional(),
  tokens_out: z.number().int().nullable().optional(),
  model: z.string().nullable().optional(),
  stop_reason: z.string().nullable().optional(),
  created_at: z.string().nullable().optional(),
});

export type ChatMessage = z.infer<typeof messageSchema>;

export const conversationListResponseSchema = z.object({
  items: z.array(conversationSchema),
  total: z.number().int().nonnegative(),
});
export type ConversationListResponse = z.infer<
  typeof conversationListResponseSchema
>;

export const messageListResponseSchema = z.object({
  items: z.array(messageSchema),
  total: z.number().int().nonnegative(),
});
export type MessageListResponse = z.infer<typeof messageListResponseSchema>;

// ---------------------------------------------------------------------------
// SSE event schemas — discriminated by the ``event:`` line. The ``data:``
// payload is the raw JSON described by each event-specific schema.
// ---------------------------------------------------------------------------

export const tokenEventSchema = z.object({
  type: z.literal("token"),
  delta: z.string(),
});

export const toolUseEventSchema = z.object({
  type: z.literal("tool_use"),
  tool_use_id: z.string(),
  tool_name: z.string(),
  input: z.record(z.unknown()).default({}),
});

export const toolResultEventSchema = z.object({
  type: z.literal("tool_result"),
  tool_use_id: z.string(),
  output: z.unknown(),
  is_error: z.boolean().default(false),
});

export const turnDoneEventSchema = z.object({
  type: z.literal("turn_done"),
  stop_reason: z.string().nullable().optional(),
  tokens_in: z.number().int().default(0),
  tokens_out: z.number().int().default(0),
  model: z.string(),
  usd_estimated: z.number().default(0),
  soft_warning: z.boolean().optional(),
});

export const errorEventSchema = z.object({
  type: z.literal("error"),
  code: z.string(),
  message: z.string(),
});

export const sseEventSchema = z.discriminatedUnion("type", [
  tokenEventSchema,
  toolUseEventSchema,
  toolResultEventSchema,
  turnDoneEventSchema,
  errorEventSchema,
]);

export type SseEvent = z.infer<typeof sseEventSchema>;
export type TokenEvent = z.infer<typeof tokenEventSchema>;
export type ToolUseEvent = z.infer<typeof toolUseEventSchema>;
export type ToolResultEvent = z.infer<typeof toolResultEventSchema>;
export type TurnDoneEvent = z.infer<typeof turnDoneEventSchema>;
export type ErrorEvent = z.infer<typeof errorEventSchema>;

// ---------------------------------------------------------------------------
// REST fetchers
// ---------------------------------------------------------------------------

export interface ListConversationsParams {
  archived?: boolean;
  limit?: number;
  offset?: number;
}

function buildConvListPath(
  projectId: string,
  params: ListConversationsParams,
): string {
  const sp = new URLSearchParams();
  if (params.archived !== undefined) sp.set("archived", String(params.archived));
  if (params.limit !== undefined) sp.set("limit", String(params.limit));
  if (params.offset !== undefined) sp.set("offset", String(params.offset));
  const qs = sp.toString();
  return qs
    ? `/api/projects/${projectId}/chat/conversations?${qs}`
    : `/api/projects/${projectId}/chat/conversations`;
}

export async function listConversations(
  projectId: string,
  params: ListConversationsParams = {},
): Promise<ConversationListResponse> {
  const data = await apiGet<unknown>(buildConvListPath(projectId, params));
  return conversationListResponseSchema.parse(data);
}

export interface CreateConversationBody {
  title?: string;
  model_override?: ChatModel;
}

export async function createConversation(
  projectId: string,
  body: CreateConversationBody = {},
): Promise<ChatConversation> {
  const data = await apiPost<unknown>(
    `/api/projects/${projectId}/chat/conversations`,
    body,
  );
  return conversationSchema.parse(data);
}

export interface ConversationDetail {
  conversation: ChatConversation;
  messages: ChatMessage[];
}

export async function getConversation(
  projectId: string,
  conversationId: string,
  options: { last?: number } = {},
): Promise<ConversationDetail> {
  const sp = new URLSearchParams();
  if (options.last !== undefined) sp.set("last", String(options.last));
  const qs = sp.toString();
  const path = qs
    ? `/api/projects/${projectId}/chat/conversations/${conversationId}?${qs}`
    : `/api/projects/${projectId}/chat/conversations/${conversationId}`;
  const raw = (await apiGet<{
    conversation: unknown;
    messages: unknown[];
  }>(path)) as { conversation: unknown; messages: unknown[] };
  return {
    conversation: conversationSchema.parse(raw.conversation),
    messages: raw.messages.map((m) => messageSchema.parse(m)),
  };
}

export interface PatchConversationBody {
  title?: string;
  archived?: boolean;
  model_override?: ChatModel;
  // Allow free-form meta updates for things like model_override; the
  // backend stores model_override under ``meta_data.model_override``.
  meta_data?: Record<string, unknown>;
}

export async function patchConversation(
  projectId: string,
  conversationId: string,
  body: PatchConversationBody,
): Promise<ChatConversation> {
  const data = await apiPatch<unknown>(
    `/api/projects/${projectId}/chat/conversations/${conversationId}`,
    body,
  );
  return conversationSchema.parse(data);
}

export async function listMessages(
  projectId: string,
  conversationId: string,
  params: { limit?: number; offset?: number } = {},
): Promise<MessageListResponse> {
  const sp = new URLSearchParams();
  if (params.limit !== undefined) sp.set("limit", String(params.limit));
  if (params.offset !== undefined) sp.set("offset", String(params.offset));
  const qs = sp.toString();
  const path = qs
    ? `/api/projects/${projectId}/chat/conversations/${conversationId}/messages?${qs}`
    : `/api/projects/${projectId}/chat/conversations/${conversationId}/messages`;
  const data = await apiGet<unknown>(path);
  return messageListResponseSchema.parse(data);
}

// ---------------------------------------------------------------------------
// SSE streaming postMessage
// ---------------------------------------------------------------------------

export interface PostMessageOptions {
  onEvent: (event: SseEvent) => void;
  signal?: AbortSignal;
}

export class ChatPostError extends Error {
  readonly status: number;
  readonly body: unknown;
  constructor(message: string, status: number, body: unknown) {
    super(message);
    this.name = "ChatPostError";
    this.status = status;
    this.body = body;
  }
}

/**
 * Parse one SSE frame block (raw text between two ``\n\n`` boundaries)
 * into an :class:`SseEvent`. Returns ``null`` when the block is empty,
 * is a comment / ping, or fails schema validation — the caller silently
 * skips those.
 *
 * Frame shape (per the W3C SSE spec):
 *
 *     event: token
 *     data: {"delta": "hola"}
 *
 * Multi-line ``data:`` lines are concatenated with ``\n``.
 */
export function parseSseFrame(raw: string): SseEvent | null {
  const trimmed = raw.replace(/^\n+/, "").replace(/\n+$/, "");
  if (!trimmed) return null;
  let eventName: string | null = null;
  const dataLines: string[] = [];
  for (const line of trimmed.split("\n")) {
    if (line.startsWith(":")) continue; // comments / pings
    if (line.startsWith("event:")) {
      eventName = line.slice("event:".length).trim();
    } else if (line.startsWith("data:")) {
      dataLines.push(line.slice("data:".length).replace(/^ /, ""));
    }
  }
  if (eventName === null || dataLines.length === 0) return null;
  let payload: unknown;
  try {
    payload = JSON.parse(dataLines.join("\n"));
  } catch {
    return null;
  }
  if (!payload || typeof payload !== "object") return null;
  const withType = { ...(payload as Record<string, unknown>), type: eventName };
  const result = sseEventSchema.safeParse(withType);
  return result.success ? result.data : null;
}

/**
 * POST a new user message and stream the assistant turn as SSE events.
 *
 * Uses ``fetch`` + ``ReadableStream.getReader()`` because:
 *
 * 1. ``EventSource`` only supports GET — we need to POST the body.
 * 2. We need ``credentials: 'include'`` to send the httpOnly access cookie.
 * 3. We need ``signal`` (AbortController) so the UI's "stop" button can
 *    tear the connection down mid-stream.
 *
 * The caller's ``onEvent`` is invoked synchronously per parsed event in
 * order. When the stream ends (turn_done or error or aborted) the
 * returned promise resolves; HTTP-level errors (401 / 403 / 409 / 500)
 * surface as :class:`ChatPostError` with the structured backend body.
 */
export async function postMessage(
  projectId: string,
  conversationId: string,
  content: string,
  options: PostMessageOptions,
): Promise<void> {
  const url = `/api/projects/${projectId}/chat/conversations/${conversationId}/messages`;
  const baseInit: RequestInit = {
    method: "POST",
    credentials: "include",
    headers: {
      Accept: "text/event-stream",
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ content }),
    signal: options.signal,
  };
  const init = withCsrfHeader(baseInit);

  let res: Response;
  try {
    res = await fetch(url, init);
  } catch (err) {
    // Network error / aborted before the request was sent.
    if ((err as { name?: string })?.name === "AbortError") return;
    throw err;
  }

  if (!res.ok || !res.body) {
    let body: unknown = null;
    try {
      const ct = res.headers.get("Content-Type") ?? "";
      body = ct.includes("application/json") ? await res.json() : await res.text();
    } catch {
      // ignore — body is optional context for the error
    }
    throw new ChatPostError(
      `POST ${url} failed with ${res.status}`,
      res.status,
      body,
    );
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";

  // Abort handling — when the AbortSignal fires we tear down the reader
  // so the upstream connection is released. The fetch call above also
  // wires the signal in so the browser cancels the request itself.
  const onAbort = () => {
    void reader.cancel().catch(() => {
      // Swallow — cancel() can race with the natural stream end.
    });
  };
  options.signal?.addEventListener("abort", onAbort);

  try {
    while (true) {
      let chunk: ReadableStreamReadResult<Uint8Array>;
      try {
        chunk = await reader.read();
      } catch (err) {
        if ((err as { name?: string })?.name === "AbortError") return;
        throw err;
      }
      if (chunk.done) break;
      buffer += decoder.decode(chunk.value, { stream: true });

      // SSE frames are separated by a blank line — split on \n\n and
      // process every complete frame; keep the trailing fragment for
      // the next chunk.
      let separatorIdx: number;
      while ((separatorIdx = buffer.indexOf("\n\n")) !== -1) {
        const frame = buffer.slice(0, separatorIdx);
        buffer = buffer.slice(separatorIdx + 2);
        const parsed = parseSseFrame(frame);
        if (parsed) options.onEvent(parsed);
      }
    }
    // Flush any trailing frame that didn't end with \n\n.
    if (buffer.trim()) {
      const parsed = parseSseFrame(buffer);
      if (parsed) options.onEvent(parsed);
    }
  } finally {
    options.signal?.removeEventListener("abort", onAbort);
  }
}

// ---------------------------------------------------------------------------
// Convenience: pretty-print USD with 4 decimals for the cost summary.
// ---------------------------------------------------------------------------
export function formatUsd(value: number | string | null | undefined): string {
  if (value === null || value === undefined) return "$0.0000";
  const n = typeof value === "string" ? Number.parseFloat(value) : value;
  if (Number.isNaN(n)) return "$0.0000";
  return `$${n.toFixed(4)}`;
}
