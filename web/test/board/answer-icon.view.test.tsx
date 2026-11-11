// @vitest-environment jsdom
// Spec 0006 section 11.9, decision R12: the 6 icons are bundled, aria-hidden, sized and coloured by props.

import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { ANSWER_ICONS, AnswerIcon, type AnswerIconName } from "../../src/components/AnswerIcon";

afterEach(cleanup);

const names = Object.keys(ANSWER_ICONS) as AnswerIconName[];

describe("AnswerIcon", () => {
  it("has the 6 icons of the design", () => {
    expect(names.sort()).toEqual(
      ["calendar-blank", "check-circle", "clock", "lock-simple", "minus-circle", "x-circle"].sort(),
    );
  });

  for (const name of names) {
    it(`draws ${name}, aria-hidden, with size and colour from props`, () => {
      const { container } = render(<AnswerIcon name={name} size={17} color="rgb(1, 2, 3)" />);
      const svg = container.querySelector(`svg[data-icon="${name}"]`);
      expect(svg).not.toBeNull();
      expect(svg?.getAttribute("aria-hidden")).toBe("true");
      expect(svg?.getAttribute("width")).toBe("17");
      expect(svg?.getAttribute("height")).toBe("17");
      expect(svg?.getAttribute("fill")).toBe("rgb(1, 2, 3)");
      expect(svg?.querySelector("path")).not.toBeNull();
    });
  }

  it("fills check-circle and draws the others regular", () => {
    const d = (el: React.ReactElement) => {
      const { container, unmount } = render(el);
      const v = container.querySelector("path")?.getAttribute("d");
      unmount();
      return v;
    };
    for (const n of names) {
      const Icon = ANSWER_ICONS[n];
      const want = n === "check-circle" ? "fill" : "regular";
      expect(d(<AnswerIcon name={n} size={16} color="red" />)).toBe(d(<Icon weight={want} />));
    }
    const C = ANSWER_ICONS["check-circle"];
    expect(d(<C weight="fill" />)).not.toBe(d(<C weight="regular" />));
  });

  it("holds no URL except the SVG namespace, so it requests nothing", () => {
    const { container } = render(
      <>
        {names.map((n) => (
          <AnswerIcon key={n} name={n} size={16} color="red" />
        ))}
      </>,
    );
    const urls = container.innerHTML.match(/https?:\/\/[^\s"'<]+/g) ?? [];
    expect(urls.filter((u) => !u.startsWith("http://www.w3.org/"))).toEqual([]);
    expect(container.querySelectorAll("image, use, style, link")).toHaveLength(0);
  });
});
