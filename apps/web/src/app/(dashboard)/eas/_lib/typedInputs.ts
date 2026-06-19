/**
 * Typed-input registry: the generalized model behind the topology guard.
 *
 * Most nodes are LINEAR — they accept at most ONE incoming FLOW edge to keep the
 * codegen DFS chain straight. A handful of nodes instead expose multiple REAL,
 * id'd, connectable TARGET handles that consume non-flow edges:
 *
 * - Combinators (LogicalAnd/Or/Xor/Not) fan in up to N Condition sources via
 *   their cond1..condN handles.
 * - Crossings (BullishCross/BearishCross) consume exactly TWO indicator
 *   (SMA/RSI/MACD) sources via their value1/value2 handles.
 *
 * Both share the same shape: a capped number of typed, non-flow inputs whose
 * SOURCE node type is constrained. This module unifies them so the topology
 * guard has one code path. Combinator metadata still lives in `combinators.ts`
 * (its public API is unchanged); this registry references it for combinators and
 * adds the crossing entries.
 */
import type { NodeType } from "../_types/graph";
import {
  isCombinator,
  maxInputs as combinatorMaxInputs,
} from "./combinators";

/**
 * What kind of SOURCE a typed input accepts:
 * - `"condition"`: only a Condition node (combinator inputs).
 * - `"indicator"`: only an SMA/RSI/MACD node (crossing value inputs).
 */
export type TypedInputSourceKind = "condition" | "indicator";

/** The typed-input contract for one node type. */
export interface TypedInputSpec {
  /** Max incoming typed (non-flow) edges this node may accept. */
  maxInputs: number;
  /** The only source-node category its typed inputs accept. */
  sourceKind: TypedInputSourceKind;
}

/** The indicator node types a crossing value input accepts. */
const INDICATOR_TYPES: ReadonlySet<NodeType> = new Set<NodeType>([
  "SMA",
  "RSI",
  "MACD",
]);

/**
 * ZScore's SIGNAL output handle ids (`zgt`/`zlt`). A combinator condition input
 * accepts a ZScore source ONLY through one of these handles — its value outputs
 * are rejected into combinators (they are operands, not boolean conditions).
 * Mirrors the backend signal→combinator rule.
 */
const ZSCORE_SIGNAL_HANDLES: ReadonlySet<string> = new Set(["zgt", "zlt"]);

/**
 * ZScore's ARRAY value output handle ids (`zmean`/`zstd`/`zsma`). A crossing
 * value input accepts a ZScore source ONLY through one of these handles — the
 * scalar z (primary/id-less) and |z| (`zabs`) and the signals are rejected,
 * since crossing detection needs a buffered array line. Mirrors the backend.
 */
const ZSCORE_ARRAY_HANDLES: ReadonlySet<string> = new Set([
  "zmean",
  "zstd",
  "zsma",
]);

/**
 * Crossing typed-input specs. Both BullishCross and BearishCross take exactly
 * two indicator value inputs (value1/value2). Combinators are NOT listed here —
 * they are resolved via `combinators.ts` so its existing map stays the single
 * source of truth for combinator capacities.
 */
const CROSSING_SPECS: Partial<Record<NodeType, TypedInputSpec>> = {
  BullishCross: { maxInputs: 2, sourceKind: "indicator" },
  BearishCross: { maxInputs: 2, sourceKind: "indicator" },
};

/** True when `type` is a crossing node (typed indicator inputs). */
export function isCrossing(type: NodeType | undefined): boolean {
  return type != null && type in CROSSING_SPECS;
}

/**
 * Resolve the typed-input spec for a node type, or `undefined` for a plain
 * linear node. Combinators are derived from `combinators.ts` (sourceKind
 * "condition"); crossings come from the local map.
 */
export function typedInputFor(
  type: NodeType | undefined,
): TypedInputSpec | undefined {
  if (type == null) return undefined;
  if (isCombinator(type)) {
    return { maxInputs: combinatorMaxInputs(type), sourceKind: "condition" };
  }
  return CROSSING_SPECS[type];
}

/** True when `type` has typed (non-flow, capped) inputs: combinator OR crossing. */
export function isTypedInputNode(type: NodeType | undefined): boolean {
  return typedInputFor(type) !== undefined;
}

/**
 * True when `sourceType` is an acceptable SOURCE for a typed input of the given
 * `sourceKind`. The check is sourceHandle-AWARE so a multi-output node (ZScore)
 * is accepted ONLY through the right kind of output handle:
 *
 * - `"condition"` (combinator inputs): a Condition node (any handle), OR a ZScore
 *   SIGNAL output (`sourceHandle` zgt/zlt). A ZScore VALUE output is REJECTED into
 *   a combinator.
 * - `"indicator"` (crossing value inputs): an SMA/RSI/MACD node (any handle), OR a
 *   ZScore ARRAY value output (`sourceHandle` zmean/zstd/zsma). The ZScore scalar
 *   z (primary/id-less), |z| (zabs) and the signals are REJECTED.
 *
 * Existing rules (Condition→combinator, SMA/RSI/MACD→crossing) are unchanged: the
 * extra `sourceHandle` arg is only consulted for ZScore sources.
 */
export function isAcceptableSource(
  sourceKind: TypedInputSourceKind,
  sourceType: NodeType | undefined,
  sourceHandle?: string | null,
): boolean {
  if (sourceType == null) return false;
  if (sourceKind === "condition") {
    if (sourceType === "Condition") return true;
    // ZScore feeds a combinator ONLY via a signal output (zgt/zlt).
    if (sourceType === "ZScore") {
      return sourceHandle != null && ZSCORE_SIGNAL_HANDLES.has(sourceHandle);
    }
    return false;
  }
  // sourceKind === "indicator" (crossing value inputs).
  if (INDICATOR_TYPES.has(sourceType)) return true;
  // ZScore feeds a crossing ONLY via an array value output (zmean/zstd/zsma).
  if (sourceType === "ZScore") {
    return sourceHandle != null && ZSCORE_ARRAY_HANDLES.has(sourceHandle);
  }
  return false;
}
