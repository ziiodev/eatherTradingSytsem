import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ConversationThread } from "@/components/chat/ConversationThread";
import type { ChatStreamMessage } from "@/components/chat/useChatStream";

function makeMessage(
  overrides: Partial<ChatStreamMessage> = {},
): ChatStreamMessage {
  return {
    id: "m1",
    conversation_id: "c1",
    role: "user",
    content: "Hola",
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

describe("ConversationThread", () => {
  it("renders the empty state when no messages and not streaming", () => {
    render(<ConversationThread messages={[]} streaming={false} />);
    expect(screen.getByTestId("chat-empty-state")).toBeInTheDocument();
    expect(
      screen.getByText(/Inicia una conversación con el asistente/),
    ).toBeInTheDocument();
  });

  it("renders user and assistant bubbles", () => {
    render(
      <ConversationThread
        messages={[
          makeMessage({ id: "u1", role: "user", content: "Hola" }),
          makeMessage({ id: "a1", role: "assistant", content: "Buenas" }),
        ]}
        streaming={false}
      />,
    );
    expect(screen.getByTestId("chat-user-bubble")).toBeInTheDocument();
    expect(screen.getByTestId("chat-assistant-bubble")).toBeInTheDocument();
    expect(screen.getByText("Hola")).toBeInTheDocument();
    expect(screen.getByText("Buenas")).toBeInTheDocument();
  });

  it("shows streaming indicator while assistant content is empty", () => {
    render(
      <ConversationThread
        messages={[
          makeMessage({ id: "u1", role: "user", content: "Hola" }),
          makeMessage({
            id: "a1",
            role: "assistant",
            content: "",
            pending: true,
          }),
        ]}
        streaming={true}
      />,
    );
    expect(screen.getByTestId("chat-streaming-indicator")).toBeInTheDocument();
  });

  it("hides streaming indicator once assistant content has tokens", () => {
    render(
      <ConversationThread
        messages={[
          makeMessage({ id: "u1", role: "user", content: "Hola" }),
          makeMessage({
            id: "a1",
            role: "assistant",
            content: "Buen",
            pending: true,
          }),
        ]}
        streaming={true}
      />,
    );
    expect(
      screen.queryByTestId("chat-streaming-indicator"),
    ).not.toBeInTheDocument();
  });
});
