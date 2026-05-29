import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useChatStream } from "@/components/chat/useChatStream";

// ---------------------------------------------------------------------------
// Fetch helpers
// ---------------------------------------------------------------------------

function streamFromFrames(frames: string[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  return new ReadableStream({
    start(controller) {
      for (const f of frames) controller.enqueue(encoder.encode(f));
      controller.close();
    },
  });
}

const PROJECT_ID = "11111111-1111-1111-1111-111111111111";
const CONVERSATION_ID = "22222222-2222-2222-2222-222222222222";

function mockGetConversation(): Response {
  return new Response(
    JSON.stringify({
      conversation: {
        id: CONVERSATION_ID,
        project_id: PROJECT_ID,
        title: "Una charla",
        tokens_in_total: 0,
        usd_estimated_total: "0.0",
        meta_data: {},
      },
      messages: [],
    }),
    { status: 200, headers: { "Content-Type": "application/json" } },
  );
}

describe("useChatStream", () => {
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

  it("loads the conversation history on mount", async () => {
    fetchMock.mockResolvedValueOnce(mockGetConversation());

    const { result } = renderHook(() =>
      useChatStream({ projectId: PROJECT_ID, conversationId: CONVERSATION_ID }),
    );

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(result.current.messages).toEqual([]);
    expect(result.current.streaming).toBe(false);
  });

  it("streams an assistant turn end-to-end (tokens + tool_use + turn_done)", async () => {
    fetchMock
      .mockResolvedValueOnce(mockGetConversation()) // refresh
      .mockResolvedValueOnce(
        new Response(
          streamFromFrames([
            'event: token\ndata: {"delta":"Ho"}\n\n',
            'event: token\ndata: {"delta":"la"}\n\n',
            'event: tool_use\ndata: {"tool_use_id":"tu_1","tool_name":"get_project_status","input":{}}\n\n',
            'event: tool_result\ndata: {"tool_use_id":"tu_1","output":{"status":"active"},"is_error":false}\n\n',
            'event: token\ndata: {"delta":"!"}\n\n',
            'event: turn_done\ndata: {"stop_reason":"end_turn","tokens_in":10,"tokens_out":5,"model":"claude-sonnet-4-5","usd_estimated":0.0001,"soft_warning":false}\n\n',
          ]),
          {
            status: 200,
            headers: { "Content-Type": "text/event-stream" },
          },
        ),
      );

    const { result } = renderHook(() =>
      useChatStream({ projectId: PROJECT_ID, conversationId: CONVERSATION_ID }),
    );

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));

    await act(async () => {
      await result.current.sendMessage("ping");
    });

    expect(result.current.streaming).toBe(false);
    // 2 messages: user + assistant. The assistant content stitches the
    // three token deltas back together.
    expect(result.current.messages).toHaveLength(2);
    const user = result.current.messages[0]!;
    const assistant = result.current.messages[1]!;
    expect(user.role).toBe("user");
    expect(user.content).toBe("ping");
    expect(assistant.role).toBe("assistant");
    expect(assistant.content).toBe("Hola!");
    expect(assistant.tool_calls).toHaveLength(1);
    expect(assistant.tool_results).toHaveLength(1);
    expect(assistant.stop_reason).toBe("end_turn");
    expect(assistant.model).toBe("claude-sonnet-4-5");
    expect(assistant.pending).toBe(false);
  });

  it("marks the assistant as interrupted on error event", async () => {
    fetchMock
      .mockResolvedValueOnce(mockGetConversation())
      .mockResolvedValueOnce(
        new Response(
          streamFromFrames([
            'event: token\ndata: {"delta":"Hola"}\n\n',
            'event: error\ndata: {"code":"STREAM_INTERRUPTED","message":"boom"}\n\n',
          ]),
          {
            status: 200,
            headers: { "Content-Type": "text/event-stream" },
          },
        ),
      );

    const { result } = renderHook(() =>
      useChatStream({ projectId: PROJECT_ID, conversationId: CONVERSATION_ID }),
    );

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));

    await act(async () => {
      await result.current.sendMessage("ping");
    });

    expect(result.current.error).toEqual({
      code: "STREAM_INTERRUPTED",
      message: "boom",
    });
    const assistant = result.current.messages[1]!;
    expect(assistant.interrupted).toBe(true);
    expect(assistant.stop_reason).toBe("aborted");
    expect(result.current.streaming).toBe(false);
  });

  it("cancelStream aborts the in-flight stream", async () => {
    let cancelled = false;
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        const enc = new TextEncoder();
        controller.enqueue(enc.encode('event: token\ndata: {"delta":"A"}\n\n'));
        // Stream stays open until cancel().
      },
      cancel() {
        cancelled = true;
      },
    });

    fetchMock
      .mockResolvedValueOnce(mockGetConversation())
      .mockResolvedValueOnce(
        new Response(stream, {
          status: 200,
          headers: { "Content-Type": "text/event-stream" },
        }),
      );

    const { result } = renderHook(() =>
      useChatStream({ projectId: PROJECT_ID, conversationId: CONVERSATION_ID }),
    );
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));

    // Fire-and-await sendMessage from inside a single act(). Because the
    // SSE stream never closes on its own, we schedule the cancel() via
    // a setTimeout so the abort fires while we're still awaiting the
    // stream.
    let sendDone = false;
    await act(async () => {
      const p = result.current.sendMessage("ping");
      // Schedule cancel after optimistic writes land (they happen
      // synchronously inside sendMessage before the first await).
      setTimeout(() => result.current.cancelStream(), 10);
      await p;
      sendDone = true;
    });
    expect(sendDone).toBe(true);

    expect(cancelled).toBe(true);
    expect(result.current.streaming).toBe(false);
    // Assistant row tagged as interrupted because turn_done never came.
    const assistant = result.current.messages.at(-1);
    expect(assistant?.role).toBe("assistant");
    expect(
      Boolean(assistant?.interrupted) ||
        assistant?.stop_reason === "disconnected",
    ).toBe(true);
  });
});
