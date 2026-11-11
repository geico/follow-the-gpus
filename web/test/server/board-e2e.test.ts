// End to end: the board server against the events fixture (spec 0006 sections 8.4 item 3, 7.3, 7.4).
// The fixture rows come from the events fixture (events to rows, see
// events-to-rows.ts). A DuckDB file is built from them with the real scripts/compact-rows.sh and
// scripts/build-db.sh. For each <hz>/<page>, the derive layer on the payload of the handler must
// equal the derive layer on the full events. No network.

import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { type BoardHandler, createHandler } from "../../server/handler";
import { aggregateAll } from "../../src/board/derive/aggregate";
import { deriveBacktest } from "../../src/board/derive/backtest";
import { deriveBookahead } from "../../src/board/derive/bookahead";
import { MACHINES, TIERS } from "../../src/board/derive/catalogue";
import { deriveDefinitions } from "../../src/board/derive/definitions";
import { deriveForecast } from "../../src/board/derive/forecast";
import { deriveHeader } from "../../src/board/derive/header";
import { deriveHowmany } from "../../src/board/derive/howmany";
import { deriveIntro } from "../../src/board/derive/intro";
import { joinData } from "../../src/board/derive/join";
import { deriveMatrix } from "../../src/board/derive/matrix";
import { deriveNow } from "../../src/board/derive/now";
import { derivePrice } from "../../src/board/derive/price";
import { deriveSawtooth } from "../../src/board/derive/sawtooth";
import { deriveSowhat } from "../../src/board/derive/sowhat";
import { deriveStickiness } from "../../src/board/derive/stickiness";
import { deriveSubstitutes } from "../../src/board/derive/substitutes";
import { makeFrame, pageList } from "../../src/board/derive/time";
import { deriveTimeofday } from "../../src/board/derive/timeofday";
import { deriveTrend } from "../../src/board/derive/trend";
import type {
  BoardData,
  Horizon,
  Meta,
  Sweep,
  ViewState,
  WindowPayload,
} from "../../src/board/derive/types";
import { type Events, eventsToRows, isUnrealizable, realizable, spanOf } from "./events-to-rows";
import { buildDb, repoRoot, webDir } from "./fixture";

const raw = JSON.parse(
  readFileSync(join(webDir, "test/fixtures/board/events.json"), "utf8"),
) as Events;
const events = realizable(raw);
const rows = eventsToRows(raw);
const span = spanOf(rows, raw.meta.t0);
const meta = raw.meta as unknown as Meta;

let work: string;
let handler: BoardHandler;
let served: Meta;

const get = async (path: string) => {
  const r = await handler.handle(path);
  expect(r.status, path).toBe(200);
  return JSON.parse(r.body);
};

beforeAll(async () => {
  work = mkdtempSync(join(tmpdir(), "ftg-e2e-"));
  const db = buildDb(rows, work);
  handler = createHandler({ dbPath: db, sqlDir: join(webDir, "sql"), repoRoot });
  served = await get("api/meta.json");
}, 120_000);

afterAll(async () => {
  await handler.close();
  rmSync(work, { recursive: true, force: true });
});

/**
 * The server may or may not keep the design's `got` in a sweep; the join sets it. The
 * per-zone list is not in the full events, so it is dropped here and checked on its
 * own below.
 */
const dropGot = (w: WindowPayload): WindowPayload => ({
  ...w,
  sweeps: w.sweeps.map(
    ({ got: _got, zoneList: _list, ...s }: Sweep & { got?: number }) => s as Sweep,
  ),
});

/** The window of the full events: every record, no range (the derive layer picks its own). */
function fullWindow(hz: Horizon, page: number): WindowPayload {
  const f = makeFrame(meta, hz, page);
  const sweeps = events.sweeps.map(({ got: _got, ...s }) => s) as unknown as Sweep[];
  return {
    hz,
    page,
    H0: f.H0,
    H1: f.H1,
    L: f.L,
    sweeps,
    cb: events.cb,
    spot: events.spot,
    azspot: events.azspot,
    price: events.price,
  };
}

const STATES: ViewState[] = [];
for (const tier of ["All", TIERS[0] as string]) {
  const gpu = MACHINES.find((m) => tier === "All" || m.tier === tier)?.name ?? "";
  STATES.push({ hz: "week", page: null, tier, gpu });
  STATES.push({ hz: "week", page: null, tier, gpu: MACHINES[MACHINES.length - 1]?.name ?? gpu });
}

/** Everything the derive layer gives for a payload: the frame, the aggregation, every section. */
function derive(data: BoardData, hz: Horizon, page: number) {
  const f = makeFrame(data.meta, hz, page);
  return {
    f,
    aggs: aggregateAll(joinData(data), f),
    sections: STATES.map((st) => {
      const s = { ...st, hz, page };
      return [
        deriveHeader(data, s),
        deriveIntro(data, s),
        deriveHowmany(data, s),
        deriveNow(data, s),
        deriveSawtooth(data, s),
        deriveStickiness(data, s),
        deriveTimeofday(data, s),
        deriveBookahead(data, s),
        deriveSubstitutes(data, s),
        deriveMatrix(data, s),
        deriveBacktest(data, s),
        deriveForecast(data, s),
        derivePrice(data, s),
        deriveSowhat(data, s),
        deriveTrend(data, s),
        deriveDefinitions(data, s),
      ];
    }),
  };
}

/** A case that derives a full page: the default 5 s is marginal under load. */
const PAGE_TIMEOUT_MS = 30_000;

const t0Ms = Date.parse(meta.t0);
const tEndMs = Date.parse(meta.tEnd);
const cases: [Horizon, number][] = (["week", "month", "all"] as const).flatMap((hz) =>
  pageList(hz, t0Ms, tEndMs).map((_, i) => [hz, i] as [Horizon, number]),
);

describe("the events fixture and the rows", () => {
  it("the generator makes no sweep that rows cannot make", () => {
    expect(raw.sweeps.filter(isUnrealizable)).toEqual([]);
    expect(span).toEqual({ tEnd: raw.meta.tEnd, nh: raw.meta.nh });
  });
});

describe("meta.json equals the meta of the events", () => {
  it("key for key, except the row count (the rows are not the generator's) and now", () => {
    // The design's data file has no `now` (spec 0006 section 7.3); its own test checks it.
    const { rows: _a, ...want } = meta as unknown as Record<string, unknown>;
    const { rows: _b, now: _n, ...have } = served as unknown as Record<string, unknown>;
    expect(have).toEqual(want);
  });
});

describe("the window of the server is the window of the derive layer", () => {
  it.each(cases)(
    "%s/%i",
    async (hz, page) => {
      const w = (await get(`api/window/${hz}/${page}.json`)) as WindowPayload;
      const f = makeFrame(meta, hz, page);
      expect([w.hz, w.page, w.H0, w.H1, w.L]).toEqual([hz, page, f.H0, f.H1, f.L]);
    },
    PAGE_TIMEOUT_MS,
  );
});

describe("the row count of the meta", () => {
  it("is the count of the fixture rows of the 9 probe types", () => {
    expect(served.rows).toBe(rows.length);
  });
});

describe("derive on the payload equals derive on the full events", () => {
  const check = async (hz: Horizon, page: number) => {
    // The per-zone list is in the payload only; the events have none.
    const window = dropGot((await get(`api/window/${hz}/${page}.json`)) as WindowPayload);
    const fromPayload = derive({ meta: served, window }, hz, page);
    // The header span prints meta.rows. The generator's count is not the count of the rows (spec 0006 D3), so the events side takes the served count. The tooltip of section 11.9 prints the zone list, which the payload side drops above, so the events side drops it too. The design's events have no meta.now (section 7.3), so the events side takes the served one: section 11.9 reads it.
    const fromEvents = derive(
      {
        meta: {
          ...meta,
          ...(served.now === undefined ? {} : { now: served.now }),
          rows: served.rows,
        },
        window: dropGot(fullWindow(hz, page)),
      },
      hz,
      page,
    );
    expect(fromPayload).toEqual(fromEvents);
  };
  it.each(cases)("%s/%i", check, PAGE_TIMEOUT_MS);
});

describe("the per-zone list of an aws_odcr sweep", () => {
  it.each(cases)(
    "%s/%i: the list agrees with the counts of the sweep",
    async (hz, page) => {
      const w = (await get(`api/window/${hz}/${page}.json`)) as WindowPayload;
      let listed = 0;
      for (const s of w.sweeps) {
        if (s.probe !== "aws_odcr" || s.zones === 0) {
          expect(s.zoneList).toBeUndefined();
          continue;
        }
        const list = s.zoneList ?? [];
        listed += 1;
        const got = list.map((z) => z.got).filter((g): g is number => g !== null);
        expect(list.length).toBe(s.zones);
        expect(got.filter((g) => g > 0).length).toBe(s.zonesYes);
        expect(got.reduce((a, b) => a + b, 0)).toBe(s.gotZoneSum);
        expect(Math.max(0, ...got)).toBe(s.gotZoneMax);
        expect(list.map((z) => z.zone)).toEqual([...list.map((z) => z.zone)].sort());
      }
      expect(listed).toBeGreaterThan(0);
    },
    PAGE_TIMEOUT_MS,
  );
});

describe("the payload holds every record of its range (spec 0006 section 7.4)", () => {
  it.each(cases)(
    "%s/%i",
    async (hz, page) => {
      const w = dropGot((await get(`api/window/${hz}/${page}.json`)) as WindowPayload);
      const full = dropGot(fullWindow(hz, page));
      const c = served.cadenceH.launch ?? 2;
      const inL = (h: number) => h >= w.L && h < w.H1;
      expect(w.sweeps).toEqual(full.sweeps.filter((s) => inL(s.h)));
      const range = (xs: readonly (readonly (number | string | null)[])[]) =>
        xs.filter((r) => inL(r[0] as number));
      expect(w.cb.map((p) => ({ ...p, checks: [], longest: [] }))).toEqual(
        full.cb.map((p) => ({ ...p, checks: [], longest: [] })),
      );
      const sortRec = (xs: readonly (readonly (number | string | null)[])[]) =>
        JSON.stringify([...xs].sort((a, b) => (JSON.stringify(a) < JSON.stringify(b) ? -1 : 1)));
      w.cb.forEach((p, i) => {
        expect(sortRec(p.checks), `${p.sku} ${p.region} checks`).toBe(
          sortRec(range(full.cb[i]?.checks ?? [])),
        );
        expect(sortRec(p.longest), `${p.sku} ${p.region} longest`).toBe(
          sortRec(range(full.cb[i]?.longest ?? [])),
        );
      });
      const inS = (h: number) => h >= w.H0 - c && h < w.H1;
      const cut = <T extends readonly number[]>(m: Readonly<Record<string, readonly T[]>>) =>
        Object.fromEntries(
          Object.entries(m)
            .map(([k, v]) => [k, v.filter((p) => inS(p[0] as number))] as const)
            .filter(([, v]) => v.length > 0),
        );
      expect(w.spot).toEqual(cut(full.spot));
      expect(w.azspot).toEqual(cut(full.azspot));
      expect(w.price).toEqual(full.price);
    },
    PAGE_TIMEOUT_MS,
  );
});
