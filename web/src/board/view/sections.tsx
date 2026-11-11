// The sections below the header, top to bottom (spec 0006 section 11). The list is fixed here so
// that a section task edits only its own derive, view, and test files.

import type { ComponentType, ReactNode } from "react";
import { BacktestView } from "./backtest";
import { BookaheadView } from "./bookahead";
import { DefinitionsView } from "./definitions";
import { ForecastView } from "./forecast";
import { HowmanyView } from "./howmany";
import { IntroView } from "./intro";
import { MatrixView } from "./matrix";
import { NowView } from "./now";
import { PriceView } from "./price";
import type { SectionProps } from "./props";
import { SawtoothView } from "./sawtooth";
import { SowhatView } from "./sowhat";
import { StickinessView } from "./stickiness";
import { SubstitutesView } from "./substitutes";
import { TimeofdayView } from "./timeofday";
import { TrendView } from "./trend";

export const SECTIONS: readonly {
  readonly name: string;
  readonly View: ComponentType<SectionProps>;
}[] = [
  { name: "intro", View: IntroView },
  { name: "now", View: NowView },
  { name: "sowhat", View: SowhatView },
  { name: "stickiness", View: StickinessView },
  { name: "backtest", View: BacktestView },
  { name: "trend", View: TrendView },
  { name: "howmany", View: HowmanyView },
  { name: "substitutes", View: SubstitutesView },
  { name: "bookahead", View: BookaheadView },
  { name: "sawtooth", View: SawtoothView },
  { name: "timeofday", View: TimeofdayView },
  { name: "forecast", View: ForecastView },
  { name: "price", View: PriceView },
  { name: "matrix", View: MatrixView },
  { name: "definitions", View: DefinitionsView },
];

/** The sections that share the 3-up row (spec 0006 sections 11.14 to 11.16). */
export const THREE_UP: readonly string[] = ["howmany", "substitutes", "bookahead"];

/** Renders every section in order, with the 3-up sections in one grid of 3 equal columns. */
export function renderSections(render: (s: (typeof SECTIONS)[number]) => ReactNode): ReactNode[] {
  const out: ReactNode[] = [];
  let row: ReactNode[] = [];
  for (const s of SECTIONS) {
    if (THREE_UP.includes(s.name)) {
      row.push(render(s));
      if (row.length === THREE_UP.length) {
        out.push(
          <div key="three-up" className="three-up" data-group="three-up">
            {row}
          </div>,
        );
        row = [];
      }
    } else out.push(render(s));
  }
  return out;
}
