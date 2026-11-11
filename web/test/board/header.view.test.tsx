// @vitest-environment jsdom
import { readFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { afterEach, describe, expect, it } from "vitest";
import type { BoardData, Meta, ViewState } from "../../src/board/derive/types";
import { DEFAULT_STATE } from "../../src/board/derive/viewstate";
import { HeaderView } from "../../src/board/view/header";
import { IntroView } from "../../src/board/view/intro";
import { stubBoardData } from "../../src/data/stub";

afterEach(cleanup);

const meta: Meta = JSON.parse(
  readFileSync(join(resolve(__dirname, "../fixtures/board"), "events.json"), "utf8"),
).meta;
const data: BoardData = { ...stubBoardData(), meta };

function Harness({ initial, loaded }: { initial: ViewState; loaded: boolean }) {
  const [state, setState] = useState(initial);
  return (
    <>
      <output data-testid="state">{JSON.stringify(state)}</output>
      <HeaderView data={loaded ? data : null} state={state} setState={setState} />
    </>
  );
}

const stateText = () => JSON.parse(screen.getByTestId("state").textContent ?? "{}");

describe("header view", () => {
  it("keeps each label in one group element with its control", () => {
    render(<Harness initial={DEFAULT_STATE} loaded={false} />);
    const pairs: [string, HTMLElement][] = [
      ["Horizon", screen.getByRole("radiogroup", { name: "Horizon" })],
      ["Tier", screen.getByLabelText("Tier")],
      ["Focus", screen.getByLabelText("Machine")],
    ];
    for (const [text, control] of pairs) {
      const label = screen.getByText(text);
      const group = label.closest(".hdr-group");
      expect(group, `${text} label has a group`).not.toBeNull();
      expect(group?.contains(control), `${text} control is in the label's group`).toBe(true);
    }
  });

  it("keeps the pager and its label in one no-wrap group", () => {
    render(<Harness initial={DEFAULT_STATE} loaded={false} />);
    const earlier = screen.getByRole("button", { name: "Earlier" });
    const group = earlier.closest(".hdr-group");
    expect(group).not.toBeNull();
    expect(group?.contains(screen.getByRole("button", { name: "Later" }))).toBe(true);
    expect(group?.querySelector(".pager-label")).not.toBeNull();
  });

  it("renders the controls before the data arrives", () => {
    render(<Harness initial={DEFAULT_STATE} loaded={false} />);
    expect(screen.getByRole("radiogroup", { name: "Horizon" })).toBeTruthy();
    expect(screen.getByRole("radio", { name: "All" }).getAttribute("aria-checked")).toBe("true");
    expect(screen.getByLabelText("Tier")).toBeTruthy();
    expect(screen.getByLabelText("Machine")).toBeTruthy();
    expect((screen.getByRole("button", { name: "Earlier" }) as HTMLButtonElement).disabled).toBe(
      true,
    );
  });

  it("draws the span and the pager, and a pick of the horizon clears the page", () => {
    render(<Harness initial={{ ...DEFAULT_STATE, hz: "week" }} loaded />);
    expect(
      screen.getByText("Oct 13 – Nov 3, 2026 · 22 days · 15,538 probe rows · 3 weekends"),
    ).toBeTruthy();
    expect(screen.getByText("Week of Mon Oct 26 (ET)")).toBeTruthy();
    const later = screen.getByRole("button", { name: "Later" }) as HTMLButtonElement;
    expect(later.disabled).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "Earlier" }));
    expect(stateText().page).toBe(1);
    expect(screen.getByText("Week of Mon Oct 19 (ET)")).toBeTruthy();
    fireEvent.click(screen.getByRole("radio", { name: "Month" }));
    expect(stateText()).toMatchObject({ hz: "month", page: null });
    expect(screen.getByRole("radio", { name: "Month" }).getAttribute("aria-checked")).toBe("true");
  });

  it("snaps the focus machine when the tier changes", () => {
    render(<Harness initial={DEFAULT_STATE} loaded />);
    const tier = screen.getByLabelText("Tier") as HTMLSelectElement;
    expect([...tier.options].map((o) => o.value)).toEqual([
      "All",
      "Frontier node",
      "Single big GPU",
      "Mid · 48 GB",
      "Small · 16–24 GB",
    ]);
    fireEvent.change(tier, { target: { value: "Small · 16–24 GB" } });
    expect(stateText()).toMatchObject({ tier: "Small · 16–24 GB", gpu: "A10G" });
    const machine = screen.getByLabelText("Machine") as HTMLSelectElement;
    expect([...machine.options].map((o) => o.value)).toEqual(["A10G", "A10", "L4", "T4"]);
    expect(machine.title).toMatch(/^Focus drives the substitutes panel/);
    fireEvent.change(machine, { target: { value: "T4" } });
    expect(stateText().gpu).toBe("T4");
  });

  it("keeps the stylesheet focus ring: no inline unset or outline", () => {
    const { container } = render(
      <>
        <Harness initial={DEFAULT_STATE} loaded />
        <IntroView data={data} state={DEFAULT_STATE} />
      </>,
    );
    for (const el of container.querySelectorAll("[style]")) {
      const style = el.getAttribute("style") ?? "";
      expect(style).not.toMatch(/all:\s*unset/);
      expect(style).not.toMatch(/outline/);
    }
    const css = readFileSync(resolve(__dirname, "../../src/board/styles.css"), "utf8");
    expect(css).toMatch(/:focus-visible\s*{[^}]*outline:\s*2px solid/);
    // Every control is a native, focusable element.
    for (const el of container.querySelectorAll("button, select")) {
      expect((el as HTMLElement).tabIndex).toBeGreaterThanOrEqual(0);
    }
  });
});

describe("intro view", () => {
  it("renders only the labelled frame for a window with no sweep", () => {
    const { container } = render(<IntroView data={data} state={DEFAULT_STATE} />);
    expect(container.querySelector("section")?.childElementCount).toBe(0);
  });

  it("draws the intro, the groups, and the chips", () => {
    const sweep = {
      h: 0,
      t: 0,
      cloud: "aws",
      region: "us-east-1",
      sku: "g4dn.xlarge",
      probe: "x",
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
    const withSweep: BoardData = { ...data, window: { ...data.window, sweeps: [sweep] } };
    render(<IntroView data={withSweep} state={{ ...DEFAULT_STATE, gpu: "T4" }} />);
    expect(screen.getByText(/^Every 2 hours we ask AWS and Azure, in 11 US regions/)).toBeTruthy();
    expect(screen.getByText(/A yes means the cloud actually handed us the capacity/)).toBeTruthy();
  });
});
