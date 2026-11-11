// Test of the generated sample (spec 0006 section 13): the database built from the output of web/scripts/make-sample.sh with the
// real scripts shows every section, every probe type and state, and the method change. No network.

import { execFileSync } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { type BoardHandler, createHandler } from "../../server/handler";
import { repoRoot, webDir } from "./fixture";

let work: string;
let h: BoardHandler;

async function get(path: string) {
  const r = await h.handle(path);
  expect(r.status).toBe(200);
  return JSON.parse(r.body);
}

beforeAll(() => {
  work = mkdtempSync(join(tmpdir(), "ftg-sample-"));
  const db = join(work, "sample.duckdb");
  // The sample is generated, not committed. The script needs the duckdb CLI on PATH.
  execFileSync(join(webDir, "scripts/make-sample.sh"), [join(work, "sample")], { stdio: "pipe" });
  execFileSync(
    join(repoRoot, "scripts/compact-rows.sh"),
    [join(work, "sample"), join(work, "rows")],
    { stdio: "pipe" },
  );
  execFileSync(join(repoRoot, "scripts/build-db.sh"), [join(work, "rows"), db], { stdio: "pipe" });
  h = createHandler({ dbPath: db, sqlDir: join(webDir, "sql"), repoRoot });
}, 180_000);
afterAll(() => rmSync(work, { recursive: true, force: true }));

describe("the generated sample", () => {
  it("has the method change, the doubling start, and the banner flag", async () => {
    const m = await get("api/meta.json");
    expect(m.simulated).toBe(true);
    expect(m.zoneStartH).toBeGreaterThan(0);
    expect(m.zones.length).toBeGreaterThan(0);
    expect(m.upStartH).not.toBeNull();
    expect(m.cbHorizonDays.length).toBeGreaterThan(0);
    expect(Object.keys(m.probeStartH).sort()).toEqual([
      "aws:aws_odcr",
      "azure:az_capacity_reservation",
      "azure:az_vmss_flex",
    ]);
  });

  it("fills every part of the window and every sweep state", async () => {
    const w = await get("api/window/all/0.json");
    const states = new Set<number>(w.sweeps.map((s: { state: number }) => s.state));
    for (const st of [2, 1, 0, -1, -2, -3, -4]) expect(states.has(st), `state ${st}`).toBe(true);
    for (const cloud of ["aws", "azure"]) {
      expect(w.sweeps.some((s: { cloud: string }) => s.cloud === cloud)).toBe(true);
    }
    // A sweep over several zones (per-zone AWS rows) and a sweep of 1 zone (before zoneStartH).
    expect(w.sweeps.some((s: { zones: number }) => s.zones > 1)).toBe(true);
    expect(w.sweeps.some((s: { zones: number }) => s.zones === 1)).toBe(true);
    expect(w.cb.some((c: { checks: unknown[] }) => c.checks.length > 0)).toBe(true);
    expect(w.cb.some((c: { longest: unknown[] }) => c.longest.length > 0)).toBe(true);
    expect(Object.keys(w.spot).length).toBeGreaterThan(0);
    expect(Object.keys(w.azspot).length).toBeGreaterThan(0);
    expect(Object.keys(w.price.series).some((k) => k.endsWith("|aws"))).toBe(true);
    expect(Object.keys(w.price.series).some((k) => k.endsWith("|azure"))).toBe(true);
  });
});
