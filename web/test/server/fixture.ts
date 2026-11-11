// Builds a fixture database from fixture rows with the real scripts/compact-rows.sh and
// scripts/build-db.sh (spec 0006 section 8.4 item 1). All rows are synthetic.

import { execFileSync } from "node:child_process";
import { mkdirSync, mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

export const webDir = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
export const repoRoot = resolve(webDir, "..");

export type FixtureRow = {
  ts: string;
  provider: "aws" | "azure";
  probe: string;
  sku: string;
  region: string;
  outcome: string;
  zone?: string | null;
  run?: string | null;
  n?: number | null;
  placed?: number | null;
  err?: string | null;
  sim?: boolean;
  details?: Record<string, unknown>;
};

export function toLine(r: FixtureRow): string {
  return JSON.stringify({
    ts_utc: r.ts,
    run_id: r.run ?? null,
    simulated: r.sim ?? false,
    provider: r.provider,
    region: r.region,
    zone: r.zone ?? null,
    probe_type: r.probe,
    sku: r.sku,
    placement_group_mode: null,
    n_requested: r.n ?? null,
    n_placed: r.placed ?? null,
    outcome: r.outcome,
    error_code: r.err ?? null,
    latency_ms: 1,
    details: r.details ?? {},
  });
}

/** Writes the rows in the mirror layout and builds <dir>/ftg.duckdb with the real scripts. */
export function buildDb(rows: readonly FixtureRow[], dir?: string): string {
  const work = dir ?? mkdtempSync(join(tmpdir(), "ftg-board-"));
  const mirror = join(work, "mirror");
  const groups = new Map<string, string[]>();
  for (const r of rows) {
    const d = join(
      mirror,
      r.provider,
      "experiment=fixture",
      `provider=${r.provider}`,
      `probe_type=${r.probe}`,
      `date=${r.ts.slice(0, 10)}`,
    );
    const g = groups.get(d) ?? [];
    g.push(toLine(r));
    groups.set(d, g);
  }
  let i = 0;
  for (const [d, lines] of groups) {
    mkdirSync(d, { recursive: true });
    writeFileSync(join(d, `20261001T000000Z_t${i++}.jsonl`), `${lines.join("\n")}\n`);
  }
  mkdirSync(mirror, { recursive: true });
  const db = join(work, "ftg.duckdb");
  execFileSync(join(repoRoot, "scripts/compact-rows.sh"), [mirror, join(work, "rows")], {
    stdio: "pipe",
  });
  execFileSync(join(repoRoot, "scripts/build-db.sh"), [join(work, "rows"), db], { stdio: "pipe" });
  return db;
}
