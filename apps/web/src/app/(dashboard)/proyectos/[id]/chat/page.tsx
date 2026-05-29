"use client";

/**
 * Chat — operator ↔ Claude assistant per project.
 *
 * Three-pane layout:
 *
 *   ┌────────────┬─────────────────────────────────────┐
 *   │            │ ConversationThread (scroll)         │
 *   │ Conv.List  │                                     │
 *   │ (240px)    ├─────────────────────────────────────┤
 *   │            │ ChatInput (sticky)                  │
 *   └────────────┴─────────────────────────────────────┘
 *
 * Feature-flag gated: ``GET /api/health`` returns
 * ``features.chat_enabled = (settings.chat_enabled AND bool(api_key))``.
 * When the flag is OFF we show an informational card and never mount
 * the chat UI.
 */

import { use, useCallback, useEffect, useState } from "react";

import { ChatInput } from "@/components/chat/ChatInput";
import { ConversationList } from "@/components/chat/ConversationList";
import { ConversationThread } from "@/components/chat/ConversationThread";
import { useChatStream } from "@/components/chat/useChatStream";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { apiGet } from "@/lib/api";
import {
  CHAT_MODEL_WHITELIST,
  type ChatConversation,
  type ChatModel,
  createConversation,
  listConversations,
  patchConversation,
} from "@/lib/chat";

interface HealthResponse {
  ok: boolean;
  features?: {
    chat_enabled?: boolean;
  };
}

interface ChatPageInnerProps {
  projectId: string;
}

export default function ChatPage({
  params,
}: {
  params: Promise<{ id: string }>;
}): React.JSX.Element {
  const { id: projectId } = use(params);
  return <ChatPageInner projectId={projectId} />;
}

// Exported for tests so they can render the inner component without
// needing to satisfy ``use(params)``'s Suspense contract (the page
// shell unwraps an awaited promise from Next.js's router).
export function ChatPageInner({ projectId }: ChatPageInnerProps): React.JSX.Element {
  const [chatEnabled, setChatEnabled] = useState<boolean | null>(null);
  const [conversations, setConversations] = useState<ChatConversation[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [archivedView, setArchivedView] = useState<boolean>(false);
  const [loadingList, setLoadingList] = useState<boolean>(true);

  // Health check — runs once on mount.
  useEffect(() => {
    let cancelled = false;
    apiGet<HealthResponse>("/api/health")
      .then((data) => {
        if (cancelled) return;
        setChatEnabled(Boolean(data.features?.chat_enabled));
      })
      .catch(() => {
        if (cancelled) return;
        setChatEnabled(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // Conversations — refresh when the filter flips.
  const refreshConversations = useCallback(async () => {
    setLoadingList(true);
    try {
      const data = await listConversations(projectId, {
        archived: archivedView,
        limit: 100,
      });
      setConversations(data.items);
      setActiveId((prev) => {
        const first = data.items[0];
        if (!first) return null;
        if (prev && data.items.some((c) => c.id === prev)) return prev;
        return first.id;
      });
    } catch {
      // Surface via error state on the thread; intentionally swallow here
      // because the list is best-effort UX, not a hard precondition.
    } finally {
      setLoadingList(false);
    }
  }, [projectId, archivedView]);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (chatEnabled) void refreshConversations();
  }, [chatEnabled, refreshConversations]);

  const activeConversation = conversations.find((c) => c.id === activeId) ?? null;

  // Hook that streams turns for the active conversation.
  const {
    messages,
    streaming,
    sendMessage,
    cancelStream,
  } = useChatStream({
    projectId,
    conversationId: activeId,
    enabled: chatEnabled === true,
  });

  const activeModel: ChatModel =
    ((activeConversation?.meta_data as { model_override?: string } | undefined)
      ?.model_override as ChatModel | undefined) ?? CHAT_MODEL_WHITELIST[0];

  const handleCreate = useCallback(async () => {
    try {
      const conv = await createConversation(projectId, {});
      setConversations((prev) => [conv, ...prev]);
      setActiveId(conv.id);
    } catch {
      // No-op: a future toast layer can surface this.
    }
  }, [projectId]);

  const handleArchive = useCallback(
    async (id: string) => {
      try {
        await patchConversation(projectId, id, { archived: true });
        setConversations((prev) => prev.filter((c) => c.id !== id));
        setActiveId((prev) => (prev === id ? null : prev));
      } catch {
        // ignore
      }
    },
    [projectId],
  );

  const handleModelChange = useCallback(
    async (model: ChatModel) => {
      if (!activeId) return;
      try {
        const updated = await patchConversation(projectId, activeId, {
          meta_data: { model_override: model },
        });
        setConversations((prev) =>
          prev.map((c) => (c.id === updated.id ? updated : c)),
        );
      } catch {
        // ignore
      }
    },
    [projectId, activeId],
  );

  // ---- Render gates ----
  if (chatEnabled === null) {
    return (
      <Card data-testid="chat-loading">
        <CardHeader>
          <CardTitle>Chat</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-[rgb(var(--foreground-muted))]">
            Cargando…
          </p>
        </CardContent>
      </Card>
    );
  }
  if (!chatEnabled) {
    return (
      <Card data-testid="chat-disabled-card">
        <CardHeader>
          <CardTitle>Chat no activado</CardTitle>
          <CardDescription>
            El chat no está activado. Configura ANTHROPIC_API_KEY o habilita
            AETHER_CHAT_ENABLED.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-[rgb(var(--foreground-muted))]">
            Una vez habilitado, podrás conversar con el asistente del
            proyecto, revisar el historial de la sesión y aprobar acciones
            sensibles antes de que se ejecuten.
          </p>
        </CardContent>
      </Card>
    );
  }

  return (
    <div
      className="flex h-[calc(100vh-280px)] min-h-[480px] overflow-hidden rounded-lg border border-[rgb(var(--border))] bg-[rgb(var(--card))]"
      data-testid="chat-surface"
    >
      <ConversationList
        conversations={conversations}
        activeId={activeId}
        archived={archivedView}
        onSelect={setActiveId}
        onCreate={handleCreate}
        onToggleArchived={setArchivedView}
        onArchive={handleArchive}
        loading={loadingList}
      />
      <section className="flex flex-1 flex-col">
        <ConversationThread messages={messages} streaming={streaming} />
        {activeConversation ? (
          <ChatInput
            model={activeModel}
            tokensInTotal={activeConversation.tokens_in_total}
            usdEstimatedTotal={activeConversation.usd_estimated_total}
            streaming={streaming}
            onSubmit={(content) => void sendMessage(content)}
            onModelChange={(m) => void handleModelChange(m)}
            onCancel={cancelStream}
          />
        ) : (
          <div
            className="border-t border-[rgb(var(--border))] p-3 text-sm text-[rgb(var(--foreground-muted))]"
            data-testid="chat-no-conversation"
          >
            Crea una conversación nueva con el botón + en la barra
            izquierda para empezar.
          </div>
        )}
      </section>
    </div>
  );
}
