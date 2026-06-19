"use client";

/**
 * Floating "tidy up" control pinned to the bottom-center of the canvas.
 *
 * Pressing it does two things in one gesture:
 *  1. Collapses EVERY node to its compact state (nodeUiStore.collapseAll) — this
 *     is ephemeral UI state, not an undoable graph mutation.
 *  2. Snaps all node positions onto a regular grid (graphStore.arrangeInGrid),
 *     committed as a SINGLE undoable step so the user can revert the rearrange.
 * It then runs fitView so the freshly-organized grid is framed in view.
 *
 * Rendered as a React Flow <Panel>, so it must live inside <ReactFlow> (it is
 * mounted by FlowCanvas) where useReactFlow()/fitView are available.
 */
import { Panel, useReactFlow } from "@xyflow/react";
import { LayoutGrid } from "lucide-react";
import { useGraphStore } from "../_stores/graphStore";
import { useNodeUiStore } from "../_stores/nodeUiStore";

export function OrganizeNodesButton() {
  const { fitView } = useReactFlow();
  const arrangeInGrid = useGraphStore((s) => s.arrangeInGrid);
  const hasNodes = useGraphStore((s) => s.nodes.length > 0);

  const handleClick = () => {
    // Minimize first, then lay the compact nodes out on the grid.
    useNodeUiStore.getState().collapseAll();
    arrangeInGrid();
    // Defer the fit until the new positions have flowed into React Flow; a
    // nested rAF clears both the React commit and React Flow's measure pass.
    requestAnimationFrame(() =>
      requestAnimationFrame(() => fitView({ padding: 0.2, duration: 400 })),
    );
  };

  return (
    // Lift the panel well clear of the bottom edge (React Flow's default 15px
    // margin glues it to the border) so the control is unmistakable. Inline
    // style is used deliberately: it beats React Flow's stylesheet margin
    // without relying on Tailwind's important modifier (v4 syntax differs).
    <Panel position="bottom-center" style={{ marginBottom: 64 }}>
      <button
        type="button"
        onClick={handleClick}
        disabled={!hasNodes}
        aria-label="Ordenar nodos"
        title="Minimizar y ordenar todos los nodos"
        className="border-border bg-gl-gray-900 text-muted-foreground hover:text-foreground hover:bg-gl-gray-800 flex h-9 w-9 items-center justify-center rounded-full border shadow-md transition-colors disabled:pointer-events-none disabled:opacity-50"
      >
        <LayoutGrid className="h-4 w-4" />
      </button>
    </Panel>
  );
}
