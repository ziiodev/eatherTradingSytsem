/**
 * Static colorToken -> Tailwind class mapping for custom nodes.
 *
 * The node-type registry stores a dynamic `colorToken` (e.g. `gl-green-500`).
 * Tailwind's JIT only emits classes it can see as COMPLETE literal strings, so
 * a runtime `border-${token}` template would be purged. This module maps each
 * known token to its concrete, statically-analyzable class strings, with a
 * neutral fallback for unknown tokens (AI/future types).
 */

/** Concrete classes derived from a registry colorToken. */
export interface NodeColorClasses {
  /** Card border accent. */
  border: string;
  /** Icon / accent text color. */
  text: string;
  /** Subtle category-badge background. */
  badgeBg: string;
  /**
   * Translucent EXPANDED-card fill, same hue as `border` at a low opacity so the
   * card reads as "transparent, tinted in the border color" and the canvas grid
   * stays subtly visible through it. STATIC literals (JIT-safe).
   */
  cardBg: string;
}

const COLOR_MAP: Record<string, NodeColorClasses> = {
  "gl-green-500": {
    border: "border-gl-green-500",
    text: "text-gl-green-500",
    badgeBg: "bg-gl-green-500/15",
    cardBg: "bg-gl-green-500/10",
  },
  "gl-red-500": {
    border: "border-gl-red-500",
    text: "text-gl-red-500",
    badgeBg: "bg-gl-red-500/15",
    cardBg: "bg-gl-red-500/10",
  },
  "gl-orange-500": {
    border: "border-gl-orange-500",
    text: "text-gl-orange-500",
    badgeBg: "bg-gl-orange-500/15",
    cardBg: "bg-gl-orange-500/10",
  },
  "gl-blue-500": {
    border: "border-gl-blue-500",
    text: "text-gl-blue-500",
    badgeBg: "bg-gl-blue-500/15",
    cardBg: "bg-gl-blue-500/10",
  },
  "gl-gray-600": {
    border: "border-gl-gray-600",
    text: "text-gl-gray-200",
    badgeBg: "bg-gl-gray-600/30",
    cardBg: "bg-gl-gray-600/15",
  },
};

/** Neutral fallback for unknown / future tokens. */
const FALLBACK: NodeColorClasses = {
  border: "border-border",
  text: "text-muted-foreground",
  badgeBg: "bg-muted",
  cardBg: "bg-muted/10",
};

/** Resolve a registry colorToken to concrete, JIT-safe Tailwind classes. */
export function nodeColorClasses(colorToken: string): NodeColorClasses {
  return COLOR_MAP[colorToken] ?? FALLBACK;
}
