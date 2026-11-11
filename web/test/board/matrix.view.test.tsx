// @vitest-environment jsdom

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { makeFrame } from "../../src/board/derive/time";
import type { BoardData, Sweep } from "../../src/board/derive/types";
import { DEFAULT_STATE } from "../../src/board/derive/viewstate";
import { App } from "../../src/board/view/app";
import { MatrixView } from "../../src/board/view/matrix";
import { GlobalTooltip } from "../../src/board/view/tooltip";
import { stubBoardData } from "../../src/data/stub";

afterEach(cleanup);

const events = JSON.parse(
  readFileSync(resolve(__dirname, "../fixtures/board/events.json"), "utf8"),
);

/** The "all" window of the fixture, with its Capacity Block records. */
function windowOf(hz: "all", page: null): BoardData {
  const f = makeFrame(events.meta, hz, page);
  const sweeps: Sweep[] = events.sweeps
    .filter((s: { h: number }) => s.h >= f.L && s.h < f.H1)
    .map(({ got, ...r }: { got: number }) => ({ ...r, gotZoneMax: got, gotZoneSum: got }));
  const inRange = (r: readonly (number | string | null)[]) => (r[0] as number) < f.H1;
  const cb = events.cb.map((p: { checks: never[]; longest: never[] }) => ({
    ...p,
    checks: p.checks.filter(inRange),
    longest: p.longest.filter(inRange),
  }));
  return {
    meta: events.meta,
    window: {
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
    },
  };
}

const state = { ...DEFAULT_STATE, hz: "all" as const, page: null, tier: "All", gpu: "L40S" };

describe("MatrixView", () => {
  it("renders an empty labelled section on an empty window", () => {
    const { container } = render(<MatrixView data={stubBoardData()} state={DEFAULT_STATE} />);
    const s = container.querySelector("section[data-section=matrix]");
    expect(s?.getAttribute("aria-label")).toBe("Where did the yes come from?");
    expect(s?.childElementCount).toBe(0);
  });

  it("is a table with scoped headers and labelled cells", () => {
    const { container } = render(
      <GlobalTooltip>
        <MatrixView data={windowOf("all", null)} state={state} />
      </GlobalTooltip>,
    );
    expect(screen.getByRole("heading", { name: "Where did the yes come from?" })).toBeTruthy();
    const table = screen.getByRole("table");
    expect(within(table).getAllByRole("columnheader").length).toBeGreaterThanOrEqual(13);
    const rowHeads = container.querySelectorAll("tbody th[scope=row]");
    expect(rowHeads).toHaveLength(12);
    expect(container.querySelectorAll("th[scope=colgroup]")).toHaveLength(2);
    const cells = container.querySelectorAll("tbody td");
    expect(cells).toHaveLength(12 * 11);
    for (const c of cells) expect(c.getAttribute("aria-label")).toMatch(/ · /);
    expect(cells[0]?.getAttribute("aria-label")).toBe(
      "B200 ×8 · AWS us-east-1. 5 of 168 checks had a 1-day block starting within 1 h · 3%" +
        " Over time: Oct 13 7% · Oct 15 0% · Oct 17 6% · Oct 19 0% · Oct 21 0% · Oct 23 0% · Oct 25 0% · Oct 27 6% · Oct 29 0% · Oct 31 0% · Nov 2 20%",
    );
  });

  it("keeps the stylesheet focus ring: no inline reset or outline on a focusable element", () => {
    const { container } = render(
      <GlobalTooltip>
        <MatrixView data={windowOf("all", null)} state={state} />
      </GlobalTooltip>,
    );
    const focusable = container.querySelectorAll<HTMLElement>("button, [tabindex]");
    expect(focusable.length).toBeGreaterThan(100);
    for (const el of focusable) {
      const css = el.getAttribute("style") ?? "";
      expect(css).not.toMatch(/outline/i);
      expect(css).not.toMatch(/all:\s*unset/i);
    }
  });

  it("marks the focus machine and sets it from a row button", () => {
    const setState = vi.fn();
    const { container } = render(
      <GlobalTooltip>
        <MatrixView data={windowOf("all", null)} state={state} setState={setState} />
      </GlobalTooltip>,
    );
    const l40s = screen.getByRole("button", { name: "L40S" });
    expect(l40s.getAttribute("aria-pressed")).toBe("true");
    expect(l40s.getAttribute("title")).toBe("g6e.xlarge");
    const t4 = screen.getByRole("button", { name: "T4" });
    expect(t4.getAttribute("title")).toBe("g4dn.xlarge, Standard_NC4as_T4_v3");
    fireEvent.click(t4);
    expect(setState).toHaveBeenCalledWith({ ...state, gpu: "T4" });
    expect(window.location.hash).toBe("");
    expect(container.querySelectorAll("button")).toHaveLength(12);
  });

  it("sets the focus through the app callback: 1 history entry, the hash follows", async () => {
    window.localStorage.clear();
    window.history.replaceState(null, "", "#hz=all&tier=All&gpu=L40S");
    const full = windowOf("all", null);
    const source = {
      meta: () => Promise.resolve(full.meta),
      window: () => Promise.resolve(full.window),
    };
    render(<App source={source} />);
    const t4 = await screen.findAllByRole("button", { name: "T4" });
    const before = window.history.length;
    fireEvent.click(t4[0] as HTMLElement);
    await waitFor(() => expect(window.location.hash).toContain("gpu=T4"));
    expect(window.history.length).toBe(before);
  });
});
