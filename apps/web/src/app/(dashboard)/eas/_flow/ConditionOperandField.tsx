"use client";

/**
 * Graph-aware operand picker for ONE Condition operand (`left` or `right`).
 *
 * It is fully controlled and stateless: it derives its UI from the current raw
 * MQL5 string by parsing it each render (see {@link parseOperand}), and on every
 * edit it serializes the resulting {@link Operand} back to a raw string and calls
 * `onChange`. The caller writes that raw string straight into `node.data` via the
 * existing flat-key `updateNodeData` path, so the stored value stays a raw MQL5
 * string and codegen/persistence remain byte-identical to hand-authored input.
 *
 * Three kinds: Indicator (dropdown of sibling SMA/RSI/MACD nodes), Number
 * (numeric input), Custom (free-text MQL5 escape hatch).
 */
import {
  bufferVarFor,
  parseOperand,
  serializeOperand,
  type OperandKind,
} from "../_lib/conditionOperand";
import type { FlowNode } from "../_types/graph";
import { ConditionIndicatorInput } from "./ConditionIndicatorInput";

// Compact controls matching InspectorField (on-node expanded body only).
const INPUT_CLASS =
  "border-border bg-background focus-visible:ring-primary w-full rounded border px-1.5 py-0.5 text-xs outline-none focus-visible:ring-2";

const KIND_OPTIONS: { value: OperandKind; label: string }[] = [
  { value: "indicator", label: "Indicator output" },
  { value: "number", label: "Number" },
  { value: "custom", label: "Custom" },
];

export function ConditionOperandField({
  label,
  value,
  indicatorNodes,
  onChange,
}: {
  /** Field label, e.g. "Left" / "Right". */
  label: string;
  /** Current raw MQL5 string stored in `node.data` for this operand. */
  value: string;
  /** Sibling indicator nodes available as references (excludes self). */
  indicatorNodes: FlowNode[];
  /** Emit the new raw MQL5 string to persist into `node.data`. */
  onChange: (raw: string) => void;
}) {
  // Derive UI state from the raw string each render — single source of truth.
  const operand = parseOperand(value, indicatorNodes);

  // Switching kind: seed a sensible default raw value for the new kind.
  const handleKindChange = (kind: OperandKind) => {
    if (kind === operand.kind) return;
    switch (kind) {
      case "indicator": {
        const first = indicatorNodes[0];
        onChange(first ? (bufferVarFor(first) ?? "") : "");
        break;
      }
      case "number":
        // Reuse a numeric literal already present, else seed "0".
        onChange(operand.kind === "number" ? operand.value : "0");
        break;
      case "custom":
        // Carry the current raw text verbatim into the escape hatch.
        onChange(value);
        break;
    }
  };

  return (
    <div className="space-y-0.5">
      <span className="text-muted-foreground text-[10px] font-medium">
        {label}
      </span>

      <select
        aria-label={`${label} operand kind`}
        value={operand.kind}
        onChange={(e) => handleKindChange(e.target.value as OperandKind)}
        className={INPUT_CLASS}
      >
        {KIND_OPTIONS.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>

      {operand.kind === "indicator" ? (
        <ConditionIndicatorInput
          label={label}
          nodeId={operand.nodeId}
          line={operand.line}
          outputId={operand.outputId}
          indicatorNodes={indicatorNodes}
          onChange={onChange}
        />
      ) : operand.kind === "number" ? (
        <input
          type="number"
          aria-label={`${label} number`}
          value={operand.value}
          onChange={(e) =>
            onChange(
              serializeOperand(
                { kind: "number", value: e.target.value },
                indicatorNodes,
              ),
            )
          }
          className={INPUT_CLASS}
        />
      ) : (
        <input
          type="text"
          aria-label={`${label} custom expression`}
          value={operand.raw}
          onChange={(e) =>
            onChange(
              serializeOperand(
                { kind: "custom", raw: e.target.value },
                indicatorNodes,
              ),
            )
          }
          className={INPUT_CLASS}
        />
      )}
    </div>
  );
}
