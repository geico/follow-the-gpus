// The Vite plugin that mounts the board handler on `<base>api/` (spec 0006 section 8.3).
// `npm run dev` and `make web/run` use it: 1 origin serves the page and the API.

import { dirname, isAbsolute, resolve } from "node:path";
import type { Plugin } from "vite";
import { type BoardHandler, createHandler } from "./handler.ts";

export function boardApi(): Plugin {
  let handler: BoardHandler | null = null;
  return {
    name: "ftg-board-api",
    configureServer(server) {
      const webRoot = server.config.root;
      const repoRoot = dirname(webRoot);
      const env = process.env.WEB_DB;
      const dbPath = env
        ? isAbsolute(env)
          ? env
          : resolve(repoRoot, env)
        : resolve(repoRoot, "data/ftg.duckdb");
      handler = createHandler({ dbPath, sqlDir: resolve(webRoot, "sql"), repoRoot });
      const h = handler;
      // A relative base ('./') serves the dev server at '/'.
      const base = server.config.base.startsWith("/") ? server.config.base : "/";
      server.middlewares.use(async (req, res, next) => {
        const url = req.url ?? "";
        if (!url.startsWith(`${base}api/`)) return next();
        if (req.method !== "GET" && req.method !== "HEAD") {
          res.statusCode = 405;
          res.setHeader("Content-Type", "application/json");
          res.end('{"error":"method not allowed"}');
          return;
        }
        const out = await h.handle(url.slice(base.length));
        res.statusCode = out.status;
        res.setHeader("Content-Type", out.contentType);
        res.setHeader("Cache-Control", "no-store");
        res.end(req.method === "HEAD" ? undefined : out.body);
      });
      server.httpServer?.once("close", () => {
        void h.close();
      });
    },
  };
}
