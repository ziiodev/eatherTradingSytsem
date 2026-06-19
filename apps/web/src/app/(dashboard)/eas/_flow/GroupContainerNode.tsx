"use client";

/**
 * Render-only frame for an EXPANDED group (registered under
 * `nodeTypes.groupContainer`). It is emitted by deriveRenderGraph behind the
 * member nodes (zIndex -1) so a group is ALWAYS visible — expanded or collapsed.
 *
 * The frame itself must NOT swallow pointer events from the member nodes sitting
 * inside it (otherwise you couldn't click/drag them). So the outer box uses
 * `pointer-events-none`; only the HEADER (and its buttons) re-enable events.
 *
 * Header actions: collapse (-> setGroupCollapsed true), rename (opens the modal),
 * and ungroup (graphStore.ungroup). Dark-mode styled with the purple accent that
 * matches GroupNode.
 *
 * This node is render-only: it never enters canonical `nodes[]` (its id is
 * `groupContainer_*`, which onNodesChange filters out).
 */
import { useState } from "react";
import { type NodeProps } from "@xyflow/react";
import { ChevronUp, Layers, Pencil, Ungroup } from "lucide-react";
import type { FlowNode } from "../_types/graph";
import type { GroupContainerData } from "../_lib/groupDerivation";
import { useGraphStore } from "../_stores/graphStore";
import { GroupNameModal } from "./GroupNameModal";

export function GroupContainerNode({ data }: NodeProps<FlowNode>) {
  const container = data as unknown as GroupContainerData;
  const setGroupCollapsed = useGraphStore((s) => s.setGroupCollapsed);
  const renameGroup = useGraphStore((s) => s.renameGroup);
  const ungroup = useGraphStore((s) => s.ungroup);
  const [renameOpen, setRenameOpen] = useState(false);

  return (
    // Outer frame: non-interactive so clicks fall through to the member nodes.
    <div
      className="border-gl-purple-500/60 bg-gl-purple-500/5 pointer-events-none h-full w-full rounded-xl border-2 border-dashed"
      style={{ width: container.width, height: container.height }}
    >
      {/* Header bar: the ONLY interactive region of the container. */}
      <div className="pointer-events-auto flex items-center gap-2 px-3 py-1.5">
        {/* Drag handle (label region only): matches the container's dragHandle
            selector so dragging here MOVES the group, while the buttons below
            stay clickable. */}
        <div className="group-drag-handle flex min-w-0 flex-1 cursor-move items-center gap-2">
          <Layers className="text-gl-purple-500 h-4 w-4 shrink-0" aria-hidden />
          <span className="text-foreground truncate text-sm font-medium">
            {container.name}
          </span>
        </div>
        <div className="flex items-center gap-1">
          <button
            type="button"
            onPointerDown={(e) => e.stopPropagation()}
            onClick={() => setRenameOpen(true)}
            aria-label="Renombrar grupo"
            title="Renombrar grupo"
            className="text-muted-foreground hover:text-foreground rounded p-0.5 transition-colors"
          >
            <Pencil className="h-3.5 w-3.5" />
          </button>
          <button
            type="button"
            onPointerDown={(e) => e.stopPropagation()}
            onClick={() => setGroupCollapsed(container.groupId, true)}
            aria-label="Contraer grupo"
            title="Contraer grupo"
            className="text-muted-foreground hover:text-foreground rounded p-0.5 transition-colors"
          >
            <ChevronUp className="h-4 w-4" />
          </button>
          <button
            type="button"
            onPointerDown={(e) => e.stopPropagation()}
            onClick={() => ungroup(container.groupId)}
            aria-label="Desagrupar"
            title="Desagrupar"
            className="text-muted-foreground hover:text-foreground rounded p-0.5 transition-colors"
          >
            <Ungroup className="h-3.5 w-3.5" />
          </button>
        </div>
      </div>

      {/* The modal needs pointer events even though the frame disables them. */}
      <div className="pointer-events-auto">
        <GroupNameModal
          open={renameOpen}
          onOpenChange={setRenameOpen}
          initialName={container.name}
          title="Renombrar grupo"
          submitLabel="Guardar"
          onSubmit={(name) => renameGroup(container.groupId, name)}
        />
      </div>
    </div>
  );
}
