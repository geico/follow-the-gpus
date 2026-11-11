// @vitest-environment jsdom
// The view of spec 0006 section 11.9. It draws what `deriveNow` returns; the tests read the
// expected text from the derive output, not from a list written by hand.

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { buildMetaNow } from "../../scripts/board-now.mjs";
import { deriveNow, type NowKind, type NowRow } from "../../src/board/derive/now";
import { makeFrame } from "../../src/board/derive/time";
import type { BoardData, CbPlace, Meta, Sweep, ViewState } from "../../src/board/derive/types";
import { DEFAULT_STATE } from "../../src/board/derive/viewstate";
import { App } from "../../src/board/view/app";
import { NowView } from "../../src/board/view/now";
import { GlobalTooltip } from "../../src/board/view/tooltip";
import { StubDataSource, stubBoardData } from "../../src/data/stub";

beforeEach(() => {
  window.localStorage.clear();
  window.history.replaceState(null, "", "#");
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const events = JSON.parse(
  readFileSync(resolve(__dirname, "../fixtures/board/events.json"), "utf8"),
);
const META: Meta = { ...events.meta, now: buildMetaNow(events) };
const STATE: ViewState = { hz: "week", page: 1, tier: "All", gpu: "H100 ×1" };

function dataOf(meta: Meta = META, hz: ViewState["hz"] = "week"): BoardData {
  const f = makeFrame(meta, hz, 1);
  const inRange = (r: readonly (number | string | null)[]) =>
    (r[0] as number) >= f.L && (r[0] as number) < f.H1;
  return {
    meta,
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

// The fixture holds 5 of the 6 answer kinds. A block 5 hours out on the B200 x8 gives "soon".
function withSoon(): BoardData {
  const now = META.now;
  if (now === undefined) throw new Error("fixture has no meta.now");
  const cb = now.cb.map((c) =>
    c.sku === "p6-b200.48xlarge" && c.leadH !== null
      ? {
          ...c,
          leadH: 5,
          start: new Date(Date.parse(META.t0) + ((c.h ?? 0) + 5) * 3600e3).toISOString(),
        }
      : c,
  );
  return dataOf({ ...META, now: { ...now, cb } });
}

const rowsOf = (d: BoardData, state: ViewState = STATE, open: ReadonlySet<string> = new Set()) => {
  const r = deriveNow(d, state, open);
  if (r.empty) throw new Error("empty");
  return r.groups.flatMap((g) => g.rows);
};

const show = (d: BoardData = dataOf(), setState: (next: ViewState) => void = () => {}) =>
  render(
    <GlobalTooltip>
      <NowView data={d} state={STATE} setState={setState} />
    </GlobalTooltip>,
  );

const ICON_OF: Record<NowKind, string> = {
  yes: "check-circle",
  soon: "clock",
  book: "calendar-blank",
  no: "x-circle",
  quota: "lock-simple",
  none: "minus-circle",
};
const button = (name: string): HTMLElement => {
  const b = screen
    .getAllByRole("button")
    .find((x) => x.firstElementChild?.firstElementChild?.textContent === name);
  if (b === undefined) throw new Error(`no row ${name}`);
  return b;
};

describe("NowView answers", () => {
  it("covers all 6 kinds on the fixture with the clock variant", () => {
    const kinds = new Set(rowsOf(withSoon()).map((r) => r.kind));
    expect([...kinds].sort()).toEqual(Object.keys(ICON_OF).sort());
  });

  for (const kind of Object.keys(ICON_OF) as NowKind[]) {
    it(`draws an icon, a word, and a qualifier for every row of kind ${kind}`, () => {
      const d = withSoon();
      const rows = rowsOf(d).filter((r: NowRow) => r.kind === kind);
      expect(rows.length).toBeGreaterThan(0);
      show(d);
      for (const r of rows) {
        const b = button(r.name);
        const icon = b.querySelector("svg[data-icon]");
        expect(icon?.getAttribute("data-icon"), r.name).toBe(ICON_OF[kind]);
        expect(icon?.getAttribute("aria-hidden")).toBe("true");
        expect(within(b).getByText(r.word)).toBeTruthy();
        expect(within(b).getByText(r.qual)).toBeTruthy();
        expect(within(b).getByText(r.plan)).toBeTruthy();
        expect(within(b).getByText(r.depBig)).toBeTruthy();
        expect(b.getAttribute("aria-expanded")).toBe("false");
      }
    });
  }

  it("draws the title, the as-of line, the column heads, the tier kickers, and the footer", () => {
    show();
    const r = deriveNow(dataOf(), STATE);
    if (r.empty) throw new Error("empty");
    expect(
      screen.getByRole("heading", { name: "Can I get one right now, anywhere?" }),
    ).toBeTruthy();
    expect(screen.getByText(r.asOf)).toBeTruthy();
    for (const head of ["Machine", "Right now", "Where · what to do", `Over ${r.winLabel}`]) {
      expect(screen.getByText(head)).toBeTruthy();
    }
    for (const g of r.groups) expect(screen.getByText(g.name)).toBeTruthy();
    expect(screen.getByText(/^A place is a cloud plus a Region/)).toBeTruthy();
    expect(screen.getAllByRole("button")).toHaveLength(12);
  });

  it("has no legend and no window control on the collapsed rows", () => {
    show();
    expect(screen.queryByRole("list", { name: "Legend" })).toBeNull();
    expect(screen.queryByText("got all we asked")).toBeNull();
    expect(screen.queryByRole("radiogroup")).toBeNull();
    expect(screen.queryByText("Over", { exact: true })).toBeNull();
  });

  it("renders an empty labelled section when meta.now is absent", () => {
    const { now: _now, ...old } = META;
    const { container } = render(
      <NowView data={{ ...stubBoardData(), meta: old }} state={STATE} />,
    );
    const s = container.querySelector("section[data-section=now]");
    expect(s?.getAttribute("aria-label")).toBe("Can I get one right now, anywhere?");
    expect(s?.childElementCount).toBe(0);
  });
});

describe("NowView open rows", () => {
  it("opens and closes a row, lists its places, and sets the focus machine", () => {
    const setState = vi.fn();
    show(dataOf(), setState);
    const t4 = button("T4");
    fireEvent.click(t4);
    expect(setState).toHaveBeenCalledWith({ ...STATE, gpu: "T4" });
    expect(t4.getAttribute("aria-expanded")).toBe("true");
    const detail = document.getElementById(t4.getAttribute("aria-controls") ?? "") as HTMLElement;
    const want = rowsOf(dataOf(), STATE, new Set(["T4"])).find((r) => r.name === "T4");
    expect(want?.places.length).toBeGreaterThan(0);
    expect(detail.querySelectorAll("[data-strip]")).toHaveLength(want?.places.length ?? -1);
    for (const p of want?.places ?? []) {
      expect(within(detail).getAllByText(p.nowTxt).length).toBeGreaterThan(0);
    }
    expect(within(detail).getByText("Every place we asked")).toBeTruthy();
    expect(within(detail).getByText("got all we asked")).toBeTruthy();
    expect(within(detail).getByText("blank = not asked")).toBeTruthy();
    expect(within(detail).getAllByText("try first")).toHaveLength(1);
    expect(detail.textContent).not.toMatch(/by zone/i);
    fireEvent.click(t4);
    expect(t4.getAttribute("aria-expanded")).toBe("false");
    expect(document.getElementById(detail.id)).toBeNull();
  });

  it("draws the Capacity Block rows of a block machine with the soonest tag", () => {
    show(withSoon());
    const b200 = button("H100 ×8");
    fireEvent.click(b200);
    const detail = document.getElementById(b200.getAttribute("aria-controls") ?? "") as HTMLElement;
    const want = rowsOf(withSoon(), STATE, new Set(["H100 ×8"])).find((r) => r.name === "H100 ×8");
    expect(want?.blocks.length).toBeGreaterThan(0);
    expect(within(detail).getAllByText("Capacity Block · booked ahead")).toHaveLength(
      want?.blocks.length ?? -1,
    );
    expect(within(detail).getAllByText("soonest")).toHaveLength(1);
    for (const blk of want?.blocks ?? []) expect(within(detail).getByText(blk.offer)).toBeTruthy();
  });

  it("marks the zone change inside the window with a dashed line titled for it", () => {
    const d = dataOf();
    const mid = (d.window.H0 + d.window.H1) / 2;
    show({ ...d, meta: { ...d.meta, zoneStartH: mid } });
    fireEvent.click(button("T4"));
    expect(screen.getAllByTitle("AWS asks every zone from here").length).toBeGreaterThan(0);
    expect(screen.getByText("Dashed line: AWS asks every zone from here.")).toBeTruthy();
  });
});

// jsdom has no layout: give the cell row a 1000 px box with 1 px gaps between the cells.
const box = (left: number, width: number) => ({
  left,
  top: 0,
  right: left + width,
  bottom: 16,
  width,
  height: 16,
  x: left,
  y: 0,
  toJSON: () => ({}),
});
const rect = (el: HTMLElement) => {
  vi.spyOn(el, "getBoundingClientRect").mockReturnValue(box(0, 1000));
  const n = el.children.length;
  const w = (1000 - (n - 1)) / n;
  return Array.from(el.children, (c, k) => {
    vi.spyOn(c, "getBoundingClientRect").mockReturnValue(box(k * (w + 1), w));
    return { left: k * (w + 1), w };
  });
};
const openStrip = (): HTMLElement => {
  show();
  fireEvent.click(button("T4"));
  return document.querySelector("[data-strip]") as HTMLElement;
};
const title = () => {
  const t = screen.queryAllByText(/^\d+ \w+ \d+:\d+ UTC$/);
  return t.length > 0 ? t[0]?.textContent : null;
};

describe("NowView strip tooltip", () => {
  it("shows the tooltip on mouse move and hides it on mouse leave", async () => {
    const strip = openStrip();
    const cells = rect(strip);
    expect(cells.length).toBeGreaterThan(10);
    fireEvent.mouseMove(strip.firstElementChild as HTMLElement, { clientX: 1, clientY: 10 });
    await waitFor(() => expect(title()).not.toBeNull());
    expect(screen.getByRole("tooltip")).toBeTruthy();
    fireEvent.mouseLeave(strip);
    await waitFor(() => expect(title()).toBeNull());
  });

  it("keeps the tooltip mounted across cells and gaps and follows the pointer", async () => {
    const strip = openStrip();
    rect(strip);
    fireEvent.mouseMove(strip, { clientX: 1, clientY: 10 });
    await waitFor(() => expect(title()).not.toBeNull());
    const seen = new Set<string | null | undefined>([title()]);
    let hidden = 0;
    const obs = new MutationObserver(() => {
      if (title() === null) hidden += 1;
    });
    obs.observe(document.body, { childList: true, subtree: true });
    // The event target is the strip itself, as in a gap between 2 cells.
    for (let x = 1; x < 1000; x += 7) {
      fireEvent.mouseMove(strip, { clientX: x, clientY: 10 });
      seen.add(title());
    }
    await new Promise((r) => setTimeout(r, 50));
    obs.disconnect();
    expect(hidden).toBe(0);
    expect(title()).not.toBeNull();
    expect(seen.size).toBeGreaterThan(10);
  });

  it("names the nearest cell in a gap and at the ends of the strip", async () => {
    const strip = openStrip();
    const cells = rect(strip);
    const last = cells.length - 1;
    const hover = async (x: number) => {
      fireEvent.mouseMove(strip, { clientX: x, clientY: 10 });
      await waitFor(() => expect(title()).not.toBeNull());
      return title();
    };
    const c = (k: number) => cells[k] as { left: number; w: number };
    const want1 = await hover(c(last - 1).left + c(last - 1).w / 2);
    const want2 = await hover(c(last).left + c(last).w / 2);
    expect(want1).not.toBe(want2);
    expect(await hover(c(last - 1).left + c(last - 1).w + 0.2)).toBe(want1);
    expect(await hover(c(last).left - 0.2)).toBe(want2);
    expect(await hover(c(last).left + c(last).w - 0.05)).toBe(want2);
  });

  it("shows the tooltip on keyboard focus and moves between cells with the arrow keys", async () => {
    const strip = openStrip();
    rect(strip);
    expect(strip.getAttribute("tabindex")).toBe("0");
    fireEvent.focus(strip);
    await waitFor(() => expect(title()).not.toBeNull());
    const last = title();
    fireEvent.keyDown(strip, { key: "ArrowLeft" });
    await waitFor(() => expect(title()).not.toBe(last));
    fireEvent.keyDown(strip, { key: "ArrowRight" });
    await waitFor(() => expect(title()).toBe(last));
    fireEvent.blur(strip);
    await waitFor(() => expect(title()).toBeNull());
  });

  it("keeps the stylesheet focus ring: no focusable element sets an inline all or outline", () => {
    const { container } = show();
    fireEvent.click(button("T4"));
    const focusable = container.querySelectorAll<HTMLElement>(
      "button, a[href], input, select, textarea, [tabindex]",
    );
    expect(focusable.length).toBeGreaterThan(12);
    for (const el of focusable) {
      const style = el.getAttribute("style") ?? "";
      expect(style, el.textContent ?? "").not.toMatch(/(^|;)\s*(all|outline[a-z-]*)\s*:/);
    }
  });
});

describe("the Day button", () => {
  it("sets hz=day, clears the page, and round-trips through the hash", async () => {
    window.history.replaceState(null, "", "#hz=week&page=0&tier=All&gpu=H100%20%C3%971");
    render(<App source={new StubDataSource()} />);
    const group = screen.getByRole("radiogroup", { name: "Horizon" });
    expect(
      within(group)
        .getAllByRole("radio")
        .map((r) => r.textContent),
    ).toEqual(["Day", "Week", "Month", "All"]);
    within(group).getByRole("radio", { name: "Day" }).click();
    await waitFor(() => expect(window.location.hash).toBe("#hz=day&tier=All&gpu=H100%20%C3%971"));
    expect(within(group).getByRole("radio", { name: "Day" }).getAttribute("aria-checked")).toBe(
      "true",
    );
    expect(JSON.parse(window.localStorage.getItem("ftg.v2.view") ?? "{}").hz).toBe("day");
    cleanup();
    render(<App source={new StubDataSource()} />);
    expect(
      within(screen.getByRole("radiogroup", { name: "Horizon" }))
        .getByRole("radio", { name: "Day" })
        .getAttribute("aria-checked"),
    ).toBe("true");
  });

  it("opens a hash with a day page", async () => {
    window.history.replaceState(null, "", "#hz=day&page=2&tier=All&gpu=H100%20%C3%971");
    render(<App source={new StubDataSource()} />);
    await waitFor(() => expect(window.location.hash).toContain("hz=day"));
    expect(DEFAULT_STATE.hz).toBe("all");
  });
});
