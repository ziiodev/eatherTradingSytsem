"use client";

/**
 * EXPANDED node header: per-type icon + title (data.label or the registry
 * category fallback) + a category badge + the registry subtitle, with a
 * top-right DUPLICATE button calling graphStore.duplicateNode(id).
 *
 * The duplicate click stops propagation so it never triggers the node's
 * double-click / selection behavior.
 */
import { Copy } from "lucide-react";
import type { NodeData } from "../_types/graph";
import { getNodeTypeMeta } from "../_lib/nodeTypeRegistry";
import { useGraphStore } from "../_stores/graphStore";
import { nodeColorClasses } from "./nodeColors";

export function NodeHeader({
  nodeId,
  data,
}: {
  nodeId: string;
  data: NodeData;
}) {
  const meta = getNodeTypeMeta(data.type);
  const colors = nodeColorClasses(meta.colorToken);
  const Icon = meta.icon;
  const duplicateNode = useGraphStore((s) => s.duplicateNode);

  // Title prefers the node's label, falling back to the registry category.
  const title = data.label || meta.category;

  return (
    <header className="flex items-start gap-1.5">
      <Icon className={`mt-0.5 h-4 w-4 shrink-0 ${colors.text}`} />
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-1.5">
          <span className="truncate text-xs font-semibold">{title}</span>
          <span
            className={`rounded px-1 py-0.5 text-[9px] font-medium ${colors.text} ${colors.badgeBg}`}
          >
            {meta.category}
          </span>
        </div>
        <p className="text-muted-foreground truncate text-[10px]">
          {meta.subtitle}
        </p>
      </div>
      <button
        type="button"
        aria-label="Duplicate node"
        title="Duplicate node"
        onClick={(e) => {
          e.stopPropagation();
          duplicateNode(nodeId);
        }}
        className="text-muted-foreground hover:text-foreground shrink-0 rounded p-0.5 transition-colors"
      >
        <Copy className="h-3 w-3" />
      </button>
    </header>
  );
}
