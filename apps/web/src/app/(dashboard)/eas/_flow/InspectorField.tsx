"use client";

/**
 * A single editable param-field row (rendered by NodeParamFields inside the
 * on-node expanded body). Renders a number/text/select input from a registry
 * FieldDef and pushes changes back via onChange (flat key).
 */
import { normalizeOptions, type FieldDef } from "../_lib/nodeParamSchemas";

// Compact controls sized for the on-node expanded body (the only consumer now
// that the side inspector is gone): tighter padding + smaller font.
const INPUT_CLASS =
  "border-border bg-background focus-visible:ring-primary w-full rounded border px-1.5 py-0.5 text-xs outline-none focus-visible:ring-2";

export function InspectorField({
  field,
  value,
  onChange,
}: {
  field: FieldDef;
  value: number | string | boolean;
  onChange: (value: number | string | boolean) => void;
}) {
  // Boolean fields render an inline checkbox + label on a single row (the
  // checkbox carries/writes a real JS boolean, never a string/number).
  if (field.kind === "boolean") {
    return (
      <label className="flex items-center gap-1.5">
        <input
          type="checkbox"
          checked={Boolean(value)}
          onChange={(e) => onChange(e.target.checked)}
          className="border-border bg-background accent-primary h-3 w-3 rounded"
        />
        <span className="text-muted-foreground text-[10px] font-medium">
          {field.label}
        </span>
      </label>
    );
  }
  return (
    <label className="block space-y-0.5">
      <span className="text-muted-foreground text-[10px] font-medium">
        {field.label}
      </span>
      {field.kind === "select" ? (
        (() => {
          const current = String(value);
          const opts = normalizeOptions(field.options);
          // Unknown-value fallback: surface a legacy/stored value the option set
          // doesn't know about so the native select shows it verbatim instead of
          // silently coercing the selection to option[0].
          const display = opts.some((o) => o.value === current)
            ? opts
            : [{ value: current, label: current }, ...opts];
          return (
            <select
              value={current}
              onChange={(e) => onChange(e.target.value)}
              className={INPUT_CLASS}
            >
              {display.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
          );
        })()
      ) : field.kind === "number" ? (
        <input
          type="number"
          value={String(value)}
          min={field.min}
          step={field.step}
          onChange={(e) => {
            const next = Number(e.target.value);
            // Reject NaN (empty/partial input) and clamp to the field minimum.
            if (Number.isNaN(next)) return;
            onChange(field.min != null ? Math.max(field.min, next) : next);
          }}
          className={INPUT_CLASS}
        />
      ) : (
        <input
          type="text"
          value={String(value)}
          onChange={(e) => onChange(e.target.value)}
          className={INPUT_CLASS}
        />
      )}
    </label>
  );
}
