import { readFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { deriveIntro } from "../../src/board/derive/intro";
import { makeFrame } from "../../src/board/derive/time";
import type {
  BoardData,
  CbPlace,
  Horizon,
  Meta,
  Sweep,
  ViewState,
  WindowPayload,
} from "../../src/board/derive/types";

// The intro (spec 0006 section 10.4): the tier subtitle, and the headline groups and chips.
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

describe("deriveIntro partial note", () => {
  const f = makeFrame(meta, "day", null);
  const last = f.pages.length - 1;
  const text = (page: number) => {
    const state: ViewState = { hz: "day", page, tier: "All", gpu: "T4" };
    const r = deriveIntro(windowOf("day", page), state);
    if (r.section !== "intro" || r.empty) throw new Error("no intro");
    return r.intro;
  };
  it("has no partial note on a full day", () => {
    expect(text(5)).not.toContain("a partial day");
    expect(text(5)).toMatch(/Showing 1 day, \w{3} \d+\.$/);
  });
  it("keeps the note on the first and the last day", () => {
    expect(text(0)).toContain("(a partial day: the data starts or ends inside it)");
    expect(text(last)).toContain("(a partial day: the data starts or ends inside it)");
  });
});
