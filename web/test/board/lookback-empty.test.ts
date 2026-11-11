import { readFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { deriveBacktest } from "../../src/board/derive/backtest";
import { deriveDefinitions } from "../../src/board/derive/definitions";
import { deriveForecast } from "../../src/board/derive/forecast";
import { deriveMatrix } from "../../src/board/derive/matrix";
import { deriveSowhat } from "../../src/board/derive/sowhat";
import { deriveStickiness } from "../../src/board/derive/stickiness";
import { deriveSubstitutes } from "../../src/board/derive/substitutes";
import { makeFrame } from "../../src/board/derive/time";
import type { BoardData, CbPlace, Meta, Sweep } from "../../src/board/derive/types";
import { DEFAULT_STATE } from "../../src/board/derive/viewstate";

// Spec 0006 section 11.2 item 8: a record in the lookback range L..H0 does not count, so a window whose
// only data is there is empty in every section that tests for an empty window.
const events = JSON.parse(
  readFileSync(join(resolve(__dirname, "../fixtures/board"), "events.json"), "utf8"),
);
const meta: Meta = events.meta;
type FixtureSweep = Omit<Sweep, "gotZoneMax" | "gotZoneSum"> & { got: number };

function lookbackOnly(): { data: BoardData; hz: "week" } {
  const f = makeFrame(meta, "week", null);
  const lb = (events.sweeps as FixtureSweep[]).filter((s) => s.h >= f.L && s.h < f.H0);
  if (lb.length === 0) throw new Error("fixture has no lookback sweep");
  const sweeps: Sweep[] = lb.map(({ got, ...r }) => ({ ...r, gotZoneMax: got, gotZoneSum: got }));
  const before = (r: readonly (number | string | null)[]) => (r[0] as number) < f.H0;
  const cb: CbPlace[] = (events.cb as CbPlace[]).map((p) => ({
    ...p,
    checks: p.checks.filter(before),
    longest: p.longest.filter(before),
  }));
  const window = {
    hz: "week" as const,
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
  return { data: { meta, window }, hz: "week" };
}

describe("an empty window with lookback-only data", () => {
  const { data, hz } = lookbackOnly();
  const state = { ...DEFAULT_STATE, hz, page: null };
  const cases: [string, () => { empty: boolean }][] = [
    ["matrix", () => deriveMatrix(data, state)],
    ["sowhat", () => deriveSowhat(data, state)],
    ["stickiness", () => deriveStickiness(data, state)],
    ["substitutes", () => deriveSubstitutes(data, state)],
    ["definitions", () => deriveDefinitions(data, state)],
    ["forecast", () => deriveForecast(data, state)],
    ["backtest", () => deriveBacktest(data, state)],
  ];
  for (const [name, run] of cases) {
    it(`${name} is empty`, () => {
      expect(run().empty).toBe(true);
    });
  }
});
