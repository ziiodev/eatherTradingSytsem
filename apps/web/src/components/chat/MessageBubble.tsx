"use client";

/**
 * MessageBubble — renders one message in the chat thread.
 *
 * Per-role styling:
 *
 * * ``user``      → GitHub-blue accent, right-aligned, no markdown
 *                  rendering needed (operator input is plain text).
 * * ``assistant`` → neutral surface, left-aligned, markdown body via
 *                  ``MarkdownView`` (which already pipes ``remark-gfm`` +
 *                  ``rehype-sanitize`` — XSS payloads are stripped before
 *                  reaching the DOM).
 * * ``tool``      → muted slim row indicating a tool result was recorded;
 *                  the actual tool call shape is rendered as a
 *                  collapsible block UNDER the assistant message that
 *                  spawned it.
 *
 * Tool calls are rendered inside the assistant bubble using the native
 * ``<details>`` element. Native disclosure widgets are fully a11y +
 * keyboard-navigable for free (Enter / Space on the summary toggles
 * the open state) and don't require a Radix primitive.
 */

import { ChevronRight, AlertTriangle } from "lucide-react";

import { MarkdownView } from "@/components/MarkdownView";
import type { ChatStreamMessage } from "@/components/chat/useChatStream";
import { cn } from "@/lib/utils";

export interface MessageBubbleProps {
  message: ChatStreamMessage;
}

export function MessageBubble({ message }: MessageBubbleProps): React.JSX.Element {
  if (message.role === "user") {
    return <UserBubble message={message} />;
  }
  if (message.role === "assistant") {
    return <AssistantBubble message={message} />;
  }
  if (message.role === "tool") {
    // ``tool`` rows are stored for replay; the visible UX lives inside
    // the assistant bubble. We render a tiny stub so an operator who
    // expands "show all" still sees the order of events.
    return (
      <p
        className="px-2 py-1 text-[10px] uppercase tracking-wide text-[rgb(var(--foreground-muted))]"
        data-testid="chat-tool-stub"
      >
        herramientas ejecutadas
      </p>
    );
  }
  // system or unknown role — render nothing visible.
  return <></>;
}

function UserBubble({ message }: { message: ChatStreamMessage }): React.JSX.Element {
  return (
    <div className="flex justify-end" data-testid="chat-user-bubble">
      <div className="max-w-[80%] rounded-lg border border-[rgb(var(--accent)/0.4)] bg-[rgb(var(--accent)/0.15)] px-3 py-2 text-sm text-[rgb(var(--foreground))]">
        <pre className="whitespace-pre-wrap break-words font-sans">
          {message.content}
        </pre>
      </div>
    </div>
  );
}

function AssistantBubble({ message }: { message: ChatStreamMessage }): React.JSX.Element {
  const hasContent = message.content.length > 0;
  return (
    <div className="flex justify-start" data-testid="chat-assistant-bubble">
      <div
        className={cn(
          "max-w-[85%] rounded-lg border border-[rgb(var(--border))] bg-[rgb(var(--background-elevated))] px-3 py-2 text-sm",
          message.interrupted &&
            "border-[rgb(var(--danger)/0.5)] bg-[rgb(var(--danger)/0.08)]",
        )}
      >
        {message.tool_calls.length > 0 && (
          <div className="mb-2 space-y-1.5" data-testid="chat-tool-calls">
            {message.tool_calls.map((call, idx) => (
              <ToolCallBlock
                key={String(call.id ?? idx)}
                call={call}
                result={message.tool_results[idx]}
              />
            ))}
          </div>
        )}
        {hasContent && (
          <MarkdownView
            source={message.content}
            data-testid="chat-assistant-markdown"
          />
        )}
        {!hasContent && message.pending && (
          <span
            className="text-xs text-[rgb(var(--foreground-muted))]"
            data-testid="chat-typing-indicator"
          >
            …pensando
          </span>
        )}
        {message.interrupted && (
          <p
            className="mt-2 flex items-center gap-1 text-[10px] uppercase tracking-wide text-[rgb(var(--danger))]"
            data-testid="chat-interrupted-flag"
          >
            <AlertTriangle className="h-3 w-3" /> (interrumpido)
          </p>
        )}
      </div>
    </div>
  );
}

interface ToolCallBlockProps {
  call: Record<string, unknown>;
  result?: Record<string, unknown>;
}

function ToolCallBlock({ call, result }: ToolCallBlockProps): React.JSX.Element {
  const name = String(call.name ?? "tool");
  const input = call.input ?? {};
  const isError = Boolean(result?.is_error);
  return (
    <details
      className={cn(
        "group rounded border border-[rgb(var(--border))] bg-[rgb(var(--background))] text-xs",
        isError && "border-[rgb(var(--danger)/0.4)]",
      )}
      data-testid={`chat-tool-call-${name}`}
    >
      <summary className="flex cursor-pointer select-none items-center gap-1 px-2 py-1.5 text-[rgb(var(--foreground-muted))] hover:bg-[rgb(var(--background-elevated))]">
        <ChevronRight className="h-3 w-3 transition-transform group-open:rotate-90" />
        <span className="font-mono">
          Llamada a herramienta: <span className="text-[rgb(var(--foreground))]">{name}</span>
          {isError && (
            <span className="ml-2 text-[rgb(var(--danger))]">[error]</span>
          )}
        </span>
      </summary>
      <div className="space-y-2 border-t border-[rgb(var(--border))] px-2 py-2 font-mono text-[11px]">
        <div>
          <p className="mb-1 text-[10px] uppercase tracking-wide text-[rgb(var(--foreground-muted))]">
            Input
          </p>
          <pre className="overflow-x-auto whitespace-pre-wrap break-words text-[rgb(var(--foreground))]">
            {JSON.stringify(input, null, 2)}
          </pre>
        </div>
        {result !== undefined && (
          <div>
            <p className="mb-1 text-[10px] uppercase tracking-wide text-[rgb(var(--foreground-muted))]">
              Resultado
            </p>
            <pre className="overflow-x-auto whitespace-pre-wrap break-words text-[rgb(var(--foreground))]">
              {JSON.stringify(result.content ?? result, null, 2)}
            </pre>
          </div>
        )}
      </div>
    </details>
  );
}

export default MessageBubble;
