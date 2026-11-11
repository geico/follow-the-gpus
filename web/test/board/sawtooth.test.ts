import { readFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { deriveSawtooth, sawTipAt } from "../../src/board/derive/sawtooth";
import { makeFrame } from "../../src/board/derive/time";
import type {
  BoardData,
  CbPlace,
  Horizon,
  Meta,
  Sweep,
  WindowPayload,
} from "../../src/board/derive/types";
import { DEFAULT_STATE } from "../../src/board/derive/viewstate";
import { stubBoardData } from "../../src/data/stub";

// Sawtooth values (spec 0006 section 10.4): the series, the note, the Y scale, and the tooltips.
const dir = resolve(__dirname, "../fixtures/board");
const read = (f: string) => JSON.parse(readFileSync(join(dir, f), "utf8"));
const events = read("events.json");
const meta: Meta = events.meta;

type FixtureSweep = Omit<Sweep, "gotZoneMax" | "gotZoneSum"> & { got: number };

function windowOf(hz: Horizon, page: number | null): BoardData {
  const f = makeFrame(meta, hz, page);
  const sweeps: Sweep[] = (events.sweeps as FixtureSweep[])
    .filter((s) => s.h >= f.L && s.h < f.H1)
    .map(({ got, ...rest }) => ({ ...rest, gotZoneMax: got, gotZoneSum: got }));
  const inRange = (r: readonly (number | string | null)[]) => {
    const h = r[0] as number;
    return h >= f.L && h < f.H1;
  };
  const cb: CbPlace[] = (events.cb as CbPlace[]).map((p) => ({
    ...p,
    checks: p.checks.filter(inRange),
    longest: p.longest.filter(inRange),
  }));
  const window: WindowPayload = {
    hz,
    page: f.page,
    H0: f.H0,
    H1: f.H1,
    L: f.L,
    sweeps,
    cb,
    spot: {},
    azspot: {},
    price: events.price,
  };
  return { meta, window };
}

describe("sawtooth derive", () => {
  it("returns a typed empty result with no Capacity Block place", () => {
    expect(deriveSawtooth(stubBoardData(), DEFAULT_STATE)).toEqual({
      section: "sawtooth",
      empty: true,
    });
  });

  it("jumps only when a lead rises more than 2 days; a break keeps the previous lead", () => {
    const base = windowOf("all", null);
    const checks = [
      [0, 1, 5, 24, 100, 14, 1],
      [2, 1, 7, 24, 100, 14, 1], // +2: no jump
      [4, 0, null, 24, null, 14, 1], // break
      [6, 1, 9.5, 24, null, 14, 1], // +2.5 over the lead before the break: jump
      [8, 1, 6, 24, null, 14, 1],
    ];
    const cb: CbPlace[] = [{ sku: "p6-b200.48xlarge", region: "r1", checks, longest: [] }];
    const r = deriveSawtooth(
      { meta, window: { ...base.window, cb } },
      { ...DEFAULT_STATE, gpu: "B200 ×8" },
    );
    if (r.empty) throw new Error("expected a section");
    const row = r.rows[0];
    expect(row?.jumps).toBe(1);
    expect(row?.found).toBe(4);
    expect(row?.n).toBe(5);
    expect(row?.path.match(/M/g)?.length).toBe(2);
    expect(row?.points[1]?.lines[0]).toContain("starts in 7 days · $100 upfront");
    expect(row?.points[3]?.lines[0]).toMatch(/starts in 9.5 days$/);
    expect(row?.points[2]?.lines[0]).toMatch(/: no block within the horizon$/);
    expect(r.scale).toBe(14);
  });

  it("keeps a place with no check in the window and says so", () => {
    const base = windowOf("all", null);
    const cb: CbPlace[] = [
      { sku: "p6-b200.48xlarge", region: "r1", checks: [[0, 1, 3, 24, null, 14, 1]], longest: [] },
      { sku: "p6-b200.48xlarge", region: "r2", checks: [], longest: [] },
    ];
    const r = deriveSawtooth({ meta, window: { ...base.window, cb } }, DEFAULT_STATE);
    if (r.empty) throw new Error("expected a section");
    expect(r.scale).toBe(7);
    expect(r.yLabels.map((y) => y.label)).toEqual(["0 d", "4 d", "7 d"]);
    const row = r.rows[1];
    expect(row?.count).toBe("0 of 0 checks found one");
    expect(row?.note).toBe("no block within the horizon");
    if (row) expect(sawTipAt(row, 500).lines).toEqual(["No checks here."]);
  });
});
