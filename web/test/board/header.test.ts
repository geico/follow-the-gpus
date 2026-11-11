import { describe, expect, it } from "vitest";
import { deriveControls } from "../../src/board/derive/header";
import { DEFAULT_STATE } from "../../src/board/derive/viewstate";

describe("header controls (section 11.7)", () => {
  it("has the Day button first (section 11.7)", () => {
    expect(deriveControls(DEFAULT_STATE).horizons.map((h) => h.label)).toEqual([
      "Day",
      "Week",
      "Month",
      "All",
    ]);
  });
});
