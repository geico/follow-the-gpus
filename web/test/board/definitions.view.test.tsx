// @vitest-environment jsdom

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { makeFrame } from "../../src/board/derive/time";
import type { BoardData, Sweep } from "../../src/board/derive/types";
import { DEFAULT_STATE } from "../../src/board/derive/viewstate";
import { DefinitionsView } from "../../src/board/view/definitions";
import { stubBoardData } from "../../src/data/stub";

afterEach(cleanup);

const events = JSON.parse(
  readFileSync(resolve(__dirname, "../fixtures/board/events.json"), "utf8"),
);

function data(): BoardData {
  const f = makeFrame(events.meta, "all", null);
  const sweeps: Sweep[] = events.sweeps
    .filter((s: { h: number }) => s.h >= f.L && s.h < f.H1)
    .map(({ got, ...r }: { got: number }) => ({ ...r, gotZoneMax: got, gotZoneSum: got }));
  return {
    meta: events.meta,
    window: {
      hz: "all",
      page: f.page,
      H0: f.H0,
      H1: f.H1,
      L: f.L,
      sweeps,
      cb: [],
      spot: {},
      azspot: {},
      price: events.price,
    },
  };
}

describe("DefinitionsView", () => {
  it("renders an empty labelled section on an empty window", () => {
    const { container } = render(<DefinitionsView data={stubBoardData()} state={DEFAULT_STATE} />);
    const s = container.querySelector("section[data-section=definitions]");
    expect(s?.getAttribute("aria-label")).toBe("Definitions, quota walls, not measured");
    expect(s?.childElementCount).toBe(0);
  });

  it("draws 3 definitions, the walls, and 6 not-measured items", () => {
    render(<DefinitionsView data={data()} state={DEFAULT_STATE} />);
    const defs = within(screen.getByRole("list", { name: "Definitions of a yes" })).getAllByRole(
      "listitem",
    );
    expect(defs).toHaveLength(3);
    // The full text of correction C7, from spec 0006 section 11.22.
    expect(defs[1]?.textContent).toBe(
      "Azure, right now — a capacity reservation, granted in full or not at all, or a scale set that really starts the VMs; both are deleted at once.",
    );
    expect(screen.getByText("InsufficientInstanceCapacity").tagName).toBe("CODE");
    expect(screen.getByText(/^Never reached the capacity question/)).toBeTruthy();
    expect(
      screen.getByRole("heading", { name: "For capacity planning: the quota walls" }),
    ).toBeTruthy();
    const nm = within(screen.getByRole("list", { name: "Not measured" })).getAllByRole("listitem");
    expect(nm).toHaveLength(6);
  });
});
