// @vitest-environment jsdom

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { makeFrame } from "../../src/board/derive/time";
import type { BoardData, Sweep } from "../../src/board/derive/types";
import { DEFAULT_STATE } from "../../src/board/derive/viewstate";
import { SubstitutesView } from "../../src/board/view/substitutes";
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

describe("SubstitutesView", () => {
  it("renders an empty labelled section on an empty window", () => {
    const { container } = render(<SubstitutesView data={stubBoardData()} state={DEFAULT_STATE} />);
    const s = container.querySelector("section[data-section=substitutes]");
    expect(s?.getAttribute("aria-label")).toBe("Substitutes");
    expect(s?.childElementCount).toBe(0);
  });

  it("draws the title, the intro, 6 labelled rows, and the footer", () => {
    render(<SubstitutesView data={data()} state={DEFAULT_STATE} />);
    expect(
      screen.getByRole("heading", { name: "When H100 ×1 said no, what said yes?" }),
    ).toBeTruthy();
    expect(screen.getByText(/^In the 24 rounds when no place handed us a H100 ×1/)).toBeTruthy();
    const list = screen.getByRole("list", { name: /Share of the no rounds/ });
    const items = within(list).getAllByRole("listitem");
    expect(items.map((i) => i.textContent)).toEqual([
      "T4100%",
      "A10G96%",
      "L483%",
      "RTX PRO 600054%",
      "A1042%",
      "L40S33%",
    ]);
    expect(screen.getByText(/^A substitute is a different machine\./)).toBeTruthy();
  });
});
