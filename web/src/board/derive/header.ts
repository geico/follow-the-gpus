// Section "Header" (spec 0006 section 11.7): the title, the span caption, the horizon group, the
// pager, and the tier and focus options. Pure: no DOM, no React.

import { machinesOfTier, TIERS } from "./catalogue";
import { count, dateLabel, plural } from "./format";
import { makeFrame } from "./time";
import type { BoardData, EmptySection, Horizon, ViewState } from "./types";

export const FOCUS_TITLE =
  "Focus drives the substitutes panel, the time-of-day grid and the highlighted row elsewhere";

const HORIZONS: readonly { readonly value: Horizon; readonly label: string }[] = [
  { value: "day", label: "Day" },
  { value: "week", label: "Week" },
  { value: "month", label: "Month" },
  { value: "all", label: "All" },
];

export type HeaderOption = { readonly value: string; readonly label: string };

/** The controls that need no data, so that they render before the data arrives (section 11.6). */
export type HeaderControls = {
  readonly horizons: readonly {
    readonly value: Horizon;
    readonly label: string;
    readonly selected: boolean;
  }[];
  readonly tiers: readonly HeaderOption[];
  readonly machines: readonly HeaderOption[];
  readonly focusTitle: string;
};

export function deriveControls(state: ViewState): HeaderControls {
  return {
    horizons: HORIZONS.map((h) => ({ ...h, selected: h.value === state.hz })),
    tiers: [{ value: "All", label: "All machines" }, ...TIERS.map((t) => ({ value: t, label: t }))],
    machines: machinesOfTier(state.tier).map((m) => ({ value: m, label: m })),
    focusTitle: FOCUS_TITLE,
  };
}

export type HeaderFull = HeaderControls & {
  readonly section: "header";
  readonly empty: false;
  readonly title: string;
  /** "Oct 13 – Nov 3, 2026 · 22 days · 15,538 probe rows · 3 weekends". */
  readonly span: string;
  readonly pageLabel: string;
  /** The 0-based index of the page shown (the default page when the state names none). */
  readonly page: number;
  readonly noPrev: boolean;
  readonly noNext: boolean;
};

export type HeaderResult = EmptySection<"header"> | HeaderFull;

export function deriveHeader(data: BoardData, state: ViewState): HeaderResult {
  const { meta } = data;
  const f = makeFrame(meta, state.hz, state.page);
  const t0 = Date.parse(meta.t0);
  const tEnd = Date.parse(meta.tEnd);
  const days = meta.dates.length;
  const w = meta.weekends;
  const span =
    `${dateLabel(t0)} – ${dateLabel(tEnd)}, ${new Date(t0).getUTCFullYear()}` +
    ` · ${plural(days, "day")} · ${count(meta.rows)} probe rows · ${plural(w, "weekend")}`;
  return {
    section: "header",
    empty: false,
    title: "follow-the-gpus",
    span,
    ...deriveControls(state),
    pageLabel: f.pageLabel,
    page: f.page,
    noPrev: f.noPrev,
    noNext: f.noNext,
  };
}
