/**
 * Topology connection guard for the React Flow canvas.
 *
 * Mirrors the backend strict validator: the codegen dispatcher renders the
 * OnTick body as a FLAT DFS walk from Start over FLOW edges, so the chain must
 * stay LINEAR — at most one outgoing FLOW edge per node, no self-loops, no
 * cycles. Beyond flow, two node families expose REAL typed TARGET handles:
 *   - combinators fan in Condition sources via cond1..condN handles;
 *   - crossings consume SMA/RSI/MACD sources via value1/value2 handles.
 * Those are VALUE/CONDITION edges, NOT flow edges, so they are excluded from the
 * linear in/out-degree counts (see `typedInputs.ts` for the unified model).
 *
 * The guard is HANDLE-AWARE: it reads `sourceHandle`/`targetHandle` to tell a
 * flow edge (id-less target handle) from a typed edge (value1/value2/condN). It
 * evaluates a SINGLE proposed connection against the current edge set and
 * rejects it only when it would violate a rule. It stays no stricter than the
 * backend for existing (non-typed-input) graphs.
 *
 * Pure and side-effect free so it can be reused by both the `isValidConnection`
 * prop and the store's `onConnect` guard.
 */
import type { Connection, Edge } from "@xyflow/react";
import type { NodeType } from "../_types/graph";
import { isAcceptableSource, typedInputFor } from "./typedInputs";

/** The minimal endpoint+handle shape both `Connection` and `Edge` satisfy. */
interface EdgeLike {
  source?: string | null;
  target?: string | null;
  sourceHandle?: string | null;
  targetHandle?: string | null;
}

/** Resolve a node id to its domain type (undefined if the node is unknown). */
export type NodeTypeResolver = (id: string) => NodeType | undefined;

/** True when an edge is a FLOW edge: its target handle is the primary id-less one. */
function isFlowEdge(e: EdgeLike): boolean {
  return e.targetHandle == null || e.targetHandle === "";
}

/**
 * Return `true` if `connection` may be added to `edges` under the topology
 * rules, given a `nodeTypeOf` resolver:
 *
 * 1. No self-loop. Out-degree <= 1 counting ONLY FLOW edges, so a source that
 *    value/condition-feeds a typed-input node can still flow onward once.
 * 2. FLOW target keeps in-degree <= 1 (linear chain).
 * 3. TYPED target (combinator/crossing): the proposed edge must land on a typed
 *    handle, that handle must be free, the typed in-degree must be < maxInputs,
 *    and the SOURCE must match the typed sourceKind (Condition for combinators,
 *    SMA/RSI/MACD for crossings). Combinator NESTING is blocked because a
 *    combinator/crossing source is never an acceptable Condition/indicator.
 */
export function isValidConnection(
  connection: EdgeLike,
  edges: Edge[],
  nodeTypeOf: NodeTypeResolver,
): boolean {
  const { source, target } = connection;
  // Both endpoints must be present (defensive: React Flow can emit nulls).
  if (!source || !target) return false;
  // No self-loop (backend: source == target is rejected).
  if (source === target) return false;
  // Out-degree <= 1: the source must not already have an outgoing FLOW edge.
  // Value/condition edges don't consume the source's single flow slot.
  if (edges.some((e) => e.source === source && isFlowEdge(e))) return false;

  const targetType = nodeTypeOf(target);
  const sourceType = nodeTypeOf(source);
  const spec = typedInputFor(targetType);

  // FLOW edge into the target (id-less target handle).
  if (isFlowEdge(connection)) {
    // Typed-input nodes don't accept flow edges into their primary handle in v1
    // (they are fed exclusively through their typed handles).
    if (spec) return false;
    // Linear node: in-degree (flow) capped at 1.
    return !edges.some((e) => e.target === target && isFlowEdge(e));
  }

  // TYPED edge (value1/value2 or cond1..condN). The target must be a node that
  // actually exposes typed inputs; otherwise reject (no stray handles).
  if (!spec) return false;
  // The proposed handle must be free (one edge per typed handle).
  const handle = connection.targetHandle;
  if (edges.some((e) => e.target === target && e.targetHandle === handle)) {
    return false;
  }
  // Typed in-degree cap (count only non-flow edges into the target).
  const typedIncoming = edges.filter(
    (e) => e.target === target && !isFlowEdge(e),
  ).length;
  if (typedIncoming >= spec.maxInputs) return false;
  // The source must match the typed sourceKind. This also blocks combinator/
  // crossing nesting (those source types are never Condition/indicator). The
  // sourceHandle is passed so a ZScore source is gated by its output kind
  // (signal handles → combinators, array-value handles → crossings).
  return isAcceptableSource(
    spec.sourceKind,
    sourceType,
    connection.sourceHandle,
  );
}

/**
 * Curried form for React Flow's `isValidConnection` prop, bound to live edges
 * and a node-type resolver.
 */
export const makeIsValidConnection =
  (edges: Edge[], nodeTypeOf: NodeTypeResolver) =>
  (connection: Connection | Edge): boolean =>
    isValidConnection(connection, edges, nodeTypeOf);
