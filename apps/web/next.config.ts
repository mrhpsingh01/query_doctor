import type { NextConfig } from "next";

// The browser only ever talks to this origin; /api/* is proxied to the FastAPI service.
const API_URL = process.env.QD_API_URL ?? "http://127.0.0.1:8010";

const nextConfig: NextConfig = {
  env: {
    NEXT_PUBLIC_QD_DEMO_MODE: process.env.NEXT_PUBLIC_QD_DEMO_MODE ?? (process.env.VERCEL === "1" ? "true" : "false"),
  },
  async redirects() {
    const demo = process.env.NEXT_PUBLIC_QD_DEMO_MODE === "true" || (process.env.NEXT_PUBLIC_QD_DEMO_MODE === undefined && process.env.VERCEL === "1");
    return demo ? ["/workload/:fingerprint+", "/projects/:path*", "/models/:path*", "/settings/:path*", "/analyses/:id((?!new$).*)"].map(source => ({ source, destination: "/", permanent: false })) : [];
  },
  output: "standalone", // a self-contained server for the Docker image
  devIndicators: false, // keeps the dev-mode badge out of the README screenshots
  allowedDevOrigins: ["127.0.0.1"], // the Playwright journeys open the dev server by IP; without this the page never hydrates
  compress: false, // keeps server-sent progress events from being buffered by the proxy
  async rewrites() {
    const demo = process.env.NEXT_PUBLIC_QD_DEMO_MODE === "true" || (process.env.NEXT_PUBLIC_QD_DEMO_MODE === undefined && process.env.VERCEL === "1");
    if (demo) return []; // the public showcase is fully static: there is no API to proxy to
    return [{ source: "/api/:path*", destination: `${API_URL}/api/:path*` }];
  },
};

export default nextConfig;
