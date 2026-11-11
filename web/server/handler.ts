// The board request handler (spec 0006 section 8.1). It takes a path and returns a status, a
// content type, and a body. It answers `api/meta.json` and `api/window/<hz>/<page>.json` from a
// DuckDB file opened read-only. It holds no credential, loads no extension, and makes no
// network call: the database is opened with external access off.

import { existsSync, readFileSync, statSync } from "node:fs";
import { isAbsolute, join, relative } from "node:path";
import { type DuckDBConnection, DuckDBInstance } from "@duckdb/node-api";
import { etDayOfWeek, pageList, pageWindow } from "../src/board/derive/time.ts";
import type { WindowHorizon } from "../src/board/derive/types.ts";

export type HandlerResponse = { status: number; contentType: string; body: string };

export type HandlerOptions = {
  /** The DuckDB file (`WEB_DB`). */
  dbPath: string;
  /** The folder that holds the window SQL (`web/sql`). */
  sqlDir: string;
  /** The repo root: the path in a 503 message is relative to it. */
  repoRoot: string;
};

export interface BoardHandler {
  handle(path: string): Promise<HandlerResponse>;
  /** The connection of the current database, for the tests (opens the file when needed). */
  connection(): Promise<DuckDBConnection | null>;
  close(): Promise<void>;
}

type Row = Record<string, unknown>;
type Params = Record<string, string | number | boolean>;

type Loaded = {
  sig: string;
  instance: DuckDBInstance;
  conn: DuckDBConnection;
  empty: { simulated: boolean } | null;
  t0: string;
  t0Ms: number;
  tEndMs: number;
  meta: Row | null;
  nh: number;
  cad: number;
  bodies: Map<string, string>;
};

const JSON_TYPE = "application/json";
const HOUR_MS = 3_600_000;
const SQL_FILES = ["mode", "rows", "meta", "sweeps", "cb", "spot", "azspot", "price"] as const;
const WIDE = { lo: -1_000_000_000, hi: 1_000_000_000 };

const reply = (status: number, value: unknown): HandlerResponse => ({
  status,
  contentType: JSON_TYPE,
  body: JSON.stringify(value),
});
const r2 = (x: number): number => Math.round(x * 100) / 100;

function upperMedian(gaps: number[]): number | null {
  if (gaps.length === 0) return null;
  const s = [...gaps].sort((a, b) => a - b);
  return r2(s[Math.floor(s.length / 2)] ?? 0);
}

/** Gaps between consecutive values of each group (values arrive sorted within a group). */
function gapsOf(groups: Map<string, number[]>): number[] {
  const gaps: number[] = [];
  for (const v of groups.values())
    for (let i = 1; i < v.length; i++) gaps.push(r2((v[i] ?? 0) - (v[i - 1] ?? 0)));
  return gaps;
}

/** The distinct values of each group, ascending (several checks in 1 hour are 1 point). */
function distinctSorted(groups: Map<string, number[]>): Map<string, number[]> {
  return new Map(
    [...groups].map(([k, v]) => [k, [...new Set(v)].sort((a, b) => a - b)] as [string, number[]]),
  );
}

function push<T>(m: Map<string, T[]>, k: string, v: T): void {
  const a = m.get(k);
  if (a) a.push(v);
  else m.set(k, [v]);
}

function sortedObject<T>(m: Map<string, T>): Record<string, T> {
  return Object.fromEntries([...m.entries()].sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0)));
}

/** A sweep row in the key order of spec 0006 section 5.3 (window payloads and `meta.now`). */
function shapeSweep({
  h,
  t,
  cloud,
  region,
  sku,
  probe,
  asked,
  gotZoneMax,
  gotZoneSum,
  state,
  nMax,
  reason,
  err,
  zones,
  zonesYes,
  zoneList,
}: Row): Row {
  return {
    h,
    t,
    cloud,
    region,
    sku,
    probe,
    asked,
    gotZoneMax,
    gotZoneSum,
    state,
    nMax,
    reason,
    err,
    zones,
    zonesYes,
    // Only an aws_odcr sweep with a named zone carries the per-zone list (spec 0006 section 5.3).
    ...(Array.isArray(zoneList) && zoneList.length > 0 ? { zoneList } : {}),
  };
}

const byKey = (a: string, b: string): number => (a < b ? -1 : a > b ? 1 : 0);

/**
 * `meta.now` (spec 0006 section 7.3): the latest round of each sku, the place counts over the whole
 * data, and the latest 1-day Capacity Block check of each place. `sweeps` are the rows of
 * sweeps.sql over the whole data, so the sweep rule of section 5.3 has 1 copy.
 */
function buildNow(sweeps: Row[], cbRows: Row[], cad: number): Row {
  const answered = sweeps.filter((s) => Number(s.state) !== -2);
  const lastRound = new Map<string, number>();
  for (const s of answered) {
    const r = Math.floor(Number(s.h) / cad);
    lastRound.set(String(s.sku), Math.max(lastRound.get(String(s.sku)) ?? -Infinity, r));
  }
  const nowSweeps = answered
    .filter((s) => Math.floor(Number(s.h) / cad) === lastRound.get(String(s.sku)))
    .map(shapeSweep);

  type Place = {
    sku: string;
    cloud: string;
    region: string;
    probe: string;
    n: number;
    na: number;
    yes: number;
    nu: number;
    no: number;
    lastYesH: number | null;
  };
  const byPlace = new Map<string, Place>();
  for (const s of answered) {
    const k = `${s.sku}\u0000${s.cloud}\u0000${s.region}`;
    let p = byPlace.get(k);
    if (!p) {
      p = {
        sku: String(s.sku),
        cloud: String(s.cloud),
        region: String(s.region),
        probe: String(s.probe),
        n: 0,
        na: 0,
        yes: 0,
        nu: 0,
        no: 0,
        lastYesH: null,
      };
      byPlace.set(k, p);
    }
    const state = Number(s.state);
    if (state >= -1) p.n++;
    if (state === -1) p.na++;
    if (state === -3) p.nu++;
    if (state === -4) p.no++;
    if (Number(s.gotZoneMax) > 0) {
      p.yes++;
      p.lastYesH = Math.max(p.lastYesH ?? -Infinity, Number(s.h));
    }
  }
  const places = [...byPlace.values()].sort(
    (a, b) => byKey(a.sku, b.sku) || byKey(a.cloud, b.cloud) || byKey(a.region, b.region),
  );

  // Capacity Block places: the 1-day, 1-machine checks are those of section 11.3 item 4.
  type Cb = { checks: Row[]; longest: Row[] };
  const cbPlaces = new Map<string, Cb & { sku: string; region: string }>();
  for (const c of cbRows) {
    const k = `${c.sku}\u0000${c.region}`;
    let p = cbPlaces.get(k);
    if (!p) {
      p = { sku: String(c.sku), region: String(c.region), checks: [], longest: [] };
      cbPlaces.set(k, p);
    }
    if (c.kind === "longest") p.longest.push(c);
    if (c.kind === "check" && (c.n6 === null || Number(c.n6) === 1)) {
      if (c.n3 === null || Number(c.n3) === 24) p.checks.push(c);
    }
  }
  const cb = [...cbPlaces.values()]
    .sort((a, b) => byKey(a.sku, b.sku) || byKey(a.region, b.region))
    .map((p) => {
      const h = p.checks.length ? Math.max(...p.checks.map((c) => Number(c.h))) : null;
      // The found check at the latest hour with the smallest lead.
      const best = p.checks
        .filter((c) => Number(c.h) === h && Number(c.n1) === 1 && c.n7 !== null)
        .sort((a, b) => Number(a.n7) - Number(b.n7))[0];
      const lastLong = p.longest.length ? Math.max(...p.longest.map((c) => Number(c.h))) : null;
      const longs = p.longest
        .filter((c) => Number(c.h) === lastLong && Number(c.n1) === 1 && c.n3 !== null)
        .map((c) => Number(c.n3));
      return {
        sku: p.sku,
        region: p.region,
        h,
        leadH: best ? Number(best.n7) : null,
        durH: best && best.n3 !== null ? Number(best.n3) : null,
        start: best && best.s !== null ? String(best.s) : null,
        longestH: longs.length ? Math.max(...longs) : null,
      };
    });
  return { sweeps: nowSweeps, places, cb };
}

export function createHandler(opts: HandlerOptions): BoardHandler {
  const sql = Object.fromEntries(
    SQL_FILES.map((n) => [n, readFileSync(join(opts.sqlDir, `${n}.sql`), "utf8")]),
  ) as Record<(typeof SQL_FILES)[number], string>;

  let loaded: Loaded | null = null;
  // One request at a time: a reload never closes a connection that a request still uses.
  let queue: Promise<unknown> = Promise.resolve();

  const run = <T>(fn: () => Promise<T>): Promise<T> => {
    const p = queue.then(fn, fn);
    queue = p.catch(() => undefined);
    return p;
  };

  async function query(conn: DuckDBConnection, text: string, params?: Params): Promise<Row[]> {
    const reader = await conn.runAndReadAll(text, params);
    return reader.getRowObjectsJson() as Row[];
  }

  async function load(sig: string): Promise<Loaded> {
    const instance = await DuckDBInstance.create(opts.dbPath, {
      access_mode: "READ_ONLY",
      enable_external_access: "false",
    });
    let conn: DuckDBConnection | null = null;
    try {
      conn = await instance.connect();
      return await loadFrom(sig, instance, conn);
    } catch (e) {
      // A throw after create must not leak the instance or the connection.
      conn?.closeSync();
      instance.closeSync();
      throw e;
    }
  }

  async function loadFrom(
    sig: string,
    instance: DuckDBInstance,
    conn: DuckDBConnection,
  ): Promise<Loaded> {
    await conn.run("SET TimeZone = 'UTC'");
    const [mode] = await query(conn, sql.mode);
    const real = Number(mode?.real_rows ?? 0);
    const simRows = Number(mode?.simulated_rows ?? 0);
    const simulated = real === 0 && simRows > 0;
    const base: Loaded = {
      sig,
      instance,
      conn,
      empty: null,
      t0: "",
      t0Ms: 0,
      tEndMs: 0,
      meta: null,
      nh: 0,
      cad: 2,
      bodies: new Map(),
    };
    if (real === 0 && simRows === 0) return { ...base, empty: { simulated: false } };
    await conn.run(sql.rows, { sim: simulated });
    const [m] = await query(conn, sql.meta);
    if (!m) return { ...base, empty: { simulated } };

    const t0 = String(m.t0);
    const t0Ms = Date.parse(t0);
    const tEndMs = Date.parse(String(m.t_end));
    const nh = Number(m.nh);
    const sweeps = await query(conn, sql.sweeps, { t0, ...WIDE });
    const cbRows = await query(conn, sql.cb, { t0, ...WIDE });

    // Cadence: the upper median gap between consecutive sweeps of 1 (cloud, region, sku), and
    // between consecutive distinct hours of the checks of 1 (region, sku) (spec 0006 section 7.3).
    const byTriple = new Map<string, number[]>();
    const probeStartH = new Map<string, number>();
    const firstH = new Map<string, number>();
    const regions = new Set<string>();
    let upStartH: number | null = null;
    for (const s of sweeps) {
      const h = Number(s.h);
      push(byTriple, `${s.cloud}|${s.region}|${s.sku}`, Number(s.t));
      const pk = `${s.cloud}:${s.probe}`;
      probeStartH.set(pk, Math.min(probeStartH.get(pk) ?? Infinity, Number(s.t)));
      firstH.set(String(s.sku), Math.min(firstH.get(String(s.sku)) ?? Infinity, h));
      regions.add(`${s.cloud}|${s.region}`);
      if (Number(s.nMax) > Number(s.asked) && (upStartH === null || h < upStartH)) upStartH = h;
    }
    const byPlace = new Map<string, number[]>();
    const cbFirstH = new Map<string, number>();
    for (const c of cbRows) {
      if (c.kind === "place") regions.add(`aws|${c.region}`);
      if (c.kind !== "check") continue;
      push(byPlace, `${c.region}|${c.sku}`, Number(c.h));
      const dur = c.n3 === null ? null : Number(c.n3);
      if ((c.n6 === null || Number(c.n6) === 1) && (dur === null || dur === 24)) {
        cbFirstH.set(String(c.sku), Math.min(cbFirstH.get(String(c.sku)) ?? Infinity, Number(c.h)));
      }
    }
    const dates: string[] = [];
    const dayMs = 24 * HOUR_MS;
    for (let ms = Math.floor(t0Ms / dayMs) * dayMs; ms <= tEndMs; ms += dayMs)
      dates.push(new Date(ms).toISOString().slice(0, 10));
    const launch = upperMedian(gapsOf(byTriple));
    const meta: Row = {
      t0,
      tEnd: String(m.t_end),
      nh,
      dates,
      weekends: dates.filter((d) => etDayOfWeek(Date.parse(`${d}T12:00:00Z`)) === 6).length,
      cadenceH: { launch, cb: upperMedian(gapsOf(distinctSorted(byPlace))) },
      probeStartH: sortedObject(probeStartH),
      zones: (m.zones as string[] | null) ?? [],
      cbHorizonDays: (m.cb_horizon_days as number[] | null) ?? [],
      regions: [...regions].sort(),
      rows: Number(m.row_count),
      sweeps: sweeps.length,
      firstH: sortedObject(firstH),
      cbFirstH: sortedObject(cbFirstH),
      upStartH,
      zoneStartH: m.zone_start_h === null ? null : Number(m.zone_start_h),
      simulated,
      now: buildNow(sweeps, cbRows, launch === null || launch === 0 ? 2 : launch),
    };
    return {
      ...base,
      t0,
      t0Ms,
      tEndMs,
      meta,
      nh,
      cad: launch === null || launch === 0 ? 2 : launch,
    };
  }

  /** The loaded database for the current file, or a 503 response when the file is missing. */
  async function current(): Promise<Loaded | HandlerResponse> {
    if (!existsSync(opts.dbPath)) {
      if (loaded) {
        loaded.conn.closeSync();
        loaded.instance.closeSync();
        loaded = null;
      }
      const shown = relative(opts.repoRoot, opts.dbPath);
      return reply(503, {
        error: `no database at ${isAbsolute(shown) ? opts.dbPath : shown}; run make data/db`,
      });
    }
    const st = statSync(opts.dbPath);
    const sig = `${st.size}:${st.mtimeMs}:${st.ino}`;
    if (loaded && loaded.sig !== sig) {
      loaded.conn.closeSync();
      loaded.instance.closeSync();
      loaded = null;
    }
    if (!loaded) loaded = await load(sig);
    return loaded;
  }

  async function window(l: Loaded, hz: WindowHorizon, page: number): Promise<HandlerResponse> {
    if (!l.meta) return reply(404, { error: "no rows" });
    const pg = pageList(hz, l.t0Ms, l.tEndMs)[page];
    if (!pg) return reply(404, { error: `no page ${page} in ${hz}` });
    const { H0, H1 } = pageWindow(pg, l.t0Ms, l.nh);
    const L = Math.min(H0, Math.max(0, 2 * H0 - H1));
    const t0 = l.t0;
    const sweeps = (await query(l.conn, sql.sweeps, { t0, lo: L, hi: H1 })).map(shapeSweep);

    const places = new Map<
      string,
      { sku: unknown; region: unknown; checks: unknown[]; longest: unknown[] }
    >();
    for (const c of await query(l.conn, sql.cb, { t0, lo: L, hi: H1 })) {
      const k = `${c.sku}|${c.region}`;
      let p = places.get(k);
      if (!p) {
        p = { sku: c.sku, region: c.region, checks: [], longest: [] };
        places.set(k, p);
      }
      if (c.kind === "check") p.checks.push([c.h, c.n1, c.n2, c.n3, c.n4, c.n5, c.n6, c.n7]);
      if (c.kind === "longest") p.longest.push([c.h, c.n1, c.n2, c.n3, c.n4, c.s]);
    }
    const cb = [...places.values()].sort((a, b) =>
      `${a.sku}\u0000${a.region}` < `${b.sku}\u0000${b.region}` ? -1 : 1,
    );

    const range = { t0, lo: H0 - l.cad, hi: H1 };
    const spot = new Map<string, unknown[]>();
    for (const x of await query(l.conn, sql.spot, range)) push(spot, String(x.key), [x.h, x.score]);
    const azspot = new Map<string, unknown[]>();
    for (const x of await query(l.conn, sql.azspot, range)) {
      push(azspot, String(x.key), [x.h, x.high, x.medium, x.low, x.other]);
    }
    const dates = (l.meta.dates as string[]) ?? [];
    const series = new Map<string, (number | null)[]>();
    for (const x of await query(l.conn, sql.price, {})) {
      const i = dates.indexOf(String(x.date));
      if (i < 0) continue;
      let s = series.get(String(x.key));
      if (!s) {
        s = dates.map(() => null);
        series.set(String(x.key), s);
      }
      s[i] = Number(x.price);
    }
    return reply(200, {
      hz,
      page,
      H0,
      H1,
      L,
      sweeps,
      cb,
      spot: sortedObject(spot),
      azspot: sortedObject(azspot),
      price: { dates, series: sortedObject(series) },
    });
  }

  return {
    handle(path: string): Promise<HandlerResponse> {
      const p = path.split("?")[0]?.replace(/^\.?\//, "") ?? "";
      const win = /^api\/window\/(day|week|month|all)\/(0|[1-9][0-9]*)\.json$/.exec(p);
      if (p !== "api/meta.json" && !win) return Promise.resolve(reply(404, { error: "not found" }));
      return run(async () => {
        try {
          const l = await current();
          if ("status" in l) return l;
          if (p === "api/meta.json") {
            return reply(200, l.empty ? { t0: null, simulated: l.empty.simulated } : l.meta);
          }
          const hz = win?.[1] as WindowHorizon;
          const page = Number(win?.[2]);
          const key = `${hz}/${page}`;
          const hit = l.bodies.get(key);
          if (hit !== undefined) return { status: 200, contentType: JSON_TYPE, body: hit };
          const res = await window(l, hz, page);
          if (res.status === 200) l.bodies.set(key, res.body);
          return res;
        } catch (e) {
          return reply(500, { error: e instanceof Error ? e.message : String(e) });
        }
      });
    },
    connection(): Promise<DuckDBConnection | null> {
      return run(async () => {
        const l = await current();
        return "status" in l ? null : l.conn;
      });
    },
    close(): Promise<void> {
      return run(async () => {
        if (loaded) {
          loaded.conn.closeSync();
          loaded.instance.closeSync();
          loaded = null;
        }
      });
    },
  };
}
