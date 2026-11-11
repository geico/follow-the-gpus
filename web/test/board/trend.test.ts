import { readFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { makeFrame } from "../../src/board/derive/time";
import { deriveTrend } from "../../src/board/derive/trend";
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

// Trend values (spec 0006 section 10.4).
const dir = resolve(__dirname, "../fixtures/board");
const read = (f: string) => JSON.parse(readFileSync(join(dir, f), "utf8"));
const events = read("events.json");
const meta: Meta = events.meta;

function dataOf(hz: Horizon, page: number | null): BoardData {
  const f = makeFrame(meta, hz, page);
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
    sweeps: (events.sweeps as Sweep[]).filter((s) => s.h >= f.L && s.h < f.H1),
    cb,
    spot: {},
    azspot: {},
    price: events.price,
  };
  return { meta, window };
}

describe("trend derive", () => {
  it("returns the empty result for the stub data", () => {
    expect(deriveTrend(stubBoardData(), DEFAULT_STATE)).toEqual({ section: "trend", empty: true });
  });

  // A Capacity Block offer that starts 5 h out is not a yes to "now" (section 11.13).
  it("counts a block that starts later than 1 h as no yes", () => {
    const base = dataOf("all", null);
    const later = (lead: number | null): BoardData => ({
      ...base,
      window: {
        ...base.window,
        cb: base.window.cb.map((p) => ({
          ...p,
          checks: p.checks.map((c) => {
            const r = [...c];
            r[1] = 1;
            r[7] = lead;
            return r as unknown as typeof c;
          }),
        })),
      },
    });
    const state = { ...DEFAULT_STATE, tier: "Frontier node" as const, gpu: "B200 ×8" };
    const blockRow = (lead: number | null) => {
      const r = deriveTrend(later(lead), state);
      if (r.empty) throw new Error("empty trend");
      return r.rows.find((x) => x.sub === "block within 1 h");
    };
    expect(blockRow(5)?.b).toBe("0%");
    expect(blockRow(0)?.b).toBe("100%");
  });

  // A 1-slice day page draws a dot and no line, no area (section 11.13).
  it("gives a day page one dot at its rate, with no line and no area", () => {
    const r = deriveTrend(dataOf("day", null), { ...DEFAULT_STATE, hz: "day" });
    if (r.empty) throw new Error("empty trend");
    expect(r.days).toBe(1);
    const dotted = r.rows.filter((x) => x.dot !== undefined);
    expect(dotted.length).toBeGreaterThan(0);
    for (const x of r.rows) {
      expect(x.path).toBe("");
      expect(x.area).toBe("");
      expect(x.dot === undefined || (x.dot >= 0 && x.dot <= 100)).toBe(true);
    }
  });
});
