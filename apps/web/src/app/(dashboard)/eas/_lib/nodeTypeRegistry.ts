/**
 * Per-type presentation metadata for the visual node editor.
 *
 * Drives the (single, `nodeTypes.custom`) React Flow renderer: each of the 10
 * domain node types maps to an icon, a GitLab-palette color token, a category,
 * a one-line subtitle, and its lateral input/output ports.
 *
 * This module is PRESENTATION ONLY — it never touches `node.data` (the codegen
 * param contract lives in `nodeParamSchemas.ts` and must stay untouched here).
 *
 * Port contract:
 * - A port with `id === undefined` is the PRIMARY, id-less handle. Primary
 *   handles stay id-less so legacy edges (saved with sourceHandle/targetHandle
 *   undefined) bind without migration.
 * - Extra ports carry an explicit `id` and are COSMETIC: shown in the expanded
 *   body but rendered non-connectable (the linear-topology guard is the
 *   server-parity safety net regardless).
 */
import type { LucideIcon } from "lucide-react";
import {
  Play,
  Flag,
  GitBranch,
  ChartLine,
  Activity,
  ChartSpline,
  Waves,
  TrendingUp,
  TrendingDown,
  ShieldCheck,
  ScrollText,
  Ampersand,
  Split,
  Ban,
  Diff,
  Sigma,
} from "lucide-react";
import type { NodeType } from "../_types/graph";

/**
 * A single lateral connection point on a node. `id === undefined` marks the
 * PRIMARY id-less handle; any other port is COSMETIC (non-connectable) UNLESS
 * `connectable` is explicitly `true` — the boolean-combinator inputs are real,
 * id'd, connectable target handles, distinct from the cosmetic extra ports on
 * existing nodes (which stay `isConnectable={false}`).
 */
export interface Port {
  id?: string;
  label: string;
  side: "left" | "right";
  /** When `true`, an id'd port renders as a REAL connectable handle. */
  connectable?: boolean;
  /**
   * FE-INTERNAL semantic class of an OUTPUT port, driving its handle accent
   * (value → blue, signal → orange) and the operand picker's awareness of how
   * many selectable outputs a node has. This NEVER leaves the frontend: it is
   * not part of the serialized graph, `node.data`, or any backend parity
   * contract — do NOT mirror it into a schema/parity file.
   */
  kind?: "value" | "signal";
}

/** Presentation metadata for one domain node type. */
export interface NodeTypeMeta {
  icon: LucideIcon;
  /** GitLab-palette Tailwind color token, e.g. `gl-green-500` (no bg-/text-). */
  colorToken: string;
  category: string;
  subtitle: string;
  inputPorts: Port[];
  outputPorts: Port[];
}

/** Primary (id-less) input handle on the left edge. */
const IN: Port = { label: "Entrada", side: "left" };
/** Primary (id-less) output handle on the right edge. */
const OUT: Port = { label: "Salida", side: "right" };

/**
 * Build `count` REAL connectable combinator input ports with ids `cond1..condN`
 * and labels `Condition 1..N`, all on the left edge. Used by LogicalAnd/Or/Xor
 * (6) and LogicalNot (1). These are id'd AND connectable, unlike cosmetic ports.
 */
function combinatorInputs(count: number): Port[] {
  return Array.from({ length: count }, (_, i) => ({
    id: `cond${i + 1}`,
    label: `Condition ${i + 1}`,
    side: "left" as const,
    connectable: true,
  }));
}

/** The single labeled output handle shared by all three combinators. */
const RESULT_OUT: Port = { label: "Resultado", side: "right" };

/**
 * The two REAL connectable VALUE input ports for the crossing family
 * (value1/value2). Like combinator inputs they are id'd AND connectable, but
 * they accept indicator (SMA/RSI/MACD) sources rather than Condition sources.
 */
const CROSSING_INPUTS: Port[] = [
  { id: "value1", label: "Value 1", side: "left", connectable: true },
  { id: "value2", label: "Value 2", side: "left", connectable: true },
];

/** The single labeled crossing-signal output handle (primary, id-less). */
const CROSS_SIGNAL_OUT: Port = { label: "Señal de Cruce", side: "right" };

/**
 * Stochastic's TWO outputs, declared so the operand picker can offer an N-way
 * (N=2) output selector. The %K line is the PRIMARY (id-less) output and renders
 * exactly as today; the %D line is a NON-connectable picker-awareness descriptor
 * (no value-edge / no extra node handle). Both map to the prefix-encoded
 * `stochk_`/`stochd_` operand strings via the operand `line`, NOT an `:outputId`,
 * so serialized refs stay byte-identical. `kind` is FE-internal accent metadata.
 */
const STOCH_OUTPUTS: Port[] = [
  { label: "%K", side: "right", kind: "value" },
  { id: "signal", label: "%D", side: "right", kind: "signal" },
];

/**
 * RSI's TWO selectable outputs, declared so the existing N-way (N=2) operand
 * picker auto-shows a selector. The current value ("Valor") is the PRIMARY
 * (id-less) output and serializes byte-identically as `rsi_<id>[shift]` (no
 * `:outputId`); the "Previous Value" output carries `outputId: "prev"` and
 * serializes as `rsi_<id>:prev[shift]`. Both are VALUE/blue. Neither is a
 * connectable handle — they are picker-awareness descriptors only, so the RSI
 * node still mounts a single primary output handle exactly as today.
 */
const RSI_OUTPUTS: Port[] = [
  { label: "Valor", side: "right", kind: "value" },
  { id: "prev", label: "Previous Value", side: "right", kind: "value" },
];

/**
 * ZScore's SEVEN outputs, the first indicator mixing VALUE and SIGNAL ports.
 *
 * Five VALUE outputs (blue) + two SIGNAL outputs (orange):
 *  - "Z-Score" is the PRIMARY (id-less) value output. Unlike SMA/RSI/MACD, the
 *    ZScore primary serializes WITH an explicit `:value` token
 *    (`zscore_<id>:value[shift]`) — the picker maps the id-less port to
 *    `outputId: "value"` (see ConditionIndicatorInput). It is a connectable
 *    source handle.
 *  - "|Z-Score|" (`zabs`) is OPERAND-ONLY: `connectable: false`, so NodeHandles
 *    never mounts it as a handle (no edge accepts it) — it exists purely as a
 *    picker-awareness descriptor.
 *  - "Media rodante (μ)" (`zmean`), "Desviación σ" (`zstd`) and
 *    "Media simple (SMA)" (`zsma`) are ARRAY value outputs (connectable),
 *    eligible as crossing value inputs.
 *  - "Z > 0" (`zgt`) / "Z < 0" (`zlt`) are SIGNAL outputs (connectable),
 *    eligible only as combinator condition inputs.
 *
 * `kind` is FE-internal accent metadata (value → blue, signal → orange); it is
 * never serialized. Output→operand serialization lives in conditionOperand.ts.
 */
const ZSCORE_OUTPUTS: Port[] = [
  { label: "Z-Score", side: "right", kind: "value", connectable: true },
  { id: "zabs", label: "|Z-Score|", side: "right", kind: "value" },
  {
    id: "zmean",
    label: "Media rodante (μ)",
    side: "right",
    kind: "value",
    connectable: true,
  },
  {
    id: "zstd",
    label: "Desviación σ",
    side: "right",
    kind: "value",
    connectable: true,
  },
  {
    id: "zsma",
    label: "Media simple (SMA)",
    side: "right",
    kind: "value",
    connectable: true,
  },
  {
    id: "zgt",
    label: "Z > 0",
    side: "right",
    kind: "signal",
    connectable: true,
  },
  {
    id: "zlt",
    label: "Z < 0",
    side: "right",
    kind: "signal",
    connectable: true,
  },
];

/**
 * The authoritative per-type table covering ALL 10 node types. Color tokens are
 * EXISTING GitLab palette tokens declared in `app/globals.css` (`@theme inline`).
 */
export const NODE_TYPE_REGISTRY: Record<NodeType, NodeTypeMeta> = {
  // Flow control: Start is output-only (no input), End is input-only (no output).
  Start: {
    icon: Play,
    colorToken: "gl-green-500",
    category: "Flujo",
    subtitle: "Inicio de la estrategia",
    inputPorts: [],
    outputPorts: [OUT],
  },
  End: {
    icon: Flag,
    colorToken: "gl-red-500",
    category: "Flujo",
    subtitle: "Fin de la estrategia",
    inputPorts: [IN],
    outputPorts: [],
  },
  // Condition: primary input + ONE cosmetic "Value 2" input; primary output "Señal".
  Condition: {
    icon: GitBranch,
    colorToken: "gl-orange-500",
    category: "Lógica",
    subtitle: "Comparación condicional",
    inputPorts: [IN, { id: "value2", label: "Valor 2", side: "left" }],
    outputPorts: [{ label: "Señal", side: "right" }],
  },
  // Indicators (blue).
  SMA: {
    icon: ChartLine,
    colorToken: "gl-blue-500",
    category: "Indicador",
    subtitle: "Media móvil",
    inputPorts: [IN],
    outputPorts: [OUT],
  },
  RSI: {
    icon: Activity,
    colorToken: "gl-blue-500",
    category: "Indicador",
    subtitle: "Índice de fuerza relativa",
    inputPorts: [IN],
    // Two outputs (Valor / Previous Value) for picker awareness; renders as today
    // (only the primary id-less "Valor" handle is mounted — see NodeHandles).
    outputPorts: RSI_OUTPUTS,
  },
  MACD: {
    icon: ChartSpline,
    colorToken: "gl-blue-500",
    category: "Indicador",
    subtitle: "Convergencia/divergencia",
    inputPorts: [IN],
    outputPorts: [OUT],
  },
  Stochastic: {
    icon: Waves,
    colorToken: "gl-blue-500",
    category: "Indicador",
    subtitle: "Oscilador estocástico",
    inputPorts: [IN],
    // Two outputs (%K / %D) for picker awareness; renders as today (only the
    // primary id-less %K handle is mounted — see NodeHandles / STOCH_OUTPUTS).
    outputPorts: STOCH_OUTPUTS,
  },
  ZScore: {
    icon: Sigma,
    colorToken: "gl-blue-500",
    category: "Indicador",
    subtitle: "Z-Score (rodante)",
    inputPorts: [IN],
    // Seven outputs (5 value + 2 signal): the primary id-less "Z-Score" handle
    // plus four connectable named handles (zmean/zstd/zsma value, zgt/zlt signal)
    // and the operand-only non-connectable |Z-Score| descriptor (see ZSCORE_OUTPUTS
    // / NodeHandles for which ports mount as real handles).
    outputPorts: ZSCORE_OUTPUTS,
  },
  // Orders.
  Buy: {
    icon: TrendingUp,
    colorToken: "gl-green-500",
    category: "Orden",
    subtitle: "Abrir posición de compra",
    inputPorts: [IN],
    outputPorts: [OUT],
  },
  Sell: {
    icon: TrendingDown,
    colorToken: "gl-red-500",
    category: "Orden",
    subtitle: "Abrir posición de venta",
    inputPorts: [IN],
    outputPorts: [OUT],
  },
  // Risk / utility.
  RiskManagement: {
    icon: ShieldCheck,
    colorToken: "gl-orange-500",
    category: "Riesgo",
    subtitle: "Gestión de riesgo",
    inputPorts: [IN],
    outputPorts: [OUT],
  },
  Log: {
    icon: ScrollText,
    colorToken: "gl-gray-600",
    category: "Utilidad",
    subtitle: "Registrar mensaje",
    inputPorts: [IN],
    outputPorts: [OUT],
  },
  // Boolean combinators (orange "Lógica"). Their input ports are REAL,
  // connectable, id'd handles (cond1..condN) — not cosmetic. The single
  // "Resultado" output is the primary id-less handle (standard out-degree wiring).
  LogicalAnd: {
    icon: Ampersand,
    colorToken: "gl-orange-500",
    category: "Lógica",
    subtitle: "Y lógico (AND)",
    inputPorts: combinatorInputs(6),
    outputPorts: [RESULT_OUT],
  },
  LogicalOr: {
    icon: Split,
    colorToken: "gl-orange-500",
    category: "Lógica",
    subtitle: "O lógico (OR)",
    inputPorts: combinatorInputs(6),
    outputPorts: [RESULT_OUT],
  },
  LogicalNot: {
    icon: Ban,
    colorToken: "gl-orange-500",
    category: "Lógica",
    subtitle: "Negación (NOT)",
    inputPorts: combinatorInputs(1),
    outputPorts: [RESULT_OUT],
  },
  LogicalXor: {
    icon: Diff,
    colorToken: "gl-orange-500",
    category: "Lógica",
    subtitle: "O exclusivo (XOR)",
    inputPorts: combinatorInputs(6),
    outputPorts: [RESULT_OUT],
  },
  // Crossing family (orange "Lógica"). Each takes TWO REAL connectable VALUE
  // inputs (value1/value2) fed by indicator nodes, and emits the primary id-less
  // "Señal de Cruce" output (standard out-degree wiring).
  BullishCross: {
    icon: TrendingUp,
    colorToken: "gl-orange-500",
    category: "Lógica",
    subtitle: "Cruce alcista",
    inputPorts: CROSSING_INPUTS,
    outputPorts: [CROSS_SIGNAL_OUT],
  },
  BearishCross: {
    icon: TrendingDown,
    colorToken: "gl-orange-500",
    category: "Lógica",
    subtitle: "Cruce bajista",
    inputPorts: CROSSING_INPUTS,
    outputPorts: [CROSS_SIGNAL_OUT],
  },
};

/**
 * Typed accessor with a safe fallback (Log) for unknown / future types, so the
 * renderer never crashes on a node whose domain type isn't in the registry.
 */
export function getNodeTypeMeta(type: NodeType): NodeTypeMeta {
  return NODE_TYPE_REGISTRY[type] ?? NODE_TYPE_REGISTRY.Log;
}
