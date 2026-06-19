"use client";

/**
 * Renders one collapsible palette SUBGROUP nested inside a group's body: an
 * indented, clickable sub-header (small chevron + muted Spanish label), then its
 * body. A non-empty subgroup renders its draggable rows; an empty subgroup
 * renders a single non-draggable "Próximamente" placeholder row.
 *
 * Expand state is EPHEMERAL UI state owned by `paletteUiStore` (default
 * collapsed — the subgroup id is simply absent from the expanded set). It is
 * not persisted, not in node.data, and is reset to all-collapsed on strategy
 * load. While `searching` is true the body is forced expanded (transient view
 * override; the store is never mutated by search), and empty/placeholder rows
 * are suppressed since the filter only passes subgroups that have hits. The
 * sub-header is a real <button> with `aria-expanded` for keyboard
 * accessibility — visually subordinate to the group header (smaller chevron,
 * indented/muted styling).
 */
import { ChevronDown, ChevronRight } from "lucide-react";
import type { PaletteSubgroup as PaletteSubgroupType } from "../_lib/paletteGroups";
import { usePaletteUiStore } from "../_stores/paletteUiStore";
import { PaletteItem } from "./PaletteItem";
import { PalettePlaceholderRow } from "./PalettePlaceholderRow";

export function PaletteSubgroup({
  subgroup,
  searching = false,
}: {
  subgroup: PaletteSubgroupType;
  searching?: boolean;
}) {
  const Icon = subgroup.icon;
  // Ephemeral expand state keyed by subgroup id; absent = collapsed. Search
  // wins: while searching the body is shown regardless of the stored state.
  const storeExpanded = usePaletteUiStore((s) => s.expanded.has(subgroup.id));
  const toggle = usePaletteUiStore((s) => s.toggle);
  const expanded = searching || storeExpanded;
  const Chevron = expanded ? ChevronDown : ChevronRight;
  const bodyId = `palette-subgroup-body-${subgroup.id}`;

  return (
    <div className="ml-2 space-y-1">
      <button
        type="button"
        aria-expanded={expanded}
        aria-controls={bodyId}
        onClick={() => toggle(subgroup.id)}
        className="text-muted-foreground hover:text-foreground mt-1 flex w-full items-center gap-1 text-[0.65rem] font-medium"
      >
        {Icon ? <Icon className="h-3 w-3 shrink-0" aria-hidden /> : null}
        <span>{subgroup.label}</span>
        <Chevron className="ml-auto h-3 w-3 shrink-0" aria-hidden />
      </button>

      {expanded ? (
        <div id={bodyId} className="space-y-1">
          {/* Empty subgroup -> non-draggable "Próximamente" row, but NOT while
              searching (the filter only passes subgroups that have hits). */}
          {subgroup.items.length === 0 ? (
            searching ? null : (
              <PalettePlaceholderRow />
            )
          ) : (
            subgroup.items.map((type) => <PaletteItem key={type} type={type} />)
          )}
        </div>
      ) : null}
    </div>
  );
}
