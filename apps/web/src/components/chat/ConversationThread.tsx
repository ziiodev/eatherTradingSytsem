"use client";

/**
 * ConversationThread — scrollable message thread.
 *
 * Auto-scroll behaviour: when a new message arrives we scroll the
 * container to the bottom IF the operator is already near the bottom.
 * If they scrolled up to read older content we don't yank them down —
 * a sticky "Nuevo mensaje" button could be added later (out of scope
 * for v1).
 *
 * Streaming indicator (typing dots) renders below the message list
 * when ``streaming=true`` AND the last message is an assistant row
 * still waiting for its first token (so we don't show dots after the
 * model has already started speaking).
 *
 * Empty state mirrors the spec literal so the operator knows the
 * assistant has full project context.
 */

import { useEffect, useRef } from "react";

import { MessageBubble } from "@/components/chat/MessageBubble";
import type { ChatStreamMessage } from "@/components/chat/useChatStream";

const NEAR_BOTTOM_PX = 120;
const EMPTY_STATE_COPY =
  "Inicia una conversación con el asistente del proyecto. Tiene contexto completo: configuración, métricas, trades recientes, sleep reports y Q-Table.";

export interface ConversationThreadProps {
  messages: ChatStreamMessage[];
  streaming: boolean;
}

export function ConversationThread({
  messages,
  streaming,
}: ConversationThreadProps): React.JSX.Element {
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const wasAtBottomRef = useRef<boolean>(true);

  // Track scroll position so we only auto-scroll when the operator is
  // already pinned to the bottom.
  const handleScroll = () => {
    const el = scrollRef.current;
    if (!el) return;
    const distance = el.scrollHeight - el.scrollTop - el.clientHeight;
    wasAtBottomRef.current = distance <= NEAR_BOTTOM_PX;
  };

  useEffect(() => {
    if (!wasAtBottomRef.current) return;
    const el = scrollRef.current;
    if (el) {
      el.scrollTop = el.scrollHeight;
    }
  }, [messages, streaming]);

  if (messages.length === 0 && !streaming) {
    return (
      <div
        className="flex flex-1 items-center justify-center px-6 py-12"
        data-testid="chat-empty-state"
      >
        <p className="max-w-md text-center text-sm text-[rgb(var(--foreground-muted))]">
          {EMPTY_STATE_COPY}
        </p>
      </div>
    );
  }

  return (
    <div
      ref={scrollRef}
      onScroll={handleScroll}
      className="flex-1 space-y-3 overflow-y-auto px-4 py-4"
      data-testid="chat-thread"
    >
      {messages.map((m) => (
        <MessageBubble key={m.id} message={m} />
      ))}
      {streaming && messages.at(-1)?.content === "" && (
        <p
          className="px-2 text-xs text-[rgb(var(--foreground-muted))]"
          data-testid="chat-streaming-indicator"
        >
          ● ● ●
        </p>
      )}
    </div>
  );
}

export default ConversationThread;
