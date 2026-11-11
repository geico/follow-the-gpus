import { describe, expect, it } from "vitest";
import { deriveSubstitutes } from "../../src/board/derive/substitutes";
import { DEFAULT_STATE } from "../../src/board/derive/viewstate";
import { stubBoardData } from "../../src/data/stub";

describe("substitutes derive on an empty window", () => {
  it("returns the typed empty result", () => {
    expect(deriveSubstitutes(stubBoardData(), DEFAULT_STATE)).toEqual({
      section: "substitutes",
      empty: true,
    });
  });
});
