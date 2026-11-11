// @vitest-environment jsdom
import { readFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { createElement } from "react";
import { afterEach, describe, expect, it } from "vitest";
import { derivePrice } from "../../src/board/derive/price";
import { makeFrame } from "../../src/board/derive/time";
import type {
  BoardData,
  CbPlace,
  Horizon,
  Meta,
  Sweep,
  ViewState,
  WindowPayload,
} from "../../src/board/derive/types";
import { DEFAULT_STATE } from "../../src/board/derive/viewstate";
import { PriceView } from "../../src/board/view/price";
import { GlobalTooltip } from "../../src/board/view/tooltip";
import { stubBoardData } from "../../src/data/stub";

// The price rows (spec 0006 section 10.4): the rows, the verdict, the first and last price date, and the tooltips.
const dir = resolve(__dirname, "../fixtures/board");
const read = (f: string) => JSON.parse(readFileSync(join(dir, f), "utf8"));
const events = read("events.json");
const meta: Meta = events.meta;

type FixtureSweep = Omit<Sweep, "gotZoneMax" | "gotZoneSum"> & { got: number };

function windowOf(hz: Horizon, page: number | null): BoardData {
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

afterEach(cleanup);

describe("price view", () => {
  const state: ViewState = { ...DEFAULT_STATE, hz: "week" };
  it("draws one row per series, the verdict, and the tooltip on hover and focus", async () => {
    const data = windowOf("week", null);
    const r = derivePrice(data, state);
    render(createElement(GlobalTooltip, null, createElement(PriceView, { data, state })));
    expect(screen.getByRole("region", { name: "Did price move with scarcity?" })).toBeTruthy();
    expect(screen.getAllByRole("listitem")).toHaveLength(r.rows.length);
    expect(screen.getByText(r.verdict)).toBeTruthy();
    expect(screen.getAllByText("+4.2%")).toHaveLength(1);
    expect(screen.getAllByText("AWS").length).toBeGreaterThan(0);
    const first = r.rows[0];
    if (first === undefined) throw new Error("no row");
    const chart = screen.getAllByRole("button")[0] as HTMLElement;
    expect(chart.getAttribute("aria-label")).toContain(first.tip.title);
    fireEvent.mouseMove(chart, { clientX: 10, clientY: 10 });
    expect((await screen.findAllByText(first.tip.title)).length).toBeGreaterThan(0);
    fireEvent.mouseLeave(chart);
    fireEvent.focus(chart);
    expect((await screen.findAllByText(first.tip.lines[1] as string)).length).toBeGreaterThan(0);
  });

  it("renders only the label for an empty window", () => {
    const { container } = render(createElement(PriceView, { data: stubBoardData(), state }));
    expect(container.querySelector("section")?.childElementCount).toBe(0);
  });

  // Empty means no price date in the window, not none in all the data (section 11.20).
  it("is empty on a day page past the last price date, and not on one inside", () => {
    const short = { ...events.price, dates: events.price.dates.slice(0, 5) };
    const on = (page: number) => {
      const d = windowOf("day", page);
      return derivePrice(
        { ...d, window: { ...d.window, price: short } },
        { ...DEFAULT_STATE, hz: "day", page },
      );
    };
    expect(on(2).empty).toBe(false);
    expect(on(10).empty).toBe(true);
  });
});
