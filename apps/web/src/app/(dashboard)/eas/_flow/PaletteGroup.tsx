"use client";

/**
 * Renders one collapsible palette group: a clickable header (chevron + optional
 * group icon + muted uppercase Spanish label), then its body. Placeholder groups
 * show a single non-draggable "Próximamente" row; otherwise direct `items`
 * render as draggable rows and each `subgroup` renders as its own collapsible
 * <PaletteSubgroup/> (an indented sub-header + its rows). Collapsing this group
 * hides the whole body, including every subgroup.
 *
 * Expand state is EPHEMERAL UI state owned by `paletteUiStore` (default
 * collapsed — the group id is simply absent from the expanded set). It is not
 * persisted, not in node.data, and is reset to all-collapsed on strategy load.
 * While `searching` is true, the body is forced expanded (a transient view
 * override) so search hits are visible; the store is never mutated by search.
 * The header is a real <button> with `aria-expanded` for keyboard
 * accessibility. Provisional groups render identically to normal groups.
 */
import { ChevronDown, ChevronRight } from "lucide-react";
import type { PaletteGroup as PaletteGroupType } from "../_lib/paletteGroups";
import { usePaletteUiStore } from "../_stores/paletteUiStore";
import { PaletteItem } from "./PaletteItem";
import { PalettePlaceholderRow } from "./PalettePlaceholderRow";
import { PaletteSubgroup } from "./PaletteSubgroup";

export function PaletteGroup({
  group,
  searching = false,
}: {
  group: PaletteGroupType;
  searching?: boolean;
}) {
  const Icon = group.icon;
  // Ephemeral expand state keyed by group id; absent = collapsed. Search wins:
  // while searching the body is shown regardless of the stored collapse state.
  const storeExpanded = usePaletteUiStore((s) => s.expanded.has(group.id));
  const toggle = usePaletteUiStore((s) => s.toggle);
  const expanded = searching || storeExpanded;
  const Chevron = expanded ? ChevronDown : ChevronRight;
  const bodyId = `palette-group-body-${group.id}`;

  return (
    <div className="mb-3">
      <button
        type="button"
        aria-expanded={expanded}
        aria-controls={bodyId}
        onClick={() => toggle(group.id)}
        className="text-muted-foreground hover:text-foreground mb-1 flex w-full items-center gap-1.5 text-xs font-semibold"
      >
        {Icon ? <Icon className="h-3.5 w-3.5 shrink-0" aria-hidden /> : null}
        <span>{group.label}</span>
        <Chevron className="ml-auto h-3.5 w-3.5 shrink-0" aria-hidden />
      </button>

      {expanded ? (
        <div id={bodyId}>
          {group.placeholder ? (
            <PalettePlaceholderRow />
          ) : (
            <div className="space-y-1">
              {group.items?.map((type) => (
                <PaletteItem key={type} type={type} />
              ))}

              {group.subgroups?.map((sub) => (
                <PaletteSubgroup
                  key={sub.id}
                  subgroup={sub}
                  searching={searching}
                />
              ))}
            </div>
          )}
        </div>
      ) : null}
    </div>
  );
}
