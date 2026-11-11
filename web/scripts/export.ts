// The static export (spec 0006 section 9). It calls the board handler for api/meta.json and for
// every api/window/<hz>/<page>.json, and writes each body, unchanged, to the same path under the
// output folder. Run: node scripts/export.ts <db-file> <out-dir> (use `make web/export`).
// No network call and no credential.

import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { createHandler } from "../server/handler.ts";

const HORIZONS = ["day", "week", "month", "all"] as const;

export type ExportOptions = { dbPath: string; outDir: string; sqlDir: string; repoRoot: string };

/** Writes the API files; returns their paths relative to outDir. Throws on a non-200 answer. */
export async function exportApi(o: ExportOptions): Promise<string[]> {
  const handler = createHandler({ dbPath: o.dbPath, sqlDir: o.sqlDir, repoRoot: o.repoRoot });
  const written: string[] = [];
  const put = async (path: string, mustExist: boolean): Promise<boolean> => {
    const r = await handler.handle(path);
    if (r.status === 404 && !mustExist) return false;
    if (r.status !== 200) throw new Error(`export: ${path} answered ${r.status}: ${r.body}`);
    const file = join(o.outDir, path);
    mkdirSync(dirname(file), { recursive: true });
    writeFileSync(file, r.body);
    written.push(path);
    return true;
  };
  try {
    await put("api/meta.json", true);
    // A page past the last one answers 404; so does every page of an empty database.
    for (const hz of HORIZONS) {
      for (let page = 0; await put(`api/window/${hz}/${page}.json`, false); page++);
    }
  } finally {
    await handler.close();
  }
  return written;
}

async function main(): Promise<void> {
  const [db, out] = process.argv.slice(2);
  if (!db || !out) throw new Error("usage: export.ts <db-file> <out-dir>");
  const webDir = resolve(dirname(fileURLToPath(import.meta.url)), "..");
  const files = await exportApi({
    dbPath: resolve(db),
    outDir: resolve(out),
    sqlDir: join(webDir, "sql"),
    repoRoot: dirname(webDir),
  });
  console.log(`export: wrote ${files.length} files under ${out}/api`);
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main().catch((e) => {
    console.error(e instanceof Error ? e.message : String(e));
    process.exit(1);
  });
}
