"use client";

/**
 * Sidebar palette for the node editor. Renders the node types organized into
 * fixed, collapsible Spanish-headed groups (see `lib/paletteGroups.ts`, the
 * single source of truth for the taxonomy). A search box at the top filters the
 * palette as the user types (accent-insensitive, by friendly label or raw type
 * id); while searching, matching groups/subgroups render expanded regardless of
 * the ephemeral collapse state. Each group otherwise starts collapsed and can be
 * toggled via its header. Each leaf row is draggable; dropping it onto the
 * canvas places a node at the cursor (see FlowCanvas onDrop). The drag payload
 * carries the node type under NODE_DND_MIME.
 *
 * The DnD constant is re-exported here so FlowCanvas's existing
 * `import { NODE_DND_MIME } from "./NodeSidebar"` keeps resolving.
 */
import { useState } from "react";
import { Search } from "lucide-react";
import { filterPaletteGroups } from "../_lib/paletteGroups";
import { PaletteGroup } from "./PaletteGroup";

export { NODE_DND_MIME } from "./paletteConsts";

export function NodeSidebar() {
  const [query, setQuery] = useState("");
  const searching = query.trim() !== "";
  const groups = filterPaletteGroups(query);

  return (
    <aside className="border-border flex w-52 shrink-0 flex-col border-r p-3">
      <div className="relative mb-3">
        <Search
          className="text-muted-foreground pointer-events-none absolute top-1/2 left-2 h-3.5 w-3.5 -translate-y-1/2"
          aria-hidden
        />
        <input
          type="search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Buscar nodos…"
          aria-label="Buscar nodos"
          className="border-border bg-background focus-visible:ring-primary w-full rounded-md border py-1 pr-2 pl-7 text-sm outline-none focus-visible:ring-2"
        />
      </div>
      <div className="-mr-1 flex-1 overflow-y-auto pr-1">
        {searching && groups.length === 0 ? (
          <p className="text-muted-foreground px-2 py-1 text-sm italic">
            Sin resultados
          </p>
        ) : (
          groups.map((group) => (
            <PaletteGroup key={group.id} group={group} searching={searching} />
          ))
        )}
      </div>
    </aside>
  );
}
