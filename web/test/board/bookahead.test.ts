import { readFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { deriveBookahead } from "../../src/board/derive/bookahead";
import { makeFrame } from "../../src/board/derive/time";
import type {
  BoardData,
  CbPlace,
  Horizon,
  Meta,
  WindowPayload,
} from "../../src/board/derive/types";
import { DEFAULT_STATE } from "../../src/board/derive/viewstate";
import { stubBoardData } from "../../src/data/stub";

// The book-ahead section (spec 0006 section 10.4): the rows, the scale footer, the note, and the
// tooltip of every row.
const dir = resolve(__dirname, "../fixtures/board");
const read = (f: string) => JSON.parse(readFileSync(join(dir, f), "utf8"));
const events = read("events.json");
const meta: Meta = events.meta;

function windowOf(hz: Horizon, page: number | null, extra: CbPlace[] = []): BoardData {
  const f = makeFrame(meta, hz, page);
  const inRange = (r: readonly (number | string | null)[]) => {
    const h = r[0] as number;
    return h >= f.L && h < f.H1;
  };
  const cb: CbPlace[] = [...(events.cb as CbPlace[]), ...extra].map((p) => ({
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
    sweeps: [],
    cb,
    spot: {},
    azspot: {},
    price: events.price,
  };
  return { meta, window };
}

describe("bookahead edge cases", () => {
  it("is empty with no Capacity Block check", () => {
    expect(deriveBookahead(stubBoardData(), DEFAULT_STATE)).toEqual({
      section: "bookahead",
      empty: true,
    });
  });

  it("lists bigger 1-day blocks in the footer, sorted, and never offered", () => {
    const extra: CbPlace = {
      sku: "p5.4xlarge",
      region: "us-east-1",
      checks: [
        [200, 1, 3, 24, 100, 14, 4],
        [202, 0, null, 24, null, 14, 4],
        [204, 0, null, 24, null, 14, 8],
      ],
      longest: [],
    };
    const state = { ...DEFAULT_STATE, hz: "all" as const, page: null };
    const r = deriveBookahead(windowOf("all", null, [extra]), state);
    if (r.empty) throw new Error("checks exist");
    expect(r.foot).toMatch(
      / Bigger 1-day blocks were also asked for — H100 ×1 ×4 machines: 50% of 2 checks, starting ~3 d out · H100 ×1 ×8 machines: never offered in 1 check\.$/,
    );
  });

  it("says fee not listed and skips the lead when unknown", () => {
    const only: CbPlace = {
      sku: "g6e.xlarge",
      region: "us-east-2",
      checks: [[200, 1, null, 24, null, 14, 1]],
      longest: [[200, 1, null, 30, null, null]],
    };
    const base = windowOf("all", null);
    const data = { ...base, window: { ...base.window, cb: [only] } };
    const r = deriveBookahead(data, { ...DEFAULT_STATE, hz: "all", page: null });
    if (r.empty) throw new Error("checks exist");
    const row = r.rows[0];
    expect(row?.lab).toBe("30 h");
    expect(row?.lead).toBe("–");
    expect(row?.labTone).toBe("--color-bg");
    expect(row?.lines[0]).toBe(
      "Longest block listed for 1 machine: 30 hours · typical listing 30 hours",
    );
    expect(row?.lines[2]).toBe("Fee not listed.");
  });
});
