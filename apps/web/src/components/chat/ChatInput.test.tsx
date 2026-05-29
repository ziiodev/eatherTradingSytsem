import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ChatInput } from "@/components/chat/ChatInput";

describe("ChatInput", () => {
  const defaultProps = {
    model: "claude-sonnet-4-5" as const,
    tokensInTotal: 1234,
    usdEstimatedTotal: 0.0123,
    streaming: false,
    onSubmit: () => undefined,
    onModelChange: () => undefined,
  };

  it("renders model selector with whitelist entries", () => {
    render(<ChatInput {...defaultProps} />);
    const select = screen.getByTestId("chat-model-select") as HTMLSelectElement;
    expect(select.value).toBe("claude-sonnet-4-5");
    expect(select.options).toHaveLength(2);
  });

  it("renders cost summary", () => {
    render(<ChatInput {...defaultProps} />);
    const summary = screen.getByTestId("chat-cost-summary");
    expect(summary.textContent).toMatch(/1.?234 tok/);
    expect(summary.textContent).toMatch(/\$0\.0123/);
  });

  it("calls onSubmit with trimmed content on Enter", () => {
    const onSubmit = vi.fn();
    render(<ChatInput {...defaultProps} onSubmit={onSubmit} />);
    const textarea = screen.getByTestId(
      "chat-input-textarea",
    ) as HTMLTextAreaElement;
    fireEvent.change(textarea, { target: { value: "  hola  " } });
    fireEvent.keyDown(textarea, { key: "Enter", shiftKey: false });
    expect(onSubmit).toHaveBeenCalledWith("hola");
    // Clears after submit.
    expect(textarea.value).toBe("");
  });

  it("Shift+Enter does not submit", () => {
    const onSubmit = vi.fn();
    render(<ChatInput {...defaultProps} onSubmit={onSubmit} />);
    const textarea = screen.getByTestId(
      "chat-input-textarea",
    ) as HTMLTextAreaElement;
    fireEvent.change(textarea, { target: { value: "linea uno" } });
    fireEvent.keyDown(textarea, { key: "Enter", shiftKey: true });
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("calls onSubmit when the submit button is clicked", () => {
    const onSubmit = vi.fn();
    render(<ChatInput {...defaultProps} onSubmit={onSubmit} />);
    const textarea = screen.getByTestId(
      "chat-input-textarea",
    ) as HTMLTextAreaElement;
    fireEvent.change(textarea, { target: { value: "click submit" } });
    fireEvent.click(screen.getByTestId("chat-submit"));
    expect(onSubmit).toHaveBeenCalledWith("click submit");
  });

  it("disables submit + textarea while streaming", () => {
    render(<ChatInput {...defaultProps} streaming={true} />);
    const submit = screen.getByTestId("chat-submit") as HTMLButtonElement;
    expect(submit.disabled).toBe(true);
    const textarea = screen.getByTestId(
      "chat-input-textarea",
    ) as HTMLTextAreaElement;
    expect(textarea.disabled).toBe(true);
  });

  it("dispatches model changes through onModelChange", () => {
    const onModelChange = vi.fn();
    render(<ChatInput {...defaultProps} onModelChange={onModelChange} />);
    const select = screen.getByTestId("chat-model-select") as HTMLSelectElement;
    fireEvent.change(select, { target: { value: "claude-haiku-4-5" } });
    expect(onModelChange).toHaveBeenCalledWith("claude-haiku-4-5");
  });

  it("shows a Cancel button when streaming + onCancel provided", () => {
    const onCancel = vi.fn();
    render(
      <ChatInput {...defaultProps} streaming={true} onCancel={onCancel} />,
    );
    fireEvent.click(screen.getByTestId("chat-cancel"));
    expect(onCancel).toHaveBeenCalledTimes(1);
  });
});
