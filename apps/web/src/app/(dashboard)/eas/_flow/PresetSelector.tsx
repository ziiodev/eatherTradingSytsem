"use client";

/**
 * Pure-UI preset `<select>` for the RSI node, rendered ABOVE the params in the
 * expanded body. Picking a named preset bulk-applies its bundled params in ONE
 * coalesced `updateNodeData` call (a multi-key partial => a single undo step);
 * "Personalizado" is a no-op so hand-tuned values are kept.
 *
 * The selected preset is LOCAL display state ONLY — it is deliberately NOT
 * derived from `node.data` and NOT written back (no `data.preset` key), so the
 * serialized graph stays byte-identical regardless of which preset was used.
 * On mount it shows "Personalizado" (no inference from current params).
 */
import { useState } from "react";
import { useGraphStore } from "../_stores/graphStore";
import {
  RSI_PRESETS,
  RSI_PRESET_NAMES,
  type RsiPresetParams,
} from "../_lib/indicatorPresets";

const INPUT_CLASS =
  "border-border bg-background focus-visible:ring-primary w-full rounded border px-1.5 py-0.5 text-xs outline-none focus-visible:ring-2";

/** Initial selection: no preset is inferred from stored params. */
const CUSTOM = "Personalizado";

export function PresetSelector({ nodeId }: { nodeId: string }) {
  const updateNodeData = useGraphStore((s) => s.updateNodeData);
  // Local-only: which preset label is shown. NOT persisted to node.data.
  const [selected, setSelected] = useState<string>(CUSTOM);

  const onSelect = (name: string) => {
    setSelected(name);
    const preset: RsiPresetParams | null = RSI_PRESETS[name] ?? null;
    // "Personalizado" (null) is a no-op — leave the user's params untouched.
    if (preset == null) return;
    // Multi-key partial => one coalesced history snapshot => single undo step.
    updateNodeData(nodeId, { ...preset });
  };

  return (
    <label className="block space-y-0.5">
      <span className="text-muted-foreground text-[10px] font-medium">
        Preset
      </span>
      <select
        aria-label="RSI preset"
        value={selected}
        onChange={(e) => onSelect(e.target.value)}
        className={INPUT_CLASS}
      >
        {RSI_PRESET_NAMES.map((name) => (
          <option key={name} value={name}>
            {name}
          </option>
        ))}
      </select>
    </label>
  );
}
