import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import react from "@vitejs/plugin-react";
import type { Plugin } from "vite";
import { defineConfig } from "vitest/config";

/**
 * fixtures/ lives outside console/ because it is shared across lanes. Serve it
 * read-only at /fixtures/* so the in-browser parity check can fetch the same
 * file the vitest run uses, rather than a copy that can drift.
 */
function serveSharedFixtures(): Plugin {
  const root = resolve(__dirname, "..");
  return {
    name: "pact-shared-fixtures",
    configureServer(server) {
      server.middlewares.use("/fixtures", (req, res, next) => {
        const rel = decodeURIComponent((req.url ?? "/").split("?")[0]);
        // Refuse traversal outright rather than normalising it away.
        if (rel.includes("..")) return next();
        try {
          const body = readFileSync(resolve(root, "fixtures" + rel));
          res.setHeader("content-type", "application/json; charset=utf-8");
          res.end(body);
        } catch {
          next();
        }
      });
    },
  };
}

// Ports are fixed by 00-SHARED-CONTRACTS section 4. Proxying instead of
// hardcoding origins keeps the app same-origin, so no CORS and no mixed
// content surprises when this is demoed off a laptop.
const GATE = process.env.PACT_GATE_URL ?? "http://localhost:8000";
const MERCHANT = process.env.PACT_MERCHANT_URL ?? "http://localhost:8100";
const SIM = process.env.PACT_SIM_URL ?? "http://localhost:8300";

export default defineConfig({
  plugins: [react(), serveSharedFixtures()],
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      "/api/gate": {
        target: GATE,
        changeOrigin: true,
        rewrite: (p) => p.replace(/^\/api\/gate/, ""),
        // SSE must not be buffered or the decision feed arrives in bursts.
        configure: (proxy) => {
          proxy.on("proxyRes", (proxyRes) => {
            proxyRes.headers["cache-control"] = "no-cache, no-transform";
          });
        },
      },
      "/api/merchant": {
        target: MERCHANT,
        changeOrigin: true,
        rewrite: (p) => p.replace(/^\/api\/merchant/, ""),
        configure: (proxy) => {
          proxy.on("proxyRes", (proxyRes) => {
            proxyRes.headers["cache-control"] = "no-cache, no-transform";
          });
        },
      },
      "/api/sim": {
        target: SIM,
        changeOrigin: true,
        rewrite: (p) => p.replace(/^\/api\/sim/, ""),
      },
    },
  },
  build: {
    target: "es2022",
    outDir: "dist",
  },
  test: {
    environment: "node",
    include: ["test/**/*.test.{ts,tsx}"],
  },
});
