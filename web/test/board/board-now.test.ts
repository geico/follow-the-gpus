import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { buildMetaNow } from "../../scripts/board-now.mjs";

// `meta.now` built from the fixture by spec 0006 section 7.3. The tests call
// the same function.
const events = JSON.parse(
  readFileSync(resolve(__dirname, "../fixtures/board/events.json"), "utf8"),
);
const now = buildMetaNow(events);
const T0 = Date.parse(events.meta.t0);

describe("meta.now of the fixture", () => {
  it("keeps, for each sku, only the sweeps of its largest round (no state -2)", () => {
    expect(now.sweeps.length).toBeGreaterThan(0);
    expect(now.sweeps.every((s) => s.state !== -2)).toBe(true);
    for (const sku of new Set(now.sweeps.map((s) => s.sku))) {
      const rounds = new Set(
        now.sweeps.filter((s) => s.sku === sku).map((s) => Math.floor(s.h / 2)),
      );
      expect(rounds.size, sku).toBe(1);
    }
    // The 2 latest-round cases of the issue: a machine whose latest round is all -1 "restricted", and -3 and -4.
    const a100 = now.sweeps.filter((s) => s.sku === "Standard_NC24ads_A100_v4");
    expect(a100).toHaveLength(3);
    expect(a100.every((s) => s.state === -1 && s.reason === "restricted")).toBe(true);
    const states = new Set(now.sweeps.map((s) => s.state));
    expect(states.has(-3) && states.has(-4)).toBe(true);
  });

  it("counts the places over the whole data, in the order of sku, cloud, region", () => {
    const keys = now.places.map((p) => `${p.sku}|${p.cloud}|${p.region}`);
    expect(keys).toEqual([...keys].sort());
    for (const p of now.places) {
      const mine = events.sweeps.filter(
        (s: { sku: string; cloud: string; region: string; state: number }) =>
          s.sku === p.sku && s.cloud === p.cloud && s.region === p.region && s.state !== -2,
      );
      expect(p.n).toBe(mine.filter((s: { state: number }) => s.state >= -1).length);
      expect(p.na).toBe(mine.filter((s: { state: number }) => s.state === -1).length);
      expect(p.yes).toBe(mine.filter((s: { got: number }) => s.got > 0).length);
      expect(p.nu).toBe(mine.filter((s: { state: number }) => s.state === -3).length);
      expect(p.no).toBe(mine.filter((s: { state: number }) => s.state === -4).length);
    }
  });

  it("gives the latest 1-day check of each Capacity Block place: under 1 h, exactly 1 h, 1 to 24 h, over 24 h, and none", () => {
    const lead = (sku: string, region: string) =>
      now.cb.find((c) => c.sku === sku && c.region === region);
    expect(now.cb).toHaveLength(6);
    expect(lead("p6-b200.48xlarge", "us-east-1")?.leadH).toBe(0.5);
    expect(lead("p5.4xlarge", "us-east-1")?.leadH).toBe(1);
    expect(lead("p5.4xlarge", "us-west-2")?.leadH).toBe(12);
    expect(lead("p5.48xlarge", "us-east-1")?.leadH).toBe(26);
    for (const [sku, region] of [
      ["p6-b200.48xlarge", "us-west-2"],
      ["p5.48xlarge", "us-east-2"],
    ] as const) {
      expect(lead(sku, region)).toMatchObject({ h: 501, leadH: null, durH: null, start: null });
    }
    const east = lead("p6-b200.48xlarge", "us-east-1");
    expect(east?.durH).toBe(24);
    expect(east?.start).toBe(new Date(T0 + 501.5 * 3600e3).toISOString());
    // The latest check is in the hour before the latest sweep round (h 502 and 503, round 251), as on
    // real data: round 250.
    expect(now.cb.every((c) => c.h === null || Math.floor(c.h / 2) === 250)).toBe(true);
    expect(now.cb.some((c) => typeof c.longestH === "number")).toBe(true);
  });
});
