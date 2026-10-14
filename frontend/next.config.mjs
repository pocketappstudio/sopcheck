/** @type {import('next').NextConfig} */
// Two build modes:
//  * default (`next build && next start`): the browser only talks to Next.js (same origin). /api and /files are
//    proxied to FastAPI, so the httpOnly session cookie is first-party and readable by middleware.
//  * STATIC_EXPORT=1 (GitHub Pages): `next build` writes a static site to ./out. There is no server, so no proxy and
//    no middleware: the browser calls NEXT_PUBLIC_API_BASE directly (CORS) with a Bearer token, and pages guard
//    themselves client-side (RequireAuth). Set NEXT_PUBLIC_BASE_PATH=/<repo> for project pages (omit for a custom domain).
const BACKEND_URL = process.env.BACKEND_URL || "http://localhost:8000";
const STATIC = process.env.STATIC_EXPORT === "1";
const BASE_PATH = process.env.NEXT_PUBLIC_BASE_PATH || "";

const nextConfig = STATIC
  ? { output: "export", basePath: BASE_PATH || undefined, trailingSlash: true, images: { unoptimized: true } }
  : {
      async rewrites() {
        return [
          { source: "/api/:path*", destination: `${BACKEND_URL}/api/:path*` },
          { source: "/files/:path*", destination: `${BACKEND_URL}/files/:path*` },
        ];
      },
    };

export default nextConfig;
