"use client";

/**
 * Synthetic React Flow renderer for a COLLAPSED group (registered under
 * `nodeTypes.groupNode`). It stands in for the group's hidden member nodes and
 * shows the group name + member count, an EXPAND chevron, a rename action (opens
 * the modal), and an ungroup action.
 *
 * Critically, it mounts one <Handle> per `data.ports[]` entry whose `id` EXACTLY
 * matches the synthetic port id derived in `groupPorts.ts`, so the proxy edges
 * rerouted onto this node attach to the right handles. Inbound ports are `target`
 * handles on the LEFT; outbound ports are `source` handles on the RIGHT.
 *
 * Collapse is driven by the PERSISTED `GroupMeta.collapsed` flag, so this node
 * renders only while the group is collapsed; expanding (chevron, double-click, or
 * ungroup) sets the flag false and unmounts it.
 */
import { useState } from "react";
import { Handle, Position, type NodeProps } from "@xyflow/react";
import { ChevronDown, Layers, Pencil, Ungroup } from "lucide-react";
import type { FlowNode } from "../_types/graph";
import type { GroupNodeData } from "../_lib/groupDerivation";
import type { GroupPort } from "../_lib/groupPorts";
import { useGraphStore } from "../_stores/graphStore";
import { GroupNameModal } from "./GroupNameModal";

const PORT_IN = "!bg-gl-blue-500 !border-gl-blue-500 !h-3 !w-3";
const PORT_OUT = "!bg-gl-orange-500 !border-gl-orange-500 !h-3 !w-3";

/** Evenly spread the i-th of `total` handles down the node's vertical edge. */
function spread(i: number, total: number): number {
  return total <= 1 ? 50 : ((i + 0.5) / total) * 100;
}

/** Render one synthetic boundary handle whose id matches the proxy edge end. */
function PortHandle({ port, index, total }: { port: GroupPort; index: number; total: number }) {
  const isTarget = port.side === "target";
  return (
    <Handle
      type={isTarget ? "target" : "source"}
      id={port.id}
      position={isTarget ? Position.Left : Position.Right}
      className={isTarget ? PORT_IN : PORT_OUT}
      style={{ top: `${spread(index, total)}%`, [isTarget ? "left" : "right"]: 0 }}
    />
  );
}

export function GroupNode({ data, selected }: NodeProps<FlowNode>) {
  // `data` is the synthetic group payload, not canonical NodeData.
  const group = data as unknown as GroupNodeData;
  const setGroupCollapsed = useGraphStore((s) => s.setGroupCollapsed);
  const renameGroup = useGraphStore((s) => s.renameGroup);
  const ungroup = useGraphStore((s) => s.ungroup);
  const [renameOpen, setRenameOpen] = useState(false);

  const inbound = group.ports.filter((p) => p.side === "target");
  const outbound = group.ports.filter((p) => p.side === "source");

  return (
    <div
      className={`bg-gl-gray-900 border-gl-purple-500 relative flex min-w-[160px] flex-col gap-1 rounded-lg border-2 px-3 py-2 shadow-sm ${
        selected ? "ring-gl-orange-500 ring-2" : ""
      }`}
    >
      {inbound.map((p, i) => (
        <PortHandle key={p.id} port={p} index={i} total={inbound.length} />
      ))}
      {outbound.map((p, i) => (
        <PortHandle key={p.id} port={p} index={i} total={outbound.length} />
      ))}

      <div className="flex items-center gap-2">
        <Layers className="text-gl-purple-500 h-4 w-4 shrink-0" aria-hidden />
        <span className="text-foreground truncate text-sm font-medium">
          {group.name}
        </span>
        <button
          type="button"
          onPointerDown={(e) => e.stopPropagation()}
          onClick={() => setRenameOpen(true)}
          aria-label="Renombrar grupo"
          title="Renombrar grupo"
          className="text-muted-foreground hover:text-foreground rounded p-0.5 transition-colors"
        >
          <Pencil className="h-3 w-3" />
        </button>
        <button
          type="button"
          onPointerDown={(e) => e.stopPropagation()}
          onClick={() => setGroupCollapsed(group.groupId, false)}
          aria-label="Expandir grupo"
          title="Expandir grupo"
          className="text-muted-foreground hover:text-foreground ml-auto rounded p-0.5 transition-colors"
        >
          {/* Pointing down: collapsed -> click to expand. */}
          <ChevronDown className="h-4 w-4" />
        </button>
      </div>

      <div className="flex items-center justify-between gap-2">
        <span className="text-muted-foreground text-[11px]">
          {group.memberCount} nodos
        </span>
        <button
          type="button"
          onPointerDown={(e) => e.stopPropagation()}
          onClick={() => ungroup(group.groupId)}
          aria-label="Desagrupar"
          title="Desagrupar"
          className="text-muted-foreground hover:text-foreground inline-flex items-center gap-1 rounded px-1 py-0.5 text-[11px] transition-colors"
        >
          <Ungroup className="h-3 w-3" />
          Desagrupar
        </button>
      </div>

      <GroupNameModal
        open={renameOpen}
        onOpenChange={setRenameOpen}
        initialName={group.name}
        title="Renombrar grupo"
        submitLabel="Guardar"
        onSubmit={(name) => renameGroup(group.groupId, name)}
      />
    </div>
  );
}
