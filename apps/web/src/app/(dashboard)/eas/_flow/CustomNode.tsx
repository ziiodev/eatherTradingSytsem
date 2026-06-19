"use client";

/**
 * The single custom React Flow renderer (registered under the `nodeTypes.custom`
 * key) used by ALL 10 node types — and any AI/future type, via the registry's
 * getNodeTypeMeta fallback.
 *
 * It reads the EPHEMERAL expand flag from nodeUiStore (never from node.data) and
 * switches between the COMPACT and EXPANDED bodies. Both bodies mount the PRIMARY
 * id-less handles (through NodeHandles), so flipping states never unmounts the
 * primary handles and legacy edges (saved with sourceHandle/targetHandle
 * undefined) stay bound.
 *
 * The double-click toggle is wired at the canvas level (FlowCanvas, Phase 4);
 * this component only CONSUMES isExpanded so that toggle reflects immediately.
 */
import { type NodeProps } from "@xyflow/react";
import type { FlowNode } from "../_types/graph";
import { useNodeUiStore } from "../_stores/nodeUiStore";
import { NodeCompact } from "./NodeCompact";
import { NodeExpanded } from "./NodeExpanded";

export function CustomNode({ id, data, selected }: NodeProps<FlowNode>) {
  // Subscribe narrowly to THIS node's expand flag so unrelated toggles don't
  // re-render it. Expand state lives ONLY in nodeUiStore, never in node.data.
  const expanded = useNodeUiStore((s) => s.expanded.has(id));

  return expanded ? (
    <NodeExpanded nodeId={id} data={data} selected={!!selected} />
  ) : (
    <NodeCompact nodeId={id} data={data} selected={!!selected} />
  );
}
