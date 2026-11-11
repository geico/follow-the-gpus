import { describe, expect, it } from "vitest";
import { deriveMatrix } from "../../src/board/derive/matrix";
import { DEFAULT_STATE } from "../../src/board/derive/viewstate";
import { stubBoardData } from "../../src/data/stub";

describe("matrix derive on an empty window", () => {
  it("returns the typed empty result", () => {
    expect(deriveMatrix(stubBoardData(), DEFAULT_STATE)).toEqual({
      section: "matrix",
      empty: true,
    });
  });
});
