import { readFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { deriveHowmany } from "../../src/board/derive/howmany";
import { makeFrame } from "../../src/board/derive/time";
import type { BoardData, Horizon, Meta, Sweep, WindowPayload } from "../../src/board/derive/types";
import { DEFAULT_STATE } from "../../src/board/derive/viewstate";
import { stubBoardData } from "../../src/data/stub";

// The how-many section (spec 0006 section 10.4): the ladders, their sub lines, the note, and the
// tooltip of every rung. The fixture holds `got`; it maps to `gotZoneMax` and `gotZoneSum`.
const dir = resolve(__dirname, "../fixtures/board");
const read = (f: string) => JSON.parse(readFileSync(join(dir, f), "utf8"));
const events = read("events.json");
const meta: Meta = events.meta;

type FixtureSweep = Omit<Sweep, "gotZoneMax" | "gotZoneSum"> & { got: number };

function windowOf(hz: Horizon, page: number | null, m: Meta = meta): BoardData {
  const f = makeFrame(m, hz, page);
  const sweeps: Sweep[] = (events.sweeps as FixtureSweep[])
    .filter((s) => s.h >= f.L && s.h < f.H1)
    .map(({ got, ...rest }) => ({ ...rest, gotZoneMax: got, gotZoneSum: got }));
  const window: WindowPayload = {
    hz,
    page: f.page,
    H0: f.H0,
    H1: f.H1,
    L: f.L,
    sweeps,
    cb: [],
    spot: {},
    azspot: {},
    price: events.price,
  };
  return { meta: m, window };
}

describe("howmany edge cases", () => {
  it("is empty with no sweep in the window", () => {
    expect(deriveHowmany(stubBoardData(), DEFAULT_STATE)).toEqual({
      section: "howmany",
      empty: true,
    });
  });

  it("says that doubling began after the window when no sweep doubled", () => {
    const data = windowOf("week", null, { ...meta, upStartH: meta.nh + 5 });
    const flat = {
      ...data,
      window: {
        ...data.window,
        sweeps: data.window.sweeps.map((s) => ({ ...s, nMax: s.asked })),
      },
    };
    const r = deriveHowmany(flat, { ...DEFAULT_STATE, hz: "week", page: null });
    if (r.empty) throw new Error("sweeps exist");
    expect(r.note).toMatch(
      / Doubling only began Nov \d+, after this window: every round here asked the base size\.$/,
    );
  });

  it("draws a bar of at least 3% for a count above 0", () => {
    const r = deriveHowmany(windowOf("week", null), { ...DEFAULT_STATE, hz: "week", page: null });
    if (r.empty) throw new Error("sweeps exist");
    const a100 = r.rows.find((x) => x.name === "A100 ×1");
    expect(a100?.rungs[4]?.h).toBe(3);
  });
});
