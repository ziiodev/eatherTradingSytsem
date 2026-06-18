/**
 * ``useChatStream`` — React hook that drives one SSE-streamed assistant
 * turn against ``/api/pairs/{pairId}/chat/conversations/{conversationId}/messages``.
 *
 * State machine:
 *
 *   idle ──sendMessage──▶ streaming
 *     • optimistic user message appended immediately
 *     • empty assistant message materialised with role='assistant',
 *       content='', tool_calls/tool_results=[]
 *   streaming ── token ──▶ append delta to current assistant.content
 *   streaming ── tool_use ──▶ append {id, name, input} to current
 *                              assistant.tool_calls
 *   streaming ── tool_result ──▶ append result entry into current
 *                                 assistant.tool_results
 *   streaming ── turn_done ──▶ finalize (stop_reason, tokens, usd) ──▶ idle
 *   streaming ── error ──▶ set error, finalize partial, ──▶ idle
 *   streaming ── cancelStream / disconnect ──▶ tag assistant as
 *                                            ``(interrumpido)`` ──▶ idle
 *
 * The hook returns ``messages`` as a UI-only superset of
 * :type:`ChatMessage` because the streaming assistant row doesn't yet
 * have a DB id; we synthesise a stable client id so React keys stay
 * deterministic.
 */

"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  type ChatMessage,
  type ChatMessageRole,
  type SseEvent,
  ChatPostError,
  getConversation,
  postMessage,
} from "@/lib/chat";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface ChatStreamMessage extends Omit<ChatMessage, "id"> {
  /** Stable client-side id (may be the DB id for persisted rows). */
  id: string;
  /** Set on the optimistic user / assistant rows so the UI can flag them. */
  pending?: boolean;
  /** Set when the stream was interrupted before turn_done. */
  interrupted?: boolean;
  /** Set when the persisted history has not yet been replaced by the DB row. */
  optimistic?: boolean;
  // tool_calls / tool_results allow null in the wire schema; here we use
  // narrowed arrays (possibly empty) because the hook always materialises
  // them.
  tool_calls: Array<Record<string, unknown>>;
  tool_results: Array<Record<string, unknown>>;
}

export interface ChatStreamError {
  code: string;
  message: string;
}

export interface UseChatStreamOptions {
  pairId: string;
  conversationId: string | null;
  enabled?: boolean;
}

export interface UseChatStreamResult {
  messages: ChatStreamMessage[];
  streaming: boolean;
  error: ChatStreamError | null;
  sendMessage: (content: string) => Promise<void>;
  cancelStream: () => void;
  refresh: () => Promise<void>;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function clientId(prefix: string): string {
  // Browser-native randomUUID is widely available in the dashboard
  // target environments (Next.js 16 + modern Chromium/Firefox). The
  // fallback keeps Jest/happy-dom + older Safari working.
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return `${prefix}-${(crypto as Crypto).randomUUID()}`;
  }
  return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

function toStreamMessage(row: ChatMessage): ChatStreamMessage {
  return {
    ...row,
    tool_calls: row.tool_calls ?? [],
    tool_results: row.tool_results ?? [],
  };
}

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

export function useChatStream(
  options: UseChatStreamOptions,
): UseChatStreamResult {
  const { pairId, conversationId, enabled = true } = options;
  const [messages, setMessages] = useState<ChatStreamMessage[]>([]);
  const [streaming, setStreaming] = useState(false);
  const [error, setError] = useState<ChatStreamError | null>(null);

  // Track the assistant id currently receiving deltas so each SSE event
  // can locate and mutate the right row in O(1).
  const activeAssistantIdRef = useRef<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  // ---------------- Initial load ----------------
  const refresh = useCallback(async (): Promise<void> => {
    if (!conversationId || !enabled) return;
    try {
      const detail = await getConversation(pairId, conversationId, {
        last: 200,
      });
      setMessages(detail.messages.map(toStreamMessage));
    } catch (e) {
      // The page-level error boundary handles auth redirects; here we
      // just report the structured error so the UI can surface it.
      setError({
        code: e instanceof ChatPostError ? String(e.status) : "LOAD_FAILED",
        message:
          e instanceof Error
            ? e.message
            : "No se pudo cargar la conversación.",
      });
    }
  }, [pairId, conversationId, enabled]);

  useEffect(() => {
    if (!conversationId || !enabled) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setMessages([]);
      return;
    }
    void refresh();
    // Cleanup: cancel any in-flight stream when the conversation changes
    // or the component unmounts.
    return () => {
      abortRef.current?.abort();
      abortRef.current = null;
      activeAssistantIdRef.current = null;
    };
  }, [conversationId, enabled, refresh]);

  // ---------------- SSE event handler ----------------
  const applyEvent = useCallback((ev: SseEvent) => {
    const assistantId = activeAssistantIdRef.current;
    if (!assistantId) return;

    setMessages((prev) => {
      const idx = prev.findIndex((m) => m.id === assistantId);
      if (idx === -1) return prev;
      const current = prev[idx];
      if (!current) return prev;
      let next: ChatStreamMessage = current;

      if (ev.type === "token") {
        next = { ...current, content: current.content + ev.delta };
      } else if (ev.type === "tool_use") {
        next = {
          ...current,
          tool_calls: [
            ...current.tool_calls,
            {
              id: ev.tool_use_id,
              name: ev.tool_name,
              input: ev.input,
            },
          ],
        };
      } else if (ev.type === "tool_result") {
        next = {
          ...current,
          tool_results: [
            ...current.tool_results,
            {
              tool_use_id: ev.tool_use_id,
              content: ev.output,
              is_error: ev.is_error,
            },
          ],
        };
      } else if (ev.type === "turn_done") {
        next = {
          ...current,
          stop_reason: ev.stop_reason ?? "end_turn",
          tokens_in: ev.tokens_in ?? null,
          tokens_out: ev.tokens_out ?? null,
          model: ev.model,
          pending: false,
        };
      } else if (ev.type === "error") {
        next = {
          ...current,
          stop_reason: "aborted",
          interrupted: true,
          pending: false,
        };
      }

      const updated = [...prev];
      updated[idx] = next;
      return updated;
    });

    if (ev.type === "turn_done") {
      activeAssistantIdRef.current = null;
      setStreaming(false);
    } else if (ev.type === "error") {
      setError({ code: ev.code, message: ev.message });
      activeAssistantIdRef.current = null;
      setStreaming(false);
    }
  }, []);

  // ---------------- sendMessage ----------------
  const sendMessage = useCallback(
    async (content: string): Promise<void> => {
      if (!conversationId || !enabled) return;
      if (streaming) return;
      setError(null);

      const userId = clientId("user");
      const assistantId = clientId("asst");
      activeAssistantIdRef.current = assistantId;

      const now = new Date().toISOString();
      const userRow: ChatStreamMessage = {
        id: userId,
        conversation_id: conversationId,
        role: "user" as ChatMessageRole,
        content,
        tool_calls: [],
        tool_results: [],
        tokens_in: null,
        tokens_out: null,
        model: null,
        stop_reason: null,
        created_at: now,
        optimistic: true,
      };
      const assistantRow: ChatStreamMessage = {
        id: assistantId,
        conversation_id: conversationId,
        role: "assistant" as ChatMessageRole,
        content: "",
        tool_calls: [],
        tool_results: [],
        tokens_in: null,
        tokens_out: null,
        model: null,
        stop_reason: null,
        created_at: now,
        pending: true,
      };
      setMessages((prev) => [...prev, userRow, assistantRow]);
      setStreaming(true);

      const controller = new AbortController();
      abortRef.current = controller;
      try {
        await postMessage(pairId, conversationId, content, {
          onEvent: applyEvent,
          signal: controller.signal,
        });

        // If the stream closed without turn_done (eg connection drop)
        // we still have the assistant row pending. Tag it as interrupted.
        // Capture the id locally because the ref is nulled below — the
        // setMessages callback may run after that null is committed.
        const pendingAssistant = activeAssistantIdRef.current;
        if (pendingAssistant) {
          setMessages((prev) =>
            prev.map((m) =>
              m.id === pendingAssistant
                ? {
                    ...m,
                    pending: false,
                    interrupted: true,
                    stop_reason: m.stop_reason ?? "disconnected",
                  }
                : m,
            ),
          );
          activeAssistantIdRef.current = null;
          setStreaming(false);
        }
      } catch (e) {
        const failureCode =
          e instanceof ChatPostError ? String(e.status) : "STREAM_FAILED";
        const failureMsg =
          e instanceof Error ? e.message : "Error al transmitir la conversación.";
        setError({ code: failureCode, message: failureMsg });
        setMessages((prev) =>
          prev.map((m) =>
            m.id === assistantId
              ? {
                  ...m,
                  pending: false,
                  interrupted: true,
                  stop_reason: m.stop_reason ?? "aborted",
                }
              : m,
          ),
        );
        activeAssistantIdRef.current = null;
        setStreaming(false);
      } finally {
        abortRef.current = null;
      }
    },
    [pairId, conversationId, enabled, streaming, applyEvent],
  );

  // ---------------- cancelStream ----------------
  const cancelStream = useCallback(() => {
    abortRef.current?.abort();
  }, []);

  return useMemo(
    () => ({ messages, streaming, error, sendMessage, cancelStream, refresh }),
    [messages, streaming, error, sendMessage, cancelStream, refresh],
  );
}
