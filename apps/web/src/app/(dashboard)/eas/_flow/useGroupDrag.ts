"use client";

/**
 * Drag handlers that let a GROUP be moved as a unit. Dragging the synthetic
 * collapsed group-node OR the expanded container frame moves ALL of the group's
 * member nodes by the same delta — so the move persists (member positions are
 * canonical) and the synthetic node's DERIVED position follows the members with
 * no snap-back fight against React Flow's controlled drag.
 *
 * Wired into <ReactFlow> as onNodeDragStart / onNodeDrag / onNodeDragStop.
 *
 * Store actions are read off getState() (no subscription) so the canvas never
 * re-renders from this hook, matching the existing handler patterns.
 */
import { useCallback, useRef } from "react";
import type { OnNodeDrag } from "@xyflow/react";
import type { FlowNode } from "../_types/graph";
import { useGraphStore } from "../_stores/graphStore";
import type {
  GroupContainerData,
  GroupNodeData,
} from "../_lib/groupDerivation";

/** Active-drag bookkeeping for the synthetic group node being moved. */
type DragRef = {
  /** React Flow id of the synthetic node being dragged. */
  id: string;
  /** Canonical group id whose members we translate. */
  groupId: string;
  /** Last observed synthetic position, to compute incremental deltas. */
  last: { x: number; y: number };
  /** Whether we already pushed the pre-drag snapshot for undo. */
  snapshotted: boolean;
};

/** Read the groupId off a synthetic node, or null if it isn't a group node. */
function syntheticGroupId(node: FlowNode): string | null {
  const d = node.data as Partial<GroupNodeData & GroupContainerData>;
  if (d.__isGroupNode || d.__isGroupContainer) {
    return (d.groupId as string) ?? null;
  }
  return null;
}

export function useGroupDrag() {
  const drag = useRef<DragRef | null>(null);

  const onNodeDragStart = useCallback<OnNodeDrag<FlowNode>>((_e, node) => {
    const groupId = syntheticGroupId(node);
    if (groupId === null) return;
    // Defer the undo snapshot to the first real move so a click with no drag
    // doesn't pollute history.
    drag.current = {
      id: node.id,
      groupId,
      last: { x: node.position.x, y: node.position.y },
      snapshotted: false,
    };
  }, []);

  const onNodeDrag = useCallback<OnNodeDrag<FlowNode>>((_e, node) => {
    const ref = drag.current;
    if (!ref || ref.id !== node.id) return;
    const dx = node.position.x - ref.last.x;
    const dy = node.position.y - ref.last.y;
    if (dx === 0 && dy === 0) return;
    if (!ref.snapshotted) {
      useGraphStore.getState().snapshotForUndo();
      ref.snapshotted = true;
    }
    ref.last = { x: node.position.x, y: node.position.y };
    useGraphStore.getState().moveGroupBy(ref.groupId, dx, dy);
  }, []);

  const onNodeDragStop = useCallback<OnNodeDrag<FlowNode>>((_e, node) => {
    if (drag.current?.id === node.id) drag.current = null;
  }, []);

  return { onNodeDragStart, onNodeDrag, onNodeDragStop };
}
