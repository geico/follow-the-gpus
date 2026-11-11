// @vitest-environment jsdom

import { readFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { cleanup, render, screen } from "@testing-library/react";
import { createElement } from "react";
import { afterEach, describe, expect, it } from "vitest";
import { machineByName } from "../../src/board/derive/catalogue";
import { makeFrame } from "../../src/board/derive/time";
import { businessBand, deriveTimeofday } from "../../src/board/derive/timeofday";
import type {
  BoardData,
  CbPlace,
  Horizon,
  Meta,
  Sweep,
  WindowPayload,
} from "../../src/board/derive/types";
import { DEFAULT_STATE } from "../../src/board/derive/viewstate";
import { TimeofdayView } from "../../src/board/view/timeofday";
import { GlobalTooltip } from "../../src/board/view/tooltip";
import { stubBoardData } from "../../src/data/stub";

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
  const window: WindowPayload = {
    hz,
    page: f.page,
    H0: f.H0,
    H1: f.H1,
    L: f.L,
    sweeps,
    cb: events.cb as CbPlace[],
    spot: {},
    azspot: {},
    price: events.price,
  };
  return { meta, window };
}

const VIEW = { hz: "week" as const, page: null, tier: "All", gpu: "H100 ×1" };

describe("time of day rules", () => {
  it("is empty on a store with no sweeps", () => {
    expect(deriveTimeofday(stubBoardData(), DEFAULT_STATE)).toEqual({
      section: "timeofday",
      empty: true,
    });
  });

  it("places the brackets by the zone offsets at the window midpoint", () => {
    const summer = Date.UTC(2026, 9, 20);
    const winter = Date.UTC(2026, 10, 20);
    expect(businessBand("America/New_York", summer).range).toBe("13–21");
    expect(businessBand("America/Los_Angeles", summer).range).toBe("16–24");
    expect(businessBand("America/New_York", winter).range).toBe("14–22");
    expect(businessBand("America/Los_Angeles", winter).range).toBe("17–01");
  });

  it("follows the daylight-time change of 2026-11-01 for a window spanning it", () => {
    const base = windowOf("all", null);
    const t0 = Date.parse(meta.t0);
    const at = (iso: string) => Math.round((Date.parse(iso) - t0) / 3600e3);
    // A window whose midpoint is before the change (Oct 29 to Nov 2) or after it (Oct 31 to Nov 12).
    const mk = (a: string, b: string): BoardData => ({
      meta: { ...meta, nh: at(b) + 1 },
      window: { ...base.window, H0: at(a), H1: at(b) },
    });
    const state = { ...DEFAULT_STATE, hz: "all" as const, page: null, gpu: "H100 ×1", tier: "All" };
    const before = deriveTimeofday(mk("2026-10-29T00:00:00Z", "2026-11-02T00:00:00Z"), state);
    const after = deriveTimeofday(mk("2026-10-31T00:00:00Z", "2026-11-12T00:00:00Z"), state);
    if (before.empty || after.empty) throw new Error("empty");
    expect(before.bands.map((b) => b.range)).toEqual(["13–21", "16–24"]);
    expect(after.bands.map((b) => b.range)).toEqual(["14–22", "17–01"]);
    expect(after.bands[1]?.pieces).toHaveLength(2);
  });
});

describe("time of day, a region with no coast", () => {
  it("forms no resolution group", () => {
    const state = { ...DEFAULT_STATE, ...VIEW };
    const base = windowOf("week", null);
    const res = (d: BoardData) => {
      const r = deriveTimeofday(d, state);
      if (r.empty) throw new Error("empty");
      return r.res;
    };
    const types = [
      ...(machineByName(state.gpu)?.aws ?? []),
      ...(machineByName(state.gpu)?.azure ?? []),
    ];
    const first = base.window.sweeps.find((s) => s.state >= 0 && types.includes(s.sku));
    if (first === undefined) throw new Error("no sweep");
    // Sweeps with no ask in an unknown region at every hour: many groups under minAsks.
    const extra: Sweep[] = Array.from({ length: base.window.H1 - base.window.H0 }, (_, i) => ({
      ...first,
      region: "nowhere-1",
      h: base.window.H0 + i,
      t: base.window.H0 + i,
      asked: 0,
      state: 0,
    }));
    const withExtra: BoardData = {
      ...base,
      window: { ...base.window, sweeps: [...base.window.sweeps, ...extra] },
    };
    expect(res(withExtra)).toBe(res(base));
  });
});

describe("time of day view", () => {
  afterEach(cleanup);

  it("renders the grid with roles, labels, and the verdict", () => {
    const data = windowOf("week", null);
    render(
      createElement(
        GlobalTooltip,
        null,
        createElement(TimeofdayView, { data, state: { ...DEFAULT_STATE, ...VIEW } }),
      ),
    );
    expect(
      screen.getByRole("region", { name: "Does the time of day matter? H100 ×1" }),
    ).toBeTruthy();
    expect(screen.getAllByRole("button")).toHaveLength(3 * 12);
    expect(screen.getByLabelText(/^H100 ×1 · East regions · 00–02 UTC\. 9 of 30/)).toBeTruthy();
    expect(screen.getByTestId("tod-verdict").textContent).toBeTruthy();
    expect(screen.getAllByRole("button")[0]?.tagName).toBe("BUTTON");
  });

  it("aligns the brackets with the hour columns from the table layout", () => {
    render(
      createElement(TimeofdayView, {
        data: windowOf("week", null),
        state: { ...DEFAULT_STATE, ...VIEW },
      }),
    );
    const px = (v: string) => Number.parseFloat(v);
    const table = screen.getByRole("table");
    const spacing = px(table.style.borderSpacing);
    const label = px(table.querySelector("th")?.style.width ?? "");
    const brackets = screen.getByTestId("tod-brackets");
    expect(spacing).toBeGreaterThan(0);
    // Hour columns begin after the edge gap, the label column, and 1 gap; they end 1 edge gap in.
    expect(px(brackets.style.marginLeft)).toBe(label + 2 * spacing);
    expect(px(brackets.style.marginRight)).toBe(spacing);
  });

  it("draws the legend gradient from the shell tokens and names the corner head Row, visually hidden", () => {
    render(
      createElement(TimeofdayView, {
        data: windowOf("week", null),
        state: { ...DEFAULT_STATE, ...VIEW },
      }),
    );
    const bg = screen.getByTestId("tod-legend-gradient").style.background;
    expect(bg).toContain("linear-gradient");
    expect(bg).toContain("var(--color-neutral-900)");
    expect(bg).toContain("var(--color-accent-400)");
    const corner = screen.getByText("Row");
    expect(corner.closest("th")?.textContent).toBe("Row");
    expect(corner.className).toBe("sr-only");
    const css = readFileSync(join(__dirname, "../../src/board/styles.css"), "utf8");
    expect(css).toMatch(
      /\.sr-only\s*{[^}]*width: 1px;[^}]*height: 1px;[^}]*clip: rect\(0, 0, 0, 0\)/,
    );
  });

  it("renders only the labelled shell for a store with no sweeps", () => {
    const { container } = render(
      createElement(TimeofdayView, { data: stubBoardData(), state: DEFAULT_STATE }),
    );
    expect(container.querySelector("section")?.childElementCount).toBe(0);
  });
});
