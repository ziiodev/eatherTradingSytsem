/**
 * Schema-driven parameter registry for the visual node editor.
 *
 * Each node type maps to an ordered list of editable fields. The `default`
 * values here MUST EQUAL the backend MQL5 codegen defaults EXACTLY, so that a
 * freshly placed node (or an untouched graph) generates byte-identical code.
 * The codegen engine reads params as FLAT keys directly off `node.data`.
 */
import type { NodeData, NodeType } from "../_types/graph";

/**
 * A select option pairing a stored `value` (the raw MQL5 constant baked into
 * generated code) with a human-friendly `label` shown in the inspector.
 */
export interface FieldOption {
  value: string;
  label: string;
}

/** A single editable field rendered by the NodeInspector. */
export interface FieldDef {
  key: string;
  label: string;
  kind: "number" | "text" | "select" | "boolean";
  /**
   * The field's default value. `number`/`text`/`select` use number|string as
   * before; the `boolean` kind (a checkbox) carries a JS `boolean` default.
   */
  default: number | string | boolean;
  /**
   * For `select` fields: the allowed options. Bare strings are treated as
   * `{ value: o, label: o }`; `FieldOption` objects decouple stored value from
   * the visible label. Normalize with `normalizeOptions` before rendering.
   */
  options?: Array<string | FieldOption>;
  /** For `number` fields: minimum allowed value (used for clamping). */
  min?: number;
  /** For `number` fields: input step granularity. */
  step?: number;
}

/**
 * Normalize a heterogeneous option list into `FieldOption[]`: `undefined` -> `[]`,
 * a bare string `o` -> `{ value: o, label: o }`, an object passes through.
 */
export function normalizeOptions(
  opts?: Array<string | FieldOption>,
): FieldOption[] {
  if (opts == null) return [];
  return opts.map((o) => (typeof o === "string" ? { value: o, label: o } : o));
}

/**
 * Operator options for Condition nodes, pinned EXACTLY to the codegen set.
 * VALUES are the raw operators baked into generated code; labels are verbose.
 */
const OPERATOR_OPTIONS: FieldOption[] = [
  { value: ">", label: "Greater than (>)" },
  { value: "<", label: "Less than (<)" },
  { value: ">=", label: "Greater than or equal (>=)" },
  { value: "<=", label: "Less than or equal (<=)" },
  { value: "==", label: "Equal (==)" },
  { value: "!=", label: "Not equal (!=)" },
];

/**
 * Applied-price options for indicators, ordered. VALUES are the fully-qualified
 * MQL5 constants baked verbatim into generated code; the codegen default is
 * `PRICE_CLOSE`, so it MUST stay first/default to keep graphs byte-identical.
 */
const APPLIED_PRICE_OPTIONS: FieldOption[] = [
  { value: "PRICE_CLOSE", label: "Close" },
  { value: "PRICE_OPEN", label: "Open" },
  { value: "PRICE_HIGH", label: "High" },
  { value: "PRICE_LOW", label: "Low" },
  { value: "PRICE_MEDIAN", label: "Median (HL/2)" },
  { value: "PRICE_TYPICAL", label: "Typical (HLC/3)" },
  { value: "PRICE_WEIGHTED", label: "Weighted (HLCC/4)" },
];

/**
 * Moving-average method options for the SMA node, ordered. VALUES are the
 * fully-qualified MQL5 constants; the codegen default is `MODE_SMA`.
 */
const MA_METHOD_OPTIONS: FieldOption[] = [
  { value: "MODE_SMA", label: "Simple (SMA)" },
  { value: "MODE_EMA", label: "Exponential (EMA)" },
  { value: "MODE_SMMA", label: "Smoothed (SMMA)" },
  { value: "MODE_LWMA", label: "Linear weighted (LWMA)" },
];

/**
 * Price-field options for the Stochastic node, ordered. VALUES are the raw MQL5
 * constants baked verbatim into generated code; the codegen default is
 * `STO_LOWHIGH`, so it MUST stay first/default to keep graphs byte-identical.
 */
const STO_PRICE_FIELD_OPTIONS: FieldOption[] = [
  { value: "STO_LOWHIGH", label: "Low/High" },
  { value: "STO_CLOSECLOSE", label: "Close/Close" },
];

/**
 * Rolling-window mode options for the ZScore node, ordered. VALUES are the raw
 * tokens the backend codegen branches on; the default is `inclusive` (the
 * shift bar is counted INSIDE the N-bar window), so it MUST stay first/default
 * to keep graphs byte-identical with the backend ZScore schema.
 */
const ZSCORE_WINDOW_OPTIONS: FieldOption[] = [
  { value: "inclusive", label: "Inclusiva (barra shift dentro de N)" },
  { value: "exclusive", label: "Exclusiva" },
];

/**
 * Standard-deviation estimator options for the ZScore node, ordered. VALUES are
 * the raw tokens the backend codegen branches on; the default is `sample` (the
 * Bessel-corrected n−1 estimator), so it MUST stay first/default to match the
 * backend ZScore schema.
 */
const ZSCORE_STDDEV_OPTIONS: FieldOption[] = [
  { value: "sample", label: "Muestral (n−1, Bessel)" },
  { value: "population", label: "Poblacional (n)" },
];

/**
 * Shared field block for the crossing family (BullishCross/BearishCross). Both
 * types carry the SAME params; keys + defaults are byte-identical to the backend
 * crossing schema. `filtrar_ruido`/`usar_metodo_desplazamiento` reuse the
 * existing `boolean` checkbox kind (introduced by the LogicalAnd combinator).
 */
const CROSSING_FIELDS: FieldDef[] = [
  {
    key: "barras_confirmacion",
    label: "Barras de Confirmación",
    kind: "number",
    default: 1,
    min: 1,
    step: 1,
  },
  {
    key: "distancia_minima_pips",
    label: "Distancia Mínima (pips)",
    kind: "number",
    default: 0,
    min: 0,
    step: 1,
  },
  {
    key: "filtrar_ruido",
    label: "Filtrar Ruido",
    kind: "boolean",
    default: true,
  },
  {
    key: "usar_metodo_desplazamiento",
    label: "Usar Método de Desplazamiento",
    kind: "boolean",
    default: false,
  },
];

/**
 * Field definitions per node type. Keys + defaults mirror the codegen FLAT
 * param contract. Start/End carry no params.
 */
export const NODE_PARAM_SCHEMAS: Record<NodeType, FieldDef[]> = {
  Start: [],
  End: [],
  Buy: [
    {
      key: "lots",
      label: "Lots",
      kind: "number",
      default: 0.1,
      min: 0,
      step: 0.01,
    },
    {
      key: "stop_loss",
      label: "Stop loss",
      kind: "number",
      default: 0,
      min: 0,
      step: 1,
    },
    {
      key: "take_profit",
      label: "Take profit",
      kind: "number",
      default: 0,
      min: 0,
      step: 1,
    },
    {
      key: "sl_pips",
      label: "SL pips (risk sizing)",
      kind: "number",
      default: 0,
      min: 0,
      step: 1,
    },
    {
      key: "tp_pips",
      label: "TP pips",
      kind: "number",
      default: 0,
      min: 0,
      step: 1,
    },
    {
      key: "trail_pips",
      label: "Trailing stop (pips)",
      kind: "number",
      default: 0,
      min: 0,
      step: 1,
    },
    {
      key: "trail_start_pips",
      label: "Trailing start (pips)",
      kind: "number",
      default: 0,
      min: 0,
      step: 1,
    },
    {
      key: "trail_step_pips",
      label: "Trailing step (pips)",
      kind: "number",
      default: 0,
      min: 0,
      step: 1,
    },
  ],
  Sell: [
    {
      key: "lots",
      label: "Lots",
      kind: "number",
      default: 0.1,
      min: 0,
      step: 0.01,
    },
    {
      key: "stop_loss",
      label: "Stop loss",
      kind: "number",
      default: 0,
      min: 0,
      step: 1,
    },
    {
      key: "take_profit",
      label: "Take profit",
      kind: "number",
      default: 0,
      min: 0,
      step: 1,
    },
    {
      key: "sl_pips",
      label: "SL pips (risk sizing)",
      kind: "number",
      default: 0,
      min: 0,
      step: 1,
    },
    {
      key: "tp_pips",
      label: "TP pips",
      kind: "number",
      default: 0,
      min: 0,
      step: 1,
    },
    {
      key: "trail_pips",
      label: "Trailing stop (pips)",
      kind: "number",
      default: 0,
      min: 0,
      step: 1,
    },
    {
      key: "trail_start_pips",
      label: "Trailing start (pips)",
      kind: "number",
      default: 0,
      min: 0,
      step: 1,
    },
    {
      key: "trail_step_pips",
      label: "Trailing step (pips)",
      kind: "number",
      default: 0,
      min: 0,
      step: 1,
    },
  ],
  SMA: [
    {
      key: "period",
      label: "Period",
      kind: "number",
      default: 14,
      min: 1,
      step: 1,
    },
    {
      key: "shift",
      label: "Shift",
      kind: "number",
      default: 0,
      min: 0,
      step: 1,
    },
    {
      key: "ma_method",
      label: "MA method",
      kind: "select",
      default: "MODE_SMA",
      options: MA_METHOD_OPTIONS,
    },
    {
      key: "applied_price",
      label: "Applied price",
      kind: "select",
      default: "PRICE_CLOSE",
      options: APPLIED_PRICE_OPTIONS,
    },
  ],
  // RSI param block — order/defaults LOCKED byte-identical to the backend
  // schema_data NodeDef (period, nivel_sobreventa, nivel_sobrecompra, bar_shift,
  // applied_price). `bar_shift` is the bar offset the operand picker reads when
  // serializing an RSI reference; the two `nivel_*` levels are oversold/overbought
  // thresholds. Defaults must match codegen so untouched graphs stay byte-identical.
  RSI: [
    {
      key: "period",
      label: "Period",
      kind: "number",
      default: 14,
      min: 1,
      step: 1,
    },
    {
      key: "nivel_sobreventa",
      label: "Nivel Sobreventa",
      kind: "number",
      default: 30,
      min: 0,
      step: 1,
    },
    {
      key: "nivel_sobrecompra",
      label: "Nivel Sobrecompra",
      kind: "number",
      default: 70,
      min: 0,
      step: 1,
    },
    {
      key: "bar_shift",
      label: "Bar Shift",
      kind: "number",
      default: 0,
      min: 0,
      step: 1,
    },
    {
      key: "applied_price",
      label: "Applied price",
      kind: "select",
      default: "PRICE_CLOSE",
      options: APPLIED_PRICE_OPTIONS,
    },
  ],
  MACD: [
    {
      key: "fast_ema",
      label: "Fast EMA",
      kind: "number",
      default: 12,
      min: 1,
      step: 1,
    },
    {
      key: "slow_ema",
      label: "Slow EMA",
      kind: "number",
      default: 26,
      min: 1,
      step: 1,
    },
    {
      key: "signal",
      label: "Signal",
      kind: "number",
      default: 9,
      min: 1,
      step: 1,
    },
    {
      key: "applied_price",
      label: "Applied price",
      kind: "select",
      default: "PRICE_CLOSE",
      options: APPLIED_PRICE_OPTIONS,
    },
  ],
  Stochastic: [
    {
      key: "k_period",
      label: "%K period",
      kind: "number",
      default: 14,
      min: 1,
      step: 1,
    },
    {
      key: "d_period",
      label: "%D period",
      kind: "number",
      default: 3,
      min: 1,
      step: 1,
    },
    {
      key: "slowing",
      label: "Slowing",
      kind: "number",
      default: 3,
      min: 1,
      step: 1,
    },
    {
      key: "ma_method",
      label: "MA method",
      kind: "select",
      default: "MODE_SMA",
      options: MA_METHOD_OPTIONS,
    },
    {
      key: "price_field",
      label: "Price field",
      kind: "select",
      default: "STO_LOWHIGH",
      options: STO_PRICE_FIELD_OPTIONS,
    },
  ],
  // ZScore param block — order/defaults LOCKED byte-identical to the backend
  // schema_data NodeDef (periodo_ventana, barras_sma, desplazamiento_barra,
  // precio_aplicado, ventana_mu_sigma, desviacion_estandar). The rolling Z-score
  // is ASSEMBLED from native pieces (iMA μ over `periodo_ventana`, iStdDev σ, and
  // a SECOND iMA SMA over `barras_sma`) plus a computed scalar `z`. Defaults must
  // match codegen so untouched graphs stay byte-identical.
  ZScore: [
    {
      key: "periodo_ventana",
      label: "Periodo Ventana",
      kind: "number",
      default: 20,
      min: 2,
      step: 1,
    },
    {
      key: "barras_sma",
      label: "Barras SMA",
      kind: "number",
      default: 500,
      min: 1,
      step: 1,
    },
    {
      key: "desplazamiento_barra",
      label: "Desplazamiento de Barra",
      kind: "number",
      default: 0,
      min: 0,
      step: 1,
    },
    {
      key: "precio_aplicado",
      label: "Precio Aplicado",
      kind: "select",
      default: "PRICE_CLOSE",
      options: APPLIED_PRICE_OPTIONS,
    },
    {
      key: "ventana_mu_sigma",
      label: "Ventana μ/σ",
      kind: "select",
      default: "inclusive",
      options: ZSCORE_WINDOW_OPTIONS,
    },
    {
      key: "desviacion_estandar",
      label: "Desviación Estándar",
      kind: "select",
      default: "sample",
      options: ZSCORE_STDDEV_OPTIONS,
    },
  ],
  Condition: [
    { key: "left", label: "Left", kind: "text", default: "0" },
    {
      key: "operator",
      label: "Operator",
      kind: "select",
      default: ">",
      options: OPERATOR_OPTIONS,
    },
    { key: "right", label: "Right", kind: "text", default: "0" },
  ],
  RiskManagement: [
    {
      key: "max_positions",
      label: "Max positions",
      kind: "number",
      default: 1,
      min: 0,
      step: 1,
    },
    {
      key: "risk_percent",
      label: "Risk percent",
      kind: "number",
      default: 1.0,
      min: 0,
      step: 0.1,
    },
  ],
  Log: [{ key: "message", label: "Message", kind: "text", default: "log" }],
  // Boolean combinators. Keys + defaults are byte-identical to the backend
  // schema_data/snapshot. LogicalOr/LogicalNot/LogicalXor carry no params.
  LogicalAnd: [
    {
      key: "requerir_todas",
      label: "Requerir Todas",
      kind: "boolean",
      default: false,
    },
    {
      key: "min_true",
      label: "Condiciones Verdaderas Mínimas",
      kind: "number",
      default: 2,
      min: 1,
      step: 1,
    },
  ],
  LogicalOr: [],
  LogicalNot: [],
  LogicalXor: [],
  // Crossing family: both share CROSSING_FIELDS (keys/defaults byte-identical
  // to the backend crossing schema). Each is fed by two indicator value edges.
  BullishCross: CROSSING_FIELDS,
  BearishCross: CROSSING_FIELDS,
};

/**
 * Build the default flat `data` payload for a node type: the React Flow label
 * and domain type, plus every registry field at its codegen-matching default.
 */
export function defaultDataFor(type: NodeType): NodeData {
  const data: NodeData = { label: type, type };
  for (const field of NODE_PARAM_SCHEMAS[type]) {
    data[field.key] = field.default;
  }
  return data;
}
