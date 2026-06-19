/**
 * Global graph state for the visual editor (Zustand).
 * Holds the React Flow nodes/edges and exposes the change handlers React Flow
 * expects, plus helpers to add nodes and (de)serialize the graph for the API.
 *
 * In-session undo/redo is hand-rolled directly in this store (no zundo, no
 * middleware): `past[]`/`future[]` hold deep-cloned full-graph snapshots, and a
 * private `commit()` snapshots the PRESENT state BEFORE a mutation. History is
 * in-session only and is reset whenever the graph is hydrated/replaced.
 */
import { create } from "zustand";
import {
  addEdge,
  applyEdgeChanges,
  applyNodeChanges,
  type Connection,
  type EdgeChange,
  type NodeChange,
} from "@xyflow/react";
import type {
  FlowEdge,
  FlowNode,
  GroupMeta,
  NodeData,
  NodeType,
  SerializedGraph,
} from "../_types/graph";
import { defaultDataFor } from "../_lib/nodeParamSchemas";
import { isValidConnection } from "../_lib/graphTopology";
import { useNodeUiStore } from "./nodeUiStore";
import {
  addGroup,
  pruneGroupsForNodes,
  renameGroupIn,
  removeGroupIn,
} from "../_lib/groupOps";

/**
 * A full-graph history snapshot. Groups travel WITH nodes/edges as one
 * coordinated unit so undo/redo restore membership and names atomically.
 */
type GraphSnapshot = {
  nodes: FlowNode[];
  edges: FlowEdge[];
  groups: GroupMeta[];
};

/** Max undoable steps retained; oldest entries are dropped past this bound. */
const HISTORY_LIMIT = 50;

interface GraphState {
  nodes: FlowNode[];
  edges: FlowEdge[];
  /**
   * Render-only authoring groups (additive overlay). Persisted + undoable, but
   * never part of canonical nodes/edges. Defaults to [] for old strategies.
   */
  groups: GroupMeta[];

  // In-session history (not persisted across reload/navigation).
  past: GraphSnapshot[];
  future: GraphSnapshot[];
  canUndo: boolean;
  canRedo: boolean;
  /**
   * Tracks the last coalescible edit so consecutive edits to the SAME node +
   * field collapse into a single undo step. Reset by any other mutation.
   */
  lastEdit: { id: string; field: string } | null;

  // React Flow change handlers.
  onNodesChange: (changes: NodeChange<FlowNode>[]) => void;
  onEdgesChange: (changes: EdgeChange<FlowEdge>[]) => void;
  onConnect: (connection: Connection) => void;

  // Editor actions.
  addNode: (type: NodeType, position: { x: number; y: number }) => void;
  /**
   * Clone an existing node (data + type) under a fresh id, offset slightly so
   * it doesn't sit exactly on top of the original. Committed as ONE undo step.
   * The clone lands COMPACT (never added to nodeUiStore's expanded set).
   */
  duplicateNode: (id: string) => void;
  updateNodeData: (id: string, partial: Partial<NodeData>) => void;
  removeNode: (id: string) => void;
  /**
   * Tidy up: reposition every node onto a regular grid, ordered by their current
   * reading order (top-to-bottom, then left-to-right) so the rearrangement feels
   * stable. Committed as ONE undoable step. Pairs with collapseAll() in the UI so
   * the canvas reads as a clean grid of compact nodes.
   */
  arrangeInGrid: () => void;
  setGraph: (graph: SerializedGraph) => void;
  hydrate: (graph: SerializedGraph) => void;
  /**
   * Replace nodes+edges with a server-computed merged graph (AI generate),
   * committing it as ONE undoable step so the pre-generate canvas lands on
   * `past[]` and the user can Undo the whole generation. Unlike setGraph/hydrate
   * this PRESERVES history.
   */
  applyMergedGraph: (graph: SerializedGraph) => void;
  /**
   * Add a single AI-suggested node additively (undoable, like addNode), mapping
   * the backend node-spec shape onto the store's node shape.
   */
  addNodeFromSpec: (spec: NodeSpec) => void;
  /**
   * Create a named group from `nodeIds` and return its new id, or `null` if the
   * request is rejected (empty name, or fewer than 2 distinct members). A node
   * belongs to at most one group: the ids are removed from any other group, and
   * groups left below 2 members are dissolved — all in ONE undoable step.
   */
  createGroup: (nodeIds: string[], name: string) => string | null;
  /** Rename a group (undoable). Blank names are ignored (no-op, no history). */
  renameGroup: (id: string, name: string) => void;
  /** Dissolve a group; member nodes/edges are untouched. ONE undoable step. */
  ungroup: (id: string) => void;
  /**
   * Set a group's persisted `collapsed` flag. This is a VIEW toggle, not a graph
   * mutation: it updates groups[] WITHOUT a commit()/undo entry, but IS
   * serialized (toJSON includes groups) so it round-trips on reload.
   */
  setGroupCollapsed: (id: string, collapsed: boolean) => void;
  /** Flip a group's persisted `collapsed` flag (also non-undoable, serialized). */
  toggleGroupCollapsed: (id: string) => void;
  /**
   * Move every member node of `groupId` by `(dx, dy)` WITHOUT a commit()/history
   * entry — used during a live drag of the synthetic group node / container so
   * the members follow the cursor. No-op when both deltas are 0. The synthetic
   * node's DERIVED position shifts by the same delta, so React Flow's controlled
   * position matches its internal drag position (no snap-back).
   */
  moveGroupBy: (groupId: string, dx: number, dy: number) => void;
  /**
   * Push the PRESENT state onto the undo stack as ONE step (reuses commit()).
   * Called once at the start of a group drag so the whole drag is undoable as a
   * single entry, restoring the pre-drag member positions.
   */
  snapshotForUndo: () => void;
  undo: () => void;
  redo: () => void;
  toJSON: () => SerializedGraph;
}

/**
 * The permissive node-spec shape the backend returns from suggest-next. Mirrors
 * the canonical node dict; we only rely on `data` (carrying `type` + flat
 * params) and an optional `position`, and always re-key the id locally.
 */
export interface NodeSpec {
  id?: string;
  type?: string;
  position?: { x?: number; y?: number };
  data?: Record<string, unknown>;
  [key: string]: unknown;
}

/** Generate a reasonably-unique id without pulling in a uuid dependency. */
const newId = () => `n_${Math.random().toString(36).slice(2, 10)}`;

/** Deep clone a graph so snapshots can't be mutated by later React Flow ops. */
const cloneGraph = (
  nodes: FlowNode[],
  edges: FlowEdge[],
  groups: GroupMeta[],
): GraphSnapshot => structuredClone({ nodes, edges, groups });

export const useGraphStore = create<GraphState>((set, get) => {
  /**
   * Snapshot the PRESENT graph onto `past` before a mutation runs, drop the
   * redo branch, and bound the stack. Returns the new history fields to merge
   * into the same `set` that applies the mutation (so history + state move
   * together atomically).
   */
  const commit = (): Pick<
    GraphState,
    "past" | "future" | "canUndo" | "canRedo"
  > => {
    const { nodes, edges, groups, past } = get();
    const next = [...past, cloneGraph(nodes, edges, groups)];
    // Bound the history: drop the oldest entries past the cap.
    if (next.length > HISTORY_LIMIT)
      next.splice(0, next.length - HISTORY_LIMIT);
    return { past: next, future: [], canUndo: true, canRedo: false };
  };

  /** Fresh, empty history (used on hydrate / setGraph). */
  const emptyHistory = (): Pick<
    GraphState,
    "past" | "future" | "canUndo" | "canRedo" | "lastEdit"
  > => ({
    past: [],
    future: [],
    canUndo: false,
    canRedo: false,
    lastEdit: null,
  });

  return {
    nodes: [],
    edges: [],
    groups: [],

    past: [],
    future: [],
    canUndo: false,
    canRedo: false,
    lastEdit: null,

    onNodesChange: (changes) =>
      set((s) => {
        // The canvas renders a DERIVED graph that can contain synthetic
        // group-nodes AND group containers (ids like `group_*` / `groupContainer_*`
        // that are NOT present in canonical `nodes`). React Flow emits changes
        // (select/position/dimensions) for those too. We must IGNORE them:
        // applying them would return a brand-new `nodes` array on every measure,
        // re-deriving the render graph and leaving the synthetic node stuck
        // `visibility:hidden`. Keep only changes targeting a canonical node (plus
        // adds); since synthetic ids are never in `s.nodes`, this filter drops them.
        const canonicalIds = new Set(s.nodes.map((n) => n.id));
        const relevant = changes.filter(
          (c) => c.type === "add" || canonicalIds.has(c.id),
        );
        // Nothing canonical changed (e.g. ephemeral synthetic group-node delta):
        // return the SAME state so the store reference is stable and no churn.
        if (relevant.length === 0) return s;

        // Snapshot exactly once per meaningful node mutation: a removal, or the
        // END of a drag (dragging===false). IGNORE pure selection, dimension/
        // measured changes, and intermediate drag deltas (dragging===true).
        const meaningful = relevant.some(
          (c) =>
            c.type === "remove" ||
            (c.type === "position" && c.dragging === false),
        );
        const history = meaningful ? commit() : null;
        // Prune any removed node ids out of groups[] within the SAME step, and
        // dissolve groups that fall below 2 members.
        const removedIds = relevant
          .filter((c) => c.type === "remove")
          .map((c) => c.id);
        const groups =
          removedIds.length > 0
            ? pruneGroupsForNodes(s.groups, removedIds)
            : s.groups;
        return {
          nodes: applyNodeChanges(relevant, s.nodes),
          ...(groups !== s.groups ? { groups } : {}),
          ...(history ?? {}),
          // Any structural change breaks edit-coalescing.
          ...(meaningful ? { lastEdit: null } : {}),
        };
      }),

    onEdgesChange: (changes) =>
      set((s) => {
        // Only edge REMOVE is a meaningful undo step here (adds arrive via
        // onConnect); ignore select.
        const meaningful = changes.some((c) => c.type === "remove");
        const history = meaningful ? commit() : null;
        return {
          edges: applyEdgeChanges(changes, s.edges),
          ...(history ?? {}),
          ...(meaningful ? { lastEdit: null } : {}),
        };
      }),

    onConnect: (connection) =>
      set((s) => {
        // Server-parity safety net: reject connections that would break the
        // topology rules (out-degree <= 1, no self-loop, per-type input capacity
        // + combinator source/nesting rules). React Flow also wires this via the
        // isValidConnection prop, but guarding here protects programmatic calls.
        // The guard is target-type aware, so pass a node-type resolver.
        const typeOf = (id: string): NodeType | undefined =>
          s.nodes.find((n) => n.id === id)?.data.type;
        if (!isValidConnection(connection, s.edges, typeOf)) return s;
        return {
          edges: addEdge(connection, s.edges),
          ...commit(),
          lastEdit: null,
        };
      }),

    addNode: (type, position) =>
      set((s) => ({
        nodes: [
          ...s.nodes,
          {
            id: newId(),
            type: "custom",
            position,
            // Seed codegen-matching default flat params so a freshly placed node
            // already carries data identical to what codegen expects.
            data: defaultDataFor(type),
          },
        ],
        ...commit(),
        lastEdit: null,
      })),

    // Clone a node: deep-copy its data, generate a NEW id, offset the position
    // so the copy is visible, and insert as a single undoable step. The clone
    // lands COMPACT (we never touch nodeUiStore here), matching addNode.
    duplicateNode: (id) =>
      set((s) => {
        const original = s.nodes.find((n) => n.id === id);
        if (!original) return s;
        const clone: FlowNode = {
          id: newId(),
          type: original.type,
          position: {
            x: original.position.x + 40,
            y: original.position.y + 40,
          },
          // Deep-copy data so edits to the clone never mutate the original.
          data: structuredClone(original.data),
        };
        return {
          nodes: [...s.nodes, clone],
          ...commit(),
          lastEdit: null,
        };
      }),

    // Shallow-merge flat keys into a single node's data (immutable update).
    // Consecutive edits to the SAME node + field coalesce into one undo step:
    // only the FIRST edit of a run snapshots; same-field follow-ups don't.
    updateNodeData: (id, partial) =>
      set((s) => {
        const keys = Object.keys(partial);
        const field = keys.length === 1 ? (keys[0] ?? null) : null;
        const coalesce =
          field !== null &&
          s.lastEdit !== null &&
          s.lastEdit.id === id &&
          s.lastEdit.field === field;
        const history = coalesce ? null : commit();
        return {
          nodes: s.nodes.map((n) =>
            n.id === id ? { ...n, data: { ...n.data, ...partial } } : n,
          ),
          ...(history ?? {}),
          // Remember this edit so the next same-field edit coalesces; a multi-
          // key update isn't coalescible, so clear the marker.
          lastEdit: field !== null ? { id, field } : null,
        };
      }),

    // Remove a node and any edges connected to it.
    removeNode: (id) => {
      // Leak guard: drop any ephemeral expand state for the removed node.
      // Called via getState() INSIDE the action body (not a top-level import-
      // time call) to avoid a circular-import initialization hazard.
      useNodeUiStore.getState().collapse(id);
      set((s) => ({
        nodes: s.nodes.filter((n) => n.id !== id),
        edges: s.edges.filter((e) => e.source !== id && e.target !== id),
        // Drop the removed id from groups and dissolve too-small groups, all in
        // this single undoable step.
        groups: pruneGroupsForNodes(s.groups, [id]),
        ...commit(),
        lastEdit: null,
      }));
    },

    // Tidy the canvas: snap every node onto a regular grid. Compact nodes are a
    // 72px square, so a 130×120 cell leaves room for handles, the bottom action
    // bar, and the connecting edges. Nodes are ordered by their CURRENT position
    // (y then x) so the grid roughly preserves what the user already sees, and a
    // near-square column count keeps the block tidy rather than one long row.
    arrangeInGrid: () =>
      set((s) => {
        if (s.nodes.length === 0) return s;
        const CELL_W = 130;
        const CELL_H = 120;
        const cols = Math.ceil(Math.sqrt(s.nodes.length));
        const ordered = [...s.nodes].sort(
          (a, b) => a.position.y - b.position.y || a.position.x - b.position.x,
        );
        const positionById = new Map<string, { x: number; y: number }>();
        ordered.forEach((n, i) => {
          positionById.set(n.id, {
            x: (i % cols) * CELL_W,
            y: Math.floor(i / cols) * CELL_H,
          });
        });
        return {
          nodes: s.nodes.map((n) => ({
            ...n,
            position: positionById.get(n.id) ?? n.position,
          })),
          ...commit(),
          lastEdit: null,
        };
      }),

    // Replace the whole graph (e.g. after loading a strategy from the backend).
    // Defensively resets history so loaded state becomes the history floor.
    // `groups` defaults to [] for strategies saved before grouping existed.
    setGraph: (graph) =>
      set({
        nodes: graph.nodes,
        edges: graph.edges,
        groups: graph.groups ?? [],
        ...emptyHistory(),
      }),

    // Hydrate from a loaded/saved strategy: set the graph AND reset history so
    // the loaded state is the floor — nothing before it is undoable.
    hydrate: (graph) =>
      set({
        nodes: graph.nodes,
        edges: graph.edges,
        groups: graph.groups ?? [],
        ...emptyHistory(),
      }),

    // Apply an AI-generated merged graph as ONE undoable step. We commit() the
    // PRESENT canvas onto past[] (clearing the redo branch) and then swap in the
    // server's merged nodes/edges — so a single Undo restores the pre-generate
    // canvas. Deliberately NOT setGraph/hydrate, which would reset history.
    applyMergedGraph: (graph) =>
      set(() => ({
        nodes: graph.nodes,
        edges: graph.edges,
        groups: graph.groups ?? [],
        ...commit(),
        lastEdit: null,
      })),

    // Add a single AI-suggested node additively (undoable, like addNode).
    // Re-keys the id locally to avoid collisions, seeds codegen-matching
    // defaults for the resolved type, then overlays the spec's flat params so
    // the node carries exactly what the model proposed.
    addNodeFromSpec: (spec) =>
      set((s) => {
        const data = (spec.data ?? {}) as Record<string, unknown>;
        const type = (data.type as NodeType) ?? "Log";
        // Start from registry defaults, then overlay the model's flat params so
        // any field it omitted still has a codegen-valid default.
        const merged: NodeData = { ...defaultDataFor(type), ...data, type };
        const position = {
          x: typeof spec.position?.x === "number" ? spec.position.x : 0,
          y: typeof spec.position?.y === "number" ? spec.position.y : 0,
        };
        return {
          nodes: [
            ...s.nodes,
            { id: newId(), type: "custom", position, data: merged },
          ],
          ...commit(),
          lastEdit: null,
        };
      }),

    // Create a named group from `nodeIds`. Returns the new group id, or null if
    // rejected (blank name / fewer than 2 distinct members). The membership
    // change is committed as ONE undoable step ONLY when it actually creates a
    // group, so rejected calls leave history untouched. We capture the new id
    // outside `set` so it can be returned to the caller.
    createGroup: (nodeIds, name) => {
      let createdId: string | null = null;
      set((s) => {
        const { groups, id } = addGroup(s.groups, nodeIds, name);
        createdId = id;
        if (id === null) return s; // Rejected: no state/history change.
        // New groups land COLLAPSED (the toolbar collapses on create). The flag
        // is persisted, so the collapse round-trips on reload.
        const withCollapsed = groups.map((g) =>
          g.id === id ? { ...g, collapsed: true } : g,
        );
        return { groups: withCollapsed, ...commit(), lastEdit: null };
      });
      return createdId;
    },

    // Rename a group (undoable). Blank/whitespace names are ignored: the helper
    // returns the same array reference, so we skip the commit and history entry.
    renameGroup: (id, name) =>
      set((s) => {
        const groups = renameGroupIn(s.groups, id, name);
        if (groups === s.groups) return s;
        return { groups, ...commit(), lastEdit: null };
      }),

    // Dissolve a group. Member nodes/edges are left untouched in canonical
    // state; only the overlay entry is removed. ONE undoable step (no-op if the
    // id doesn't exist, leaving history clean).
    ungroup: (id) =>
      set((s) => {
        const groups = removeGroupIn(s.groups, id);
        if (groups.length === s.groups.length) return s;
        return { groups, ...commit(), lastEdit: null };
      }),

    // Set a group's persisted `collapsed` flag. Collapse is a VIEW toggle, not
    // an undoable graph mutation: we update groups[] WITHOUT commit() (no undo
    // entry) so the user's history isn't polluted with view churn — but the flag
    // IS serialized via toJSON, so it round-trips on reload. No-op if unchanged.
    setGroupCollapsed: (id, collapsed) =>
      set((s) => {
        const target = s.groups.find((g) => g.id === id);
        if (!target || (target.collapsed ?? false) === collapsed) return s;
        return {
          groups: s.groups.map((g) =>
            g.id === id ? { ...g, collapsed } : g,
          ),
        };
      }),

    // Flip a group's persisted `collapsed` flag (non-undoable, serialized).
    toggleGroupCollapsed: (id) =>
      set((s) => {
        const target = s.groups.find((g) => g.id === id);
        if (!target) return s;
        const next = !(target.collapsed ?? false);
        return {
          groups: s.groups.map((g) =>
            g.id === id ? { ...g, collapsed: next } : g,
          ),
        };
      }),

    // Move every member of a group by (dx, dy) during a LIVE drag. No commit()
    // here — undo is handled by a single snapshotForUndo() at drag start — so the
    // continuous deltas don't each create a history entry. No-op at (0, 0).
    moveGroupBy: (groupId, dx, dy) =>
      set((s) => {
        if (dx === 0 && dy === 0) return s;
        const group = s.groups.find((g) => g.id === groupId);
        if (!group) return s;
        const members = new Set(group.nodeIds);
        return {
          nodes: s.nodes.map((n) =>
            members.has(n.id)
              ? {
                  ...n,
                  position: { x: n.position.x + dx, y: n.position.y + dy },
                }
              : n,
          ),
        };
      }),

    // Snapshot the PRESENT state onto the undo stack as one step. Used at the
    // start of a group drag so the subsequent moveGroupBy churn is undoable as a
    // single entry (commit() already clears the redo branch and bounds history).
    snapshotForUndo: () => set(() => ({ ...commit() })),

    // Reverse the most recent meaningful change: push PRESENT onto future,
    // pop the latest snapshot off past and make it present.
    undo: () =>
      set((s) => {
        if (s.past.length === 0) return {};
        const previous = s.past[s.past.length - 1];
        if (!previous) return {};
        const newPast = s.past.slice(0, -1);
        return {
          nodes: previous.nodes,
          edges: previous.edges,
          groups: previous.groups ?? [],
          past: newPast,
          future: [...s.future, cloneGraph(s.nodes, s.edges, s.groups)],
          canUndo: newPast.length > 0,
          canRedo: true,
          lastEdit: null,
        };
      }),

    // Re-apply the most recently undone change.
    redo: () =>
      set((s) => {
        if (s.future.length === 0) return {};
        const next = s.future[s.future.length - 1];
        if (!next) return {};
        const newFuture = s.future.slice(0, -1);
        return {
          nodes: next.nodes,
          edges: next.edges,
          groups: next.groups ?? [],
          past: [...s.past, cloneGraph(s.nodes, s.edges, s.groups)],
          future: newFuture,
          canUndo: true,
          canRedo: newFuture.length > 0,
          lastEdit: null,
        };
      }),

    // Snapshot for persistence / codegen. `groups` is included only when
    // non-empty so strategies without groups serialize exactly as before
    // (additive, backward-compatible) and codegen stays byte-identical.
    toJSON: () => {
      const { nodes, edges, groups } = get();
      return groups.length > 0
        ? { nodes, edges, groups }
        : { nodes, edges };
    },
  };
});
