import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ConversationList } from "@/components/chat/ConversationList";
import type { ChatConversation } from "@/lib/chat";

function makeConv(overrides: Partial<ChatConversation> = {}): ChatConversation {
  return {
    id: "11111111-1111-1111-1111-111111111111",
    project_id: "22222222-2222-2222-2222-222222222222",
    title: "Charla 1",
    created_at: "2025-01-01T00:00:00Z",
    updated_at: "2025-01-01T00:00:00Z",
    archived_at: null,
    tokens_in_total: 1234,
    usd_estimated_total: 0.0123,
    meta_data: {},
    ...overrides,
  };
}

describe("ConversationList", () => {
  it("renders an empty state", () => {
    render(
      <ConversationList
        conversations={[]}
        activeId={null}
        archived={false}
        onSelect={() => undefined}
        onCreate={() => undefined}
        onToggleArchived={() => undefined}
        onArchive={() => undefined}
      />,
    );
    expect(screen.getByText(/Sin conversaciones todavía/)).toBeInTheDocument();
  });

  it("renders an archived empty state", () => {
    render(
      <ConversationList
        conversations={[]}
        activeId={null}
        archived={true}
        onSelect={() => undefined}
        onCreate={() => undefined}
        onToggleArchived={() => undefined}
        onArchive={() => undefined}
      />,
    );
    expect(
      screen.getByText(/No hay conversaciones archivadas/),
    ).toBeInTheDocument();
  });

  it("renders rows and calls onSelect on click", () => {
    const conv1 = makeConv({ id: "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", title: "Hola" });
    const conv2 = makeConv({ id: "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb", title: "Mundo" });
    const onSelect = vi.fn();
    render(
      <ConversationList
        conversations={[conv1, conv2]}
        activeId={conv1.id}
        archived={false}
        onSelect={onSelect}
        onCreate={() => undefined}
        onToggleArchived={() => undefined}
        onArchive={() => undefined}
      />,
    );
    expect(screen.getByText("Hola")).toBeInTheDocument();
    expect(screen.getByText("Mundo")).toBeInTheDocument();
    fireEvent.click(screen.getByText("Mundo"));
    expect(onSelect).toHaveBeenCalledWith(conv2.id);
  });

  it("calls onCreate when the new-conversation button is clicked", () => {
    const onCreate = vi.fn();
    render(
      <ConversationList
        conversations={[]}
        activeId={null}
        archived={false}
        onSelect={() => undefined}
        onCreate={onCreate}
        onToggleArchived={() => undefined}
        onArchive={() => undefined}
      />,
    );
    fireEvent.click(screen.getByTestId("chat-new-conversation"));
    expect(onCreate).toHaveBeenCalledTimes(1);
  });

  it("calls onArchive when the archive button on an active row is clicked", () => {
    const conv = makeConv();
    const onArchive = vi.fn();
    render(
      <ConversationList
        conversations={[conv]}
        activeId={conv.id}
        archived={false}
        onSelect={() => undefined}
        onCreate={() => undefined}
        onToggleArchived={() => undefined}
        onArchive={onArchive}
      />,
    );
    // Active row always shows the archive button (no hover needed).
    fireEvent.click(screen.getByTestId(`chat-archive-${conv.id}`));
    expect(onArchive).toHaveBeenCalledWith(conv.id);
  });

  it("toggles archived view", () => {
    const onToggle = vi.fn();
    render(
      <ConversationList
        conversations={[]}
        activeId={null}
        archived={false}
        onSelect={() => undefined}
        onCreate={() => undefined}
        onToggleArchived={onToggle}
        onArchive={() => undefined}
      />,
    );
    fireEvent.click(screen.getByTestId("chat-archived-toggle"));
    expect(onToggle).toHaveBeenCalledWith(true);
  });
});
