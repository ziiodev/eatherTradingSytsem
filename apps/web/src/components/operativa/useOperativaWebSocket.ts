"use client";

/**
 * useOperativaWebSocket — single hook that drives the realtime Operativa
 * surface for one project.
 *
 * Design (per `sdd/project-operativa/spec/operativa-live` + design #2125):
 *
 * - WebSocket-first: opens `/api/projects/{id}/operativa/ws` (same origin,
 *   cookie-auth) and consumes the `LiveBus` event stream (`account_snapshot`,
 *   `position_snapshot`, `order_event`, `mcp_status`, `ping`).
 * - REST polling fallback: a 10s polling timer runs unconditionally while
 *   the WS is NOT in `live` state (initial connect, reconnect, terminal
 *   error). It calls `fetchAccountSummary` so the UI never goes silent.
 * - Initial REST fetch always runs on mount, independent of the WS.
 * - Exponential backoff on reconnect: 1s, 2s, 4s, 8s, …, capped at 30s.
 * - `1008` (policy violation) close code → cross-tenant/origin/auth failure
 *   — we MUST NOT reconnect (the bug is permanent). Hook sets
 *   `transportState='error'` and the REST polling stays active so the user
 *   still sees stale-but-fresh data.
 * - All transport state transitions surface through `transportState` so the
 *   page can render a "Live | Reconectando | Sólo REST | Sin conexión"
 *   chip without each card re-deriving it.
 *
 * The hook is intentionally stateless w.r.t. ordering — components consume
 * the latest snapshot of `accountSummary` and `positions`. `recentOrderEvents`
 * is a bounded ring buffer (last 50) so the page can render a "novedades"
 * sub-section without unbounded growth.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  fetchAccountSummary,
  fetchOrders,
  wsEventSchema,
  type AccountSummary,
  type OperativaOrderRecord,
  type Position,
  type WsEvent,
} from "@/lib/operativa";

/**
 * Transport state machine surfaced to consumers.
 *
 * - `connecting` — first WS open in progress, no event received yet.
 * - `live` — WS is open and at least one event has arrived.
 * - `reconnecting` — last WS closed with a transient code; backoff timer
 *   is running and REST polling is filling the gap.
 * - `rest` — feature flag disabled OR `enabled=false`, so we only do REST
 *   polling. (Not currently exercised but reserved.)
 * - `error` — permanent failure (1008 close). No reconnect attempts; REST
 *   polling keeps running so the surface stays usable.
 */
export type TransportState =
  | "connecting"
  | "live"
  | "reconnecting"
  | "rest"
  | "error";

export interface UseOperativaWebSocketOptions {
  /**
   * When false, the hook does NOT open a WS and does NOT poll. Useful for
   * pre-render / SSR boundaries that mount the component before the
   * project id is known.
   */
  enabled?: boolean;
  /**
   * Override for tests. Production callers should leave this undefined.
   */
  webSocketFactory?: (url: string) => WebSocket;
  /**
   * REST polling cadence in ms (default 10_000). Tests override to a small
   * number to keep them fast.
   */
  restPollMs?: number;
  /**
   * Maximum capacity of the recentOrderEvents ring buffer (default 50).
   */
  maxRecentOrderEvents?: number;
}

export interface UseOperativaWebSocketResult {
  accountSummary: AccountSummary | null;
  positions: Position[];
  recentOrderEvents: Array<{ event: string; order: OperativaOrderRecord }>;
  mcpStatus: "available" | "unavailable" | null;
  transportState: TransportState;
}

const INITIAL_BACKOFF_MS = 1_000;
const MAX_BACKOFF_MS = 30_000;
const WS_CLOSE_POLICY_VIOLATION = 1008;

function buildWsUrl(projectId: string): string {
  if (typeof window === "undefined") {
    return `/api/projects/${projectId}/operativa/ws`;
  }
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${window.location.host}/api/projects/${projectId}/operativa/ws`;
}

export function useOperativaWebSocket(
  projectId: string,
  options: UseOperativaWebSocketOptions = {},
): UseOperativaWebSocketResult {
  const {
    enabled = true,
    webSocketFactory,
    restPollMs = 10_000,
    maxRecentOrderEvents = 50,
  } = options;

  const [accountSummary, setAccountSummary] =
    useState<AccountSummary | null>(null);
  const [positions, setPositions] = useState<Position[]>([]);
  const [recentOrderEvents, setRecentOrderEvents] = useState<
    Array<{ event: string; order: OperativaOrderRecord }>
  >([]);
  const [mcpStatus, setMcpStatus] = useState<
    "available" | "unavailable" | null
  >(null);
  const [transportState, setTransportState] =
    useState<TransportState>("connecting");

  // Refs hold mutable, non-rendering state for the lifetime of the hook.
  const wsRef = useRef<WebSocket | null>(null);
  const backoffRef = useRef<number>(INITIAL_BACKOFF_MS);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const restTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const unmountedRef = useRef<boolean>(false);
  // Stable accessor for `transportState` inside async callbacks that
  // would otherwise close over a stale value. Kept in sync via an
  // effect to avoid mutating refs during render.
  const transportStateRef = useRef<TransportState>("connecting");
  useEffect(() => {
    transportStateRef.current = transportState;
  }, [transportState]);

  const handleEvent = useCallback(
    (raw: unknown) => {
      const parsed = wsEventSchema.safeParse(raw);
      if (!parsed.success) return;
      const evt: WsEvent = parsed.data;
      switch (evt.type) {
        case "account_snapshot":
          setAccountSummary(evt.data);
          setMcpStatus(evt.data.mcp_status);
          break;
        case "position_snapshot":
          setPositions(evt.data.positions);
          break;
        case "order_event":
          setRecentOrderEvents((prev) => {
            const next = [
              { event: evt.data.event, order: evt.data.order },
              ...prev,
            ];
            return next.slice(0, maxRecentOrderEvents);
          });
          break;
        case "mcp_status":
          setMcpStatus(evt.data.status);
          break;
        case "ping":
          // Heartbeat only — keep the connection lively, no state change.
          break;
      }
      // Any successfully-parsed event proves we're live.
      if (transportStateRef.current !== "live") {
        transportStateRef.current = "live";
        setTransportState("live");
      }
    },
    [maxRecentOrderEvents],
  );

  // ----- REST poll loop -----------------------------------------------
  const restPoll = useCallback(async () => {
    if (unmountedRef.current) return;
    try {
      const summary = await fetchAccountSummary(projectId);
      if (unmountedRef.current) return;
      // The WS is the source of truth once we're live — never let a
      // REST response that resolved during the gap overwrite a fresher
      // WS-derived snapshot.
      if (transportStateRef.current === "live") return;
      setAccountSummary(summary);
      setMcpStatus(summary.mcp_status);
    } catch {
      // Swallow network errors — the next tick may succeed. UI surfaces
      // the gap via stale `source_at`.
    }
  }, [projectId]);

  // The two functions form a recursive pair: openWebSocket triggers
  // scheduleReconnect on close, scheduleReconnect calls openWebSocket
  // after the backoff. Resolve the forward reference by stashing the
  // reconnect callback in a ref so each definition stays well-formed.
  const scheduleReconnectRef = useRef<() => void>(() => {});

  // ----- WS lifecycle --------------------------------------------------
  const openWebSocket = useCallback(() => {
    if (unmountedRef.current) return;

    const url = buildWsUrl(projectId);
    let ws: WebSocket;
    try {
      ws = webSocketFactory ? webSocketFactory(url) : new WebSocket(url);
    } catch {
      // Couldn't even construct — treat as transient and schedule a retry.
      scheduleReconnectRef.current();
      return;
    }
    wsRef.current = ws;

    ws.onopen = () => {
      if (unmountedRef.current) return;
      // We don't flip to `live` yet — that happens when the first event
      // arrives. Until then we're "connecting".
      setTransportState("connecting");
    };

    ws.onmessage = (event: MessageEvent) => {
      if (unmountedRef.current) return;
      let payload: unknown;
      try {
        payload =
          typeof event.data === "string"
            ? JSON.parse(event.data)
            : event.data;
      } catch {
        return;
      }
      handleEvent(payload);
    };

    ws.onerror = () => {
      // Browsers don't expose the underlying reason; `onclose` follows.
    };

    ws.onclose = (event: CloseEvent) => {
      if (unmountedRef.current) return;
      wsRef.current = null;
      if (event.code === WS_CLOSE_POLICY_VIOLATION) {
        // Permanent failure: cross-tenant / origin / auth. Don't retry.
        transportStateRef.current = "error";
        setTransportState("error");
        return;
      }
      scheduleReconnectRef.current();
    };
  }, [projectId, webSocketFactory, handleEvent]);

  const scheduleReconnect = useCallback(() => {
    if (unmountedRef.current) return;
    transportStateRef.current = "reconnecting";
    setTransportState("reconnecting");
    const delay = backoffRef.current;
    backoffRef.current = Math.min(backoffRef.current * 2, MAX_BACKOFF_MS);
    reconnectTimerRef.current = setTimeout(() => {
      reconnectTimerRef.current = null;
      if (unmountedRef.current) return;
      openWebSocket();
    }, delay);
  }, [openWebSocket]);

  // Keep the ref in sync so `openWebSocket` can call the latest version
  // without taking a cyclic dependency.
  useEffect(() => {
    scheduleReconnectRef.current = scheduleReconnect;
  }, [scheduleReconnect]);

  // Reset backoff when we go live.
  useEffect(() => {
    if (transportState === "live") {
      backoffRef.current = INITIAL_BACKOFF_MS;
    }
  }, [transportState]);

  // Mount: kick off initial REST fetch + WS connect + REST polling.
  useEffect(() => {
    unmountedRef.current = false;
    if (!enabled) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setTransportState("rest");
      return undefined;
    }

    setTransportState("connecting");
    // Initial REST fetches — independent of WS so the UI has data
    // before the first WS event arrives.
    void restPoll();
    void fetchOrders(projectId, { limit: 1 }).catch(() => {
      /* swallowed — the history table fetches its own slice anyway */
    });

    openWebSocket();
    restTimerRef.current = setInterval(() => {
      // Run REST polling unconditionally during non-live states so the
      // UI never sits with stale data while we reconnect.
      if (transportStateRef.current !== "live") {
        void restPoll();
      }
    }, restPollMs);

    return () => {
      unmountedRef.current = true;
      if (reconnectTimerRef.current) {
        clearTimeout(reconnectTimerRef.current);
        reconnectTimerRef.current = null;
      }
      if (restTimerRef.current) {
        clearInterval(restTimerRef.current);
        restTimerRef.current = null;
      }
      if (wsRef.current) {
        try {
          wsRef.current.close();
        } catch {
          /* ignore — best-effort cleanup */
        }
        wsRef.current = null;
      }
    };
  }, [enabled, projectId, openWebSocket, restPoll, restPollMs]);

  return useMemo(
    () => ({
      accountSummary,
      positions,
      recentOrderEvents,
      mcpStatus,
      transportState,
    }),
    [accountSummary, positions, recentOrderEvents, mcpStatus, transportState],
  );
}
