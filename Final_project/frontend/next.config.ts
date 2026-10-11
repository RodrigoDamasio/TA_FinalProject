import path from "node:path";
import type { NextConfig } from "next";

const isDev = process.env.NODE_ENV === "development";
const apiOrigin = (() => {
  try {
    return new URL(process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000").origin;
  } catch {
    return "http://localhost:8000";
  }
})();

// FRONTEND_PLAN §5: connect-src is the API origin only. No nonces (static pages), so inline
// scripts stay allowed for Next's bootstrap; untrusted text is never rendered as HTML.
const csp = [
  "default-src 'self'",
  `script-src 'self' 'unsafe-inline'${isDev ? " 'unsafe-eval'" : ""}`,
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' blob: data:",
  "font-src 'self'",
  `connect-src 'self' ${apiOrigin}`,
  "object-src 'none'",
  "base-uri 'self'",
  "form-action 'self'",
  "frame-ancestors 'none'",
].join("; ");

const nextConfig: NextConfig = {
  // A stray package-lock.json in the home folder would otherwise be taken as the root.
  turbopack: { root: path.join(__dirname) },
  // The build type-checks the app only; `npm run typecheck` checks the tests as well.
  typescript: { tsconfigPath: "tsconfig.build.json" },
  async headers() {
    return [
      {
        source: "/(.*)",
        headers: [
          { key: "Content-Security-Policy", value: csp },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
          { key: "X-Content-Type-Options", value: "nosniff" },
        ],
      },
    ];
  },
};

export default nextConfig;
