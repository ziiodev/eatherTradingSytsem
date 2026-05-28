/**
 * Browser-side Sentry bootstrap.
 *
 * Next.js auto-loads this file in the client bundle when present (see
 * https://nextjs.org/docs/app/api-reference/file-conventions/instrumentation-client).
 * Gated on ``NEXT_PUBLIC_SENTRY_DSN`` so an unconfigured environment never
 * calls init — the dynamic import keeps the SDK code-split out of the
 * critical path when DSN is unset.
 *
 * The PII scrubber lives in ``./src/lib/sentry-scrub`` and mirrors the
 * backend's ``apps/api/src/aether_api/core/pii.py``. Keep them in sync.
 *
 * ``@sentry/nextjs`` is a real dependency (listed in package.json) so the
 * bundler can resolve it. The DSN guard alone decides whether it runs.
 */

import { scrubMapping } from "@/lib/sentry-scrub";

const dsn = process.env.NEXT_PUBLIC_SENTRY_DSN;
const env = process.env.NEXT_PUBLIC_SENTRY_ENVIRONMENT ?? process.env.NODE_ENV;
const release = process.env.NEXT_PUBLIC_SENTRY_RELEASE;

if (dsn) {
  void import("@sentry/nextjs")
    .then((Sentry) => {
      Sentry.init({
        dsn,
        environment: env,
        release,
        // Tracing is delegated to backend OTel; keep browser sample rate at 0.
        tracesSampleRate: 0.0,
        replaysSessionSampleRate: 0,
        replaysOnErrorSampleRate: 0,
        beforeSend(event) {
          return scrubMapping(event as unknown as Record<string, unknown>) as unknown as typeof event;
        },
      });
    })
    .catch((err: unknown) => {
      // eslint-disable-next-line no-console
      console.warn("[sentry] client init skipped:", err);
    });
}
