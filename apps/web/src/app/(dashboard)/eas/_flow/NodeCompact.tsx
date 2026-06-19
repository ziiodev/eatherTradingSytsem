"use client";

/**
 * COMPACT node state: a small rounded card with a category-colored border and
 * dark background, showing ONLY the per-type icon (no name/label text), the
 * primary lateral handles, and the bottom action bar.
 *
 * The primary id-less handles are rendered here via NodeHandles(compact) so they
 * stay mounted whenever this branch is shown (toggling to EXPANDED keeps them
 * mounted there too — see CustomNode).
 */
import type { NodeData } from "../_types/graph";
import { getNodeTypeMeta } from "../_lib/nodeTypeRegistry";
import { nodeColorClasses } from "./nodeColors";
import { NodeHandles } from "./NodeHandles";
import { NodeActionBar } from "./NodeActionBar";

export function NodeCompact({
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
  const Icon = meta.icon;

  return (
    // Small square-ish card: fixed ~72px square so every compact node shares the
    // same footprint regardless of icon. `relative` anchors the action bar to the
    // bottom edge; extra bottom padding leaves room for that overlapping bar.
    <div
      className={`bg-gl-gray-900 relative flex h-[72px] w-[72px] flex-col items-center justify-center rounded-lg border-2 pb-2 shadow-sm ${
        selected ? "ring-gl-orange-500 ring-2" : ""
      } ${colors.border}`}
    >
      <NodeHandles
        inputPorts={meta.inputPorts}
        outputPorts={meta.outputPorts}
        expanded={false}
      />

      {/* Per-type icon only — no name text in COMPACT (per spec). */}
      <Icon className={`h-7 w-7 ${colors.text}`} aria-label={data.type} />

      {/* Bottom action bar tucked onto the card's bottom edge (screenshots). */}
      <div className="absolute -bottom-3 left-1/2 -translate-x-1/2">
        <NodeActionBar nodeId={nodeId} info={data.type} />
      </div>
    </div>
  );
}
