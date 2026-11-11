import { describe, expect, it } from "vitest";
import { countsOf, foldCells, unionOf } from "../../src/board/derive/aggregate";
import {
  cbHorizon,
  coastOf,
  gpusPerMachine,
  MACHINES,
  machinesOfTier,
} from "../../src/board/derive/catalogue";
import {
  count,
  dateLabel,
  dayLabel,
  heat,
  hrs,
  median,
  ps,
  sh,
  tone,
} from "../../src/board/derive/format";
import {
  isWeekendEt,
  LOS_ANGELES,
  makeFrame,
  NEW_YORK,
  utcOffsetHours,
} from "../../src/board/derive/time";
import { stubMeta } from "../../src/data/stub";

const META = {
  ...stubMeta(),
  t0: "2026-10-13T07:00:00.000Z",
  tEnd: "2026-11-03T06:30:00.000Z",
  nh: 505,
};

describe("formatters (section 11.4)", () => {
  it("prints counts, percents, shares, and hours", () => {
    expect(count(1234)).toBe("1,234");
    expect(count(null)).toBe("–");
    expect(ps(null)).toBe("–");
    expect(ps(0.4)).toBe("<1%");
    expect(ps(99.5)).toBe("99%");
    expect(ps(100)).toBe("100%");
    expect(ps(44.4)).toBe("44%");
    expect(sh(1, 0)).toBeNull();
    expect(sh(1, 4)).toBe(25);
    expect(hrs(26)).toBe("26 h");
    expect(hrs(48)).toBe("2 d");
    expect(hrs(60)).toBe("2.5 d");
  });

  it("takes the upper median and formats dates in UTC", () => {
    expect(median([])).toBeNull();
    expect(median([3, 1, 2, 4])).toBe(3);
    expect(dayLabel(Date.parse("2026-10-13T23:00:00Z"))).toBe("13 Tue");
    expect(dateLabel(Date.parse("2026-10-13T23:00:00Z"))).toBe("Oct 13");
  });

  it("gives the colour tokens", () => {
    expect(heat(null)).toBe("transparent");
    expect(heat(100)).toBe(
      "color-mix(in oklch, var(--color-accent-400) 100%, var(--color-neutral-900))",
    );
    expect(tone(null)).toBe("--color-neutral-500");
    expect(tone(60)).toBe("--color-accent-300");
    expect(tone(25)).toBe("--color-neutral-200");
    expect(tone(24)).toBe("--color-neutral-400");
  });
});

describe("catalogue", () => {
  it("holds 12 machines, GPU counts, coasts, and the horizon", () => {
    expect(MACHINES).toHaveLength(12);
    expect(MACHINES.map(gpusPerMachine)).toEqual([8, 8, 8, 8, 1, 1, 1, 1, 1, 1, 1, 1]);
    expect(machinesOfTier("Mid · 48 GB")).toEqual(["L40S"]);
    expect(coastOf("us-east-1")).toBe("East");
    expect(coastOf("centralus")).toBe("Central");
    expect(coastOf("westus3")).toBe("West");
    expect(coastOf("nowhere")).toBeNull();
    expect(cbHorizon([])).toBe(14);
    expect(cbHorizon([7, 14])).toBe(7);
  });
});

describe("time model", () => {
  it("reads the New York and Los Angeles offsets across the daylight-time change", () => {
    expect(utcOffsetHours(Date.parse("2026-10-31T12:00:00Z"), NEW_YORK)).toBe(-4);
    expect(utcOffsetHours(Date.parse("2026-11-02T12:00:00Z"), NEW_YORK)).toBe(-5);
    expect(utcOffsetHours(Date.parse("2026-10-31T12:00:00Z"), LOS_ANGELES)).toBe(-7);
    expect(utcOffsetHours(Date.parse("2026-11-02T12:00:00Z"), LOS_ANGELES)).toBe(-8);
  });

  it("decides the weekend in New York", () => {
    expect(isWeekendEt(Date.parse("2026-10-24T03:59:00Z"))).toBe(false); // Fri 23:59 EDT
    expect(isWeekendEt(Date.parse("2026-10-24T04:00:00Z"))).toBe(true);
    expect(isWeekendEt(Date.parse("2026-10-26T03:59:00Z"))).toBe(true); // Sun 23:59 EDT
    expect(isWeekendEt(Date.parse("2026-10-26T04:00:00Z"))).toBe(false);
  });

  it("makes the week page of 169 hours across the change", () => {
    const f = makeFrame(META, "week", null);
    expect(f.pageLabel).toBe("Week of Mon Oct 26 (ET)");
    expect(f.H1 - f.H0).toBe(169);
    expect(f.pages).toHaveLength(4);
    expect(f.noNext).toBe(false);
    expect(makeFrame(META, "week", 0).noPrev).toBe(true);
  });

  it("lists the UTC day pages from the day of T0 (section 11.2 items 2, 3, 5, 6)", () => {
    const f = makeFrame(META, "day", null);
    expect(f.pages).toHaveLength(22);
    expect(f.page).toBe(21);
    expect(f.pageLabel).toBe("Tue Nov 3 (UTC)");
    expect(f.grain).toBe(2);
    expect(f.grainName).toBe("round");
    expect({ H0: f.H0, H1: f.H1 }).toEqual({ H0: 497, H1: 505 });
    const first = makeFrame(META, "day", 0);
    expect(first.pageLabel).toBe("Tue Oct 13 (UTC)");
    expect({ H0: first.H0, H1: first.H1 }).toEqual({ H0: -7, H1: 17 });
    expect(first.noPrev).toBe(true);
    expect(f.noNext).toBe(true);
  });

  it("keeps the last day page by default even when it has little coverage", () => {
    const meta = { ...META, tEnd: "2026-11-03T00:10:00.000Z", nh: 497 };
    expect(makeFrame(meta, "day", null).page).toBe(21);
    // a week page with the same low coverage falls back one page
    expect(makeFrame(meta, "week", null).page).toBe(2);
  });

  it("takes the ceiling for H1 (section 11.2 item 4)", () => {
    const meta = {
      ...META,
      t0: "2026-10-13T00:00:00.000Z",
      tEnd: "2026-10-14T06:20:00.000Z",
      nh: 100,
    };
    const f = makeFrame(meta, "all", null);
    expect(f.H1).toBe(31);
  });

  it("keeps H1 above H0 on a window of 1 hour", () => {
    const meta = { ...META, tEnd: "2026-10-13T07:30:00.000Z", nh: 1 };
    const f = makeFrame(meta, "all", null);
    expect(f.H1).toBeGreaterThan(f.H0);
  });
});

describe("aggregation helpers", () => {
  it("builds the union: -1 only when every cell is -1", () => {
    expect(
      unionOf(
        [
          [null, -1, -1, 0],
          [null, -1, 2, null],
        ],
        4,
      ),
    ).toEqual([null, -1, 2, 0]);
  });

  it("counts yes, tried and the longest wait", () => {
    expect(countsOf([2, 0, 2], 2)).toEqual({
      tried: 3,
      yes: 2,
      waitH: 4,
      waitKind: "between two yeses",
    });
    expect(countsOf([2, 2, 2], 2).waitH).toBe(0);
    expect(countsOf([0, 0, 0, 2], 2)).toMatchObject({ waitH: 6, waitKind: "before the first yes" });
    expect(countsOf([2, 0, 0, 0], 2)).toMatchObject({ waitH: 6, waitKind: "since the last yes" });
    expect(countsOf([-1, null], 2)).toMatchObject({ tried: 0, yes: 0 });
  });

  it("folds rounds into day cells", () => {
    const f = makeFrame({ ...META, t0: "2026-10-13T00:00:00.000Z", nh: 48 }, "month", null);
    const series = new Array(f.NR).fill(null);
    series[0] = 2;
    series[1] = 0;
    series[2] = -1;
    expect(foldCells(series, f)[0]).toEqual({ n: 2, y: 1, share: 50, quota: false });
  });
});
