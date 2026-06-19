/**
 * Pure render-graph derivation for the group overlay.
 *
 * `deriveRenderGraph` maps the CANONICAL graph (`nodes`/`edges` + the
 * render-only `groups[]`) into the `nodes`/`edges` arrays React Flow actually
 * renders. It NEVER mutates the canonical arrays. Collapse is driven by the
 * PERSISTED `group.collapsed` flag (not an ephemeral set), so the view
 * round-trips on reload:
 * - COLLAPSED group  -> hide members + internal edges, emit ONE synthetic
 *   "groupNode" with rerouted boundary (proxy) edges.
 * - EXPANDED group   -> members stay verbatim, plus a render-only
 *   "groupContainer" frame is emitted BEHIND them (low zIndex) so the group is
 *   always visible.
 *
 * The function is pure and idempotent: same inputs -> structurally identical
 * outputs, with deterministic synthetic ids (see `groupPorts.ts`).
 */
import type { Node } from "@xyflow/react";
import type { FlowEdge, FlowNode, GroupMeta } from "../_types/graph";
import { classifyGroupEdges, type GroupPort } from "./groupPorts";

/** Marker + payload carried on a synthetic collapsed-group node's `data`. */
export interface GroupNodeData extends Record<string, unknown> {
  /** Discriminator so the canvas can route to the GroupNode component. */
  __isGroupNode: true;
  groupId: string;
  name: string;
  /** Number of member nodes hidden inside this group. */
  memberCount: number;
  /** Synthetic ports the group-node must render handles for. */
  ports: GroupPort[];
}

/** Marker + payload carried on a synthetic EXPANDED-group container's `data`. */
export interface GroupContainerData extends Record<string, unknown> {
  /** Discriminator so the canvas can route to the GroupContainerNode component. */
  __isGroupContainer: true;
  groupId: string;
  name: string;
  /** Rendered size of the frame (so the component fills its node box). */
  width: number;
  height: number;
}

/** The render-ready graph React Flow consumes. */
export interface RenderGraph {
  nodes: FlowNode[];
  edges: FlowEdge[];
}

/** Padding (px) around a member bounding box for the expanded container frame. */
const CONTAINER_PADDING = 32;
/** Extra top padding so the container header never overlaps a member node. */
const CONTAINER_HEADER = 36;
/** Fallback member box when a node hasn't been measured yet. */
const NODE_W = 184;
const NODE_H = 80;

/** Stable id for the synthetic node that stands in for a collapsed group. */
export const groupNodeId = (groupId: string): string => `group_${groupId}`;

/** Stable id for the render-only container framing an expanded group. */
export const groupContainerId = (groupId: string): string =>
  `groupContainer_${groupId}`;

/** Whether a group should render collapsed (default: expanded). */
const isCollapsed = (g: GroupMeta): boolean => g.collapsed === true;

/** Build the render-only container frame for one EXPANDED group. */
function containerFor(g: GroupMeta, canonicalNodes: FlowNode[]): FlowNode | null {
  const members = canonicalNodes.filter((n) => g.nodeIds.includes(n.id));
  if (members.length === 0) return null;
  const minX = Math.min(...members.map((n) => n.position.x));
  const minY = Math.min(...members.map((n) => n.position.y));
  const maxX = Math.max(
    ...members.map((n) => n.position.x + (n.measured?.width ?? NODE_W)),
  );
  const maxY = Math.max(
    ...members.map((n) => n.position.y + (n.measured?.height ?? NODE_H)),
  );
  const width = maxX - minX + CONTAINER_PADDING * 2;
  const height = maxY - minY + CONTAINER_PADDING * 2 + CONTAINER_HEADER;
  const data: GroupContainerData = {
    __isGroupContainer: true,
    groupId: g.id,
    name: g.name,
    width,
    height,
  };
  const synthetic: Node<GroupContainerData> = {
    id: groupContainerId(g.id),
    type: "groupContainer",
    position: {
      x: minX - CONTAINER_PADDING,
      y: minY - CONTAINER_PADDING - CONTAINER_HEADER,
    },
    width,
    height,
    // Render BEHIND members and never let the frame steal pointer events from
    // the member nodes inside; the component itself re-enables events on its
    // interactive header/border only.
    zIndex: -1,
    selectable: false,
    // Draggable so the whole group can be MOVED; the drag handle (header label)
    // restricts what starts the drag so the action buttons stay clickable.
    draggable: true,
    dragHandle: ".group-drag-handle",
    data,
  };
  return synthetic as unknown as FlowNode;
}

/** Build the synthetic collapsed-group node for one COLLAPSED group. */
function collapsedNodeFor(
  g: GroupMeta,
  canonicalNodes: FlowNode[],
  canonicalEdges: FlowEdge[],
): { node: FlowNode; internalEdgeIds: Set<string>; proxies: FlowEdge[] } {
  const nodeId = groupNodeId(g.id);
  const cls = classifyGroupEdges(nodeId, g.nodeIds, canonicalEdges);
  const proxies: FlowEdge[] = [
    ...cls.inbound.map((p) => p.edge),
    ...cls.outbound.map((p) => p.edge),
  ];
  const members = canonicalNodes.filter((n) => g.nodeIds.includes(n.id));
  // Position the synthetic node at the CENTER of the members' bounding box so
  // it appears where the cluster was. Falls back to origin if none present.
  const center =
    members.length > 0
      ? {
          x: members.reduce((s, n) => s + n.position.x, 0) / members.length,
          y: members.reduce((s, n) => s + n.position.y, 0) / members.length,
        }
      : { x: 0, y: 0 };
  const data: GroupNodeData = {
    __isGroupNode: true,
    groupId: g.id,
    name: g.name,
    memberCount: g.nodeIds.length,
    ports: cls.ports,
  };
  const synthetic: Node<GroupNodeData> = {
    id: nodeId,
    // NOTE: must NOT be "group" — that is a RESERVED React Flow built-in node
    // type whose default styles + semantics break the synthetic node.
    type: "groupNode",
    position: center,
    // Seed dimensions so the node lays out on the first frame instead of
    // staying hidden until the ResizeObserver measures it.
    initialWidth: 184,
    initialHeight: 64,
    // Draggable by its whole body so the collapsed group can be MOVED; its own
    // action buttons stopPropagation on pointerDown so they never start a drag.
    draggable: true,
    data,
  };
  return { node: synthetic as unknown as FlowNode, internalEdgeIds: cls.internalEdgeIds, proxies };
}

/**
 * Derive the React Flow render graph from the PERSISTED group flags. Expanded
 * groups keep their members and gain a container frame; collapsed groups
 * collapse to one synthetic node with rerouted boundary edges.
 */
export function deriveRenderGraph(
  canonicalNodes: FlowNode[],
  canonicalEdges: FlowEdge[],
  groups: GroupMeta[],
): RenderGraph {
  const collapsed = groups.filter(isCollapsed);
  const expanded = groups.filter((g) => !isCollapsed(g));

  // Containers for expanded groups (render-only; placed FIRST so they paint
  // behind the member nodes that follow).
  const containers: FlowNode[] = [];
  for (const g of expanded) {
    const c = containerFor(g, canonicalNodes);
    if (c) containers.push(c);
  }

  if (collapsed.length === 0) {
    if (containers.length === 0) {
      // Fast path: nothing to overlay -> render canonical arrays verbatim.
      return { nodes: canonicalNodes, edges: canonicalEdges };
    }
    return { nodes: [...containers, ...canonicalNodes], edges: canonicalEdges };
  }

  // Member id -> the collapsed group it belongs to (at most one, by invariant).
  const memberToGroup = new Map<string, GroupMeta>();
  for (const g of collapsed)
    for (const id of g.nodeIds) memberToGroup.set(id, g);

  const hiddenEdgeIds = new Set<string>();
  const proxyEdges: FlowEdge[] = [];
  const groupNodes: FlowNode[] = [];

  for (const g of collapsed) {
    const { node, internalEdgeIds, proxies } = collapsedNodeFor(
      g,
      canonicalNodes,
      canonicalEdges,
    );
    for (const id of internalEdgeIds) hiddenEdgeIds.add(id);
    proxyEdges.push(...proxies);
    groupNodes.push(node);
  }

  // Drop hidden member nodes; keep everything else (expanded members, loose).
  const visibleCanonical = canonicalNodes.filter(
    (n) => !memberToGroup.has(n.id),
  );
  const nodes: FlowNode[] = [
    ...containers,
    ...visibleCanonical,
    ...groupNodes,
  ];

  // Keep edges that aren't internal and weren't rerouted; append proxies.
  const reroutedIds = new Set(
    proxyEdges.map((e) => e.id.replace(/^proxy_/, "")),
  );
  const edges: FlowEdge[] = canonicalEdges.filter(
    (e) => !hiddenEdgeIds.has(e.id) && !reroutedIds.has(e.id),
  );
  edges.push(...proxyEdges);

  return { nodes, edges };
}
