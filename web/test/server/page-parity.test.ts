// The server answers a window path with the page list and window of the derive layer, the same
// ones the page uses (spec 0006 section 11.2 items 2 and 4). The test reads the pages
// the server really serves and compares each window with the frame of the derive layer.

import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { createHandler } from "../../server/handler";
import { makeFrame } from "../../src/board/derive/time";
import type { Meta } from "../../src/board/derive/types";
import { buildDb, repoRoot, webDir } from "./fixture";
import { rows } from "./rows";

let work: string;
let db: string;

beforeAll(() => {
  work = mkdtempSync(join(tmpdir(), "ftg-parity-"));
  db = buildDb(rows, work);
}, 120_000);
afterAll(() => rmSync(work, { recursive: true, force: true }));

describe("server and derive page lists", () => {
  it("serve the same pages with the same windows for every horizon", async () => {
    const h = createHandler({ dbPath: db, sqlDir: join(webDir, "sql"), repoRoot });
    try {
      const meta = JSON.parse((await h.handle("api/meta.json")).body) as Meta;
      for (const hz of ["day", "week", "month", "all"] as const) {
        const pages = makeFrame(meta, hz, 0).pages.length;
        for (let i = 0; i < pages; i++) {
          const r = await h.handle(`api/window/${hz}/${i}.json`);
          expect(r.status, `${hz}/${i}`).toBe(200);
          const f = makeFrame(meta, hz, i);
          const w = JSON.parse(r.body) as { H0: number; H1: number };
          expect({ H0: w.H0, H1: w.H1 }, `${hz}/${i}`).toEqual({ H0: f.H0, H1: f.H1 });
        }
        expect((await h.handle(`api/window/${hz}/${pages}.json`)).status, hz).toBe(404);
      }
    } finally {
      await h.close();
    }
  });
});
