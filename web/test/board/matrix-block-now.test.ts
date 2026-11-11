import { readFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { deriveMatrix } from "../../src/board/derive/matrix";
import { makeFrame } from "../../src/board/derive/time";
import type { BoardData, CbPlace, Meta } from "../../src/board/derive/types";

// Spec 0006 sections 11.3 item 4 and 11.21: the BLOCK cell uses the same now rule as
// the strip, so the board never says "11% now" in one section and "100% now" in another.
const events = JSON.parse(
  readFileSync(join(resolve(__dirname, "../fixtures/board"), "events.json"), "utf8"),
);
const meta: Meta = events.meta;
const f = makeFrame(meta, "week", 1);

// A check: [h, found, leadDays, durationH, fee, horizonDays, nRequested, leadH].
const check = (round: number, leadH: number) => [
  f.H0 + round * f.cad,
  1,
  Math.round((leadH / 24) * 100) / 100,
  24,
  120,
  14,
  1,
  leadH,
];

describe("matrix BLOCK cell", () => {
  // 4 checks, all found: 1 starts 30 min out, 3 start 5 days out.
  const place: CbPlace = {
    sku: "p6-b200.48xlarge",
    region: "us-east-1",
    checks: [check(0, 0.5), check(1, 120), check(2, 120), check(3, 120)],
    longest: [],
  };
  const data: BoardData = {
    meta,
    window: {
      hz: "week",
      page: f.page,
      H0: f.H0,
      H1: f.H1,
      L: f.L,
      sweeps: [],
      cb: [place],
      spot: {},
      azspot: {},
      price: events.price,
    },
  };

  it("counts only the offers that start under 1 h after the check", () => {
    const r = deriveMatrix(data, { hz: "week", page: 1, tier: "All", gpu: "B200 ×8" });
    if (r.empty) throw new Error("empty");
    const cell = r.groups
      .flatMap((g) => g.rows)
      .find((row) => row.name === "B200 ×8")
      ?.cells.find((c) => c.kind === "block");
    expect(cell?.p).toBe(25);
    expect(cell?.big).toBe("25%");
    expect(cell?.lines[0]).toBe("1 of 4 checks had a 1-day block starting within 1 h · 25%");
  });
});
