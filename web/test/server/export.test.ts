// Test of the static export (spec 0006 section 9): each exported file equals the handler's
// response byte for byte, on a fixture database built by the real scripts. No network.

import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { exportApi } from "../../scripts/export";
import { createHandler } from "../../server/handler";
import { buildDb, repoRoot, webDir } from "./fixture";
import { rows } from "./rows";

const sqlDir = join(webDir, "sql");
let work: string;
let db: string;

beforeAll(() => {
  work = mkdtempSync(join(tmpdir(), "ftg-export-"));
  db = buildDb(rows, work);
}, 120_000);
afterAll(() => rmSync(work, { recursive: true, force: true }));

describe("static export", () => {
  it("writes every API path with the bytes of the handler", async () => {
    const outDir = join(work, "dist");
    const files = await exportApi({ dbPath: db, outDir, sqlDir, repoRoot });
    expect(files[0]).toBe("api/meta.json");
    for (const hz of ["day", "week", "month", "all"]) {
      expect(files).toContain(`api/window/${hz}/0.json`);
    }
    const h = createHandler({ dbPath: db, sqlDir, repoRoot });
    try {
      for (const f of files) {
        const r = await h.handle(f);
        expect(r.status).toBe(200);
        expect(readFileSync(join(outDir, f)).equals(Buffer.from(r.body, "utf8")), f).toBe(true);
      }
      // The list is complete: the next page of each horizon does not exist.
      for (const hz of ["day", "week", "month", "all"]) {
        const n = files.filter((f) => f.startsWith(`api/window/${hz}/`)).length;
        expect((await h.handle(`api/window/${hz}/${n}.json`)).status).toBe(404);
      }
    } finally {
      await h.close();
    }
  }, 120_000);
});
