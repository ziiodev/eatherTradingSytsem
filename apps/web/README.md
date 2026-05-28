# `@aether/web` — Aether Trading System dashboard

Next.js 16 (App Router) + Tailwind CSS v4 (CSS-first config, no `tailwind.config.js`) + shadcn/ui (copied components, not an npm dep). UI language: Spanish. Visual theme: **GitHub Dark** via CSS variables in `src/app/globals.css`.

## Prerequisites

- Node LTS (see root `.nvmrc`)
- pnpm (the only JS package manager; do not use npm/yarn here)
- A running `apps/api` on `http://localhost:8000` for the auth-gated routes to actually work. The dev proxy in `next.config.ts` rewrites `/api/*` to that backend.

## Scripts

| Command          | What it does                                            |
|------------------|---------------------------------------------------------|
| `pnpm dev`       | Start Next.js dev server on `http://localhost:3000`.    |
| `pnpm build`     | Production build (`.next/`).                            |
| `pnpm start`     | Serve the production build.                             |
| `pnpm lint`      | ESLint via `eslint-config-next`.                        |
| `pnpm typecheck` | `tsc --noEmit` (strict mode).                           |
| `pnpm test`      | Vitest run (happy-dom + Testing Library).               |

## Environment

Copy the root `.env.example` to `.env` (the dev proxy reads `NEXT_PUBLIC_API_URL` if set). Locally, the default `http://localhost:8000` is fine.

```
NEXT_PUBLIC_API_URL=http://localhost:8000
```

## Routing

- `(auth)/login` — the only unauthenticated route.
- `(dashboard)/*` — everything else. Middleware (`middleware.ts`) redirects to `/login?return_to=...` if the access cookie is missing.
- Sidebar entries (fixed, four total, in this order): `Proyectos`, `Agentes`, `Skills`, `Configuración`.

## Theming

Tailwind v4 is configured **entirely in CSS**:

- `src/app/globals.css` imports `tailwindcss` and declares the `@theme` block + the GitHub Dark palette as `--background`, `--foreground`, `--border`, `--accent`, etc.
- There is intentionally **no `tailwind.config.js` / `tailwind.config.ts`**. Do not add one.
- shadcn/ui components live under `src/components/ui/` — they are copied source, not an npm package. Never `import ... from "@shadcn/ui"`.

## Security model (frontend side)

- Auth tokens live in **httpOnly cookies** issued by the FastAPI backend. The frontend never touches `localStorage` / `sessionStorage` for tokens.
- CSRF: double-submit cookie. `src/lib/auth.ts` reads the `csrf_token` cookie and `src/lib/api.ts` attaches it as `X-CSRF-Token` on state-changing requests.
