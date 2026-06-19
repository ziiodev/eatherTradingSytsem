/**
 * Ephemeral view-state for the group overlay (Zustand).
 *
 * Holds ONLY in-session selection state that must never leak into the persisted
 * strategy JSON or undo history:
 * - `selectedNodeIds` — the current canvas selection, the source the toolbar
 *   "Group" button reads to decide if it is enabled (>= 2 selected).
 *
 * NOTE: collapse/expand is NO LONGER tracked here — it moved into the PERSISTED
 * data model (`GroupMeta.collapsed`, see graphStore) so the view round-trips on
 * reload. This store is intentionally SEPARATE from graphStore, NOT persisted,
 * and NOT routed through undo.
 *
 * The selection is stored internally as a Set; the setter returns a new Set so
 * Zustand subscribers re-render on change.
 */
import { create } from "zustand";

interface GroupUiState {
  /** Ids of nodes currently selected on the canvas. */
  selectedNodeIds: Set<string>;

  /** Replace the current selection with `ids`. */
  setSelectedNodeIds: (ids: Iterable<string>) => void;
  /** Reset to the initial empty-selection state. */
  reset: () => void;
}

export const useGroupUiStore = create<GroupUiState>((set) => ({
  selectedNodeIds: new Set<string>(),

  setSelectedNodeIds: (ids) => set({ selectedNodeIds: new Set(ids) }),

  reset: () =>
    set((s) =>
      s.selectedNodeIds.size === 0
        ? s
        : { selectedNodeIds: new Set<string>() },
    ),
}));
