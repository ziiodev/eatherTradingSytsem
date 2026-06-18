/**
 * Chat page E2E smoke test — mounts the real page with a mocked fetch
 * so we exercise the SSE consumer path end-to-end (health → list
 * conversations → load conversation → POST + stream tokens + tool +
 * turn_done). Also locks the disabled-state UI when
 * ``features.chat_enabled = false``.
 */

import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ChatPageInner } from "./page";

const PROJECT_ID = "11111111-1111-1111-1111-111111111111";
const CONVERSATION_ID = "22222222-2222-2222-2222-222222222222";

function streamFromFrames(frames: string[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  return new ReadableStream({
    start(controller) {
      for (const f of frames) controller.enqueue(encoder.encode(f));
      controller.close();
    },
  });
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("ChatPage", () => {
  const fetchMock = vi.fn();

  beforeEach(() => {
    fetchMock.mockReset();
    vi.stubGlobal("fetch", fetchMock);
    Object.defineProperty(document, "cookie", {
      configurable: true,
      get: () => "",
      set: () => undefined,
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("renders the disabled card when features.chat_enabled is false", async () => {
    fetchMock.mockImplementation((url: string) => {
      if (url === "/api/health") {
        return Promise.resolve(
          jsonResponse({ ok: true, features: { chat_enabled: false } }),
        );
      }
      return Promise.reject(new Error(`unexpected fetch ${url}`));
    });

    render(<ChatPageInner pairId={PROJECT_ID} />);

    await waitFor(() =>
      expect(screen.getByTestId("chat-disabled-card")).toBeInTheDocument(),
    );
    expect(
      screen.getByText(/Configura ANTHROPIC_API_KEY o habilita/),
    ).toBeInTheDocument();
    // The chat surface must NOT mount.
    expect(screen.queryByTestId("chat-surface")).not.toBeInTheDocument();
  });

  it("renders the surface and consumes an SSE stream end-to-end", async () => {
    fetchMock.mockImplementation((url: string, init: RequestInit = {}) => {
      const method = (init.method ?? "GET").toUpperCase();
      if (url === "/api/health") {
        return Promise.resolve(
          jsonResponse({ ok: true, features: { chat_enabled: true } }),
        );
      }
      if (
        url.startsWith(`/api/pairs/${PROJECT_ID}/chat/conversations`) &&
        method === "GET" &&
        !url.includes(`/${CONVERSATION_ID}`)
      ) {
        return Promise.resolve(
          jsonResponse({
            items: [
              {
                id: CONVERSATION_ID,
                pair_id: PROJECT_ID,
                title: "Una charla",
                created_at: "2025-01-01T00:00:00Z",
                updated_at: "2025-01-01T00:00:00Z",
                archived_at: null,
                tokens_in_total: 0,
                usd_estimated_total: "0.0",
                meta_data: {},
              },
            ],
            total: 1,
          }),
        );
      }
      if (
        url ===
          `/api/pairs/${PROJECT_ID}/chat/conversations/${CONVERSATION_ID}` &&
        method === "GET"
      ) {
        return Promise.resolve(
          jsonResponse({
            conversation: {
              id: CONVERSATION_ID,
              pair_id: PROJECT_ID,
              title: "Una charla",
              tokens_in_total: 0,
              usd_estimated_total: "0.0",
              meta_data: {},
            },
            messages: [],
          }),
        );
      }
      if (
        url ===
          `/api/pairs/${PROJECT_ID}/chat/conversations/${CONVERSATION_ID}/messages` &&
        method === "POST"
      ) {
        const stream = streamFromFrames([
          'event: token\ndata: {"delta":"Ho"}\n\n',
          'event: token\ndata: {"delta":"la"}\n\n',
          'event: tool_use\ndata: {"tool_use_id":"tu_1","tool_name":"get_project_status","input":{}}\n\n',
          'event: tool_result\ndata: {"tool_use_id":"tu_1","output":{"status":"active"},"is_error":false}\n\n',
          'event: token\ndata: {"delta":"!"}\n\n',
          'event: turn_done\ndata: {"stop_reason":"end_turn","tokens_in":12,"tokens_out":7,"model":"claude-sonnet-4-5","usd_estimated":0.0123,"soft_warning":false}\n\n',
        ]);
        return Promise.resolve(
          new Response(stream, {
            status: 200,
            headers: { "Content-Type": "text/event-stream" },
          }),
        );
      }
      return Promise.reject(new Error(`unexpected fetch ${method} ${url}`));
    });

    render(<ChatPageInner pairId={PROJECT_ID} />);

    // Surface mounts after health resolves + conversations load.
    await waitFor(() =>
      expect(screen.getByTestId("chat-surface")).toBeInTheDocument(),
    );
    await waitFor(() =>
      expect(screen.getByText("Una charla")).toBeInTheDocument(),
    );

    // Submit a turn — type + click submit.
    const textarea = screen.getByTestId(
      "chat-input-textarea",
    ) as HTMLTextAreaElement;
    fireEvent.change(textarea, { target: { value: "ping" } });
    await act(async () => {
      fireEvent.click(screen.getByTestId("chat-submit"));
    });

    // Assistant bubble must materialise with the stitched tokens.
    await waitFor(() => {
      const assistant = screen.queryByTestId("chat-assistant-bubble");
      expect(assistant).toBeInTheDocument();
    });
    await waitFor(() => {
      const md = screen.queryByTestId("chat-assistant-markdown");
      expect(md?.textContent).toMatch(/Hola!/);
    });
    // Tool call collapsible is rendered.
    await waitFor(() => {
      expect(
        screen.getByTestId("chat-tool-call-get_project_status"),
      ).toBeInTheDocument();
    });
  });
});
