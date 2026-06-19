"use client";

/**
 * Shared param-field renderer — the SINGLE source of truth for editing a node's
 * flat `data` params. Consumed by the on-node EXPANDED body (NodeExpandedBody),
 * which renders these controls in-place and writes through the
 * `updateNodeData(id, …)` path (shared coalescing/undo).
 *
 * It maps each NODE_PARAM_SCHEMAS field to InspectorField, except the Condition
 * `left`/`right` operands which route through the guided ConditionOperandField
 * (needs the sibling indicator list). `nodeParamSchemas.ts` is untouched.
 */
import { NODE_PARAM_SCHEMAS, type FieldDef } from "../_lib/nodeParamSchemas";
import { indicatorNodesFrom } from "../_lib/conditionOperand";
import type { NodeData, NodeType } from "../_types/graph";
import { useGraphStore } from "../_stores/graphStore";
import { InspectorField } from "./InspectorField";
import { ConditionOperandField } from "./ConditionOperandField";

/** Render one schema field for the given node id+data (Condition-aware). */
function FieldRow({
  nodeId,
  type,
  data,
  field,
}: {
  nodeId: string;
  type: NodeType;
  data: NodeData;
  field: FieldDef;
}) {
  const updateNodeData = useGraphStore((s) => s.updateNodeData);
  // Full node list — only consumed by the Condition branch to enumerate the
  // sibling indicator nodes referenceable by an operand.
  const nodes = useGraphStore((s) => s.nodes);

  // Condition is the only graph-aware node: its left/right operands are authored
  // through the guided picker (which needs the sibling indicator list), while
  // operator + every other node/field keeps the generic InspectorField.
  const isCondition = type === "Condition";
  if (isCondition && (field.key === "left" || field.key === "right")) {
    return (
      <ConditionOperandField
        label={field.label}
        value={String((data[field.key] as string | undefined) ?? field.default)}
        indicatorNodes={indicatorNodesFrom(nodes, nodeId)}
        onChange={(raw) => updateNodeData(nodeId, { [field.key]: raw })}
      />
    );
  }
  return (
    <InspectorField
      field={field}
      value={
        (data[field.key] as number | string | boolean | undefined) ??
        field.default
      }
      onChange={(value) => updateNodeData(nodeId, { [field.key]: value })}
    />
  );
}

/**
 * Render every editable param field for a node. Returns `null` when the type has
 * no params (Start/End) so callers can decide on an empty-state message.
 */
export function NodeParamFields({
  nodeId,
  type,
  data,
}: {
  nodeId: string;
  type: NodeType;
  data: NodeData;
}) {
  const fields: FieldDef[] = NODE_PARAM_SCHEMAS[type] ?? [];
  if (fields.length === 0) return null;
  return (
    <div className="space-y-2">
      {fields.map((field) => (
        <FieldRow
          key={field.key}
          nodeId={nodeId}
          type={type}
          data={data}
          field={field}
        />
      ))}
    </div>
  );
}
