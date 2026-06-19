/**
 * Helpers for exporting generated MQL5 code from the result modal.
 * Kept separate so GeneratedCodeModal stays small and focused on rendering.
 */

/** Sanitize a (possibly messy) EA name into a safe filename stem. */
function sanitizeEaName(eaName?: string): string {
  const base = (eaName ?? "")
    .normalize("NFKD")
    .replace(/[^a-zA-Z0-9_-]+/g, "_")
    .replace(/^_+|_+$/g, "");
  return base || "GeneratedEA";
}

/** Build a safe `<name>.mq5` filename from a (possibly messy) EA name. */
export function mq5Filename(eaName?: string): string {
  return `${sanitizeEaName(eaName)}.mq5`;
}

/** Build a safe `<name>.py` filename from a (possibly messy) EA name. */
export function pyFilename(eaName?: string): string {
  return `${sanitizeEaName(eaName)}.py`;
}

/** Trigger a browser download of `code` as `filename` via a transient anchor. */
export function downloadCode(code: string, filename: string): void {
  const blob = new Blob([code], { type: "text/plain;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}
