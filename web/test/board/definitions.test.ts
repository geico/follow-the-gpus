import { readFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { deriveDefinitions } from "../../src/board/derive/definitions";
import { makeFrame } from "../../src/board/derive/time";
import type { BoardData, Horizon, Meta, Sweep, WindowPayload } from "../../src/board/derive/types";
import { DEFAULT_STATE } from "../../src/board/derive/viewstate";
import { stubBoardData } from "../../src/data/stub";

// The definitions section (spec 0006 sections 10.4 and 11.22): the AWS method, the zones item, the
// parties item, and the walls text. The Azure definition (correction C7), the AWS days-ahead
// definition, the one-zone method, the no-wall sentence, and the fixed not-measured items are
// spec-derived.
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

describe("definitions, spec-derived", () => {
  const state = DEFAULT_STATE;
  const all = (m: Meta) => {
    const r = deriveDefinitions(windowOf("all", null, m), state);
    if (r.empty) throw new Error("window holds sweeps");
    return r;
  };

  it("the Azure definition is the template text of correction C7", () => {
    expect(all(meta).defs[1]).toEqual({
      lead: "Azure, right now",
      text: "a capacity reservation, granted in full or not at all, or a scale set that really starts the VMs; both are deleted at once.",
    });
  });

  it("with no per-zone start, the one-zone method and zones item are used", () => {
    const r = all({ ...meta, zoneStartH: null });
    expect(
      r.defs[0]?.text.startsWith("a capacity reservation in zone a of each region. Granted"),
    ).toBe(true);
    expect(r.notMeasured[4]).toBe(
      "Other availability zones: AWS reservations were asked in us-east-1a, us-east-2a, us-west-2a only",
    );
  });

  it("joins several zone letters with a slash", () => {
    const r = all({ ...meta, zones: ["us-east-1a", "us-east-1b"], zoneStartH: null });
    expect(r.defs[0]?.text).toContain("in zone a/b of each region");
  });

  it("with a per-zone start at or before H0, only the per-zone text is used", () => {
    const r = all({ ...meta, zoneStartH: -100 });
    expect(r.defs[0]?.text).not.toContain("Before");
    expect(r.notMeasured[4]).toMatch(/^All that the zones of a region hold/);
  });

  it("keeps the fixed not-measured items and a 0 party size with no asks", () => {
    const d = windowOf("all", null);
    const zero = {
      ...d,
      window: {
        ...d.window,
        sweeps: d.window.sweeps.map((s) => ({ ...s, asked: 0, nMax: 0 })),
      },
    };
    const r = deriveDefinitions(zero, state);
    if (r.empty) throw new Error("window holds sweeps");
    expect(r.notMeasured).toHaveLength(6);
    expect(r.notMeasured[0]).toBe("How long you keep a machine once it starts");
    expect(r.notMeasured[2]).toBe("Parties over 0 machines; blocks for more than 1 machine");
    expect(r.notMeasured[5]).toBe("Spot forecasts as availability — shown only in the dashed box");
  });

  it("takes N from the sweeps with H0 <= h < H1, not from the lookback range", () => {
    const d = windowOf("week", 0);
    const { H0, sweeps } = d.window;
    const inside = sweeps.filter((s) => s.h >= H0).map((s) => ({ ...s, asked: 2, nMax: 3 }));
    const first = sweeps[0];
    if (!first) throw new Error("window holds sweeps");
    const lookback = { ...first, h: H0 - 1, asked: 9, nMax: 9 };
    const mixed = { ...d, window: { ...d.window, sweeps: [...inside, lookback] } };
    const r = deriveDefinitions(mixed, state);
    if (r.empty) throw new Error("window holds sweeps");
    expect(r.notMeasured[2]).toBe("Parties over 3 machines; blocks for more than 1 machine");
  });

  it("prints the no-wall sentence when no place is a quota wall", () => {
    const d = windowOf("all", null);
    const clean = {
      ...d,
      window: {
        ...d.window,
        sweeps: d.window.sweeps.map((s) => (s.state === -1 ? { ...s, state: 0 } : s)),
      },
    };
    const r = deriveDefinitions(clean, state);
    if (r.empty) throw new Error("window holds sweeps");
    expect(r.walls).toBe(
      "No quota walls for this tier: every place we asked gave a capacity answer.",
    );
    expect(r.hasWalls).toBe(false);
  });
});

describe("definitions derive on an empty window", () => {
  it("returns the typed empty result", () => {
    expect(deriveDefinitions(stubBoardData(), DEFAULT_STATE)).toEqual({
      section: "definitions",
      empty: true,
    });
  });
});
