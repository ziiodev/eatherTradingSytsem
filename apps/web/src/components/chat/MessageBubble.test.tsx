import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { MessageBubble } from "@/components/chat/MessageBubble";
import type { ChatStreamMessage } from "@/components/chat/useChatStream";

function makeMessage(
  overrides: Partial<ChatStreamMessage> = {},
): ChatStreamMessage {
  return {
    id: "m1",
    conversation_id: "c1",
    role: "assistant",
    content: "",
    tool_calls: [],
    tool_results: [],
    tokens_in: null,
    tokens_out: null,
    model: null,
    stop_reason: null,
    created_at: "2025-01-01T00:00:00Z",
    ...overrides,
  } as ChatStreamMessage;
}

describe("MessageBubble", () => {
  it("renders a user bubble with plain text", () => {
    render(
      <MessageBubble
        message={makeMessage({ role: "user", content: "Hola Claude" })}
      />,
    );
    expect(screen.getByTestId("chat-user-bubble")).toBeInTheDocument();
    expect(screen.getByText("Hola Claude")).toBeInTheDocument();
  });

  it("renders an assistant bubble with markdown", () => {
    render(
      <MessageBubble
        message={makeMessage({ role: "assistant", content: "## Resumen\n\n**Hola**" })}
      />,
    );
    expect(screen.getByTestId("chat-assistant-bubble")).toBeInTheDocument();
    expect(screen.getByText("Resumen")).toBeInTheDocument();
    expect(screen.getByText("Hola").tagName.toLowerCase()).toBe("strong");
  });

  it("sanitizes XSS attempts in markdown", () => {
    const xss = '<script>window.__pwn__ = true</script>\n\nHola';
    render(
      <MessageBubble
        message={makeMessage({ role: "assistant", content: xss })}
      />,
    );
    // The <script> tag must NOT have ended up in the DOM.
    expect(document.querySelector("script")).toBeNull();
    // The visible payload should be the safe text after sanitisation.
    const md = screen.getByTestId("chat-assistant-markdown");
    expect(md.innerHTML).not.toContain("<script");
    // The sanitized text "Hola" still renders.
    expect(screen.getByText(/Hola/)).toBeInTheDocument();
    expect(
      (window as unknown as { __pwn__?: unknown }).__pwn__,
    ).toBeUndefined();
  });

  it("renders tool call details as a collapsible", () => {
    render(
      <MessageBubble
        message={makeMessage({
          role: "assistant",
          content: "Listo.",
          tool_calls: [
            {
              id: "tu_1",
              name: "get_recent_trades",
              input: { since_hours: 4 },
            },
          ],
          tool_results: [
            {
              tool_use_id: "tu_1",
              content: [{ ticket: 1 }],
              is_error: false,
            },
          ],
        })}
      />,
    );
    const block = screen.getByTestId("chat-tool-call-get_recent_trades");
    expect(block.tagName.toLowerCase()).toBe("details");
    expect(block.hasAttribute("open")).toBe(false);
    // Toggle by clicking summary.
    const summary = block.querySelector("summary")!;
    fireEvent.click(summary);
    expect(block.hasAttribute("open")).toBe(true);
    // Input + result are visible after expanding.
    expect(screen.getByText(/since_hours/)).toBeInTheDocument();
    expect(screen.getByText(/ticket/)).toBeInTheDocument();
  });

  it("shows typing indicator while content is empty + pending", () => {
    render(
      <MessageBubble
        message={makeMessage({ role: "assistant", content: "", pending: true })}
      />,
    );
    expect(screen.getByTestId("chat-typing-indicator")).toBeInTheDocument();
  });

  it("shows interrupted flag", () => {
    render(
      <MessageBubble
        message={makeMessage({
          role: "assistant",
          content: "incomplete",
          interrupted: true,
        })}
      />,
    );
    expect(screen.getByTestId("chat-interrupted-flag")).toBeInTheDocument();
  });
});
