"use client";

/**
 * A single draggable palette row for one domain node type. Dragging carries the
 * bare NodeType id under NODE_DND_MIME; FlowCanvas's onDrop reads it and places
 * the node. Presentation (icon + accent color) comes from the node-type
 * registry via static, JIT-safe Tailwind classes (no runtime template classes).
 */
import { getNodeTypeMeta } from "../_lib/nodeTypeRegistry";
import { PALETTE_ITEM_LABELS } from "../_lib/paletteGroups";
import { nodeColorClasses } from "./nodeColors";
import { NODE_DND_MIME } from "./paletteConsts";
import type { NodeType } from "../_types/graph";

export function PaletteItem({ type }: { type: NodeType }) {
  const meta = getNodeTypeMeta(type);
  const Icon = meta.icon;
  // Static class string from the color map — safe for Tailwind JIT.
  const iconColor = nodeColorClasses(meta.colorToken).text;
  // Palette-only friendly label; falls back to the raw NodeType id.
  const label = PALETTE_ITEM_LABELS[type] ?? type;

  const onDragStart = (e: React.DragEvent) => {
    e.dataTransfer.setData(NODE_DND_MIME, type);
    e.dataTransfer.effectAllowed = "move";
  };

  return (
    <div
      draggable
      onDragStart={onDragStart}
      className="border-border hover:bg-accent flex w-full cursor-grab items-center gap-2 rounded border px-2 py-1 text-left text-sm active:cursor-grabbing"
    >
      <Icon className={`${iconColor} h-4 w-4 shrink-0`} aria-hidden />
      <span>{label}</span>
    </div>
  );
}
