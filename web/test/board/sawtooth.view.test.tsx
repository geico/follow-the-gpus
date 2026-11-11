// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import type { BoardData, CbPlace } from "../../src/board/derive/types";
import { DEFAULT_STATE } from "../../src/board/derive/viewstate";
import { SawtoothView } from "../../src/board/view/sawtooth";
import { GlobalTooltip } from "../../src/board/view/tooltip";
import { stubBoardData } from "../../src/data/stub";

afterEach(cleanup);

function withBlocks(): BoardData {
  const s = stubBoardData();
  const checks = [
    [0, 1, 5, 24, 100, 14, 1],
    [2, 1, 3, 24, 90, 14, 1],
  ];
  const cb: CbPlace[] = [{ sku: "p6-b200.48xlarge", region: "us-east-1", checks, longest: [] }];
  return { ...s, window: { ...s.window, cb } };
}

describe("sawtooth view", () => {
  it("renders only the labelled frame with no Capacity Block place", () => {
    const { container } = render(<SawtoothView data={stubBoardData()} state={DEFAULT_STATE} />);
    expect(container.querySelector("section")?.childElementCount).toBe(0);
  });

  it("draws a labelled chart per region with the note and a tooltip on hover and keys", () => {
    render(
      <GlobalTooltip>
        <SawtoothView data={withBlocks()} state={{ ...DEFAULT_STATE, gpu: "B200 ×8" }} />
      </GlobalTooltip>,
    );
    expect(screen.getByRole("heading", { name: /B200 ×8, check by check/ })).toBeTruthy();
    expect(screen.getByText("the slot crept closer all window")).toBeTruthy();
    const chart = screen.getByRole("img", {
      name: "us-east-1: 2 of 2 checks found one; the slot crept closer all window",
    });
    expect(chart.getAttribute("tabindex")).toBe("0");
    fireEvent.keyDown(chart, { key: "ArrowRight" });
    expect(screen.getByText("B200 ×8 · us-east-1")).toBeTruthy();
    expect(screen.getByText(/next free 1-day block starts in 5 days · \$100 upfront/)).toBeTruthy();
    fireEvent.keyDown(chart, { key: "ArrowRight" });
    expect(screen.getByText(/starts in 3 days · \$90 upfront/)).toBeTruthy();
  });
});
