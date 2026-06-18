import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";

import {
  CHAT_MODEL_WHITELIST,
  ChatPostError,
  conversationSchema,
  errorEventSchema,
  messageSchema,
  parseSseFrame,
  postMessage,
  sseEventSchema,
  toolUseEventSchema,
  tokenEventSchema,
  turnDoneEventSchema,
} from "@/lib/chat";

describe("chat schemas", () => {
  it("parses a minimal conversation", () => {
    const result = conversationSchema.safeParse({
      id: "11111111-1111-1111-1111-111111111111",
      pair_id: "22222222-2222-2222-2222-222222222222",
      title: "Una charla",
      created_at: "2025-01-01T00:00:00Z",
      tokens_in_total: 0,
      usd_estimated_total: "0.0",
      meta_data: {},
    });
    expect(result.success).toBe(true);
    if (result.success) {
      expect(result.data.usd_estimated_total).toBe(0);
    }
  });

  it("parses a message with tool_calls", () => {
    const result = messageSchema.safeParse({
      id: "11111111-1111-1111-1111-111111111111",
      conversation_id: "22222222-2222-2222-2222-222222222222",
      role: "assistant",
      content: "Hola",
      tool_calls: [{ id: "tu_1", name: "get_recent_trades", input: {} }],
      stop_reason: "end_turn",
    });
    expect(result.success).toBe(true);
  });

  it("rejects an unknown role", () => {
    const result = messageSchema.safeParse({
      id: "11111111-1111-1111-1111-111111111111",
      conversation_id: "22222222-2222-2222-2222-222222222222",
      role: "narrator",
      content: "x",
    });
    expect(result.success).toBe(false);
  });

  it("locks the model whitelist", () => {
    expect(CHAT_MODEL_WHITELIST).toContain("claude-sonnet-4-5");
    expect(CHAT_MODEL_WHITELIST).toContain("claude-haiku-4-5");
  });

  it("discriminated union schemas parse their own events", () => {
    expect(
      sseEventSchema.safeParse({ type: "token", delta: "hola" }).success,
    ).toBe(true);
    expect(
      sseEventSchema.safeParse({
        type: "tool_use",
        tool_use_id: "tu_1",
        tool_name: "x",
        input: {},
      }).success,
    ).toBe(true);
    expect(
      sseEventSchema.safeParse({
        type: "turn_done",
        stop_reason: "end_turn",
        tokens_in: 1,
        tokens_out: 2,
        model: "claude-sonnet-4-5",
        usd_estimated: 0.0001,
      }).success,
    ).toBe(true);
    // Individual schemas line up.
    expect(tokenEventSchema.safeParse({ type: "token", delta: "x" }).success).toBe(true);
    expect(
      toolUseEventSchema.safeParse({
        type: "tool_use",
        tool_use_id: "tu_1",
        tool_name: "n",
      }).success,
    ).toBe(true);
    expect(
      turnDoneEventSchema.safeParse({
        type: "turn_done",
        model: "claude-haiku-4-5",
      }).success,
    ).toBe(true);
    expect(
      errorEventSchema.safeParse({
        type: "error",
        code: "X",
        message: "boom",
      }).success,
    ).toBe(true);
  });
});

describe("parseSseFrame", () => {
  it("parses a token event", () => {
    const ev = parseSseFrame('event: token\ndata: {"delta": "hola"}');
    expect(ev?.type).toBe("token");
    if (ev?.type === "token") {
      expect(ev.delta).toBe("hola");
    }
  });

  it("ignores comment lines and ping lines", () => {
    const ev = parseSseFrame(
      ': keepalive\nevent: token\ndata: {"delta": "x"}',
    );
    expect(ev?.type).toBe("token");
  });

  it("returns null for an empty frame", () => {
    expect(parseSseFrame("")).toBeNull();
    expect(parseSseFrame("\n\n")).toBeNull();
  });

  it("returns null when data is not JSON", () => {
    expect(parseSseFrame("event: token\ndata: nope")).toBeNull();
  });

  it("returns null when event lacks data", () => {
    expect(parseSseFrame("event: token")).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// postMessage — SSE consumption + abort
// ---------------------------------------------------------------------------

function buildSseStream(frames: string[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  return new ReadableStream({
    start(controller) {
      for (const frame of frames) {
        controller.enqueue(encoder.encode(frame));
      }
      controller.close();
    },
  });
}

function buildSplitSseStream(chunks: string[]): ReadableStream<Uint8Array> {
  // Lets us test partial-frame buffering across chunk boundaries.
  const encoder = new TextEncoder();
  return new ReadableStream({
    start(controller) {
      for (const c of chunks) controller.enqueue(encoder.encode(c));
      controller.close();
    },
  });
}

describe("postMessage SSE consumer", () => {
  const fetchMock = vi.fn();

  beforeEach(() => {
    fetchMock.mockReset();
    vi.stubGlobal("fetch", fetchMock);
    // CSRF cookie absent — withCsrfHeader is a no-op when missing.
    Object.defineProperty(document, "cookie", {
      configurable: true,
      get: () => "",
      set: () => undefined,
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("invokes onEvent for each frame in order", async () => {
    const stream = buildSseStream([
      'event: token\ndata: {"delta":"Ho"}\n\n',
      'event: token\ndata: {"delta":"la"}\n\n',
      'event: tool_use\ndata: {"tool_use_id":"tu_1","tool_name":"get_project_status","input":{}}\n\n',
      'event: tool_result\ndata: {"tool_use_id":"tu_1","output":{"status":"active"},"is_error":false}\n\n',
      'event: turn_done\ndata: {"stop_reason":"end_turn","tokens_in":10,"tokens_out":5,"model":"claude-sonnet-4-5","usd_estimated":0.0001,"soft_warning":false}\n\n',
    ]);
    fetchMock.mockResolvedValueOnce(
      new Response(stream, {
        status: 200,
        headers: { "Content-Type": "text/event-stream" },
      }),
    );

    const events: string[] = [];
    await postMessage("p1", "c1", "ping", {
      onEvent: (ev) => events.push(ev.type),
    });

    expect(events).toEqual([
      "token",
      "token",
      "tool_use",
      "tool_result",
      "turn_done",
    ]);
  });

  it("buffers partial frames across chunk boundaries", async () => {
    // Chunk 1 = first frame + half of second.
    // Chunk 2 = rest of second frame + complete third frame.
    const stream = buildSplitSseStream([
      'event: token\ndata: {"delta":"X"}\n\nevent: tok',
      'en\ndata: {"delta":"Y"}\n\nevent: turn_done\ndata: {"model":"claude-sonnet-4-5"}\n\n',
    ]);
    fetchMock.mockResolvedValueOnce(
      new Response(stream, {
        status: 200,
        headers: { "Content-Type": "text/event-stream" },
      }),
    );

    const events: { type: string; delta?: string }[] = [];
    await postMessage("p1", "c1", "ping", {
      onEvent: (ev) => {
        if (ev.type === "token") events.push({ type: ev.type, delta: ev.delta });
        else events.push({ type: ev.type });
      },
    });

    expect(events).toEqual([
      { type: "token", delta: "X" },
      { type: "token", delta: "Y" },
      { type: "turn_done" },
    ]);
  });

  it("raises ChatPostError on HTTP error", async () => {
    fetchMock.mockResolvedValueOnce(
      new Response(
        JSON.stringify({ detail: { code: "CHAT_BUDGET_EXCEEDED" } }),
        {
          status: 409,
          headers: { "Content-Type": "application/json" },
        },
      ),
    );
    let caught: unknown = null;
    try {
      await postMessage("p1", "c1", "ping", { onEvent: () => undefined });
    } catch (e) {
      caught = e;
    }
    expect(caught).toBeInstanceOf(ChatPostError);
    if (caught instanceof ChatPostError) {
      expect(caught.status).toBe(409);
    }
  });

  it("honours AbortController", async () => {
    // The stream is never-ending; abort interrupts the read loop.
    let cancelled = false;
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        const encoder = new TextEncoder();
        controller.enqueue(encoder.encode('event: token\ndata: {"delta":"A"}\n\n'));
        // Never close — we rely on cancel().
      },
      cancel() {
        cancelled = true;
      },
    });

    fetchMock.mockResolvedValueOnce(
      new Response(stream, {
        status: 200,
        headers: { "Content-Type": "text/event-stream" },
      }),
    );

    const controller = new AbortController();
    const events: string[] = [];

    const promise = postMessage("p1", "c1", "ping", {
      onEvent: (ev) => {
        events.push(ev.type);
        // After the first event, ask the consumer to stop. Without
        // abort the stream would keep the test hanging.
        controller.abort();
      },
      signal: controller.signal,
    });

    await promise;
    expect(events).toContain("token");
    expect(cancelled).toBe(true);
  });
});
