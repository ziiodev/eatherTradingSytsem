"use client";

/**
 * Bottom action bar shared by COMPACT and EXPANDED node states.
 *
 * Two circular controls: an info "i" (blue) and a delete "x" (red). Delete wires
 * to graphStore.removeNode(id) (which also drops the node's ephemeral expand
 * state). The info control is a minimal placeholder hook for now — it toggles a
 * small tooltip showing the node type; full info UX is a later phase.
 *
 * Clicks call stopPropagation so they don't bubble to the node's double-click /
 * selection handlers (the buttons are inside the React Flow node body).
 */
import { useState } from "react";
import { Info, X } from "lucide-react";
import { useGraphStore } from "../_stores/graphStore";

// Circular buttons get a solid dark fill so they read as distinct pills when the
// bar overlaps the card's bottom edge (per the reference screenshots).
const BTN_BASE =
  "bg-gl-gray-900 flex h-5 w-5 items-center justify-center rounded-full border text-[10px] transition-colors";

export function NodeActionBar({
  nodeId,
  info,
}: {
  nodeId: string;
  /** Short text surfaced by the info control's tooltip (e.g. node type). */
  info?: string;
}) {
  const removeNode = useGraphStore((s) => s.removeNode);
  const [showInfo, setShowInfo] = useState(false);

  return (
    <div className="relative flex items-center justify-center gap-2">
      <button
        type="button"
        aria-label="Node info"
        title={info}
        onClick={(e) => {
          e.stopPropagation();
          setShowInfo((v) => !v);
        }}
        className={`${BTN_BASE} border-gl-blue-500 text-gl-blue-500 hover:bg-gl-blue-500/15`}
      >
        <Info className="h-3 w-3" />
      </button>
      <button
        type="button"
        aria-label="Delete node"
        onClick={(e) => {
          e.stopPropagation();
          removeNode(nodeId);
        }}
        className={`${BTN_BASE} border-gl-red-500 text-gl-red-500 hover:bg-gl-red-500/15`}
      >
        <X className="h-3 w-3" />
      </button>

      {showInfo && info && (
        <div className="bg-gl-gray-800 text-muted-foreground absolute -top-6 left-1/2 -translate-x-1/2 rounded px-2 py-0.5 text-[10px] whitespace-nowrap">
          {info}
        </div>
      )}
    </div>
  );
}
