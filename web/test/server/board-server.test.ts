// Tests of the board server (spec 0006 section 8.4): a fixture database built by the real
// scripts, full response bodies, the read-only open, and the DuckDB version. No network.

import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { DuckDBConnection, DuckDBInstance } from "@duckdb/node-api";
import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";
import { type BoardHandler, createHandler } from "../../server/handler";
import { HttpDataSource } from "../../src/data/source";
import { stubMeta, stubWindow } from "../../src/data/stub";
import { buildDb, type FixtureRow, repoRoot, webDir } from "./fixture";
import { rows } from "./rows";

const G5 = "g5.xlarge";
const P5 = "p5.48xlarge";
const P4 = "p4d.24xlarge";
const ND = "Standard_ND96";

const mk = (dbPath: string): BoardHandler =>
  createHandler({ dbPath, sqlDir: join(webDir, "sql"), repoRoot });

async function get(h: BoardHandler, path: string) {
  const r = await h.handle(path);
  return { status: r.status, type: r.contentType, text: r.body, json: JSON.parse(r.body) };
}

/** The window of a payload without the per-zone lists (they have their own test). */
const noZoneList = (j: { sweeps: Record<string, unknown>[] }) => ({
  ...j,
  sweeps: j.sweeps.map(({ zoneList: _list, ...s }) => s),
});

// The sweep keys, in order (spec 0006 section 5.3).
const sw = (
  h: number,
  t: number,
  cloud: string,
  region: string,
  sku: string,
  probe: string,
  asked: number,
  got: number,
  sum: number,
  state: number,
  nMax: number,
  reason: string | null,
  err: string | null,
  zones: number,
  zonesYes: number,
) => ({
  h,
  t,
  cloud,
  region,
  sku,
  probe,
  asked,
  gotZoneMax: got,
  gotZoneSum: sum,
  state,
  nMax,
  reason,
  err,
  zones,
  zonesYes,
});

const r0 = sw(0, 0.1, "aws", "us-east-1", G5, "aws_odcr", 2, 2, 2, 2, 2, null, null, 1, 1);
const r1 = [
  // 3 zones: a full yes with an upward search, a halving yes, an error zone.
  sw(2, 2.1, "aws", "us-east-1", G5, "aws_odcr", 4, 4, 6, 2, 8, null, null, 3, 2),
  sw(2, 2.2, "aws", "us-west-2", P4, "aws_odcr", 0, 0, 0, -4, 0, null, "NotOffered", 0, 0),
  sw(
    2,
    2.3,
    "azure",
    "eastus",
    ND,
    "az_capacity_reservation",
    4,
    0,
    0,
    -1,
    0,
    "restricted",
    "SkuRestricted",
    1,
    0,
  ),
  sw(2, 2.4, "azure", "eastus", ND, "az_vmss_flex", 4, 0, 0, -1, 0, "quota", "QuotaExceeded", 1, 0),
  sw(
    2,
    2.5,
    "azure",
    "westus2",
    ND,
    "az_capacity_reservation",
    4,
    0,
    0,
    -3,
    0,
    null,
    "SkuNotAvailable",
    1,
    0,
  ),
];
const dense = Array.from({ length: 12 }, (_, k) =>
  sw(4 + 2 * k, 4 + 2 * k, "aws", "us-east-2", G5, "aws_odcr", 1, 1, 1, 2, 1, null, null, 1, 1),
);
const r2 = [
  sw(172, 172.1, "azure", "eastus", ND, "az_capacity_reservation", 8, 4, 4, 1, 4, null, null, 1, 1),
  // a quota zone next to an answered zone does not change the state
  sw(172, 172.2, "aws", "us-east-1", G5, "aws_odcr", 4, 4, 4, 2, 4, null, null, 2, 1),
];
const allSweeps = [r0, ...r1, ...dense, ...r2];

const p5Week0 = {
  sku: P5,
  region: "us-east-1",
  checks: [
    [0, 0, null, null, null, 14, 1, null],
    [0, 1, 3, 24, 100.5, 14, 1, 72],
  ],
  longest: [[0, 1, 2, 336, 24, "2026-10-07"]],
};
const p5Week1 = {
  sku: P5,
  region: "us-east-1",
  checks: [[172, 1, 1, 24, 90, 14, 1, 24]],
  longest: [[172, 1, 2, 168, 24, "2026-10-14"]],
};
const p4Check = [[2, 0, null, null, null, 7, 2, null]];
const p4 = (checks: unknown[]) => ({ sku: P4, region: "us-west-2", checks, longest: [] });

const dates = Array.from({ length: 8 }, (_, i) => `2026-10-${String(5 + i).padStart(2, "0")}`);
const price = {
  dates,
  series: {
    [`${ND}|azure`]: [1.2346, null, null, null, null, null, null, null],
    [`${G5}|aws`]: [2.8333, null, null, null, null, null, null, 5.1234],
  },
};
const azPoint = (h: number, a: number, b: number, c: number, d: number) => [h, a, b, c, d];

const nineTypes = new Set([
  "aws_odcr",
  "az_capacity_reservation",
  "az_vmss_flex",
  "aws_cb_offering",
  "aws_cb_longest",
  "aws_spot_score",
  "az_spot_score",
  "aws_spot_price",
  "az_spot",
]);
const realRowCount = rows.filter((r) => nineTypes.has(r.probe) && !r.sim).length;

const expectedMeta = {
  t0: "2026-10-05T10:00:00.000Z",
  tEnd: "2026-10-12T14:21:00.000Z",
  nh: 174,
  dates,
  weekends: 1,
  cadenceH: { launch: 2, cb: 172 },
  probeStartH: {
    "aws:aws_odcr": 0.1,
    "azure:az_capacity_reservation": 2.3,
    "azure:az_vmss_flex": 2.4,
  },
  zones: ["us-east-1a"],
  cbHorizonDays: [7, 14],
  regions: ["aws|us-east-1", "aws|us-east-2", "aws|us-west-2", "azure|eastus", "azure|westus2"],
  rows: realRowCount,
  sweeps: 20,
  firstH: { [ND]: 2, [G5]: 0, [P4]: 2 },
  cbFirstH: { [P5]: 0 },
  upStartH: 2,
  zoneStartH: 2,
  simulated: false,
  now: {
    // The latest round of each sku: p4d at hour 2 (state -4), the others at hour 172.
    sweeps: [
      sw(2, 2.2, "aws", "us-west-2", P4, "aws_odcr", 0, 0, 0, -4, 0, null, "NotOffered", 0, 0),
      sw(
        172,
        172.1,
        "azure",
        "eastus",
        ND,
        "az_capacity_reservation",
        8,
        4,
        4,
        1,
        4,
        null,
        null,
        1,
        1,
      ),
      {
        ...sw(172, 172.2, "aws", "us-east-1", G5, "aws_odcr", 4, 4, 4, 2, 4, null, null, 2, 1),
        zoneList: [
          { zone: "us-east-1a", got: null },
          { zone: "us-east-1b", got: 4 },
        ],
      },
    ],
    places: [
      {
        sku: ND,
        cloud: "azure",
        region: "eastus",
        probe: "az_capacity_reservation",
        n: 3,
        na: 2,
        yes: 1,
        nu: 0,
        no: 0,
        lastYesH: 172,
      },
      {
        sku: ND,
        cloud: "azure",
        region: "westus2",
        probe: "az_capacity_reservation",
        n: 0,
        na: 0,
        yes: 0,
        nu: 1,
        no: 0,
        lastYesH: null,
      },
      {
        sku: G5,
        cloud: "aws",
        region: "us-east-1",
        probe: "aws_odcr",
        n: 3,
        na: 0,
        yes: 3,
        nu: 0,
        no: 0,
        lastYesH: 172,
      },
      {
        sku: G5,
        cloud: "aws",
        region: "us-east-2",
        probe: "aws_odcr",
        n: 12,
        na: 0,
        yes: 12,
        nu: 0,
        no: 0,
        lastYesH: 26,
      },
      {
        sku: P4,
        cloud: "aws",
        region: "us-west-2",
        probe: "aws_odcr",
        n: 0,
        na: 0,
        yes: 0,
        nu: 0,
        no: 1,
        lastYesH: null,
      },
    ],
    cb: [
      {
        sku: P4,
        region: "us-west-2",
        h: null,
        leadH: null,
        durH: null,
        start: null,
        longestH: null,
      },
      {
        sku: P5,
        region: "us-east-1",
        h: 172,
        leadH: 24,
        durH: 24,
        start: "2026-10-13T14:20:00.000Z",
        longestH: 168,
      },
    ],
  },
};

let work: string;
let handler: BoardHandler;

beforeAll(() => {
  work = mkdtempSync(join(tmpdir(), "ftg-board-test-"));
  handler = mk(buildDb(rows, join(work, "main")));
}, 120_000);

afterAll(async () => {
  await handler.close();
  rmSync(work, { recursive: true, force: true });
});

describe("meta.json", () => {
  it("equals the expected payload, key for key", async () => {
    const r = await get(handler, "api/meta.json");
    expect(r.status).toBe(200);
    expect(r.type).toBe("application/json");
    expect(r.json).toEqual(expectedMeta);
    expect(Object.keys(r.json)).toEqual(Object.keys(expectedMeta));
  });
});

describe("meta.now (spec 0006 section 7.3)", () => {
  const A = "g6.xlarge";
  const B = "g6.2xlarge";
  const C = "g6.4xlarge";
  // Hour `h` after t0 (2026-10-05T10:00Z), at minute `m`.
  const ts = (h: number, m = 10): string =>
    `${new Date(Date.UTC(2026, 9, 5, 10 + h, m)).toISOString().slice(0, 19)}Z`;
  const od = (r: Partial<FixtureRow> & Pick<FixtureRow, "ts" | "outcome" | "sku">): FixtureRow => ({
    provider: "aws",
    probe: "aws_odcr",
    region: "us-east-1",
    n: 1,
    ...r,
  });
  const cbRow = (
    probe: "aws_cb_offering" | "aws_cb_longest",
    r: Partial<FixtureRow> & Pick<FixtureRow, "ts" | "outcome" | "region">,
  ): FixtureRow => ({ provider: "aws", probe, sku: P5, ...r });
  const offer = (
    h: number,
    m: number,
    region: string,
    outcome: string,
    details: Record<string, unknown>,
    n = 1,
  ) => cbRow("aws_cb_offering", { ts: ts(h, m), region, outcome, n, details });
  const longest = (h: number, region: string, outcome: string, hours: number) =>
    cbRow("aws_cb_longest", {
      ts: ts(h, 20),
      region,
      outcome,
      details: { earliest_start: ts(h + 24, 20), longest_duration_hours: hours },
    });

  const fx: FixtureRow[] = [
    // A: 2 places in 1 latest round (hour 4, round 2); an older round (hour 0) is not in now.sweeps.
    od({ ts: ts(0, 6), sku: A, run: "a1", outcome: "success" }),
    od({ ts: ts(4, 6), sku: A, run: "a2", outcome: "no_capacity" }),
    od({ ts: ts(4, 7), sku: A, run: "a2", region: "us-east-2", outcome: "success" }),
    // B: the same machine with another type; its latest round (0) differs from A's.
    od({ ts: ts(0, 8), sku: B, run: "b1", outcome: "no_capacity" }),
    // C: places with only state -4 and -3.
    od({ ts: ts(4, 8), sku: C, run: "c1", region: "us-west-1", n: null, outcome: "restricted" }),
    {
      provider: "azure",
      probe: "az_capacity_reservation",
      sku: "Standard_NC",
      region: "westus2",
      ts: ts(4, 9),
      run: "z1",
      n: 4,
      outcome: "restricted_or_no_capacity",
    },
    // A block place with a found check at its latest hour: the smallest lead of 2 found checks.
    offer(0, 10, "us-east-1", "success", {
      start: ts(72, 10),
      duration_hours: 24,
      start_window_days: 14,
    }),
    offer(4, 10, "us-east-1", "success", {
      start: ts(4, 40),
      duration_hours: 24,
      start_window_days: 14,
    }),
    offer(4, 11, "us-east-1", "success", { start: ts(34, 11), start_window_days: 14 }),
    // Not 1-day or not 1-machine: ignored for the latest hour.
    offer(8, 10, "us-east-1", "success", {
      start: ts(9, 10),
      duration_hours: 168,
      start_window_days: 14,
    }),
    offer(
      8,
      12,
      "us-east-1",
      "success",
      { start: ts(9, 12), duration_hours: 24, start_window_days: 14 },
      2,
    ),
    // The latest longest row is found (168 h) and a smaller found one at the same hour (100 h).
    longest(0, "us-east-1", "success", 336),
    longest(4, "us-east-1", "success", 168),
    { ...longest(4, "us-east-1", "success", 100), ts: ts(4, 21) },
    // A block place with no found check at its latest hour; its latest longest row is not found.
    offer(0, 10, "us-west-2", "success", {
      start: ts(30, 10),
      duration_hours: 24,
      start_window_days: 14,
    }),
    offer(4, 10, "us-west-2", "none", { start_window_days: 14 }),
    longest(0, "us-west-2", "success", 240),
    longest(4, "us-west-2", "none", 0),
  ];

  it("holds the latest round, the whole-data place counts, and the latest block checks", async () => {
    const h = mk(buildDb(fx, join(work, "now")));
    const m = (await get(h, "api/meta.json")).json;
    await h.close();
    const sk = (x: { sku: string; region: string; h: number }) => [x.sku, x.region, x.h];
    expect(m.now.sweeps.map(sk)).toEqual([
      [B, "us-east-1", 0],
      [A, "us-east-1", 4],
      [A, "us-east-2", 4],
      [C, "us-west-1", 4],
      ["Standard_NC", "westus2", 4],
    ]);
    expect(Object.keys(m.now)).toEqual(["sweeps", "places", "cb"]);
    const pl = (
      sku: string,
      cloud: string,
      region: string,
      probe: string,
      [n, na, yes, nu, no]: number[],
      lastYesH: number | null,
    ) => ({ sku, cloud, region, probe, n, na, yes, nu, no, lastYesH });
    expect(m.now.places).toEqual([
      pl("Standard_NC", "azure", "westus2", "az_capacity_reservation", [0, 0, 0, 1, 0], null),
      pl(B, "aws", "us-east-1", "aws_odcr", [1, 0, 0, 0, 0], null),
      pl(C, "aws", "us-west-1", "aws_odcr", [0, 0, 0, 0, 1], null),
      pl(A, "aws", "us-east-1", "aws_odcr", [2, 0, 1, 0, 0], 0),
      pl(A, "aws", "us-east-2", "aws_odcr", [1, 0, 1, 0, 0], 4),
    ]);
    expect(m.now.cb).toEqual([
      {
        sku: P5,
        region: "us-east-1",
        h: 4,
        leadH: 0.5,
        durH: 24,
        start: "2026-10-05T14:40:00.000Z",
        longestH: 168,
      },
      { sku: P5, region: "us-west-2", h: 4, leadH: null, durH: null, start: null, longestH: null },
    ]);
    expect(Object.keys(m.now.cb[0])).toEqual([
      "sku",
      "region",
      "h",
      "leadH",
      "durH",
      "start",
      "longestH",
    ]);
  }, 60_000);
});

describe("window payloads", () => {
  it("week page 0: the first Monday-to-Monday window in America/New_York", async () => {
    const r = await get(handler, "api/window/week/0.json");
    expect(r.status).toBe(200);
    expect(noZoneList(r.json)).toEqual({
      hz: "week",
      page: 0,
      H0: -6,
      H1: 162,
      L: -6,
      sweeps: [r0, ...r1, ...dense],
      cb: [p4(p4Check), p5Week0],
      spot: {
        [`${G5}|us-east-1`]: [
          [-1, 4],
          [0, 8],
          [161, 6],
        ],
      },
      azspot: { [`${ND}|eastus`]: [azPoint(0, 1, 0, 1, 1)] },
      price,
    });
  });

  it("day pages: UTC days from the day of T0, the last page ends at NH", async () => {
    const first = await get(handler, "api/window/day/0.json");
    expect(first.status).toBe(200);
    expect(first.json).toMatchObject({ hz: "day", page: 0, H0: -10, H1: 14, L: -10 });
    const last = await get(handler, "api/window/day/7.json");
    expect(last.json).toMatchObject({ hz: "day", page: 7, H0: 158, H1: 174, L: 142 });
  });

  it("week page 1: the trend range L starts before the page", async () => {
    const r = await get(handler, "api/window/week/1.json");
    expect(noZoneList(r.json)).toEqual({
      hz: "week",
      page: 1,
      H0: 162,
      H1: 174,
      L: 150,
      sweeps: r2,
      cb: [p4([]), p5Week1],
      spot: {
        [`${G5}|us-east-1`]: [
          [161, 6],
          [172, 5],
        ],
      },
      azspot: { [`${ND}|eastus`]: [azPoint(172, 0, 1, 0, 0)] },
      price,
    });
  });

  it("month page 0: the UTC month", async () => {
    const r = await get(handler, "api/window/month/0.json");
    expect(noZoneList(r.json)).toEqual({
      hz: "month",
      page: 0,
      H0: -106,
      H1: 174,
      L: -106,
      sweeps: allSweeps,
      cb: [
        p4(p4Check),
        {
          ...p5Week0,
          checks: [...p5Week0.checks, ...p5Week1.checks],
          longest: [...p5Week0.longest, ...p5Week1.longest],
        },
      ],
      spot: {
        [`${G5}|us-east-1`]: [
          [-1, 4],
          [0, 8],
          [161, 6],
          [172, 5],
        ],
      },
      azspot: { [`${ND}|eastus`]: [azPoint(0, 1, 0, 1, 1), azPoint(172, 0, 1, 0, 0)] },
      price,
    });
  });

  it("an aws_odcr sweep carries its zones in zone order; no other sweep has the key", async () => {
    const sweeps = (await get(handler, "api/window/all/0.json")).json.sweeps as {
      region: string;
      probe: string;
      zones: number;
      h: number;
      zoneList?: { zone: string; got: number | null }[];
    }[];
    // r1: 3 zones in us-east-1 (a full yes with an upward search, a halving yes, an error zone).
    const east = sweeps.find(
      (s) => s.h === 2 && s.probe === "aws_odcr" && s.region === "us-east-1",
    );
    expect(east?.zoneList).toEqual([
      { zone: "us-east-1a", got: 4 },
      { zone: "us-east-1b", got: 2 },
      { zone: "us-east-1c", got: null },
    ]);
    // The Region row (state -4, no zone asked) and every Azure sweep carry no list.
    expect(sweeps.filter((s) => s.probe === "aws_odcr" && s.zones === 0).length).toBeGreaterThan(0);
    for (const s of sweeps) {
      if (s.probe !== "aws_odcr" || s.zones === 0) expect(s.zoneList).toBeUndefined();
    }
  });

  it("all page 0: the whole data, including the last hour", async () => {
    const r = await get(handler, "api/window/all/0.json");
    expect(noZoneList(r.json)).toEqual({
      hz: "all",
      page: 0,
      H0: -10,
      H1: 173,
      L: -10,
      sweeps: allSweeps,
      cb: [
        p4(p4Check),
        {
          ...p5Week0,
          checks: [...p5Week0.checks, ...p5Week1.checks],
          longest: [...p5Week0.longest, ...p5Week1.longest],
        },
      ],
      spot: {
        [`${G5}|us-east-1`]: [
          [-1, 4],
          [0, 8],
          [161, 6],
          [172, 5],
        ],
      },
      azspot: { [`${ND}|eastus`]: [azPoint(0, 1, 0, 1, 1), azPoint(172, 0, 1, 0, 0)] },
      price,
    });
  });

  it("drops the simulated row, the cleanup row, and the old per-zone restricted row", async () => {
    const r = await get(handler, "api/window/all/0.json");
    const sweeps = r.json.sweeps as { asked: number; region: string }[];
    expect(sweeps.some((s) => s.asked === 99)).toBe(false);
    expect(sweeps.filter((s) => s.region === "us-east-2")).toHaveLength(12);
  });

  it("a window loses nothing: it is the whole-data sweeps in [L, H1)", async () => {
    const all = (await get(handler, "api/window/all/0.json")).json.sweeps as { h: number }[];
    for (const [hz, page] of [
      ["day", 0],
      ["day", 3],
      ["day", 7],
      ["week", 0],
      ["week", 1],
      ["month", 0],
      ["all", 0],
    ] as const) {
      const w = (await get(handler, `api/window/${hz}/${page}.json`)).json;
      expect(w.sweeps).toEqual(all.filter((s) => s.h >= w.L && s.h < w.H1));
    }
  });

  it("gives the same bytes on every request", async () => {
    const a = await get(handler, "api/window/month/0.json");
    const b = await get(handler, "api/window/month/0.json");
    expect(a.text).toBe(b.text);
  });
});

describe("paths and statuses", () => {
  it.each([
    "api/window/week/2.json",
    "api/window/week/01.json",
    "api/window/day/8.json",
    "api/window/day/01.json",
    "api/window/year/0.json",
    "api/window/all/1.json",
    "api/other.json",
    "index.html",
  ])("404 for %s", async (p) => {
    const r = await get(handler, p);
    expect(r.status).toBe(404);
    expect(typeof r.json.error).toBe("string");
  });

  it("503 for a missing database, with a path relative to the repo", async () => {
    const h = createHandler({
      dbPath: join(repoRoot, "data/none.duckdb"),
      sqlDir: join(webDir, "sql"),
      repoRoot,
    });
    const r = await get(h, "api/meta.json");
    expect(r.status).toBe(503);
    expect(r.json).toEqual({ error: "no database at data/none.duckdb; run make data/db" });
    await h.close();
  });
});

describe("other databases", () => {
  it("zoneStartH is null when no aws_odcr row has a zone_id (spec 0006 section 7.3)", async () => {
    const odcrRow = rows.find((r) => r.probe === "aws_odcr" && r.details?.zone_id);
    const noZone = rows.map((r) => {
      if (r.probe !== "aws_odcr" || !r.details?.zone_id) return r;
      const { zone_id: _z, ...details } = r.details as Record<string, unknown>;
      return { ...r, details };
    });
    expect(odcrRow).toBeDefined();
    const h = mk(buildDb(noZone, join(work, "nozone")));
    expect((await get(h, "api/meta.json")).json.zoneStartH).toBeNull();
    await h.close();
  }, 60_000);

  it("a database with no row of the 9 types: t0 is null and a window is 404", async () => {
    const ignored: FixtureRow = {
      ts: "2026-10-01T00:00:00Z",
      provider: "aws",
      probe: "aws_cb_earliest",
      sku: P5,
      region: "us-east-1",
      outcome: "success",
    };
    const h = mk(buildDb([ignored], join(work, "empty")));
    expect((await get(h, "api/meta.json")).json).toEqual({ t0: null, simulated: false });
    expect((await get(h, "api/window/all/0.json")).status).toBe(404);
    await h.close();
  }, 60_000);

  it("a database of simulated rows only: the board uses them and sets simulated", async () => {
    const simRows = rows.filter((r) => r.sim).map((r) => ({ ...r, ts: "2026-10-05T12:36:00Z" }));
    const h = mk(buildDb(simRows, join(work, "sim")));
    const m = (await get(h, "api/meta.json")).json;
    expect(m.simulated).toBe(true);
    expect(m.rows).toBe(1);
    const w = (await get(h, "api/window/all/0.json")).json;
    expect(w.sweeps).toHaveLength(1);
    expect(w.sweeps[0].asked).toBe(99);
    await h.close();
  }, 60_000);

  it("only Spot rows are real next to simulated provisioning rows: t0 uses the fallback", async () => {
    const h = mk(
      buildDb(
        [
          {
            ts: "2026-10-05T03:20:00Z",
            provider: "aws",
            probe: "aws_spot_score",
            sku: G5,
            region: "us-east-1",
            outcome: "success",
            details: { score: 7 },
          },
          {
            ts: "2026-10-05T05:40:00Z",
            provider: "aws",
            probe: "aws_spot_score",
            sku: G5,
            region: "us-east-1",
            outcome: "success",
            details: { score: 5 },
          },
          {
            ts: "2026-10-05T01:00:00Z",
            provider: "aws",
            probe: "aws_odcr",
            sku: G5,
            region: "us-east-1",
            outcome: "success",
            n: 1,
            placed: 1,
            run: "sim1",
            zone: "us-east-1a",
            sim: true,
          },
        ],
        join(work, "spotonly"),
      ),
    );
    const m = (await get(h, "api/meta.json")).json;
    expect(m.simulated).toBe(false);
    expect(m.t0).toBe("2026-10-05T03:00:00.000Z");
    expect(m.tEnd).toBe("2026-10-05T05:40:00.000Z");
    expect(m.nh).toBe(4);
    expect((await get(h, "api/window/all/0.json")).status).toBe(200);
    await h.close();
  }, 60_000);

  it("meta.dates includes the UTC day of tEnd when its time of day is before t0's", async () => {
    const sp = (ts: string): FixtureRow => ({
      ts,
      provider: "aws",
      probe: "aws_spot_score",
      sku: G5,
      region: "us-east-1",
      outcome: "success",
      details: { score: 5 },
    });
    const h = mk(
      buildDb([sp("2026-10-05T03:20:00Z"), sp("2026-10-06T01:10:00Z")], join(work, "tendearly")),
    );
    const m = (await get(h, "api/meta.json")).json;
    expect(m.t0).toBe("2026-10-05T03:00:00.000Z");
    expect(m.tEnd).toBe("2026-10-06T01:10:00.000Z");
    expect(m.dates).toEqual(["2026-10-05", "2026-10-06"]);
    await h.close();
  }, 60_000);

  it("an aws_odcr Region row (restricted or error, zone null) counts 0 zones", async () => {
    const o = (r: Partial<FixtureRow> & Pick<FixtureRow, "region" | "outcome">): FixtureRow => ({
      ts: "2026-10-05T00:05:00Z",
      provider: "aws",
      probe: "aws_odcr",
      sku: G5,
      n: 1,
      ...r,
    });
    const h = mk(
      buildDb(
        [
          o({ region: "us-west-1", outcome: "error", run: "re", err: "Boom" }),
          o({ region: "us-west-2", outcome: "restricted", run: "rr" }),
          o({ region: "us-east-2", outcome: "no_capacity", run: "rz", zone: "us-east-2a" }),
        ],
        join(work, "regionrow"),
      ),
    );
    const w = (await get(h, "api/window/all/0.json")).json;
    const zonesOf = (region: string) =>
      (w.sweeps as { region: string; zones: number }[]).find((s) => s.region === region)?.zones;
    expect.soft(zonesOf("us-west-1")).toBe(0);
    expect.soft(zonesOf("us-west-2")).toBe(0);
    expect.soft(zonesOf("us-east-2")).toBe(1);
    await h.close();
  }, 60_000);

  it("cadenceH.cb takes the gaps between distinct hours: 3 checks in 1 hour are 1 point", async () => {
    const c = (ts: string): FixtureRow => ({
      ts,
      provider: "aws",
      probe: "aws_cb_offering",
      sku: P5,
      region: "us-east-1",
      outcome: "success",
      n: 1,
      details: { start_window_days: 7 },
    });
    const h = mk(
      buildDb(
        [
          c("2026-10-05T00:05:00Z"),
          c("2026-10-05T00:10:00Z"),
          c("2026-10-05T00:15:00Z"),
          c("2026-10-05T04:05:00Z"),
        ],
        join(work, "cbdistinct"),
      ),
    );
    expect((await get(h, "api/meta.json")).json.cadenceH).toEqual({ launch: null, cb: 4 });
    await h.close();
  }, 60_000);

  it.each([
    [1, ["2026-10-05T00:05:00Z", "2026-10-05T01:05:00Z", "2026-10-05T02:05:00Z"]],
    [2, ["2026-10-05T00:05:00Z", "2026-10-05T02:05:00Z", "2026-10-05T04:05:00Z"]],
  ])(
    "rows with no run_id make 1 round for each %i h cycle",
    async (cad, stamps) => {
      const o = (ts: string): FixtureRow => ({
        ts,
        provider: "aws",
        probe: "aws_odcr",
        sku: G5,
        region: "us-east-1",
        zone: "us-east-1a",
        outcome: "success",
        n: 1,
        placed: 1,
      });
      const h = mk(buildDb(stamps.map(o), join(work, `norun${cad}`)));
      expect((await get(h, "api/meta.json")).json.cadenceH.launch).toBe(cad);
      expect((await get(h, "api/window/all/0.json")).json.sweeps).toHaveLength(3);
      await h.close();
    },
    60_000,
  );

  it("err is the first attempt of the earliest zone, also when its code is null", async () => {
    const o = (r: Partial<FixtureRow> & Pick<FixtureRow, "ts" | "region">): FixtureRow => ({
      provider: "aws",
      probe: "aws_odcr",
      sku: G5,
      outcome: "no_capacity",
      n: 1,
      ...r,
    });
    const code = "InsufficientInstanceCapacity";
    const h = mk(
      buildDb(
        [
          // Zone level: the earliest zone has a null code, a later zone has a code.
          o({ ts: "2026-10-05T00:05:00Z", region: "us-west-1", run: "zl", zone: "az1" }),
          o({ ts: "2026-10-05T00:06:00Z", region: "us-west-1", run: "zl", zone: "az2", err: code }),
          // Step level: step 1 has a null code, step 2 has a code, in 1 zone.
          o({
            ts: "2026-10-05T00:05:00Z",
            region: "us-west-2",
            run: "sl",
            zone: "az1",
            details: { search_step: 1 },
          }),
          o({
            ts: "2026-10-05T00:06:00Z",
            region: "us-west-2",
            run: "sl",
            zone: "az1",
            n: 2,
            err: code,
            details: { search_step: 2 },
          }),
        ],
        join(work, "nullerr"),
      ),
    );
    const w = (await get(h, "api/window/all/0.json")).json;
    const errOf = (region: string) =>
      (w.sweeps as { region: string; err: string | null }[]).find((s) => s.region === region)?.err;
    expect.soft(errOf("us-west-1")).toBeNull();
    expect.soft(errOf("us-west-2")).toBeNull();
    await h.close();
  }, 60_000);

  it("reads the file again after it was replaced", async () => {
    const dir = join(work, "reload");
    const h = mk(buildDb(rows, dir));
    expect((await get(h, "api/meta.json")).json.rows).toBe(realRowCount);
    const extra: FixtureRow = {
      ts: "2026-10-06T00:00:00Z",
      provider: "aws",
      probe: "aws_spot_score",
      sku: G5,
      region: "us-east-1",
      outcome: "success",
      details: { score: 3 },
    };
    buildDb([...rows, extra], dir);
    expect((await get(h, "api/meta.json")).json.rows).toBe(realRowCount + 1);
    await h.close();
  }, 120_000);
});

describe("DuckDB", () => {
  it("opens the file read-only: a write through the connection fails", async () => {
    const conn = await handler.connection();
    expect(conn).not.toBeNull();
    await expect(conn?.run("CREATE TABLE t_write (i INTEGER)")).rejects.toThrow(/read-only/);
    await expect(conn?.run("DELETE FROM rows")).rejects.toThrow(/read-only/);
  });

  it("SELECT version() equals the pinned duckdb CLI", async () => {
    const conn = await handler.connection();
    const reader = await conn?.runAndReadAll("SELECT version() AS v");
    const out = (reader?.getRowObjectsJson() ?? []) as { v: string }[];
    const node = String(out[0]?.v);
    const cli = execFileSync("duckdb", ["-noheader", "-list", ":memory:", "SELECT version()"], {
      encoding: "utf8",
    }).trim();
    expect(node).toBe(cli);
    const pin = /^duckdb = "([^"]+)"/m.exec(readFileSync(join(repoRoot, "mise.toml"), "utf8"))?.[1];
    expect(node).toBe(`v${pin}`);
  });

  it("makes no network call: external access is off", async () => {
    const conn = await handler.connection();
    await expect(conn?.run("SELECT * FROM read_csv('http://127.0.0.1:9/x.csv')")).rejects.toThrow();
  });
});

describe("shapes against the stub DataSource", () => {
  it("meta and window have the stub's keys, in order, and the same value types", async () => {
    const meta = (await get(handler, "api/meta.json")).json;
    const win = (await get(handler, "api/window/all/0.json")).json;
    const sm = stubMeta() as unknown as Record<string, unknown>;
    expect(Object.keys(meta)).toEqual(Object.keys(sm));
    const kind = (v: unknown) => (v === null ? "null" : Array.isArray(v) ? "array" : typeof v);
    for (const [k, v] of Object.entries(sm)) {
      if (v !== null && k !== "upStartH") expect(kind(meta[k])).toBe(kind(v));
    }
    expect(Object.keys(win)).toEqual(Object.keys(stubWindow()));
    const stubSweepKeys = [
      "h",
      "t",
      "cloud",
      "region",
      "sku",
      "probe",
      "asked",
      "state",
      "nMax",
      "reason",
      "err",
    ];
    const keys = Object.keys(win.sweeps[0]);
    expect(keys.filter((k) => stubSweepKeys.includes(k))).toEqual(stubSweepKeys);
    // `got` is added by the derive join (spec 0006 section 7.4), not by the server.
    expect(keys).not.toContain("got");
    // The coverage fields and the zone fields are extra.
    expect(keys.filter((k) => !stubSweepKeys.includes(k))).toEqual([
      "gotZoneMax",
      "gotZoneSum",
      "zones",
      "zonesYes",
      "zoneList",
    ]);
    expect(Object.keys(win.cb[0])).toEqual(["sku", "region", "checks", "longest"]);
    expect(Object.keys(win.price)).toEqual(["dates", "series"]);
  });

  it("HttpDataSource reads both paths through the handler", async () => {
    const ds = new HttpDataSource("/", async (u) => {
      const r = await handler.handle(u.slice(1));
      return { ok: r.status === 200, status: r.status, json: async () => JSON.parse(r.body) };
    });
    expect((await ds.meta()).t0).toBe("2026-10-05T10:00:00.000Z");
    expect((await ds.window("week", 1)).H0).toBe(162);
    expect((await ds.window("day", 7)).H0).toBe(158);
    await expect(ds.window("day", 8)).rejects.toThrow(/404/);
    await expect(ds.window("week", 9)).rejects.toThrow(/404/);
  });
});

describe("load() failure", () => {
  it("closes the instance and the connection when a query throws", async () => {
    const empty = join(work, "no-schema.duckdb");
    const seed = await DuckDBInstance.create(empty);
    seed.closeSync();
    const instClose = vi.spyOn(DuckDBInstance.prototype, "closeSync");
    const connClose = vi.spyOn(DuckDBConnection.prototype, "closeSync");
    const h = mk(empty);
    try {
      const r = await h.handle("api/meta.json");
      expect(r.status).toBe(500);
      expect(instClose).toHaveBeenCalledTimes(1);
      expect(connClose).toHaveBeenCalledTimes(1);
    } finally {
      await h.close();
      instClose.mockRestore();
      connClose.mockRestore();
    }
  });
});
