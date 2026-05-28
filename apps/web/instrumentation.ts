/**
 * Next.js instrumentation hook — runtime-level observability bootstrap.
 *
 * Called once per server / edge runtime when the Next.js app boots. We
 * use it to initialise Sentry, gated on ``NEXT_PUBLIC_SENTRY_DSN`` so an
 * unconfigured environment is a no-op (same shape as the backend's
 * feature-flag posture).
 *
 * The Sentry SDK is imported lazily (``await import(...)``) so the
 * bundle stays slim when DSN is unset. ``@sentry/nextjs`` itself is an
 * optional peer dep — when it's not installed, the import fails and we
 * log + skip without crashing the app.
 *
 * The matching browser-side bootstrap lives in ``instrumentation-client.ts``.
 */

export async function register(): Promise<void> {
  const dsn = process.env.NEXT_PUBLIC_SENTRY_DSN;
  if (!dsn) return;

  // Runtime split: Node vs Edge. The two have different Sentry init paths
  // but share the same PII scrubber and DSN.
  const runtime = process.env.NEXT_RUNTIME;
  if (runtime === "nodejs") {
    await import("./sentry.server.config").catch((err: unknown) => {
      // eslint-disable-next-line no-console
      console.warn("[sentry] server init skipped:", err);
    });
  } else if (runtime === "edge") {
    await import("./sentry.edge.config").catch((err: unknown) => {
      // eslint-disable-next-line no-console
      console.warn("[sentry] edge init skipped:", err);
    });
  }
}
