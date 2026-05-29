"use client";

/**
 * ConversationList — left rail of the project chat surface.
 *
 * Renders one entry per conversation with the title, the cumulative
 * token / USD totals, and a hover-revealed archive button. A toggle
 * at the top filters between unarchived (default) and archived views.
 *
 * The component is purely controlled: it takes a list of conversations
 * and a set of callbacks, and never owns the source-of-truth fetch
 * state itself — that lives in the parent page so we can refresh from
 * multiple call sites (after creating, after archiving) without prop
 * drilling.
 */

import { Archive, Plus } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { type ChatConversation, formatUsd } from "@/lib/chat";
import { cn } from "@/lib/utils";

export interface ConversationListProps {
  conversations: ChatConversation[];
  activeId: string | null;
  archived: boolean;
  onSelect: (id: string) => void;
  onCreate: () => void;
  onToggleArchived: (next: boolean) => void;
  onArchive: (id: string) => void;
  loading?: boolean;
}

export function ConversationList({
  conversations,
  activeId,
  archived,
  onSelect,
  onCreate,
  onToggleArchived,
  onArchive,
  loading = false,
}: ConversationListProps): React.JSX.Element {
  return (
    <aside
      className="flex h-full w-60 flex-col border-r border-[rgb(var(--border))] bg-[rgb(var(--background))]"
      data-testid="chat-conversation-list"
    >
      <div className="flex items-center justify-between border-b border-[rgb(var(--border))] px-3 py-2">
        <h2 className="text-xs font-semibold uppercase tracking-wide text-[rgb(var(--foreground-muted))]">
          Conversaciones
        </h2>
        <Button
          size="sm"
          variant="ghost"
          onClick={onCreate}
          aria-label="Nueva conversación"
          data-testid="chat-new-conversation"
        >
          <Plus className="h-4 w-4" />
        </Button>
      </div>
      <div className="flex items-center gap-2 border-b border-[rgb(var(--border))] px-3 py-2 text-xs">
        <label className="flex items-center gap-1.5 text-[rgb(var(--foreground-muted))]">
          <input
            type="checkbox"
            checked={archived}
            onChange={(e) => onToggleArchived(e.target.checked)}
            data-testid="chat-archived-toggle"
            className="h-3.5 w-3.5 cursor-pointer"
          />
          Ver archivadas
        </label>
      </div>
      <div className="flex-1 overflow-y-auto" role="list">
        {loading ? (
          <p className="px-3 py-4 text-xs text-[rgb(var(--foreground-muted))]">
            Cargando…
          </p>
        ) : conversations.length === 0 ? (
          <p className="px-3 py-4 text-xs text-[rgb(var(--foreground-muted))]">
            {archived
              ? "No hay conversaciones archivadas."
              : "Sin conversaciones todavía."}
          </p>
        ) : (
          conversations.map((conv) => (
            <ConversationRow
              key={conv.id}
              conv={conv}
              active={conv.id === activeId}
              onSelect={onSelect}
              onArchive={onArchive}
              archived={archived}
            />
          ))
        )}
      </div>
    </aside>
  );
}

interface ConversationRowProps {
  conv: ChatConversation;
  active: boolean;
  onSelect: (id: string) => void;
  onArchive: (id: string) => void;
  archived: boolean;
}

function ConversationRow({
  conv,
  active,
  onSelect,
  onArchive,
  archived,
}: ConversationRowProps): React.JSX.Element {
  const [hover, setHover] = useState(false);
  return (
    <div
      role="listitem"
      className={cn(
        "group relative flex cursor-pointer items-start justify-between gap-2 border-b border-[rgb(var(--border))] px-3 py-2 transition-colors",
        active
          ? "bg-[rgb(var(--background-elevated))]"
          : "hover:bg-[rgb(var(--background-elevated)/0.5)]",
      )}
      onClick={() => onSelect(conv.id)}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      data-testid={`chat-conversation-row-${conv.id}`}
      data-active={active ? "true" : "false"}
    >
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm font-medium text-[rgb(var(--foreground))]">
          {conv.title}
        </p>
        <p className="mt-0.5 text-[10px] text-[rgb(var(--foreground-muted))]">
          {conv.tokens_in_total.toLocaleString("es-ES")} tok ·{" "}
          {formatUsd(conv.usd_estimated_total)}
        </p>
      </div>
      {!archived && (hover || active) && (
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            onArchive(conv.id);
          }}
          aria-label={`Archivar conversación ${conv.title}`}
          data-testid={`chat-archive-${conv.id}`}
          className="rounded p-1 text-[rgb(var(--foreground-muted))] transition-colors hover:bg-[rgb(var(--background-elevated))] hover:text-[rgb(var(--foreground))]"
        >
          <Archive className="h-3.5 w-3.5" />
        </button>
      )}
    </div>
  );
}

export default ConversationList;
