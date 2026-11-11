// @vitest-environment jsdom

import { readFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { makeFrame } from "../../src/board/derive/time";
import type { BoardData, CbPlace, Meta, Sweep } from "../../src/board/derive/types";
import { BookaheadView } from "../../src/board/view/bookahead";
import { HowmanyView } from "../../src/board/view/howmany";
import { GlobalTooltip } from "../../src/board/view/tooltip";
import { stubBoardData } from "../../src/data/stub";

afterEach(cleanup);

const events = JSON.parse(
  readFileSync(join(resolve(__dirname, "../fixtures/board"), "events.json"), "utf8"),
);
const meta: Meta = events.meta;
const state = { hz: "week" as const, page: null, tier: "All", gpu: "H100 ×1" };

function data(): BoardData {
  const f = makeFrame(meta, "week", null);
  const sweeps = (events.sweeps as (Omit<Sweep, "gotZoneMax" | "gotZoneSum"> & { got: number })[])
    .filter((s) => s.h >= f.H0 && s.h < f.H1)
    .map(({ got, ...rest }) => ({ ...rest, gotZoneMax: got, gotZoneSum: got }));
  return {
    meta,
    window: {
      hz: "week",
      page: f.page,
      H0: f.H0,
      H1: f.H1,
      L: f.L,
      sweeps,
      cb: events.cb as CbPlace[],
      spot: {},
      azspot: {},
      price: events.price,
    },
  };
}

describe("the howmany view", () => {
  it("renders labelled bars and shows the tooltip on hover and on focus", async () => {
    const { container } = render(
      <GlobalTooltip>
        <HowmanyView data={data()} state={state} />
      </GlobalTooltip>,
    );
    const sec = container.querySelector("section[data-section=howmany]");
    expect(sec?.getAttribute("aria-label")).toBe("How many at once?");
    const bar = screen.getByRole("img", {
      name: "H100 ×1 · at least 2 machines at once: 132 of 420 asks handed us at least 2 · 31%",
    });
    expect(bar.tabIndex).toBe(0);
    fireEvent.mouseMove(bar, { clientX: 10, clientY: 10 });
    expect(
      (await screen.findAllByText("132 of 420 asks handed us at least 2 · 31%")).length,
    ).toBeGreaterThan(0);
    fireEvent.mouseLeave(bar);
    fireEvent.focus(screen.getByRole("img", { name: /^T4 · at least 32 machines/ }));
    expect(
      (await screen.findAllByText("23 of 504 asks handed us at least 32 · 5%")).length,
    ).toBeGreaterThan(0);
  });

  it("renders nothing inside the section for an empty window", () => {
    const { container } = render(<HowmanyView data={stubBoardData()} state={state} />);
    expect(container.querySelector("section")?.childElementCount).toBe(0);
  });
});

describe("the bookahead view", () => {
  it("renders a table with the focus machine first and keyboard-reachable rows", async () => {
    render(
      <GlobalTooltip>
        <BookaheadView data={data()} state={state} />
      </GlobalTooltip>,
    );
    expect(
      screen.getByRole("table", { name: "Book ahead: how long a block, how soon" }),
    ).toBeTruthy();
    expect(screen.getAllByRole("columnheader").map((e) => e.textContent)).toEqual([
      "machine",
      "region",
      "longest block offered",
      "it starts in",
    ]);
    const rows = screen.getAllByRole("row").slice(1);
    expect(rows).toHaveLength(6);
    expect(rows[0]?.textContent).toBe("H100 ×1us-east-114 d~3.7 d");
    expect(rows[0]?.tabIndex).toBe(0);
    fireEvent.focus(rows[0] as HTMLElement);
    expect(
      (await screen.findAllByText("Listed upfront fee for that 1-day block: $132")).length,
    ).toBeGreaterThan(0);
    expect(screen.getByText(/^Bar scale: 0 to 14 days\./).textContent).toMatch(
      /Focus machine first\.$/,
    );
  });
});
