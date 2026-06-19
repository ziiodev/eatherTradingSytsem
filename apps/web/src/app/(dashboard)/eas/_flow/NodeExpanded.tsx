"use client";

/**
 * EXPANDED node state: a larger card composing the header (icon/title/badge/
 * subtitle + duplicate), the labeled lateral handles, the editable param body,
 * and the bottom action bar.
 *
 * The primary id-less handles are rendered here via NodeHandles(expanded) so
 * they stay mounted while expanded — and since CustomNode renders exactly one of
 * COMPACT/EXPANDED, toggling between states never leaves a frame with no primary
 * handle mounted (legacy edges stay bound).
 */
import type { NodeData } from "../_types/graph";
import { getNodeTypeMeta } from "../_lib/nodeTypeRegistry";
import { nodeColorClasses } from "./nodeColors";
import { NodeHeader } from "./NodeHeader";
import { NodeHandles } from "./NodeHandles";
import { NodeExpandedBody } from "./NodeExpandedBody";
import { NodeActionBar } from "./NodeActionBar";

export function NodeExpanded({
  nodeId,
  data,
  selected,
}: {
  nodeId: string;
  data: NodeData;
  selected: boolean;
}) {
  const meta = getNodeTypeMeta(data.type);
  const colors = nodeColorClasses(meta.colorToken);

  return (
    // Compact card (~192px). `relative` + extra bottom padding so the action bar
    // can sit centered on the bottom edge, slightly overlapping (screenshots).
    <div
      className={`relative flex w-48 flex-col gap-1.5 rounded-lg border-2 p-2 pb-3.5 shadow-md backdrop-blur-sm ${
        selected ? "ring-gl-orange-500 ring-2" : ""
      } ${colors.border} ${colors.cardBg}`}
    >
      <NodeHeader nodeId={nodeId} data={data} />

      {/* Labeled lateral handles (primary id-less + cosmetic non-connectable). */}
      <div className="flex flex-col gap-1">
        <NodeHandles
          inputPorts={meta.inputPorts}
          outputPorts={meta.outputPorts}
          expanded
        />
      </div>

      <NodeExpandedBody nodeId={nodeId} data={data} />

      {/* Action bar tucked onto the card's bottom edge (screenshots). */}
      <div className="absolute -bottom-3 left-1/2 -translate-x-1/2">
        <NodeActionBar nodeId={nodeId} info={data.type} />
      </div>
    </div>
  );
}
