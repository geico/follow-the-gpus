// @vitest-environment jsdom
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { makeFrame } from "../../src/board/derive/time";
import type { Horizon } from "../../src/board/derive/types";
import { App } from "../../src/board/view/app";
import { SECTIONS } from "../../src/board/view/sections";
import { HttpDataSource } from "../../src/data/source";
import { StubDataSource } from "../../src/data/stub";

beforeEach(() => {
  window.localStorage.clear();
  window.history.replaceState(null, "", "#");
});
afterEach(cleanup);

describe("the board on the stub source", () => {
  it("renders the header, the controls, and 15 empty labelled sections", async () => {
    const { container } = render(<App source={new StubDataSource()} />);
    expect(screen.getByText("follow-the-gpus")).toBeTruthy();
    expect(screen.getByRole("radiogroup", { name: "Horizon" })).toBeTruthy();
    expect(screen.getByRole("combobox", { name: "Tier" })).toBeTruthy();
    expect(screen.getByRole("combobox", { name: "Machine" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Earlier" })).toBeTruthy();
    await waitFor(() => expect(container.querySelector("[aria-busy]")).toBeNull());
    const names = [...container.querySelectorAll("section[data-section]")].map((e) =>
      e.getAttribute("data-section"),
    );
    expect(names).toEqual(["header", ...SECTIONS.map((s) => s.name)]);
    // Section 11.9 reads meta.now, so on the stub it still lists the machines as "Not asked".
    for (const s of container.querySelectorAll(
      "section[data-section]:not([data-section=header]):not([data-section=now])",
    )) {
      expect(s.childElementCount).toBe(0);
      expect(s.getAttribute("aria-label")).toBeTruthy();
    }
  });

  it("keeps the view state in the hash and localStorage", async () => {
    render(<App source={new StubDataSource()} />);
    const group = screen.getByRole("radiogroup", { name: "Horizon" });
    within(group).getByRole("radio", { name: "Week" }).click();
    await waitFor(() => expect(window.location.hash).toBe("#hz=week&tier=All&gpu=H100%20%C3%971"));
    expect(JSON.parse(window.localStorage.getItem("ftg.v2.view") ?? "{}").hz).toBe("week");
  });

  it("does not crash when localStorage throws", async () => {
    const get = vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("denied");
    });
    const set = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("denied");
    });
    try {
      render(<App source={new StubDataSource()} />);
      expect(screen.getByText("follow-the-gpus")).toBeTruthy();
      await waitFor(() => expect(window.location.hash).toContain("hz=all"));
    } finally {
      get.mockRestore();
      set.mockRestore();
    }
  });

  it("resets the page when a hash change changes the horizon", async () => {
    window.history.replaceState(null, "", "#hz=week&page=2&tier=All&gpu=H100%20%C3%971");
    render(<App source={new StubDataSource()} />);
    await waitFor(() => expect(window.location.hash).toContain("page=2"));
    window.history.replaceState(null, "", "#hz=month&tier=All&gpu=H100%20%C3%971");
    window.dispatchEvent(new HashChangeEvent("hashchange"));
    await waitFor(() => expect(window.location.hash).toBe("#hz=month&tier=All&gpu=H100%20%C3%971"));
    expect(window.location.hash).not.toContain("page=");
  });

  it("opens the default page for a hash with a new hz and no page, over a stored page", async () => {
    window.localStorage.setItem(
      "ftg.v2.view",
      JSON.stringify({ hz: "month", page: 1, tier: "All", gpu: "H100 ×1" }),
    );
    window.history.replaceState(null, "", "#hz=week&tier=All&gpu=H100%20%C3%971");
    render(<App source={new StubDataSource()} />);
    await waitFor(() => expect(window.location.hash).toContain("hz=week"));
    expect(window.location.hash).not.toContain("page=");
  });

  it("shows the empty-store line when the store has no rows", async () => {
    render(<App source={new StubDataSource({ t0: null, simulated: false })} />);
    await screen.findByText("No rows in this store. Run make data/sync, then make web/run.");
  });

  it("shows the path and the status of a failed request", async () => {
    const failing = new HttpDataSource("./", async () => ({
      ok: false,
      status: 500,
      json: async () => ({}),
    }));
    render(<App source={failing} />);
    await screen.findByText("api/meta.json 500");
  });
});

describe("the default page", () => {
  const base = JSON.parse(
    readFileSync(resolve(__dirname, "../fixtures/board/events.json"), "utf8"),
  ).meta;
  // 10 weeks of data, so that the default page of week and of month is past page 0.
  const tEnd = Date.parse(base.t0) + 70 * 24 * 3600 * 1000;
  const meta = { ...base, tEnd: new Date(tEnd).toISOString(), nh: 70 * 24 };
  for (const hz of ["week", "month"] as const) {
    it(`fetches the default page, not page 0, at ${hz} with no page in the state`, async () => {
      const stub = new StubDataSource(meta);
      const asked: number[] = [];
      const source = {
        meta: () => stub.meta(),
        window: (h: Horizon, page: number) => {
          asked.push(page);
          return stub.window(h, page);
        },
      };
      window.history.replaceState(null, "", `#hz=${hz}&tier=All&gpu=H100%20%C3%971`);
      const { container } = render(<App source={source} />);
      await waitFor(() => expect(container.querySelector("[aria-busy]")).toBeNull());
      const f = makeFrame(meta, hz, null);
      expect(f.page).toBeGreaterThan(0);
      // The header frames the default page; the page-0-only sections frame the fetched page.
      expect(asked.at(-1)).toBe(f.page);
      expect(container.querySelector("section[data-section=header]")?.textContent).toContain(
        f.pageLabel,
      );
    });
  }
});

describe("a page outside the page list", () => {
  const base = JSON.parse(
    readFileSync(resolve(__dirname, "../fixtures/board/events.json"), "utf8"),
  ).meta;
  const tEnd = Date.parse(base.t0) + 70 * 24 * 3600 * 1000;
  const meta = { ...base, tEnd: new Date(tEnd).toISOString(), nh: 70 * 24 };
  for (const bad of [99, -1]) {
    it(`fetches a valid page and keeps the pager working with page ${bad} at week`, async () => {
      const stub = new StubDataSource(meta);
      const asked: number[] = [];
      const source = {
        meta: () => stub.meta(),
        window: (h: Horizon, page: number) => {
          asked.push(page);
          return stub.window(h, page);
        },
      };
      window.localStorage.setItem(
        "ftg.v2.view",
        JSON.stringify({ hz: "week", page: bad, tier: "All", gpu: "H100 ×1" }),
      );
      const { container } = render(<App source={source} />);
      await waitFor(() => expect(container.querySelector("[aria-busy]")).toBeNull());
      // The stored state may drop a negative page; either way the page is valid.
      const f = makeFrame(meta, "week", bad > 0 ? bad : null);
      expect(f.page).toBeGreaterThanOrEqual(0);
      expect(asked.at(-1)).toBe(f.page);
      const header = container.querySelector("section[data-section=header]");
      expect(header?.textContent).toContain(f.pageLabel);
      const earlier = screen.getByRole("button", { name: "Earlier" }) as HTMLButtonElement;
      expect(earlier.disabled).toBe(false);
      // The hash shows the clamped page, not the requested one.
      if (bad > 0) {
        await waitFor(() => expect(window.location.hash).toContain(`page=${f.page}`));
        expect(window.location.hash).not.toContain(`page=${bad}`);
      }
      earlier.click();
      await waitFor(() => expect(asked.at(-1)).toBe(f.page - 1));
      await waitFor(() => expect(container.querySelector("[aria-busy]")).toBeNull());
      expect(header?.textContent).toContain(makeFrame(meta, "week", f.page - 1).pageLabel);
    });
  }
});

describe("the board section container", () => {
  it("keeps the section order and puts sections 11.14 to 11.16 in one 3-up group", async () => {
    const { container } = render(<App source={new StubDataSource()} />);
    await waitFor(() => expect(container.querySelector("[aria-busy]")).toBeNull());
    const groups = container.querySelectorAll("[data-group=three-up]");
    expect(groups.length).toBe(1);
    expect(
      [...(groups[0] as Element).querySelectorAll("section[data-section]")].map((e) =>
        e.getAttribute("data-section"),
      ),
    ).toEqual(["howmany", "substitutes", "bookahead"]);
    const main = container.querySelector("main.board") as Element;
    const top = [...main.children].map(
      (e) => e.getAttribute("data-section") ?? e.getAttribute("data-group"),
    );
    expect(top).toEqual([
      "header",
      "intro",
      "now",
      "sowhat",
      "stickiness",
      "backtest",
      "trend",
      "three-up",
      "sawtooth",
      "timeofday",
      "forecast",
      "price",
      "matrix",
      "definitions",
    ]);
  });
});
