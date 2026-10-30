import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // The container image (apps/web/Dockerfile) runs the self-contained server from `.next/standalone`. Opt-in so
  // `next start` (local, Railway) keeps working unchanged.
  output: process.env.NEXT_OUTPUT === "standalone" ? "standalone" : undefined,
  cacheComponents: true,
  partialPrefetching: true,
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
};

export default nextConfig;
