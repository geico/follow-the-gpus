// The board-wide singular rule (spec 0006 section 10.1): a counted noun is singular at 1.
import { describe, expect, it } from "vitest";
import { count, plural } from "../../src/board/derive/format";

describe("plural", () => {
  it("uses the singular noun only when the count is 1", () => {
    expect(plural(0, "round")).toBe("0 rounds");
    expect(plural(1, "round")).toBe("1 round");
    expect(plural(2, "region")).toBe("2 regions");
    expect(`1 of ${plural(1, "place")}`).toBe("1 of 1 place");
    expect(`median of ${plural(1, "check")}`).toBe("median of 1 check");
    expect(`In the ${plural(1, "round")} when`).toBe("In the 1 round when");
  });

  it("prints a given label in place of the plain count", () => {
    expect(plural(1234, "round", count(1234))).toBe("1,234 rounds");
    expect(plural(1, "round", count(1))).toBe("1 round");
  });
});
