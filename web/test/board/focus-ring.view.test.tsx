// @vitest-environment jsdom

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { makeFrame } from "../../src/board/derive/time";
import type { BoardData, CbPlace, Sweep, ViewState } from "../../src/board/derive/types";
import { App } from "../../src/board/view/app";
import { SECTIONS } from "../../src/board/view/sections";
import { GlobalTooltip } from "../../src/board/view/tooltip";
import { StubDataSource, stubBoardData } from "../../src/data/stub";

afterEach(() => {
  cleanup();
  window.location.hash = "";
});

const FOCUSABLE = "button, a[href], input, select, textarea, [tabindex]";
// An inline `all` or `outline*` beats the stylesheet `:focus-visible` ring (spec 0006).
const BAD = /(^|;)\s*(all|outline[a-z-]*)\s*:/;

function checkNoOverride(root: Element): number {
  const focusable = root.querySelectorAll<HTMLElement>(FOCUSABLE);
  for (const el of focusable) {
    const style = el.getAttribute("style") ?? "";
    expect(style, el.outerHTML.slice(0, 120)).not.toMatch(BAD);
    expect(el.style.getPropertyValue("all")).toBe("");
    expect(el.style.outline).toBe("");
  }
  return focusable.length;
}

const events = JSON.parse(
  readFileSync(resolve(__dirname, "../fixtures/board/events.json"), "utf8"),
);
const STATE: ViewState = { hz: "week", page: 1, tier: "All", gpu: "H100 ×1" };

function dataWithEvents(): BoardData {
  const f = makeFrame(events.meta, "week", 1);
  const inRange = (r: readonly (number | string | null)[]) =>
    (r[0] as number) >= f.L && (r[0] as number) < f.H1;
  return {
    meta: events.meta,
    window: {
      hz: "week",
      page: f.page,
      H0: f.H0,
      H1: f.H1,
      L: f.L,
      sweeps: (events.sweeps as Sweep[]).filter((s) => s.h >= f.L && s.h < f.H1),
      cb: (events.cb as CbPlace[]).map((p) => ({
        ...p,
        checks: p.checks.filter(inRange),
        longest: p.longest.filter(inRange),
      })),
      spot: {},
      azspot: {},
      price: events.price,
    },
  };
}

// Each case renders under load in jsdom; a per-test timeout keeps it off the 5 s default.
const TIMEOUT = 30_000;

describe("the board keeps the stylesheet focus ring", () => {
  // Every section on the stub data (empty) and on the event fixture (rows, buttons).
  it.each(SECTIONS.map((s) => [s.name, s.View] as const))(
    "sets no inline all or outline on a focusable element in section %s",
    (name, View) => {
      for (const data of [stubBoardData(), dataWithEvents()]) {
        const { container } = render(
          <GlobalTooltip>
            <View key={name} data={data} state={STATE} />
          </GlobalTooltip>,
        );
        expect(container.querySelectorAll("section[data-section]").length).toBe(1);
        checkNoOverride(container);
        cleanup();
      }
    },
    TIMEOUT,
  );

  it(
    "sets no inline all or outline on a focusable element in the shell",
    async () => {
      const { container } = render(<App source={new StubDataSource()} />);
      await waitFor(() => expect(container.querySelector("[aria-busy]")).toBeNull());
      expect(screen.getByRole("radiogroup", { name: "Horizon" })).toBeTruthy();
      expect(checkNoOverride(container)).toBeGreaterThan(0);
    },
    TIMEOUT,
  );
});
