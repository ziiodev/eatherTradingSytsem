/**
 * Pure, framework-free operand model for the Condition node's `left`/`right`
 * fields. The Condition node stores these as RAW MQL5 strings in `node.data`
 * (the codegen contract — see backend/app/services/codegen/nodes/condition.py),
 * so this module never persists a separate operand state: it serializes an
 * in-memory {@link Operand} to a raw string on edit, and parses a raw string
 * back into an {@link Operand} on render. Byte-identity of generated code is
 * therefore preserved for equivalent conditions.
 */
import type { FlowNode } from "../_types/graph";

/** The three v1 operand kinds the guided builder can author. */
export type OperandKind = "indicator" | "number" | "custom";

/** The two output lines of a Stochastic node. */
export type StochasticLine = "K" | "D";

/**
 * Discriminated union describing what an operand IS, independent of its raw
 * serialized form:
 *  - `indicator`: references an indicator node's buffer value.
 *                 The OPTIONAL `line` field selects the Stochastic output line
 *                 (%K main / %D signal); it is absent for single-line indicators
 *                 (SMA/RSI/MACD) so their shape — and serialized refs — are
 *                 unchanged and back-compatible.
 *                 The OPTIONAL `outputId` selects a NAMED non-primary output of a
 *                 (future) multi-output indicator; it is absent for the primary
 *                 output, keeping `<prefix>_<id>[0]` byte-identical. Stochastic
 *                 carries its line in the PREFIX (stochk_/stochd_), NOT here.
 *                 The OPTIONAL `shift` is the bar offset (defaults to 0 / current
 *                 bar); a positive shift serializes `[shift]`.
 *  - `number`:    a numeric constant (stored verbatim as typed).
 *  - `custom`:    free-text raw MQL5 (escape hatch / unparseable fallback).
 */
export type Operand =
  | {
      kind: "indicator";
      nodeId: string;
      line?: StochasticLine;
      outputId?: string;
      shift?: number;
    }
  | { kind: "number"; value: string }
  | { kind: "custom"; raw: string };

/**
 * Maps a frontend indicator node type to the backend buffer-variable prefix.
 *
 * MIRRORS the backend codegen convention in
 * `backend/app/services/codegen/nodes/{sma,rsi,macd}.py`, where each node emits
 * `var = f"{prefix}_{node_id(node)}"` (see `helpers.node_id()`), then
 * `CopyBuffer(..., 0, 1, var)` so `var[0]` is the current-bar value. For MACD
 * the read is `CopyBuffer(h, MAIN_LINE, 0, 1, var)` — i.e. the MACD operand
 * here is the MAIN line ONLY (signal/histogram are out of scope; this is the
 * accepted duplicated-convention drift flagged in explore #2244).
 *
 * This is the SINGLE source of the prefix mapping on the frontend; keep it in
 * sync if the backend buffer-naming ever changes.
 */
const INDICATOR_PREFIX: Record<string, string> = {
  SMA: "sma",
  RSI: "rsi",
  MACD: "macd",
  // ZScore is the first indicator whose outputs are ALL named: even its primary
  // "Z-Score" value carries an explicit `:value` token (the picker supplies
  // `outputId: "value"`), so no special primary-omission case is needed here.
  ZScore: "zscore",
};

/**
 * Stochastic is the only TWO-line indicator: %K (main) and %D (signal) each get
 * their own buffer-variable prefix, mirroring the backend codegen which emits
 * one `iStochastic` handle and TWO `CopyBuffer` reads into `stochk_<id>` and
 * `stochd_<id>` (see backend/app/services/codegen/nodes/stochastic.py). These
 * prefixes MUST stay byte-identical to those backend buffer-var names.
 */
const STOCH_PREFIX = { K: "stochk", D: "stochd" } as const;

/** Default Stochastic line when an operand omits one (the %K main line). */
const DEFAULT_STOCH_LINE: StochasticLine = "K";

/** The frontend node types that can be referenced as an indicator operand. */
const INDICATOR_TYPES = [...Object.keys(INDICATOR_PREFIX), "Stochastic"];

/**
 * Recognizes a serialized indicator reference. Generalized (additive) grammar:
 *
 *   <prefix>_<nodeId>[:<outputId>][<shift>]
 *
 *   - prefix:   sma | rsi | macd | zscore | stochk | stochd (disjoint, ANCHORED
 *               so a Stochastic ref never partial-matches a single-line prefix).
 *   - nodeId:   captured LAZILY so the OPTIONAL `:outputId` and the REQUIRED
 *               numeric `[shift]` peel off the tail correctly even though node
 *               ids are opaque `n_<base36>` tokens.
 *   - outputId: OPTIONAL `:token` (lowercase alnum) selecting a NAMED non-primary
 *               output of a future multi-output indicator. ABSENT for the
 *               primary output, so legacy `<prefix>_<id>[0]` stays byte-identical.
 *   - shift:    REQUIRED numeric bar offset inside `[...]`. Legacy refs always
 *               carried `[0]`, which parses identically (outputId absent, shift 0).
 */
export const INDICATOR_REF_RE =
  /^(sma|rsi|macd|zscore|stochk|stochd)_(.+?)(?::([a-z][a-z0-9]*))?\[(\d+)\]$/;

/** Optional reference selectors layered on top of the base `<prefix>_<id>`. */
export interface BufferRefOptions {
  /** Stochastic output line (carried in the prefix; ignored for others). */
  line?: StochasticLine;
  /**
   * NAMED non-primary output of a multi-output indicator, serialized as
   * `:<outputId>`. Absent (or undefined) means the PRIMARY output → no `:token`.
   * NOT used by Stochastic, which carries its line in the prefix instead.
   */
  outputId?: string;
  /** Bar offset; defaults to 0 (current bar). A positive value emits `[shift]`. */
  shift?: number;
}

/**
 * The buffer variable reference for an indicator node, or `null` if the node is
 * not a referenceable indicator.
 *
 * Byte-identity contract:
 *  - PRIMARY output (no `outputId`) at `shift 0` → `<prefix>_<id>[0]` EXACTLY,
 *    identical to the pre-refactor output (no `:token`).
 *  - A NAMED non-primary output → `<prefix>_<id>:<outputId>[shift]`.
 *  - `shift > 0` simply changes the bracketed bar offset.
 *
 * For a Stochastic node `line` selects the buffer (`stochk_<id>` for "K",
 * default, or `stochd_<id>` for "D"); Stochastic NEVER emits a `:outputId`
 * (its two outputs are encoded by the prefix), so its refs stay byte-identical.
 * Centralizes the ref shape so it is never hand-built elsewhere.
 */
export function bufferVarFor(
  node: FlowNode,
  opts?: BufferRefOptions,
): string | null {
  const shift = opts?.shift ?? 0;
  if (node.data.type === "Stochastic") {
    const prefix = STOCH_PREFIX[opts?.line ?? DEFAULT_STOCH_LINE];
    return `${prefix}_${node.id}[${shift}]`;
  }
  const prefix = INDICATOR_PREFIX[node.data.type];
  if (prefix == null) return null;
  // Named non-primary outputs append `:outputId`; the primary output omits it.
  const out = opts?.outputId != null ? `:${opts.outputId}` : "";
  return `${prefix}_${node.id}${out}[${shift}]`;
}

/**
 * The indicator nodes available as operand references for a given Condition
 * node: every SMA/RSI/MACD/Stochastic node except the Condition node itself.
 */
export function indicatorNodesFrom(
  nodes: FlowNode[],
  selfId: string,
): FlowNode[] {
  return nodes.filter(
    (n) => INDICATOR_TYPES.includes(n.data.type) && n.id !== selfId,
  );
}

/**
 * Human-friendly, UNIQUE label for an indicator node in the picker dropdown,
 * e.g. `SMA — Fast MA (n_ab12cd34)`. The node id is appended so two nodes with
 * the same `label` remain distinguishable.
 */
export function labelForIndicator(node: FlowNode): string {
  return `${node.data.type} — ${node.data.label} (${node.id})`;
}

/**
 * Serialize an {@link Operand} to the raw MQL5 string stored in `node.data`.
 * Indicator operands resolve to `<prefix>_<nodeId>[0]`; the prefix is derived
 * from the referenced node's type when found among `indicatorNodes`. If the
 * referenced node is missing (e.g. it was deleted), the empty string is
 * returned so the stale reference is visibly cleared rather than silently
 * encoding a wrong prefix.
 */
export function serializeOperand(
  op: Operand,
  indicatorNodes: FlowNode[],
): string {
  switch (op.kind) {
    case "indicator": {
      const node = indicatorNodes.find((n) => n.id === op.nodeId);
      if (node == null) return "";
      return (
        bufferVarFor(node, {
          line: op.line,
          outputId: op.outputId,
          shift: op.shift,
        }) ?? ""
      );
    }
    case "number":
      return op.value;
    case "custom":
      return op.raw;
  }
}

/**
 * Parse a raw MQL5 string from `node.data` back into an {@link Operand}.
 *
 * Resolution order:
 *  1. An `INDICATOR_REF_RE` match whose captured node id still exists among
 *     `indicatorNodes` AND whose prefix matches that node's type → `indicator`.
 *     A `stochk_`/`stochd_` prefix recovers the Stochastic line; a missing or
 *     non-Stochastic node for a stoch prefix falls through to `custom`.
 *  2. A finite numeric literal → `number` (value kept verbatim as typed).
 *  3. Anything else (including stale/foreign indicator refs and arbitrary
 *     expressions) → `custom`, preserving the raw string verbatim so existing
 *     hand-authored conditions round-trip unchanged.
 */
export function parseOperand(raw: string, indicatorNodes: FlowNode[]): Operand {
  const match = INDICATOR_REF_RE.exec(raw);
  if (match != null) {
    const [, prefix, nodeId, outputId, shiftStr] = match;
    const node = indicatorNodes.find((n) => n.id === nodeId);
    if (node != null) {
      // `shift`/`outputId` are only set when non-default so the operand shape —
      // and any re-serialization — stays byte-identical for legacy `[0]` refs.
      const shift = Number(shiftStr);
      const shiftPart = shift > 0 ? { shift } : {};
      // Two-line Stochastic refs carry the line in the prefix (never an
      // `:outputId`); a stray `:outputId` on a stoch prefix is rejected below.
      if (
        outputId == null &&
        prefix === STOCH_PREFIX.K &&
        node.data.type === "Stochastic"
      ) {
        return { kind: "indicator", nodeId: node.id, line: "K", ...shiftPart };
      }
      if (
        outputId == null &&
        prefix === STOCH_PREFIX.D &&
        node.data.type === "Stochastic"
      ) {
        return { kind: "indicator", nodeId: node.id, line: "D", ...shiftPart };
      }
      // Single-line indicators (SMA/RSI/MACD). The OPTIONAL `:outputId` selects a
      // named non-primary output; absent → primary (legacy, byte-identical).
      if (INDICATOR_PREFIX[node.data.type] === prefix) {
        const outPart = outputId != null ? { outputId } : {};
        return { kind: "indicator", nodeId: node.id, ...outPart, ...shiftPart };
      }
    }
  }

  const trimmed = raw.trim();
  if (trimmed !== "" && Number.isFinite(Number(trimmed))) {
    return { kind: "number", value: raw };
  }

  return { kind: "custom", raw };
}
