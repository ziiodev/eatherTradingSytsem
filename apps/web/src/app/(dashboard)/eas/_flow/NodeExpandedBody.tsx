"use client";

/**
 * EXPANDED node body: renders the node's existing flat `data` params through the
 * SHARED NodeParamFields helper, so on-node edits write through updateNodeData
 * (the single param-editing surface now that the inspector is gone).
 *
 * Start/End carry no params and render nothing here. Edits live inside React
 * Flow's node, so the wrapper stops mouse-down propagation to keep typing/
 * clicking inputs from starting a node drag.
 */
import type { NodeData, NodeType } from "../_types/graph";
import { NODE_PARAM_SCHEMAS } from "../_lib/nodeParamSchemas";
import { NodeParamFields } from "./NodeParamFields";
import { PresetSelector } from "./PresetSelector";

export function NodeExpandedBody({
  nodeId,
  data,
}: {
  nodeId: string;
  data: NodeData;
}) {
  const type: NodeType = data.type;
  const hasFields = (NODE_PARAM_SCHEMAS[type] ?? []).length > 0;
  if (!hasFields) return null;

  return (
    // Stop drag/selection from hijacking input interaction inside the node.
    <div className="nodrag space-y-2" onMouseDown={(e) => e.stopPropagation()}>
      {/* RSI-only pure-UI preset selector, above the params (not persisted). */}
      {type === "RSI" ? <PresetSelector nodeId={nodeId} /> : null}
      <NodeParamFields nodeId={nodeId} type={type} data={data} />
    </div>
  );
}
