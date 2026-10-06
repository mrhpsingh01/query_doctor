import type { NextConfig } from "next";

// The browser only ever talks to this origin; /api/* is proxied to the FastAPI service.
const API_URL = process.env.QD_API_URL ?? "http://127.0.0.1:8010";

const nextConfig: NextConfig = {
  output: "standalone", // a self-contained server for the Docker image
  devIndicators: false, // keeps the dev-mode badge out of the README screenshots
  allowedDevOrigins: ["127.0.0.1"], // the Playwright journeys open the dev server by IP; without this the page never hydrates
  compress: false, // keeps server-sent progress events from being buffered by the proxy
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${API_URL}/api/:path*` }];
  },
};

export default nextConfig;
