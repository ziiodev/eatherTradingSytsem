"use client";

/**
 * ChatInput — textarea + submit + model selector + cost summary.
 *
 * Layout (GitHub Dark):
 *
 *   ┌──────────────────────────────────────────────┐
 *   │ Modelo: [Sonnet 4.5 ▾]  1,234 tok · $0.0123  │
 *   │ ┌──────────────────────────────────────────┐ │
 *   │ │ Pregunta...                              │ │
 *   │ └──────────────────────────────────────────┘ │
 *   │                                    [Enviar] │
 *   └──────────────────────────────────────────────┘
 *
 * Enter submits; Shift+Enter inserts a newline. The submit button is
 * disabled while ``streaming`` so the operator can't queue concurrent
 * turns; the model selector and cost line stay visible (they're useful
 * context even mid-stream).
 *
 * Changing the model fires ``onModelChange`` which the parent maps to a
 * PATCH on ``meta_data.model_override``.
 */

import { useCallback, useState } from "react";

import { Button } from "@/components/ui/button";
import { Select } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import {
  CHAT_MODEL_LABEL,
  CHAT_MODEL_WHITELIST,
  type ChatModel,
  formatUsd,
} from "@/lib/chat";

export interface ChatInputProps {
  model: ChatModel;
  tokensInTotal: number;
  usdEstimatedTotal: number | string;
  streaming: boolean;
  disabled?: boolean;
  onSubmit: (content: string) => void;
  onModelChange: (model: ChatModel) => void;
  onCancel?: () => void;
}

export function ChatInput({
  model,
  tokensInTotal,
  usdEstimatedTotal,
  streaming,
  disabled = false,
  onSubmit,
  onModelChange,
  onCancel,
}: ChatInputProps): React.JSX.Element {
  const [value, setValue] = useState("");

  const handleSubmit = useCallback(() => {
    const trimmed = value.trim();
    if (!trimmed || streaming || disabled) return;
    onSubmit(trimmed);
    setValue("");
  }, [value, streaming, disabled, onSubmit]);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        handleSubmit();
      }
    },
    [handleSubmit],
  );

  return (
    <div
      className="flex flex-col gap-2 border-t border-[rgb(var(--border))] bg-[rgb(var(--background))] p-3"
      data-testid="chat-input"
    >
      <div className="flex items-center justify-between gap-3 text-xs text-[rgb(var(--foreground-muted))]">
        <label className="flex items-center gap-2">
          <span>Modelo:</span>
          <Select
            value={model}
            onChange={(e) => onModelChange(e.target.value as ChatModel)}
            disabled={streaming}
            data-testid="chat-model-select"
            className="h-7 w-44 text-xs"
          >
            {CHAT_MODEL_WHITELIST.map((m) => (
              <option key={m} value={m}>
                {CHAT_MODEL_LABEL[m]}
              </option>
            ))}
          </Select>
        </label>
        <span data-testid="chat-cost-summary">
          {tokensInTotal.toLocaleString("es-ES")} tok ·{" "}
          {formatUsd(usdEstimatedTotal)}
        </span>
      </div>
      <Textarea
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={handleKeyDown}
        placeholder="Pregunta al asistente del proyecto…"
        disabled={streaming || disabled}
        rows={3}
        data-testid="chat-input-textarea"
      />
      <div className="flex items-center justify-end gap-2">
        {streaming && onCancel && (
          <Button
            variant="outline"
            size="sm"
            onClick={onCancel}
            data-testid="chat-cancel"
          >
            Detener
          </Button>
        )}
        <Button
          size="sm"
          onClick={handleSubmit}
          disabled={streaming || disabled || value.trim().length === 0}
          data-testid="chat-submit"
        >
          {streaming ? "Enviando…" : "Enviar"}
        </Button>
      </div>
    </div>
  );
}

export default ChatInput;
