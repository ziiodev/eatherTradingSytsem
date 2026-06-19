"use client";

/**
 * Canvas event handlers for the group overlay, factored out of FlowCanvas to
 * keep that component small.
 *
 * - `onNodeDoubleClick` is type-aware: on a synthetic collapsed group-node
 *   (`data.__isGroupNode`) it EXPANDS the group; on a regular custom node it
 *   keeps the existing compact/expanded toggle.
 * - `onSelectionChange` mirrors the canvas selection into groupUiStore (excluding
 *   synthetic group-nodes) so the toolbar Group button can gate on >= 2 ids.
 *
 * Both read store actions off getState() so the canvas never subscribes to UI
 * state, matching the existing double-click pattern and avoiding re-render loops.
 */
import { useCallback } from "react";
import type { Node, OnSelectionChangeParams } from "@xyflow/react";
import { useNodeUiStore } from "../_stores/nodeUiStore";
import { useGroupUiStore } from "../_stores/groupUiStore";
import { useGraphStore } from "../_stores/graphStore";
import type {
  GroupContainerData,
  GroupNodeData,
} from "../_lib/groupDerivation";

export function useGroupCanvasHandlers() {
  const onNodeDoubleClick = useCallback((_e: React.MouseEvent, node: Node) => {
    const groupData = node.data as Partial<GroupNodeData>;
    if (groupData.__isGroupNode) {
      // Collapsed group node -> expand it via the persisted flag.
      useGraphStore.getState().setGroupCollapsed(groupData.groupId as string, false);
      return;
    }
    // Containers are render-only frames; ignore double-clicks on them so the
    // gesture only ever toggles real nodes or expands a collapsed group.
    if ((node.data as Partial<GroupContainerData>).__isGroupContainer) return;
    useNodeUiStore.getState().toggleExpanded(node.id);
  }, []);

  const onSelectionChange = useCallback((params: OnSelectionChangeParams) => {
    const ids = params.nodes
      .filter((n) => {
        const d = n.data as Partial<GroupNodeData & GroupContainerData>;
        return !d.__isGroupNode && !d.__isGroupContainer;
      })
      .map((n) => n.id);
    useGroupUiStore.getState().setSelectedNodeIds(ids);
  }, []);

  return { onNodeDoubleClick, onSelectionChange };
}
