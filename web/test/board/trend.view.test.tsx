// @vitest-environment jsdom
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { makeFrame } from "../../src/board/derive/time";
import type { BoardData, Meta, Sweep } from "../../src/board/derive/types";
import { DEFAULT_STATE } from "../../src/board/derive/viewstate";
import { GlobalTooltip } from "../../src/board/view/tooltip";
import { TrendView } from "../../src/board/view/trend";
import { stubBoardData } from "../../src/data/stub";

afterEach(cleanup);

function withSweeps(): BoardData {
  const s = stubBoardData();
  const base = {
    t: 0,
    cloud: "aws",
    region: "us-east-1",
    sku: "p6-b200.48xlarge",
    probe: "aws_odcr",
    asked: 1,
    gotZoneMax: 1,
    gotZoneSum: 1,
    state: 2,
    nMax: 1,
    reason: null,
    err: null,
    zones: 1,
    zonesYes: 1,
  } as const;
  const sweeps: Sweep[] = [0, 2, 4].map((h) => ({ ...base, h }));
  return { ...s, window: { ...s.window, sweeps } };
}

describe("trend view", () => {
  it("renders only the labelled frame with no row", () => {
    const { container } = render(<TrendView data={stubBoardData()} state={DEFAULT_STATE} />);
    expect(container.querySelector("section")?.childElementCount).toBe(0);
  });

  it("keeps the focus ring in the stylesheet: no inline outline or unset", () => {
    const { container } = render(
      <GlobalTooltip>
        <TrendView data={withSweeps()} state={{ ...DEFAULT_STATE, gpu: "B200 ×8" }} />
      </GlobalTooltip>,
    );
    const focusable = container.querySelectorAll("[tabindex]");
    expect(focusable.length).toBeGreaterThan(0);
    for (const el of focusable) {
      const style = el.getAttribute("style") ?? "";
      expect(style).not.toMatch(/outline/);
      expect(style).not.toMatch(/all:\s*unset/);
    }
  });

  it("shows a tooltip for a day from the keyboard", () => {
    render(
      <GlobalTooltip>
        <TrendView data={withSweeps()} state={{ ...DEFAULT_STATE, gpu: "B200 ×8" }} />
      </GlobalTooltip>,
    );
    const chart = screen.getAllByRole("img")[0];
    if (!chart) throw new Error("no chart");
    expect(chart.getAttribute("tabindex")).toBe("0");
    fireEvent.keyDown(chart, { key: "ArrowRight" });
    expect(screen.getAllByText(/B200 ×8 · /).length).toBeGreaterThan(0);
  });

  // The 1-day dot is an HTML marker, 6 px, round, centred at left 50% and top (100 - rate)%.
  it("draws the day dot as a 6 px HTML marker", () => {
    const events = JSON.parse(
      readFileSync(resolve(__dirname, "../fixtures/board/events.json"), "utf8"),
    );
    const meta: Meta = events.meta;
    const f = makeFrame(meta, "day", null);
    const sweeps = (events.sweeps as (Omit<Sweep, "gotZoneMax" | "gotZoneSum"> & { got: number })[])
      .filter((x) => x.h >= f.L && x.h < f.H1)
      .map(({ got, ...rest }) => ({ ...rest, gotZoneMax: got, gotZoneSum: got }));
    const data: BoardData = {
      meta,
      window: {
        hz: "day",
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
    const { container } = render(<TrendView data={data} state={{ ...DEFAULT_STATE, hz: "day" }} />);
    const dots = container.querySelectorAll<HTMLElement>("[data-testid=trend-dot]");
    expect(dots.length).toBeGreaterThan(0);
    const dot = dots[0] as HTMLElement;
    expect(dot.tagName).toBe("SPAN");
    expect(dot.style.width).toBe("6px");
    expect(dot.style.height).toBe("6px");
    expect(dot.style.borderRadius).toBe("50%");
    expect(dot.style.left).toBe("50%");
    expect(dot.style.top).toMatch(/^\d+(\.\d+)?%$/);
    expect(container.querySelector("svg circle")).toBeNull();
  });
});
