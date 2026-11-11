import { describe, expect, it } from "vitest";
import {
  DEFAULT_STATE,
  fromHash,
  fromStored,
  parsePage,
  toHash,
  toStored,
  withHorizon,
  withTier,
} from "../../src/board/derive/viewstate";

describe("view state (spec 0006 section 11.5)", () => {
  it("writes the hash with URI-encoded values and leaves an absent page out", () => {
    expect(toHash(DEFAULT_STATE)).toBe("#hz=all&tier=All&gpu=H100%20%C3%971");
    expect(toHash({ ...DEFAULT_STATE, hz: "week", page: 1 })).toBe(
      "#hz=week&page=1&tier=All&gpu=H100%20%C3%971",
    );
  });

  it("round-trips through the hash and the stored JSON", () => {
    const s = { hz: "month", page: 2, tier: "Mid · 48 GB", gpu: "L40S" } as const;
    expect(fromHash(toHash(s))).toEqual(s);
    expect(fromStored(toStored(s))).toEqual(s);
  });

  it("accepts hz=day and round-trips it with its page (section 11.5)", () => {
    const s = { ...DEFAULT_STATE, hz: "day", page: 3 } as const;
    expect(toHash(s)).toBe("#hz=day&page=3&tier=All&gpu=H100%20%C3%971");
    expect(fromHash(toHash(s))).toEqual(s);
    expect(fromStored(toStored(s))).toEqual(s);
  });

  it("drops the page of a hash with an unsupported hz", () => {
    const base = { ...DEFAULT_STATE, hz: "week", page: 1 } as const;
    expect(fromHash("#hz=year&page=2", base)).toEqual(base);
    expect(fromStored(JSON.stringify({ hz: "year", page: 2 }))).toEqual(DEFAULT_STATE);
  });

  it("ignores bad values and bad JSON", () => {
    expect(fromHash("#hz=year&page=-1&tier=Nope&gpu=Nope")).toEqual(DEFAULT_STATE);
    expect(fromStored("{not json")).toEqual(DEFAULT_STATE);
    expect(fromStored(null)).toEqual(DEFAULT_STATE);
  });

  it("clears the page on a new horizon and snaps the focus into the tier", () => {
    expect(withHorizon({ ...DEFAULT_STATE, hz: "week", page: 3 }, "month").page).toBeNull();
    expect(withTier(DEFAULT_STATE, "Frontier node").gpu).toBe("B200 ×8");
  });

  it("a hash with a new hz and no page does not keep the stored page", () => {
    const stored = { ...DEFAULT_STATE, hz: "month", page: 1 } as const;
    expect(fromHash("#hz=week&tier=All&gpu=H100%20%C3%971", stored).page).toBeNull();
    // The same hz keeps the stored page; a page in the hash wins.
    expect(fromHash("#hz=month&tier=All", stored).page).toBe(1);
    expect(fromHash("#hz=week&page=2", stored).page).toBe(2);
    // A bad page in the hash is no page.
    expect(fromHash("#hz=week&page=-1", stored).page).toBeNull();
  });

  it("keeps a hash machine when the stored tier does not hold it", () => {
    const stored = { ...DEFAULT_STATE, tier: "Frontier node", gpu: "B200 ×8" };
    const s = fromHash("#hz=all&gpu=L40S", stored);
    expect(s.gpu).toBe("L40S");
    expect(s.tier).toBe("All");
  });

  it("has one page validator for the hash and the stored JSON", () => {
    for (const ok of ["0", "1", "42"]) expect(parsePage(ok)).toBe(Number(ok));
    for (const bad of ["", "01", "-1", "1.5", "1e2", " 1", "x", null]) {
      expect(parsePage(bad)).toBeNull();
    }
    expect(fromHash("#page=7").page).toBe(parsePage("7"));
    expect(fromHash("#page=07").page).toBe(DEFAULT_STATE.page);
    expect(fromStored(JSON.stringify({ ...DEFAULT_STATE, page: 3 })).page).toBe(3);
  });
});
