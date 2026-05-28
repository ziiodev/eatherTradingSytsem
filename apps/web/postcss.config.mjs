// Tailwind CSS v4 PostCSS plugin.
//
// v4 ships its own PostCSS plugin (`@tailwindcss/postcss`) which subsumes
// what `tailwindcss` + `autoprefixer` did in v3. Do NOT add `autoprefixer`
// here — v4 targets modern browsers and bundles the necessary fallbacks.
//
// CSS-first config: the theme lives inside `src/app/globals.css` via the
// `@theme { ... }` block. There is intentionally NO `tailwind.config.js`.
const config = {
  plugins: {
    "@tailwindcss/postcss": {},
  },
};

export default config;
