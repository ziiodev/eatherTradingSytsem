/**
 * Ephemeral palette UI state for the node sidebar (Zustand).
 *
 * Holds ONLY which palette GROUPS / SUBGROUPS are currently EXPANDED (their
 * header chevron toggles the body). This state is intentionally:
 * - SEPARATE from `graphStore` — it never touches `node.data` or the graph.
 * - NOT persisted — a freshly loaded strategy starts with everything collapsed.
 * - NOT routed through `commit()`/undo — expanding a group is not an undoable
 *   graph mutation.
 *
 * Stored internally as a `Set<string>` of EXPANDED group/subgroup ids (the ids
 * come from `lib/paletteGroups.ts` and are globally unique, e.g. "actions" and
 * "actions-close"). An id absent from the set means COLLAPSED, so the default
 * empty set = everything collapsed. Actions return new Set instances so Zustand
 * subscribers re-render on change.
 */
import { create } from "zustand";

interface PaletteUiState {
  /** Ids of palette groups/subgroups currently rendered expanded. */
  expanded: Set<string>;

  /** Whether a given group/subgroup id is currently expanded. */
  isExpanded: (id: string) => boolean;
  /** Toggle the expanded state of a group/subgroup id. */
  toggle: (id: string) => void;
  /** Reset to the initial all-collapsed state (used on strategy load). */
  reset: () => void;
}

export const usePaletteUiStore = create<PaletteUiState>((set, get) => ({
  expanded: new Set<string>(),

  isExpanded: (id) => get().expanded.has(id),

  toggle: (id) =>
    set((s) => {
      const next = new Set(s.expanded);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return { expanded: next };
    }),

  reset: () =>
    set((s) => (s.expanded.size === 0 ? s : { expanded: new Set<string>() })),
}));
