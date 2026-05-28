import type { NextConfig } from "next";

/**
 * Next.js configuration for the Aether dashboard.
 *
 * - `rewrites()` proxies `/api/*` to the FastAPI backend (default
 *   `http://localhost:8000`) so the browser can use same-origin cookies
 *   without CORS gymnastics during development.
 * - `NEXT_PUBLIC_API_URL` can override the destination (e.g. when running
 *   the API on a different host or port).
 */
const apiBase: string =
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${apiBase}/api/:path*`,
      },
    ];
  },
};

export default nextConfig;
