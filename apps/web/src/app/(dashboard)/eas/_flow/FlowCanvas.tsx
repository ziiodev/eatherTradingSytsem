"use client";

/**
 * The React Flow canvas wrapper. Wires the Zustand graph store to React Flow
 * and renders the standard editor chrome (background grid).
 * Accepts drag-and-drop placement from the NodeSidebar, converting the drop
 * screen coordinates into flow coordinates via screenToFlowPosition.
 *
 * Must be rendered inside a <ReactFlowProvider> (see EditorShell) so the
 * useReactFlow instance is shared with the sidebar/inspector.
 */
import { useCallback, useMemo } from "react";
import {
  ReactFlow,
  Background,
  useReactFlow,
  type NodeTypes,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";

import { useGraphStore } from "../_stores/graphStore";
import { makeIsValidConnection } from "../_lib/graphTopology";
import { deriveRenderGraph } from "../_lib/groupDerivation";
import type { NodeType } from "../_types/graph";
import { CustomNode } from "./CustomNode";
import { GroupNode } from "./GroupNode";
import { GroupContainerNode } from "./GroupContainerNode";
import { NODE_DND_MIME } from "./NodeSidebar";
import { OrganizeNodesButton } from "./OrganizeNodesButton";
import { CenterViewButton } from "./CenterViewButton";
import { useGroupCanvasHandlers } from "./useGroupCanvasHandlers";
import { useGroupDrag } from "./useGroupDrag";

export function FlowCanvas() {
  const { nodes, edges, groups, onNodesChange, onEdgesChange, onConnect, addNode } =
    useGraphStore();
  const { screenToFlowPosition } = useReactFlow();

  // Render-only graph derived from the PERSISTED group flags: collapsed groups
  // become synthetic group-nodes with rerouted boundary edges; expanded groups
  // gain a render-only container frame. The CANONICAL store is never mutated —
  // change handlers below still operate on store.nodes/store.edges, and synthetic
  // nodes are filtered out before they could be written back.
  const render = useMemo(
    () => deriveRenderGraph(nodes, edges, groups),
    [nodes, edges, groups],
  );

  // Register node renderers: collapsed group node + expanded group container.
  // Memoized so React Flow does not warn about a new object identity per render.
  const nodeTypes = useMemo<NodeTypes>(
    () => ({
      custom: CustomNode,
      groupNode: GroupNode,
      groupContainer: GroupContainerNode,
    }),
    [],
  );

  // Connection guard mirroring the backend topology rules (out-degree <= 1, no
  // self-loop, per-type input capacity + combinator source/nesting rules). It is
  // target-type aware, so it needs to resolve a node id to its domain type.
  // Recomputed whenever nodes/edges change so it always sees the live graph;
  // onConnect enforces the same rules for programmatic calls.
  const isValidConnection = useMemo(() => {
    const typeOf = (id: string): NodeType | undefined =>
      nodes.find((n) => n.id === id)?.data.type;
    return makeIsValidConnection(edges, typeOf);
  }, [edges, nodes]);

  // Double-click (type-aware group expand vs node toggle) and selection-mirroring
  // handlers live in a small hook to keep this component lean.
  const { onNodeDoubleClick, onSelectionChange } = useGroupCanvasHandlers();

  // Group-drag handlers: dragging a synthetic group node / container translates
  // ALL its canonical member nodes by the same delta (and is undoable as one
  // step). Synthetic-id changes are filtered out by onNodesChange, so only the
  // member moves take effect.
  const { onNodeDragStart, onNodeDrag, onNodeDragStop } = useGroupDrag();

  const onDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
  }, []);

  const onDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      const type = e.dataTransfer.getData(NODE_DND_MIME) as NodeType;
      // Drops without our payload (e.g. from outside the app) are ignored.
      if (!type) return;
      const position = screenToFlowPosition({
        x: e.clientX,
        y: e.clientY,
      });
      addNode(type, position);
    },
    [screenToFlowPosition, addNode],
  );

  return (
    // Pin the canvas surface to the dedicated near-black token (gl-gray-1000,
    // #141318) so the lienzo reads clearly darker than the gl-gray-900
    // chrome/nodes that sit on top of it. React Flow's own
    // `--xy-background-color` defaults to `transparent`, so without this the
    // surface just inherited the (lighter) page background.
    <div className="bg-gl-gray-1000 h-full w-full">
      <ReactFlow
        // RENDER from the derived graph (collapsed groups -> synthetic nodes),
        // but mutate the CANONICAL store: applyNodeChanges/applyEdgeChanges in the
        // store ignore changes targeting ids not in nodes/edges, so synthetic
        // group-node deltas (e.g. ephemeral drag) never leak back into state.
        nodes={render.nodes}
        edges={render.edges}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onConnect={onConnect}
        onNodeDoubleClick={onNodeDoubleClick}
        onNodeDragStart={onNodeDragStart}
        onNodeDrag={onNodeDrag}
        onNodeDragStop={onNodeDragStop}
        onSelectionChange={onSelectionChange}
        isValidConnection={isValidConnection}
        onDragOver={onDragOver}
        onDrop={onDrop}
        nodeTypes={nodeTypes}
        // Double-click is reserved for expand/collapse, so suppress the default
        // double-click-to-zoom on the canvas (it would fight the node toggle).
        zoomOnDoubleClick={false}
        fitView
      >
        {/* Lift the dot color slightly so the grid stays legible against the
            darker gl-gray-1000 surface (default dots wash out at this depth). */}
        <Background color="#3a3942" />
        {/* Floating top-right control: center + zoom-to-fit all nodes. */}
        <CenterViewButton />
        {/* Floating bottom-center control: minimize + tidy all nodes. */}
        <OrganizeNodesButton />
      </ReactFlow>
    </div>
  );
}
