/**
 * Node-runtime Sentry bootstrap (Next.js server components, route handlers,
 * middleware running on Node). Loaded by ``instrumentation.ts`` when
 * ``NEXT_PUBLIC_SENTRY_DSN`` is set and ``NEXT_RUNTIME === "nodejs"``.
 *
 * ``@sentry/nextjs`` is a real dependency — the DSN guard alone decides
 * whether init runs.
 */

import { scrubMapping } from "@/lib/sentry-scrub";

const dsn = process.env.NEXT_PUBLIC_SENTRY_DSN ?? process.env.SENTRY_DSN;
const env =
  process.env.SENTRY_ENVIRONMENT ??
  process.env.NEXT_PUBLIC_SENTRY_ENVIRONMENT ??
  process.env.NODE_ENV;
const release =
  process.env.SENTRY_RELEASE ?? process.env.NEXT_PUBLIC_SENTRY_RELEASE;

if (dsn) {
  void import("@sentry/nextjs")
    .then((Sentry) => {
      Sentry.init({
        dsn,
        environment: env,
        release,
        tracesSampleRate: 0.0,
        beforeSend(event) {
          return scrubMapping(event as unknown as Record<string, unknown>) as unknown as typeof event;
        },
      });
    })
    .catch((err: unknown) => {
      // eslint-disable-next-line no-console
      console.warn("[sentry] server init skipped:", err);
    });
}
