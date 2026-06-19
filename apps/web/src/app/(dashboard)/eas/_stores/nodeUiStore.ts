/**
 * Ephemeral per-node UI state for the visual editor (Zustand).
 *
 * Holds ONLY which nodes are currently EXPANDED (double-click toggles the
 * compact/expanded body). This state is intentionally:
 * - SEPARATE from `graphStore` — it never touches `node.data`.
 * - NOT persisted — a freshly loaded strategy starts all-compact.
 * - NOT routed through `commit()`/undo — expanding/collapsing is not an
 *   undoable graph mutation.
 *
 * Stored internally as a `Set<string>` of expanded node ids; actions return new
 * Set instances so Zustand subscribers re-render on change.
 */
import { create } from "zustand";

interface NodeUiState {
  /** Ids of nodes currently rendered in their expanded state. */
  expanded: Set<string>;

  /** Whether a given node id is currently expanded. */
  isExpanded: (id: string) => boolean;
  /** Toggle the expanded state of a node id. */
  toggleExpanded: (id: string) => void;
  /** Force a node id back to compact (drops it from the expanded set). */
  collapse: (id: string) => void;
  /** Collapse every node (clears the set) without discarding the store. */
  collapseAll: () => void;
  /** Reset to the initial all-compact state (used on strategy load). */
  reset: () => void;
}

export const useNodeUiStore = create<NodeUiState>((set, get) => ({
  expanded: new Set<string>(),

  isExpanded: (id) => get().expanded.has(id),

  toggleExpanded: (id) =>
    set((s) => {
      const next = new Set(s.expanded);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return { expanded: next };
    }),

  collapse: (id) =>
    set((s) => {
      if (!s.expanded.has(id)) return s;
      const next = new Set(s.expanded);
      next.delete(id);
      return { expanded: next };
    }),

  collapseAll: () =>
    set((s) => (s.expanded.size === 0 ? s : { expanded: new Set<string>() })),

  reset: () =>
    set((s) => (s.expanded.size === 0 ? s : { expanded: new Set<string>() })),
}));
