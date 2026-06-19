"use client";

/**
 * The "indicator" branch of {@link ConditionOperandField}, extracted to keep
 * each file small. It renders the sibling-indicator picker and — only when the
 * selected indicator declares MORE THAN ONE output (registry `outputPorts`) — a
 * secondary native `<select>` choosing which output to reference (an N-way
 * generalization of the former Stochastic-only %K/%D control). Both controls
 * are fully controlled and stateless: every change re-serializes the operand to
 * its raw MQL5 string and calls `onChange`, so the chosen output round-trips via
 * the stored ref with no separate React state. A single-output indicator
 * (SMA/RSI/MACD today) shows NO output selector — unchanged behavior.
 *
 * Output→operand mapping (keeps existing strings byte-identical):
 *  - Stochastic (N=2) maps its two outputs to the operand `line` (%K → "K" /
 *    primary, %D → "D"), serialized as the prefix-encoded `stochk_`/`stochd_`.
 *  - Any other multi-output indicator maps the PRIMARY (id-less) output to no
 *    `outputId`, and a NAMED output to `outputId = port.id` → `<prefix>:<id>`.
 */
import {
  serializeOperand,
  labelForIndicator,
  type Operand,
  type StochasticLine,
} from "../_lib/conditionOperand";
import { getNodeTypeMeta, type Port } from "../_lib/nodeTypeRegistry";
import type { FlowNode } from "../_types/graph";

// Compact controls matching InspectorField (on-node expanded body only).
const INPUT_CLASS =
  "border-border bg-background focus-visible:ring-primary w-full rounded border px-1.5 py-0.5 text-xs outline-none focus-visible:ring-2";

/** Stable value for the PRIMARY (id-less) output option in the native select. */
const PRIMARY_VALUE = "__primary__";

/**
 * The selectable VALUE operand outputs of an indicator node, from the
 * presentation registry. ZScore mixes value and signal outputs, but its SIGNAL
 * outputs (zgt/zlt) are booleans wired into combinators — never value operands —
 * so they are excluded HERE. The filter is ZSCORE-SCOPED: Stochastic's %D output
 * carries `kind: "signal"` yet IS a legitimate value operand, so it must NOT be
 * filtered (no regression).
 */
function outputsFor(node: FlowNode): Port[] {
  const ports = getNodeTypeMeta(node.data.type).outputPorts;
  if (node.data.type === "ZScore") {
    return ports.filter((p) => p.kind !== "signal");
  }
  return ports;
}

/**
 * The bar offset an operand referencing `node` should carry. For RSI the shift
 * is sourced from the REFERENCED node's own `data.bar_shift` param (read at
 * serialize time) so the operand always tracks the indicator's configured
 * offset: Valor → `rsi_<id>[bar_shift]`, Previous → `rsi_<id>:prev[bar_shift]`.
 * With the default `bar_shift` of 0 this stays byte-identical to today's `[0]`.
 * Non-RSI indicators keep shift 0 (their refs are unchanged).
 */
function shiftFor(node: FlowNode): number | undefined {
  if (node.data.type !== "RSI") return undefined;
  const raw = Number(node.data.bar_shift);
  return Number.isFinite(raw) && raw > 0 ? raw : undefined;
}

/** The select `value` identifying which output of `node` the operand references. */
function selectedOutputValue(node: FlowNode, op: IndicatorOperand): string {
  if (node.data.type === "Stochastic") return op.line ?? "K";
  // ZScore's primary maps to the explicit "value" outputId, never the sentinel.
  if (node.data.type === "ZScore") return op.outputId ?? "value";
  return op.outputId ?? PRIMARY_VALUE;
}

/**
 * The native-select option `value` for a registry output `port` of `node`. For
 * ZScore the id-less primary uses the explicit `"value"` token (matching the
 * serialized `:value`); for a generic indicator the primary uses the sentinel.
 */
function optionValueFor(node: FlowNode, port: Port): string {
  if (node.data.type === "ZScore") return port.id ?? "value";
  return port.id ?? PRIMARY_VALUE;
}

/** Build the operand fields (line / outputId) for a chosen output of `node`. */
function operandForOutput(
  node: FlowNode,
  port: Port,
): Pick<IndicatorOperand, "line" | "outputId"> {
  // Stochastic encodes its output in the prefix via `line`; the id-less %K port
  // is the primary "K" line, the id'd %D port is "D".
  if (node.data.type === "Stochastic") {
    return { line: port.id === undefined ? "K" : "D" };
  }
  // ZScore is fully-named: even its PRIMARY (id-less) "Z-Score" port serializes
  // with an explicit `:value` token, so map the id-less port to `outputId:"value"`.
  if (node.data.type === "ZScore") {
    return { outputId: port.id ?? "value" };
  }
  // Generic multi-output indicator: primary → no outputId; named → its id.
  return { outputId: port.id };
}

/** Narrowed alias for the indicator operand shape (no `number`/`custom`). */
type IndicatorOperand = Extract<Operand, { kind: "indicator" }>;

export function ConditionIndicatorInput({
  label,
  nodeId,
  line,
  outputId,
  indicatorNodes,
  onChange,
}: {
  /** Field label, e.g. "Left" / "Right". */
  label: string;
  /** Currently referenced indicator node id (may be stale/missing). */
  nodeId: string;
  /** Selected Stochastic line, if any (ignored for non-Stochastic indicators). */
  line: StochasticLine | undefined;
  /** Selected named output id, if any (multi-output indicators only). */
  outputId: string | undefined;
  /** Sibling indicator nodes available as references (excludes self). */
  indicatorNodes: FlowNode[];
  /** Emit the new raw MQL5 string to persist into `node.data`. */
  onChange: (raw: string) => void;
}) {
  if (indicatorNodes.length === 0) {
    return (
      <p className="text-muted-foreground text-[10px]">
        No indicator nodes on the canvas yet.
      </p>
    );
  }

  const selected = indicatorNodes.find((n) => n.id === nodeId);
  const outputs = selected ? outputsFor(selected) : [];
  const hasMultipleOutputs = outputs.length > 1;
  const currentOp: IndicatorOperand = {
    kind: "indicator",
    nodeId,
    line,
    outputId,
  };

  // Switch the referenced node, defaulting to its PRIMARY output (no line /
  // outputId) so single-output targets keep their byte-identical refs. The shift
  // is sourced from the newly referenced node (RSI → its `bar_shift`). ZScore is
  // the exception: its primary serializes with an explicit `:value` token, so
  // default a freshly-referenced ZScore to `outputId: "value"`.
  const emitNode = (nextNodeId: string) => {
    const nextNode = indicatorNodes.find((n) => n.id === nextNodeId);
    onChange(
      serializeOperand(
        {
          kind: "indicator",
          nodeId: nextNodeId,
          outputId: nextNode?.data.type === "ZScore" ? "value" : undefined,
          shift: nextNode ? shiftFor(nextNode) : undefined,
        },
        indicatorNodes,
      ),
    );
  };

  // Switch the chosen output of the CURRENT node, preserving the node id. The
  // select `value` is the line (K/D) for Stochastic, else the output id (or the
  // primary sentinel) — map it back to the matching registry port either way.
  const emitOutput = (value: string) => {
    if (selected == null) return;
    const isStoch = selected.data.type === "Stochastic";
    const port =
      outputs.find((p) =>
        isStoch
          ? (p.id === undefined ? "K" : "D") === value
          : optionValueFor(selected, p) === value,
      ) ?? outputs[0];
    // Defensive: `outputs` is non-empty here (the secondary select only renders
    // when there are >1 outputs), but `noUncheckedIndexedAccess` widens
    // `outputs[0]` to `Port | undefined`.
    if (port == null) return;
    onChange(
      serializeOperand(
        {
          kind: "indicator",
          nodeId,
          shift: shiftFor(selected),
          ...operandForOutput(selected, port),
        },
        indicatorNodes,
      ),
    );
  };

  return (
    <div className="space-y-0.5">
      <select
        aria-label={`${label} indicator`}
        value={nodeId}
        onChange={(e) => emitNode(e.target.value)}
        className={INPUT_CLASS}
      >
        {/* Surface an unknown/stale selection verbatim instead of silently
            coercing to option[0], mirroring InspectorField's select. */}
        {selected ? null : <option value={nodeId}>{nodeId} (missing)</option>}
        {indicatorNodes.map((n) => (
          <option key={n.id} value={n.id}>
            {labelForIndicator(n)}
          </option>
        ))}
      </select>

      {hasMultipleOutputs ? (
        <select
          aria-label={`${label} indicator output`}
          value={selected ? selectedOutputValue(selected, currentOp) : ""}
          onChange={(e) => emitOutput(e.target.value)}
          className={INPUT_CLASS}
        >
          {outputs.map((port) => {
            // For Stochastic the option value is the line (K/D); for a generic
            // indicator it is the output id (or the primary sentinel).
            const value =
              selected?.data.type === "Stochastic"
                ? port.id === undefined
                  ? "K"
                  : "D"
                : selected
                  ? optionValueFor(selected, port)
                  : (port.id ?? PRIMARY_VALUE);
            return (
              <option key={value} value={value}>
                {port.label}
              </option>
            );
          })}
        </select>
      ) : null}
    </div>
  );
}
