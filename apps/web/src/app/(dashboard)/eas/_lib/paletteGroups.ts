/**
 * Declarative taxonomy for the editor node palette — the SINGLE source of truth
 * for how node types are grouped and ordered in the sidebar.
 *
 * This is PRESENTATION ONLY: it references `NodeType` ids and never reads the
 * registry `category` field, never touches `node.data`, the serialized graph,
 * codegen, or the DnD contract. Adding/moving a node in the palette = editing
 * the table below.
 *
 * Two groups (AI, Strategy Boost) are `placeholder`s: they carry no node items
 * and render a non-draggable "Próximamente" row. `General` is `provisional`
 * (interim home for flow/utility nodes) and MUST always render LAST.
 */
import type { LucideIcon } from "lucide-react";
import {
  Sparkles,
  ChartLine,
  GitBranch,
  Zap,
  ShieldCheck,
  Rocket,
  LayoutGrid,
  CircleX,
  Settings,
  ListOrdered,
  Clock,
  Binary,
  Waves,
  Gauge,
  BarChart3,
  Flame,
  Layers,
  AudioWaveform,
  ToggleLeft,
  Scale,
  Link,
  Spline,
  Sigma,
} from "lucide-react";
import type { NodeType } from "../_types/graph";
import { NODE_TYPE_REGISTRY } from "./nodeTypeRegistry";

/** A draggable leaf in the palette: a concrete domain node type. */
export type PaletteItem = NodeType;

/** An optional nested, indented cluster of items under a group. */
export interface PaletteSubgroup {
  id: string;
  label: string;
  /** Optional icon rendered before the sub-header label (subordinate to the group icon). */
  icon?: LucideIcon;
  items: NodeType[];
}

/**
 * A top-level palette group. A group either holds node `items`/`subgroups`, or
 * is a `placeholder` (renders a "Próximamente" row, no items). `provisional`
 * marks an interim group; it renders identically to a normal group.
 */
export interface PaletteGroup {
  id: string;
  label: string;
  icon?: LucideIcon;
  placeholder?: boolean;
  provisional?: boolean;
  items?: NodeType[];
  subgroups?: PaletteSubgroup[];
}

/**
 * PALETTE-ONLY display-label overrides for individual node types. This is
 * presentation sugar for the sidebar row text ONLY — it never touches the DnD
 * payload (which stays the bare NodeType), `node.data`, the on-node header, the
 * registry, or codegen. A type absent from this map falls back to its raw id.
 */
export const PALETTE_ITEM_LABELS: Partial<Record<NodeType, string>> = {
  Buy: "Compra de mercado",
  Sell: "Venta de mercado",
  Stochastic: "Estocástico",
  ZScore: "Z-Score (rodante)",
  LogicalAnd: "Y Lógico",
  LogicalOr: "O Lógico",
  LogicalNot: "NO Lógico",
  LogicalXor: "XOR Lógico",
  BullishCross: "Cruce Alcista",
  BearishCross: "Cruce Bajista",
};

/**
 * Ordered top-level groups, rendered top-to-bottom exactly as listed. `general`
 * is provisional and stays LAST.
 */
export const PALETTE_GROUPS: PaletteGroup[] = [
  {
    id: "ai",
    label: "IA",
    icon: Sparkles,
    placeholder: true,
  },
  {
    id: "indicators",
    label: "Indicadores",
    icon: ChartLine,
    // SMA stays as a DIRECT item (rendered above the subgroups). RSI/MACD now
    // live under the "Osciladores" subgroup. The remaining empty subgroups render
    // a "Próximamente" row each; empty arrays contribute no NodeTypes, so the dev
    // guard is unaffected.
    items: ["SMA"],
    subgroups: [
      {
        id: "indicators-algorithmic",
        label: "Algorítmicos",
        icon: Binary,
        items: [],
      },
      {
        id: "indicators-bill-williams",
        label: "Bill Williams",
        icon: Waves,
        items: [],
      },
      { id: "indicators-hft", label: "HFT", icon: Gauge, items: [] },
      {
        id: "indicators-market-profile",
        label: "Market Profile",
        icon: BarChart3,
        items: [],
      },
      { id: "indicators-momentum", label: "Momentum", icon: Flame, items: [] },
      {
        id: "indicators-mtf",
        label: "Multi-Timeframe",
        icon: Layers,
        items: [],
      },
      {
        id: "indicators-oscillators",
        label: "Osciladores",
        icon: AudioWaveform,
        items: ["RSI", "MACD", "Stochastic", "ZScore"],
      },
    ],
  },
  {
    id: "logic",
    label: "Lógica",
    icon: GitBranch,
    // Condition stays as a DIRECT item (rendered above the subgroups). The five
    // subgroups are still empty and render a "Próximamente" row each; empty
    // arrays contribute no NodeTypes, so the dev guard is unaffected.
    items: ["Condition"],
    subgroups: [
      {
        id: "logic-boolean",
        label: "Boolean",
        icon: ToggleLeft,
        items: ["LogicalAnd", "LogicalOr", "LogicalNot", "LogicalXor"],
      },
      {
        id: "logic-comparison",
        label: "Comparación",
        icon: Scale,
        items: [],
      },
      { id: "logic-connectors", label: "Conectores", icon: Link, items: [] },
      {
        id: "logic-crosses",
        label: "Cruces",
        icon: Spline,
        items: ["BullishCross", "BearishCross"],
      },
      { id: "logic-math", label: "Math", icon: Sigma, items: [] },
    ],
  },
  {
    id: "actions",
    label: "Acciones",
    icon: Zap,
    // All order nodes live under subgroups now; the still-empty subgroups render
    // a "Próximamente" row until they gain node items. Empty arrays contribute no
    // NodeTypes to coverage, so the dev guard is unaffected. "Órdenes" holds the
    // market order nodes (Buy, Sell).
    subgroups: [
      { id: "actions-close", label: "Cerrar", icon: CircleX, items: [] },
      { id: "actions-manage", label: "Gestión", icon: Settings, items: [] },
      {
        id: "actions-orders",
        label: "Órdenes",
        icon: ListOrdered,
        items: ["Buy", "Sell"],
      },
      { id: "actions-pending", label: "Pendientes", icon: Clock, items: [] },
    ],
  },
  {
    id: "risk",
    label: "Gestión de Riesgo",
    icon: ShieldCheck,
    items: ["RiskManagement"],
  },
  { id: "boost", label: "Strategy Boost", icon: Rocket, placeholder: true },
  {
    id: "general",
    label: "General",
    icon: LayoutGrid,
    provisional: true,
    items: ["Start", "End", "Log"],
  },
];

/**
 * Normalize text for accent-insensitive, case-insensitive substring matching:
 * decompose accents (NFD) then strip the combining diacritic marks, and
 * lowercase. So "Órdenes" -> "ordenes", "RSI" -> "rsi".
 */
function normalize(text: string): string {
  return text
    .normalize("NFD")
    .replace(/\p{Diacritic}/gu, "")
    .toLowerCase();
}

/** True when `needle` (already normalized) is a substring of `haystack`. */
function matches(haystack: string, needle: string): boolean {
  return normalize(haystack).includes(needle);
}

/** True when a node item matches the query by friendly label OR raw type id. */
function itemMatches(type: NodeType, needle: string): boolean {
  const label = PALETTE_ITEM_LABELS[type] ?? type;
  return matches(label, needle) || matches(type, needle);
}

/**
 * Pure, accent-insensitive filter over PALETTE_GROUPS for the sidebar search
 * box. Returns NEW group/subgroup objects (never mutates the source taxonomy):
 *
 * - Item: matches if the query is a substring of its friendly label or raw id.
 * - Subgroup: included if its label matches (keep ALL its items) OR it has any
 *   matching items (keep only those).
 * - Group: included if its label matches (keep all items + subgroups) OR it has
 *   any matching direct items OR any included subgroups. Placeholder groups have
 *   nothing to match, so they are EXCLUDED for a non-empty query.
 *
 * An empty/whitespace query returns PALETTE_GROUPS unchanged (reference-equal).
 */
export function filterPaletteGroups(query: string): PaletteGroup[] {
  const needle = normalize(query.trim());
  if (needle === "") return PALETTE_GROUPS;

  const out: PaletteGroup[] = [];
  for (const group of PALETTE_GROUPS) {
    // Placeholder groups carry no items: nothing to match while searching.
    if (group.placeholder) continue;

    const groupLabelHit = matches(group.label, needle);

    const items = groupLabelHit
      ? group.items
      : group.items?.filter((t) => itemMatches(t, needle));

    const subgroups = group.subgroups
      ?.map((sub) => {
        if (groupLabelHit || matches(sub.label, needle)) return sub;
        const subItems = sub.items.filter((t) => itemMatches(t, needle));
        return subItems.length > 0 ? { ...sub, items: subItems } : null;
      })
      .filter((sub): sub is PaletteSubgroup => sub !== null);

    const hasItems = (items?.length ?? 0) > 0;
    const hasSubgroups = (subgroups?.length ?? 0) > 0;
    if (groupLabelHit || hasItems || hasSubgroups) {
      out.push({ ...group, items, subgroups });
    }
  }
  return out;
}

/**
 * Flatten every node item declared across groups (direct items + subgroup
 * items) into a single ordered list. Used by the coverage guard.
 */
function flattenPaletteItems(): NodeType[] {
  const out: NodeType[] = [];
  for (const group of PALETTE_GROUPS) {
    if (group.items) out.push(...group.items);
    if (group.subgroups) {
      for (const sub of group.subgroups) out.push(...sub.items);
    }
  }
  return out;
}

/**
 * DEV-ONLY invariant: the palette must place every canonical NodeType exactly
 * once — no missing, duplicated, or unknown ids. Never throws (a palette bug
 * must not crash the editor); it only `console.error`s. No-op in production.
 */
export function assertPaletteCoverage(): void {
  if (process.env.NODE_ENV === "production") return;

  const placed = flattenPaletteItems();
  const canonical = new Set(Object.keys(NODE_TYPE_REGISTRY));
  const seen = new Set<string>();

  for (const type of placed) {
    if (!canonical.has(type)) {
      console.error(`[paletteGroups] Unknown NodeType in palette: "${type}"`);
    }
    if (seen.has(type)) {
      console.error(
        `[paletteGroups] Duplicated NodeType in palette: "${type}"`,
      );
    }
    seen.add(type);
  }

  for (const type of canonical) {
    if (!seen.has(type)) {
      console.error(`[paletteGroups] Missing NodeType in palette: "${type}"`);
    }
  }
}

// Run the coverage check once at module-eval time (dev only; no-op in prod).
assertPaletteCoverage();
