// ESLint flat config — Next.js 16 + ESLint 9.
//
// Next.js 16 removed the `next lint` subcommand and ships native flat-config
// presets via the `eslint-config-next/core-web-vitals` and
// `eslint-config-next/typescript` subpath exports — both are arrays of flat
// configs that can be spread directly here. No FlatCompat shim required.
//
// Anything that used to live in `.eslintrc.json` (rule tweaks, ignore globs)
// now lives below.

import nextCoreWebVitals from "eslint-config-next/core-web-vitals";
import nextTypeScript from "eslint-config-next/typescript";

const config = [
  // Replicates the previous `ignorePatterns` from .eslintrc.json. In flat
  // config, an entry with only `ignores` becomes a global ignore.
  {
    ignores: [
      ".next/**",
      "node_modules/**",
      "coverage/**",
      "dist/**",
      "next-env.d.ts",
    ],
  },
  // Pull in the Next.js shareable configs (native flat-config form).
  ...nextCoreWebVitals,
  ...nextTypeScript,
  // Project-local rule tweaks (mirrors the previous .eslintrc.json rules).
  {
    rules: {
      "@typescript-eslint/consistent-type-imports": "warn",
      "@typescript-eslint/no-unused-vars": [
        "warn",
        { argsIgnorePattern: "^_", varsIgnorePattern: "^_" },
      ],
    },
  },
];

export default config;
