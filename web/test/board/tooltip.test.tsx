// @vitest-environment jsdom
import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { GlobalTooltip, useTip } from "../../src/board/view/tooltip";

afterEach(cleanup);

function Probe() {
  const tip = useTip();
  return (
    <button
      type="button"
      onClick={(e) => tip.show(e, { title: "t", lines: ["same", "same", "other"] })}
    >
      go
    </button>
  );
}

describe("the global tooltip", () => {
  it("gives 2 lines with the same text distinct keys", async () => {
    const err = vi.spyOn(console, "error").mockImplementation(() => {});
    const { getByText, findAllByText } = render(
      <GlobalTooltip>
        <Probe />
      </GlobalTooltip>,
    );
    getByText("go").click();
    expect((await findAllByText("same")).length).toBeGreaterThan(0);
    const dup = err.mock.calls.filter((c) => String(c[0]).includes("same key"));
    err.mockRestore();
    expect(dup).toEqual([]);
  });

  it("takes no pointer events on its anchor or its box, so it never takes the hover from a cell", async () => {
    const { getByText, findAllByText } = render(
      <GlobalTooltip>
        <Probe />
      </GlobalTooltip>,
    );
    getByText("go").click();
    await findAllByText("other");
    const anchor = document.querySelector<HTMLElement>("[data-slot=tooltip-trigger]");
    expect(anchor?.style.pointerEvents).toBe("none");
    const box = document.querySelector<HTMLElement>("[data-slot=tooltip-content]");
    expect(box?.className).toContain("pointer-events-none");
  });
});
