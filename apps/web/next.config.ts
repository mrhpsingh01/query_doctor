import type { NextConfig } from "next";

// The browser only ever talks to this origin; /api/* is proxied to the FastAPI service.
const API_URL = process.env.QD_API_URL ?? "http://127.0.0.1:8010";

const nextConfig: NextConfig = {
  output: "standalone", // a self-contained server for the Docker image
  compress: false, // keeps server-sent progress events from being buffered by the proxy
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${API_URL}/api/:path*` }];
  },
};

export default nextConfig;
