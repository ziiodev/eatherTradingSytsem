/**
 * Pure-UI preset library for indicator nodes (RSI only for now).
 *
 * A preset is a convenience bundle of param values a user can apply in one
 * click; it is NOT a persisted concept. Applying a preset performs a single
 * coalesced `updateNodeData` call that writes the bundled flat params into
 * `node.data` (one undo step). The selected preset name is LOCAL display state
 * in the selector component — it is NEVER written to `node.data` or the
 * serialized graph (no `data.preset` key), so two graphs with identical params
 * are byte-identical regardless of which preset produced them.
 */

/** The RSI params a preset sets (a subset of the RSI schema's flat keys). */
export interface RsiPresetParams {
  period: number;
  nivel_sobreventa: number;
  nivel_sobrecompra: number;
  bar_shift: number;
}

/**
 * RSI presets keyed by their human-facing label. `"Personalizado"` maps to
 * `null` (a no-op sentinel): selecting it applies nothing, letting the user keep
 * hand-tuned values. Numeric bundles mirror the locked RSI defaults' shape.
 */
export const RSI_PRESETS: Record<string, RsiPresetParams | null> = {
  "Day Trading (15min-4H)": {
    period: 14,
    nivel_sobreventa: 30,
    nivel_sobrecompra: 70,
    bar_shift: 0,
  },
  "Scalping (1-5min)": {
    period: 7,
    nivel_sobreventa: 20,
    nivel_sobrecompra: 80,
    bar_shift: 0,
  },
  "Swing (4H-1D)": {
    period: 21,
    nivel_sobreventa: 35,
    nivel_sobrecompra: 65,
    bar_shift: 0,
  },
  Conservador: {
    period: 14,
    nivel_sobreventa: 25,
    nivel_sobrecompra: 75,
    bar_shift: 0,
  },
  // No-op sentinel: keep the user's current params untouched.
  Personalizado: null,
};

/** Ordered preset labels for rendering the selector options stably. */
export const RSI_PRESET_NAMES = Object.keys(RSI_PRESETS);
