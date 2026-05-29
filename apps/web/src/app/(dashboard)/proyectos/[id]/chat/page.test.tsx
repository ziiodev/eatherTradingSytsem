/**
 * Chat placeholder test — sibling change `project-chat` will fill this in.
 * Until then, lock the placeholder card and Spanish copy.
 */

import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import ChatPage from "./page";

describe("ChatPage (placeholder)", () => {
  it("renders the próximamente card with Spanish copy", () => {
    render(<ChatPage />);
    expect(screen.getByTestId("chat-placeholder")).toBeInTheDocument();
    expect(screen.getByText("Chat")).toBeInTheDocument();
    expect(
      screen.getByText(/próximamente/i),
    ).toBeInTheDocument();
  });
});
