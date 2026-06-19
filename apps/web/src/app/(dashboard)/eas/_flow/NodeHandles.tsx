"use client";

/**
 * Lateral connection handles for a custom node, driven by the node-type registry.
 *
 * Contract (must hold so legacy edges never detach):
 * - The PRIMARY input (registry port with `id === undefined`) renders as a
 *   `target` Handle on the LEFT with NO id; the PRIMARY output as a `source`
 *   Handle on the RIGHT with NO id. Both are ALWAYS MOUNTED in BOTH states.
 * - COSMETIC extra input ports (id'd, `connectable` falsy) render as additional
 *   left Handles, `isConnectable={false}`, and ONLY in EXPANDED.
 * - CONNECTABLE id'd input ports (`connectable === true`, the boolean-combinator
 *   inputs cond1..condN) are REAL `target` handles, mounted in BOTH states so
 *   they can be wired whether the node is compact or expanded.
 * - Start has no input handle; End has no output handle (per registry ports).
 *
 * Placement: the colored dot must TOUCH the card border in BOTH states. React
 * Flow anchors a Handle at its nearest positioned ancestor's edge; in EXPANDED
 * the handle lives inside a padded row, so we shift it by the card padding
 * (`p-2` = 8px) via inline left/right offsets to land the dot ON the border. In
 * COMPACT the handle is a direct child of the card, so the default left:0/right:0
 * already straddles the border.
 */
import { Handle, Position } from "@xyflow/react";
import type { Port } from "../_lib/nodeTypeRegistry";

const PRIMARY_IN = "!bg-gl-blue-500 !border-gl-blue-500 !h-3 !w-3";
const PRIMARY_OUT = "!bg-gl-orange-500 !border-gl-orange-500 !h-3 !w-3";
const COSMETIC = "!bg-gl-gray-600 !border-gl-gray-600 !h-2.5 !w-2.5";
// Real connectable combinator inputs: same accent as the primary input so they
// read as live target handles (vs the muted gray cosmetic dots).
const CONNECTABLE_IN = "!bg-gl-blue-500 !border-gl-blue-500 !h-3 !w-3";
// Real connectable id'd OUTPUTS, accented by their FE-internal `kind`:
// value → blue, signal → orange (default to value when unset).
const OUT_VALUE = "!bg-gl-blue-500 !border-gl-blue-500 !h-3 !w-3";
const OUT_SIGNAL = "!bg-gl-orange-500 !border-gl-orange-500 !h-3 !w-3";

/** Tailwind accent literal for a connectable id'd output of a given kind. */
function outClass(kind: Port["kind"]): string {
  return kind === "signal" ? OUT_SIGNAL : OUT_VALUE;
}

// EXPANDED rows live inside the card's `p-2` padding (8px). Pull the dot out by
// that amount so it sits on the card border instead of floating inside.
const EDGE_LEFT: React.CSSProperties = { left: "-8px" };
const EDGE_RIGHT: React.CSSProperties = { right: "-8px" };

/**
 * A primary, id-less handle — mounted in both states for edge back-compat.
 * `inset` pulls the dot to the card border when nested inside a padded row.
 */
function PrimaryHandle({
  side,
  inset,
}: {
  side: "left" | "right";
  inset?: boolean;
}) {
  const isLeft = side === "left";
  return (
    <Handle
      type={isLeft ? "target" : "source"}
      position={isLeft ? Position.Left : Position.Right}
      className={isLeft ? PRIMARY_IN : PRIMARY_OUT}
      style={inset ? (isLeft ? EDGE_LEFT : EDGE_RIGHT) : undefined}
    />
  );
}

export function NodeHandles({
  inputPorts,
  outputPorts,
  expanded,
}: {
  inputPorts: Port[];
  outputPorts: Port[];
  expanded: boolean;
}) {
  // Classify input ports: PRIMARY (id-less), CONNECTABLE id'd (combinator
  // inputs), and COSMETIC id'd (existing nodes' decorative extra ports).
  const primaryIn = inputPorts.find((p) => p.id === undefined);
  const connectableIn = inputPorts.filter(
    (p) => p.id !== undefined && p.connectable,
  );
  const cosmeticIn = inputPorts.filter(
    (p) => p.id !== undefined && !p.connectable,
  );
  // Classify output ports symmetrically: PRIMARY (id-less, always mounted) and
  // CONNECTABLE id'd outputs (real source handles for multi-output nodes). Id'd
  // NON-connectable outputs are picker-awareness descriptors only — never
  // rendered — so a single-output node looks exactly as today.
  const primaryOut = outputPorts.find((p) => p.id === undefined);
  const connectableOut = outputPorts.filter(
    (p) => p.id !== undefined && p.connectable,
  );

  if (!expanded) {
    // COMPACT: mount the primary handles AND any connectable id'd inputs (so a
    // combinator stays wireable while collapsed). Connectable inputs are spread
    // vertically along the left edge so they don't overlap into one dot.
    return (
      <>
        {primaryIn && <PrimaryHandle side="left" />}
        {connectableIn.map((port, i) => (
          <Handle
            key={port.id}
            type="target"
            id={port.id}
            position={Position.Left}
            isConnectable
            className={CONNECTABLE_IN}
            style={{ top: `${spreadPercent(i, connectableIn.length)}%` }}
          />
        ))}
        {primaryOut && <PrimaryHandle side="right" />}
        {connectableOut.map((port, i) => (
          <Handle
            key={port.id}
            type="source"
            id={port.id}
            position={Position.Right}
            isConnectable
            className={outClass(port.kind)}
            style={{ top: `${spreadPercent(i, connectableOut.length)}%` }}
          />
        ))}
      </>
    );
  }

  // EXPANDED: primary handles stay id-less + mounted; connectable inputs appear
  // as labeled REAL rows; cosmetic inputs as labeled non-connectable rows. Each
  // port is a fixed-height row so the dot centers against its muted label, and
  // each dot is pulled to the card border via the inset offset.
  return (
    <>
      {primaryIn && (
        <div className="relative flex h-3.5 items-center">
          <PrimaryHandle side="left" inset />
          <span className="text-muted-foreground pl-2.5 text-[10px] leading-none">
            {primaryIn.label}
          </span>
        </div>
      )}
      {connectableIn.map((port) => (
        <div key={port.id} className="relative flex h-3.5 items-center">
          <Handle
            type="target"
            id={port.id}
            position={Position.Left}
            isConnectable
            className={CONNECTABLE_IN}
            style={EDGE_LEFT}
          />
          <span className="text-muted-foreground pl-2.5 text-[10px] leading-none">
            {port.label}
          </span>
        </div>
      ))}
      {cosmeticIn.map((port) => (
        <div key={port.id} className="relative flex h-3.5 items-center">
          <Handle
            type="target"
            id={port.id}
            position={Position.Left}
            isConnectable={false}
            className={COSMETIC}
            style={EDGE_LEFT}
          />
          <span className="text-muted-foreground pl-2.5 text-[10px] leading-none">
            {port.label}
          </span>
        </div>
      ))}
      {primaryOut && (
        <div className="relative flex h-3.5 items-center justify-end">
          <span className="text-muted-foreground pr-2.5 text-[10px] leading-none">
            {primaryOut.label}
          </span>
          <PrimaryHandle side="right" inset />
        </div>
      )}
      {connectableOut.map((port) => (
        <div
          key={port.id}
          className="relative flex h-3.5 items-center justify-end"
        >
          <span className="text-muted-foreground pr-2.5 text-[10px] leading-none">
            {port.label}
          </span>
          <Handle
            type="source"
            id={port.id}
            position={Position.Right}
            isConnectable
            className={outClass(port.kind)}
            style={EDGE_RIGHT}
          />
        </div>
      ))}
    </>
  );
}

/**
 * Vertical position (in % of card height) for the i-th of `total` connectable
 * handles, spread evenly so they don't collapse onto a single point. A single
 * handle sits centered (50%); N handles are placed at the centers of N equal
 * bands, e.g. 6 → ~8%, 25%, 42%, 58%, 75%, 92%.
 */
function spreadPercent(i: number, total: number): number {
  if (total <= 1) return 50;
  return ((i + 0.5) / total) * 100;
}
