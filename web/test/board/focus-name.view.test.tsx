// @vitest-environment jsdom
// Spec 0006 section 11.4: the focus machine's name is --color-accent-300 on every row of every
// section. One test for each section, so a section that stops showing the name fails on its own.

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { buildMetaNow } from "../../scripts/board-now.mjs";
import { deriveBacktest } from "../../src/board/derive/backtest";
import { deriveBookahead } from "../../src/board/derive/bookahead";
import { deriveDefinitions } from "../../src/board/derive/definitions";
import { deriveForecast } from "../../src/board/derive/forecast";
import { deriveHowmany } from "../../src/board/derive/howmany";
import { deriveIntro } from "../../src/board/derive/intro";
import { deriveMatrix } from "../../src/board/derive/matrix";
import { deriveNow } from "../../src/board/derive/now";
import { derivePrice } from "../../src/board/derive/price";
import { deriveSawtooth } from "../../src/board/derive/sawtooth";
import { deriveSowhat } from "../../src/board/derive/sowhat";
import { deriveStickiness } from "../../src/board/derive/stickiness";
import { deriveSubstitutes } from "../../src/board/derive/substitutes";
import { makeFrame } from "../../src/board/derive/time";
import { deriveTimeofday } from "../../src/board/derive/timeofday";
import { deriveTrend } from "../../src/board/derive/trend";
import type { BoardData, CbPlace, Horizon, Sweep, ViewState } from "../../src/board/derive/types";
import { SECTIONS } from "../../src/board/view/sections";
import { GlobalTooltip } from "../../src/board/view/tooltip";

afterEach(cleanup);

const events = JSON.parse(
  readFileSync(resolve(__dirname, "../fixtures/board/events.json"), "utf8"),
);
const GPU = "H100 ×1";

function data(hz: Horizon): BoardData {
  const f = makeFrame(events.meta, hz, 1);
  const inRange = (r: readonly (number | string | null)[]) =>
    (r[0] as number) >= f.L && (r[0] as number) < f.H1;
  return {
    meta: { ...events.meta, now: buildMetaNow(events) },
    window: {
      hz,
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

// The derive function of each section. A section must render the focus machine name exactly when
// its derive output holds that name; the rule is read from the output, not listed by hand.
const DERIVES: Record<string, (d: BoardData, s: ViewState) => unknown> = {
  intro: deriveIntro,
  now: (d, s) => deriveNow(d, s, new Set()),
  sowhat: deriveSowhat,
  stickiness: deriveStickiness,
  backtest: deriveBacktest,
  trend: deriveTrend,
  howmany: deriveHowmany,
  substitutes: deriveSubstitutes,
  bookahead: deriveBookahead,
  sawtooth: deriveSawtooth,
  timeofday: deriveTimeofday,
  forecast: deriveForecast,
  price: derivePrice,
  matrix: deriveMatrix,
  definitions: deriveDefinitions,
};

/** True when the derive output of the section holds the focus machine name. */
function deriveHoldsName(name: string, d: BoardData, s: ViewState): boolean {
  const derive = DERIVES[name];
  if (derive === undefined) throw new Error(`no derive for section ${name}`);
  return holdsString(derive(d, s), s.gpu);
}

// A derive output holds the name when a row (an element of an array, at any depth) has it as a
// whole field value. A name in a title field, or inside a longer sentence, is not a row.
function holdsString(v: unknown, text: string, inRow = false): boolean {
  if (typeof v === "string") return inRow && v === text;
  if (Array.isArray(v)) return v.some((x) => holdsString(x, text, true));
  if (typeof v === "object" && v !== null) {
    return Object.values(v).some((x) => holdsString(x, text, inRow));
  }
  return false;
}

// Sections whose spec rows list every machine of `gl`, so the focus machine has a row on the
// fixture. Without this list the screen-equals-derive check passes when a derive function
// skips every row (zero on screen, zero in derive output).
const MACHINE_ROW_SECTIONS: Record<string, string> = {
  now: "11.9 (Row: 1 per machine of gl)",
  stickiness: "11.11 (Rows: machines of gl, mode now, tried >= 6)",
  backtest: "11.12 (Per machine of gl, mode now, >= 6 rounds)",
  trend: "11.13 (Rows: machines of gl with tried > 0)",
  howmany: "11.14 (Rows: machines of gl with a sweep of state >= 0)",
  bookahead: "11.16 (Rows: the focus machine first, then the catalogue)",
  matrix: "11.21 (Row groups: the tiers of gl)",
};

function focusNames(name: string, hz: Horizon) {
  const state: ViewState = { hz, page: 1, tier: "All", gpu: GPU };
  const View = SECTIONS.find((s) => s.name === name)?.View;
  if (View === undefined) throw new Error(`no section ${name}`);
  const { container, unmount } = render(
    <GlobalTooltip>
      <View data={data(hz)} state={state} />
    </GlobalTooltip>,
  );
  const bad: string[] = [];
  let seen = 0;
  for (const el of container.querySelectorAll("*")) {
    if (el.children.length > 0 || el.textContent?.trim() !== GPU) continue;
    seen++;
    let n: Element | null = el;
    let color = "";
    while (n && !color) {
      color = (n as HTMLElement).style?.color ?? "";
      n = n.parentElement;
    }
    if (color !== "var(--color-accent-300)") bad.push(`${name}: ${color || "no color"}`);
  }
  unmount();
  return { seen, bad };
}

describe("the focus machine name", () => {
  for (const hz of ["week", "month", "all"] as const) {
    describe(hz, () => {
      for (const { name } of SECTIONS) {
        it(`is accent on every row of the ${name} section`, () => {
          const { seen, bad } = focusNames(name, hz);
          const state: ViewState = { hz, page: 1, tier: "All", gpu: GPU };
          const holds = deriveHoldsName(name, data(hz), state);
          expect(seen > 0, `${name}: rows with the name in derive output vs on screen`).toBe(holds);
          expect(bad).toEqual([]);
          if (name in MACHINE_ROW_SECTIONS) {
            expect(
              holds,
              `${name}: derive output holds the focus name (spec ${MACHINE_ROW_SECTIONS[name]})`,
            ).toBe(true);
          }
        }, 30000);
      }
    });
  }
});
