// @vitest-environment jsdom
// Spec 0006 sections 11.9 to 11.20: the bar tracks, the bold head words, the price
// heads, and the forecast box, against spec 0006.

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { makeFrame } from "../../src/board/derive/time";
import type { BoardData, CbPlace, Horizon, Sweep, ViewState } from "../../src/board/derive/types";
import { DEFAULT_STATE } from "../../src/board/derive/viewstate";
import { BacktestView } from "../../src/board/view/backtest";
import { BookaheadView } from "../../src/board/view/bookahead";
import { ForecastView } from "../../src/board/view/forecast";
import { PriceView } from "../../src/board/view/price";
import { StickinessView } from "../../src/board/view/stickiness";
import { SubstitutesView } from "../../src/board/view/substitutes";
import { GlobalTooltip } from "../../src/board/view/tooltip";

afterEach(cleanup);

const events = JSON.parse(
  readFileSync(resolve(__dirname, "../fixtures/board/events.json"), "utf8"),
);

type FixtureSweep = Omit<Sweep, "gotZoneMax" | "gotZoneSum"> & { got: number };

function data(hz: Horizon): BoardData {
  const f = makeFrame(events.meta, hz, null);
  const inRange = (r: readonly (number | string | null)[]) => {
    const h = r[0] as number;
    return h >= f.L && h < f.H1;
  };
  const keep = (pts: readonly (readonly number[])[]) =>
    pts.filter((p) => (p[0] ?? 0) >= f.H0 - f.cad && (p[0] ?? 0) < f.H1);
  const spot: Record<string, (readonly [number, number])[]> = {};
  for (const [k, v] of Object.entries(events.spot as Record<string, [number, number][]>)) {
    const pts = keep(v) as [number, number][];
    if (pts.length > 0) spot[k] = pts;
  }
  const azspot: Record<string, (readonly number[])[]> = {};
  for (const [k, v] of Object.entries(events.azspot as Record<string, number[][]>)) {
    const pts = keep(v);
    if (pts.length > 0) azspot[k] = pts as number[][];
  }
  return {
    meta: events.meta,
    window: {
      hz,
      page: f.page,
      H0: f.H0,
      H1: f.H1,
      L: f.L,
      sweeps: (events.sweeps as FixtureSweep[])
        .filter((s) => s.h >= f.L && s.h < f.H1)
        .map(({ got, ...r }) => ({ ...r, gotZoneMax: got, gotZoneSum: got })),
      cb: (events.cb as CbPlace[]).map((p) => ({
        ...p,
        checks: p.checks.filter(inRange),
        longest: p.longest.filter(inRange),
      })),
      spot,
      azspot,
      price: events.price,
    },
  };
}

const state: ViewState = { ...DEFAULT_STATE, hz: "all", page: null, tier: "All" };
const wk: ViewState = { ...DEFAULT_STATE, hz: "week" };
const TRACK = "var(--color-neutral-900)";

function expectTracks(tracks: HTMLElement[], n: number) {
  expect(tracks.length).toBeGreaterThanOrEqual(n);
  for (const t of tracks) {
    expect(t.style.background).toBe(TRACK);
    expect(t.style.borderRadius).toBe("3px");
  }
}

describe("bar tracks (item 1)", () => {
  it("draws a track behind each substitutes bar", () => {
    render(<SubstitutesView data={data("all")} state={DEFAULT_STATE} />);
    expectTracks(screen.getAllByTestId("sub-track"), 6);
  });

  it("draws a track behind each book-ahead bar", () => {
    render(
      <GlobalTooltip>
        <BookaheadView data={data("all")} state={state} />
      </GlobalTooltip>,
    );
    const tracks = screen.getAllByTestId("book-track");
    expectTracks(tracks, 6);
    expect(within(tracks[0] as HTMLElement).getByText("14 d")).toBeTruthy();
  });

  it("draws a track behind each forecast bar", () => {
    render(<ForecastView data={data("week")} state={wk} />);
    expectTracks(screen.getAllByTestId("forecast-track"), 2);
  });
});

describe("bold head words (item 2)", () => {
  const bold = (el: HTMLElement) => {
    expect(el.style.fontWeight).toBe("600");
    expect(el.style.color).toBe("var(--color-neutral-300)");
  };

  it("bolds yes and no in the stickiness heads", () => {
    render(
      <GlobalTooltip>
        <StickinessView data={data("all")} state={state} />
      </GlobalTooltip>,
    );
    const heads = screen.getAllByRole("columnheader");
    bold(within(heads[0] as HTMLElement).getByText("yes"));
    bold(within(heads[1] as HTMLElement).getByText("no"));
  });

  it("bolds stay and follow in the backtest heads", () => {
    render(
      <GlobalTooltip>
        <BacktestView data={data("all")} state={state} />
      </GlobalTooltip>,
    );
    bold(screen.getByText("stay"));
    bold(screen.getByText("follow"));
  });

  it("bolds likely and not likely in the forecast heads", () => {
    render(<ForecastView data={data("week")} state={wk} />);
    bold(screen.getByText("likely"));
    bold(screen.getByText("not likely"));
  });
});

describe("price heads (item 3)", () => {
  it("prints machine, the sparkline head, the 2 dates, and change", () => {
    const { container } = render(
      <GlobalTooltip>
        <PriceView data={data("week")} state={wk} />
      </GlobalTooltip>,
    );
    const head = container.querySelector('[aria-hidden="true"]') as HTMLElement;
    const cells = Array.from(head.children).map((c) => c.textContent);
    expect(cells).toHaveLength(6);
    expect(cells[0]).toBe("machine");
    expect(cells[1]).toBe("");
    expect(cells[2]).toBe("daily mean, scaled to its own range");
    expect(cells[3]).toMatch(/\S/);
    expect(cells[4]).toMatch(/\S/);
    expect(cells[5]).toBe("change");
  });
});

describe("forecast box (item 4)", () => {
  it("holds the title inside the dashed box under an upper-case kicker", () => {
    render(<ForecastView data={data("week")} state={wk} />);
    const kicker = screen.getByText(/^Forecast only/);
    expect(kicker.style.textTransform).toBe("uppercase");
    expect(kicker.textContent).toMatch(/^Forecast only — /);
    const box = kicker.parentElement as HTMLElement;
    expect(box.style.border).toContain("dashed");
    const title = screen.getByRole("heading", { name: "Does a high score predict a yes?" });
    expect(box.contains(title)).toBe(true);
    expect(kicker.compareDocumentPosition(title) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });
});
