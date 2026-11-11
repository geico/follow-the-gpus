// @vitest-environment jsdom

import { readFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { cleanup, render, screen } from "@testing-library/react";
import { createElement } from "react";
import { afterEach, describe, expect, it } from "vitest";
import { deriveStickiness } from "../../src/board/derive/stickiness";
import { makeFrame } from "../../src/board/derive/time";
import type {
  BoardData,
  CbPlace,
  Horizon,
  Meta,
  Sweep,
  WindowPayload,
} from "../../src/board/derive/types";
import { DEFAULT_STATE } from "../../src/board/derive/viewstate";
import { StickinessView } from "../../src/board/view/stickiness";
import { GlobalTooltip } from "../../src/board/view/tooltip";
import { stubBoardData } from "../../src/data/stub";

// The stickiness section (spec 0006 section 10.4): the view state, the rows, the verdict, and the tooltips
// the view state, the rows, the verdict, and the tooltips of the section.
const dir = resolve(__dirname, "../fixtures/board");
const read = (f: string) => JSON.parse(readFileSync(join(dir, f), "utf8"));
const events = read("events.json");
const meta: Meta = events.meta;
type FixtureSweep = Omit<Sweep, "gotZoneMax" | "gotZoneSum"> & { got: number };

function dataOf(hz: Horizon, page: number | null): BoardData {
  const f = makeFrame(meta, hz, page);
  const sweeps: Sweep[] = (events.sweeps as FixtureSweep[])
    .filter((s) => s.h >= f.L && s.h < f.H1)
    .map(({ got, ...rest }) => ({ ...rest, gotZoneMax: got, gotZoneSum: got }));
  const inRange = (r: readonly (number | string | null)[]) => {
    const h = r[0] as number;
    return h >= f.L && h < f.H1;
  };
  const cb: CbPlace[] = (events.cb as CbPlace[]).map((p) => ({
    ...p,
    checks: p.checks.filter(inRange),
    longest: p.longest.filter(inRange),
  }));
  const window: WindowPayload = {
    hz,
    page: f.page,
    H0: f.H0,
    H1: f.H1,
    L: f.L,
    sweeps,
    cb,
    spot: {},
    azspot: {},
    price: events.price,
  };
  return { meta, window };
}

describe("stickiness derive", () => {
  it("is empty on a store with no sweeps", () => {
    expect(deriveStickiness(stubBoardData(), DEFAULT_STATE)).toEqual({
      section: "stickiness",
      empty: true,
    });
  });
});

afterEach(cleanup);

describe("stickiness view", () => {
  const state = { hz: "week" as const, page: null, tier: "All", gpu: "H100 ×1" };
  const draw = (data: BoardData) =>
    render(createElement(GlobalTooltip, null, createElement(StickinessView, { data, state })));

  it("leaves the focus ring to the stylesheet: no inline all or outline on the buttons", () => {
    draw(dataOf("week", null));
    const buttons = screen.getAllByRole("button");
    expect(buttons.length).toBeGreaterThan(0);
    for (const b of buttons) {
      const s = (b as HTMLElement).style;
      expect(s.getPropertyValue("all")).toBe("");
      expect(s.getPropertyValue("outline")).toBe("");
      expect(s.getPropertyValue("outline-style")).toBe("");
    }
  });

  it("renders only the labelled shell when the store has no sweeps", () => {
    const { container } = draw(stubBoardData());
    const s = container.querySelector("section");
    expect(s?.childElementCount).toBe(0);
    expect(s?.getAttribute("aria-label")).toBeTruthy();
  });
});
