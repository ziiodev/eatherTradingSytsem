/**
 * Boolean-combinator type metadata shared by the topology guard.
 *
 * A "combinator" is a boolean fan-in node
 * (LogicalAnd / LogicalOr / LogicalNot / LogicalXor).
 * Unlike every other node — which accepts at most ONE incoming edge to keep the
 * chain linear — a combinator target may accept up to `maxInputs` incoming
 * edges (its connectable cond1..condN ports). Out-degree stays <= 1 for all.
 */
import type { NodeType } from "../_types/graph";

/** Per-type incoming-edge capacity. AND/OR/XOR fan in up to 6; NOT takes 1. */
const COMBINATOR_MAX_INPUTS: Partial<Record<NodeType, number>> = {
  LogicalAnd: 6,
  LogicalOr: 6,
  LogicalNot: 1,
  LogicalXor: 6,
};

/** True when `type` is a boolean combinator (multi/relaxed-input node). */
export function isCombinator(type: NodeType | undefined): boolean {
  return type != null && type in COMBINATOR_MAX_INPUTS;
}

/**
 * Incoming-edge capacity for a target node type. Combinators expose their
 * relaxed fan-in (6 for AND/OR, 1 for NOT); every other type keeps the linear
 * cap of 1. An unknown/undefined type defaults to 1 (safe, linear).
 */
export function maxInputs(type: NodeType | undefined): number {
  if (type == null) return 1;
  return COMBINATOR_MAX_INPUTS[type] ?? 1;
}
