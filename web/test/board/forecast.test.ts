// @vitest-environment jsdom
import { readFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { cleanup, render, screen } from "@testing-library/react";
import { createElement } from "react";
import { afterEach, describe, expect, it } from "vitest";
import { deriveForecast } from "../../src/board/derive/forecast";
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
import { ForecastView } from "../../src/board/view/forecast";
import { stubBoardData } from "../../src/data/stub";

// The forecast box (spec 0006 section 10.4): the rows and the verdict.
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
  const window: WindowPayload = {
    hz,
    page: f.page,
    H0: f.H0,
    H1: f.H1,
    L: f.L,
    sweeps,
    cb,
    spot,
    azspot,
    price: events.price,
  };
  return { meta, window };
}

afterEach(cleanup);

describe("forecast view", () => {
  const state: ViewState = { ...DEFAULT_STATE, hz: "week" };
  it("draws the derive output with a label and one row per machine", () => {
    const data = windowOf("week", null);
    const r = deriveForecast(data, state);
    render(createElement(ForecastView, { data, state }));
    expect(screen.getByRole("region", { name: "Does a high score predict a yes?" })).toBeTruthy();
    expect(screen.getAllByRole("listitem")).toHaveLength(r.rows.length);
    expect(screen.getByText(r.verdict)).toBeTruthy();
    expect(screen.getByText(r.kicker)).toBeTruthy();
    expect(screen.getByText("73% of 180")).toBeTruthy();
    expect(screen.getByText("0% of 240")).toBeTruthy();
    expect(
      screen.getByLabelText(/^H100 ×1: got the capacity when score said likely 73% of 180; /),
    ).toBeTruthy();
  });

  it("marks the focus machine and renders nothing for an empty window", () => {
    const data = windowOf("week", null);
    render(createElement(ForecastView, { data, state }));
    expect(screen.getByText("H100 ×1").style.color).toBe("var(--color-accent-300)");
    expect(screen.getByText("T4").style.color).toBe("var(--color-text)");
    cleanup();
    const { container } = render(createElement(ForecastView, { data: stubBoardData(), state }));
    expect(container.querySelector("section")?.childElementCount).toBe(0);
  });
});
