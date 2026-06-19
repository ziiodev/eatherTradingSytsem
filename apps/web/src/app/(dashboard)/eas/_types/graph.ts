/**
 * Shared graph types. This shape is the contract between the React Flow editor,
 * the persisted `strategy` JSON column, and the backend MQL5 codegen engine.
 * Keep it stable across frontend and backend.
 */
import type { Edge, Node } from "@xyflow/react";

/**
 * The domain node types. The original MVP set, the boolean-combinator family
 * (`LogicalAnd`/`LogicalOr`/`LogicalNot`/`LogicalXor`), and the crossing family
 * (`BullishCross`/`BearishCross`) that detect indicator line crossings.
 */
export type NodeType =
  | "Start"
  | "End"
  | "Buy"
  | "Sell"
  | "SMA"
  | "RSI"
  | "MACD"
  | "Stochastic"
  | "ZScore"
  | "Condition"
  | "RiskManagement"
  | "Log"
  | "LogicalAnd"
  | "LogicalOr"
  | "LogicalNot"
  | "LogicalXor"
  | "BullishCross"
  | "BearishCross";

/**
 * Per-node data payload. Parameters live as FLAT keys alongside `label`/`type`
 * (e.g. `data.period`, `data.lots`) to match the codegen contract, which reads
 * each param directly off `node.data`.
 */
export interface NodeData extends Record<string, unknown> {
  label: string;
  type: NodeType;
}

/** A React Flow node carrying our typed data payload. */
export type FlowNode = Node<NodeData>;
export type FlowEdge = Edge;

/**
 * A render-only authoring group: a named set of member node ids. Groups never
 * enter the canonical `nodes[]`/`edges[]`; they are an additive overlay that the
 * editor uses to collapse/expand a cluster. "Group" is deliberately NOT a
 * `NodeType`, so codegen output stays byte-identical whether or not groups exist.
 */
export interface GroupMeta {
  id: string;
  name: string;
  nodeIds: string[];
  /**
   * Whether the group renders collapsed (one synthetic node) vs expanded (a
   * container framing the members). PERSISTED so the view round-trips on reload.
   * Optional for backward compat: legacy groups without it default to expanded.
   */
  collapsed?: boolean;
}

/**
 * Serialized graph persisted to the backend and fed to codegen. `groups` is
 * additive and optional: old strategies persisted before grouping existed simply
 * omit it, and codegen ignores it entirely.
 */
export interface SerializedGraph {
  nodes: FlowNode[];
  edges: FlowEdge[];
  groups?: GroupMeta[];
}
