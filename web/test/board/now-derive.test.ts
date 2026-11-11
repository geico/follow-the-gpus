import { readFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { buildMetaNow } from "../../scripts/board-now.mjs";
import { deriveNow, type NowResult } from "../../src/board/derive/now";
import { makeFrame } from "../../src/board/derive/time";
import type { BoardData, CbPlace, Meta, Sweep, WindowHorizon } from "../../src/board/derive/types";

const dir = resolve(__dirname, "../fixtures/board");
const read = (f: string) => JSON.parse(readFileSync(join(dir, f), "utf8"));
const events = read("events.json");
const meta: Meta = { ...events.meta, now: buildMetaNow(events) };
const views: [WindowHorizon, number][] = [
  ["all", 0],
  ["month", 0],
  ["month", 1],
  ["week", 0],
  ["week", 3],
  ["day", 0],
  ["day", 7],
];
const state = { hz: "all" as const, page: null, tier: "All", gpu: "T4" };

function windowOf(hz: WindowHorizon, page: number): BoardData {
  const f = makeFrame(meta, hz, page);
  const inRange = (r: readonly (number | string | null)[]) =>
    (r[0] as number) >= f.L && (r[0] as number) < f.H1;
  return {
    meta,
    window: {
      hz,
      page: f.page,
      H0: f.H0,
      H1: f.H1,
      L: f.L,
      sweeps: (events.sweeps as Sweep[]).filter((s) => s.h >= f.L && s.h < f.H1),
      cb: (events.cb as CbPlace[]).map((p) => ({
        ...p,
        checks: p.checks.filter(inRange),
        longest: p.longest.filter(inRange),
      })),
      spot: {},
      azspot: {},
      price: events.price,
    },
  };
}

function full(r: NowResult) {
  if (r.empty) throw new Error("empty");
  return r;
}

describe("the place rate names its base", () => {
  it("says the rate is of the rounds the place was asked", () => {
    const lines = views
      .flatMap(([hz, page]) => {
        const out = full(deriveNow(windowOf(hz, page), state));
        return out.groups.flatMap((gr) => gr.rows.map((r) => r.planSub));
      })
      .filter((x) => x.startsWith("It said yes in"));
    expect(lines.length).toBeGreaterThan(0);
    for (const x of lines)
      expect(x).toMatch(/^It said yes in \d+% of the rounds it was asked over /);
  });
});

// Synthetic cases: no fixture view holds "Not now", rule 3, 7a "Not available", 7b, or 7c.
describe("deriveNow answer rules on synthetic meta.now", () => {
  const base = (meta.now?.sweeps[0] ?? events.sweeps[0]) as Sweep;
  const h = Math.max(...(meta.now?.sweeps ?? []).map((s) => s.h));
  const sw = (
    sku: string,
    cloud: string,
    region: string,
    state: number,
    extra: Record<string, unknown> = {},
  ) => ({
    ...base,
    h,
    sku,
    cloud,
    region,
    state,
    asked: 2,
    gotZoneMax: state > 0 ? state : 0,
    reason: null,
    ...extra,
  });
  const pl = (sku: string, cloud: string, region: string, probe = "aws_odcr") => ({
    sku,
    cloud,
    region,
    probe,
    n: 10,
    na: 0,
    yes: 5,
    nu: 0,
    no: 0,
    lastYesH: h,
  });
  const cb = (leadH: number | null) => ({
    sku: "p6-b200.48xlarge",
    region: "us-east-1",
    h,
    leadH,
    durH: leadH === null ? null : 24,
    start:
      leadH === null
        ? null
        : new Date(Date.parse(String(meta.t0)) + (h + leadH) * 3600e3).toISOString(),
    longestH: leadH === null ? null : 24,
  });
  function rowOf(name: string, now: { sweeps: unknown[]; places: unknown[]; cb: unknown[] }) {
    const d = windowOf("all", 0);
    const out = full(deriveNow({ ...d, meta: { ...meta, now: now as never } }, state));
    const r = out.groups.flatMap((x) => x.rows).find((x) => x.name === name);
    if (r === undefined) throw new Error(`no row ${name}`);
    return r;
  }
  // The latest round is set by any machine's sweep: a block-only machine has none of its own.
  const clock = [sw("p5en.48xlarge", "aws", "us-east-1", -2)];
  const t4 = (region: string, st: number) => sw("g4dn.xlarge", "aws", region, st);
  const t4p = (region: string) => pl("g4dn.xlarge", "aws", region);

  it("rule 1: a place with a now state > 0 is Yes, singular for 1 of 1 place", () => {
    const r = rowOf("T4", { sweeps: [t4("us-east-1", 2)], places: [t4p("us-east-1")], cb: [] });
    expect([r.kind, r.word, r.qual]).toEqual(["yes", "Yes", "1 of 1 place said yes"]);
    expect(r.plan).toBe("Only AWS us-east-1 — go there.");
  });
  it("a place with no asked round in the window says its rate is over all the data", () => {
    const r = rowOf("T4", { sweeps: [t4("zz-test-1", 2)], places: [t4p("zz-test-1")], cb: [] });
    expect(r.planSub).toBe("It said yes in 50% of the rounds it was asked over all the data.");
  });
  it("rule 2: a block-only machine with an offer under 1 h is Yes", () => {
    const r = rowOf("B200 ×8", { sweeps: clock, places: [], cb: [cb(0.5)] });
    expect([r.kind, r.qual]).toEqual(["yes", "a block starts within 1 h"]);
  });
  it("rule 3: an offer at 5 h is Not now", () => {
    const r = rowOf("B200 ×8", { sweeps: clock, places: [], cb: [cb(5)] });
    expect([r.kind, r.word, r.qual]).toEqual(["soon", "Not now", "block starts in 5 h"]);
  });
  it("rule 4: an offer at 30 h is Book ahead", () => {
    const r = rowOf("B200 ×8", { sweeps: clock, places: [], cb: [cb(30)] });
    expect([r.kind, r.word]).toEqual(["book", "Book ahead"]);
  });
  it("rule 5: every place at -1 is Our quota", () => {
    const r = rowOf("T4", {
      sweeps: [t4("us-east-1", -1), t4("us-east-2", -1)],
      places: [t4p("us-east-1"), t4p("us-east-2")],
      cb: [],
    });
    expect([r.kind, r.qual]).toEqual(["quota", "all 2 places at our limit"]);
  });
  it("rule 6: -1 mixed with no capacity is No", () => {
    const r = rowOf("T4", {
      sweeps: [t4("us-east-1", -1), t4("us-east-2", 0)],
      places: [t4p("us-east-1"), t4p("us-east-2")],
      cb: [],
    });
    expect([r.kind, r.word, r.qual]).toEqual(["no", "No", "none of 1 place"]);
  });
  it("rule 7a: all -3 is Not available, all -4 is Not offered", () => {
    const places = [t4p("us-east-1"), t4p("us-east-2")];
    const a = rowOf("T4", { sweeps: [t4("us-east-1", -3), t4("us-east-2", -3)], places, cb: [] });
    expect([a.kind, a.word, a.qual, a.plan]).toEqual([
      "none",
      "Not available",
      "2 places said so in the latest round",
      "Not offered or not available in the latest round.",
    ]);
    const o = rowOf("T4", { sweeps: [t4("us-east-1", -4), t4("us-east-2", -4)], places, cb: [] });
    expect(o.word).toBe("Not offered");
    const m = rowOf("T4", { sweeps: [t4("us-east-1", -3), t4("us-east-2", -4)], places, cb: [] });
    expect(m.word).toBe("Not available");
  });
  it("a block check 1 h before the latest sweep round still gives the offer", () => {
    // On real data the Capacity Block checks run in the hour before the provisioning round.
    const at = (leadH: number | null, hh: number) => ({ ...cb(leadH), h: hh });
    const soon = rowOf("B200 ×8", { sweeps: clock, places: [], cb: [at(5, h - 1)] });
    expect([soon.kind, soon.word, soon.qual]).toEqual(["soon", "Not now", "block starts in 4 h"]);
    const book = rowOf("B200 ×8", { sweeps: clock, places: [], cb: [at(30, h - 1)] });
    expect([book.kind, book.word]).toEqual(["book", "Book ahead"]);
    // A check 4 h or more before the round is stale: it is not an offer, and it is not "no offer".
    const stale = rowOf("B200 ×8", { sweeps: clock, places: [], cb: [at(5, h - 4)] });
    expect(stale.word).toBe("No block now");
    expect(stale.blocks[0]?.offer).toMatch(/^The latest check is too old to count/);
  });
  it("a stale check that found an offer gets a neutral icon and word, not none within", () => {
    const stale = rowOf("B200 ×8", {
      sweeps: clock,
      places: [],
      cb: [{ ...cb(5), h: h - 4 }],
    });
    expect([stale.blocks[0]?.icon, stale.blocks[0]?.word]).toEqual([
      "ph ph-minus-circle",
      "too old to count",
    ]);
    // A check that found nothing keeps the x-circle and "none within".
    const none = rowOf("B200 ×8", { sweeps: clock, places: [], cb: [cb(null)] });
    expect([none.blocks[0]?.icon, none.blocks[0]?.word]).toEqual([
      "ph ph-x-circle",
      "none within 14 days",
    ]);
  });
  it("a fresh check with a lead but no length or start is not described as too old", () => {
    for (const gap of [{ durH: null }, { start: null }]) {
      const r = rowOf("B200 ×8", {
        sweeps: clock,
        places: [],
        cb: [{ ...cb(5), h: h - 1, ...gap }],
      });
      expect(r.blocks[0]?.offer).toMatch(
        /^The latest check found an offer without a start or a length/,
      );
      expect(r.blocks[0]?.word).toBe("start unknown");
    }
  });
  it("the lead shown is relative to now: the lead at the check less its age, 0 at least", () => {
    const at = (leadH: number, hh: number) => ({ ...cb(leadH), h: hh });
    // A check 3 h old with a lead of 3.5 h starts in 0.5 h from now: a yes, within 1 h.
    const yes = rowOf("B200 ×8", { sweeps: clock, places: [], cb: [at(3.5, h - 3)] });
    expect([yes.kind, yes.qual, yes.blocks[0]?.word]).toEqual([
      "yes",
      "a block starts within 1 h",
      "starts within 1 h",
    ]);
    // A lead already passed is floored at 0, not negative.
    const past = rowOf("B200 ×8", { sweeps: clock, places: [], cb: [at(1, h - 3)] });
    expect([past.kind, past.leadH]).toEqual(["yes", 0]);
    // A lead of 6 h at a check 3 h old is 3 h from now.
    const soon = rowOf("B200 ×8", { sweeps: clock, places: [], cb: [at(6, h - 3)] });
    expect([soon.qual, soon.blocks[0]?.word]).toEqual(["block starts in 3 h", "in 3 h"]);
  });
  it("a check newer than the latest round does not lengthen the lead", () => {
    // A check run after the start of the latest round has h above nowH. Its age is 0 at least.
    const r = rowOf("B200 ×8", { sweeps: clock, places: [], cb: [{ ...cb(0.5), h: h + 1 }] });
    expect([r.kind, r.leadH, r.qual]).toEqual(["yes", 0.5, "a block starts within 1 h"]);
    expect(r.blocks[0]?.word).toBe("starts within 1 h");
  });
  it("rule 7b: a block-only machine with no offer is No block now", () => {
    const r = rowOf("B200 ×8", { sweeps: clock, places: [], cb: [cb(null)] });
    expect([r.kind, r.word, r.qual, r.plan]).toEqual([
      "none",
      "No block now",
      "no Capacity Block offered in the latest round",
      "We ask for it only as a Capacity Block.",
    ]);
  });
  it("rule 7c: -3 mixed with a place not asked in the latest round is Not asked", () => {
    const r = rowOf("T4", {
      sweeps: [t4("us-east-1", -3)],
      places: [t4p("us-east-1"), t4p("us-east-2")],
      cb: [],
    });
    expect([r.kind, r.word, r.qual]).toEqual([
      "none",
      "Not asked",
      "not asked in the latest round",
    ]);
    const e = rowOf("T4", { sweeps: [], places: [], cb: [] });
    expect([e.word, e.qual]).toEqual(["Not asked", "no round yet"]);
  });
  it("tie order: equal rates go by cloud, then by position in the latest round, not by region", () => {
    const places = [t4p("zz-1"), t4p("zz-2"), t4p("zz-3")];
    const r = rowOf("T4", {
      sweeps: [t4("zz-3", 2), t4("zz-1", 2), t4("zz-2", 0)],
      places,
      cb: [],
    });
    expect(r.plan).toBe("Try AWS zz-3 first.");
    expect(r.chips.map((c) => c.label)).toEqual(["AWS zz-3", "AWS zz-1"]);
    expect(r.places.map((p) => p.region)).toEqual(["zz-3", "zz-1", "zz-2"]);
    const n = rowOf("T4", {
      sweeps: [t4("zz-2", 0)],
      places: [t4p("zz-1"), t4p("zz-2")],
      cb: [],
    });
    expect(n.places.map((p) => p.region)).toEqual(["zz-2", "zz-1"]);
  });
  it("a full answer ranks over a partial one at the same rate", () => {
    const r = rowOf("T4", {
      sweeps: [t4("zz-1", 1), t4("zz-2", 2)],
      places: [t4p("zz-1"), t4p("zz-2")],
      cb: [],
    });
    expect(r.plan).toBe("Try AWS zz-2 first.");
  });
  it("rule 7b: a cb entry with a null h is kept and prints no 'checked'", () => {
    const r = rowOf("B200 ×8", { sweeps: clock, places: [], cb: [{ ...cb(null), h: null }] });
    expect([r.kind, r.word]).toEqual(["none", "No block now"]);
    expect(r.blocks.length).toBe(1);
    expect(r.blocks[0]?.offer).toBe(
      `No Capacity Block offered within ${r.blocks[0]?.word.match(/\d+/)?.[0]} days at the latest check.`,
    );
  });
  it("rule 5: any restricted -1 sweep of a place keeps the size access request text", () => {
    const r = rowOf("T4", {
      sweeps: [
        { ...t4("us-east-1", -1), reason: "restricted" },
        { ...t4("us-east-1", -1), reason: "quota" },
      ],
      places: [t4p("us-east-1")],
      cb: [],
    });
    expect(r.plan).toBe("Ask for a quota increase or a size access request first.");
  });
  it("absent meta.now gives the empty section, not a throw", () => {
    const d = windowOf("all", 0);
    const { now: _n, ...old } = d.meta;
    expect(deriveNow({ ...d, meta: old as Meta }, state)).toEqual({ section: "now", empty: true });
  });
});

describe("the cadence text", () => {
  const cadenceAt = (launch: number) => {
    const d = windowOf("all", 0);
    const r = deriveNow(
      { ...d, meta: { ...d.meta, cadenceH: { ...d.meta.cadenceH, launch } } },
      state,
    );
    return full(r).cadence;
  };
  it("says 'hour' at 1 h and 'N hours' at 2 h", () => {
    expect(cadenceAt(1)).toBe("hour");
    expect(cadenceAt(2)).toBe("2 hours");
  });
});
