// Section "Substitutes" (spec 0006 section 11.15): when the focus machine had no yes in a round,
// how often did some other machine of the catalogue have one.

import { aggregateAll } from "./aggregate";
import { MACHINES } from "./catalogue";
import { plural, ps, sh, tone } from "./format";
import { hasWindowSweep } from "./inwindow";
import { joinData } from "./join";
import { makeFrame } from "./time";
import type { BoardData, EmptySection, ViewState } from "./types";

/** The rows that the board shows. */
export const SUBS_SHOWN = 6;

export const SUBS_FOOTER =
  "A substitute is a different machine. For restartable or small jobs this is the real menu at the moment you were told no.";

export type SubRow = {
  readonly name: string;
  /** Bar width in percent; a missing share is 0. */
  readonly w: number;
  /** The label: `ps` of the share, or "–". */
  readonly big: string;
  /** The share in percent; null when the machine was not asked in any no round. */
  readonly pp: number | null;
  /** The colour token name of the label (`tone`). */
  readonly tone: string;
};

export type SubstitutesData = {
  readonly section: "substitutes";
  readonly empty: false;
  /** The section title: "When {gpu} said no, what said yes?". */
  readonly title: string;
  readonly gpu: string;
  /** The number of rounds when the focus machine's `src` is 0. */
  readonly noRounds: number;
  readonly intro: string;
  readonly rows: readonly SubRow[];
  readonly footer: string;
};

/** The result of the section, read by the "so what" card as `e.subs`. */
export type SubstitutesResult = EmptySection<"substitutes"> | SubstitutesData;

export function deriveSubstitutes(data: BoardData, state: ViewState): SubstitutesResult {
  if (!hasWindowSweep(data)) return { section: "substitutes", empty: true };
  const f = makeFrame(data.meta, data.window.hz, data.window.page);
  const aggs = aggregateAll(joinData(data), f);
  const gpu = state.gpu;
  const focus = aggs.find((a) => a.machine.name === gpu);
  const noIdx: number[] = [];
  focus?.src.forEach((c, r) => {
    if (c === 0) noIdx.push(r);
  });

  const order = MACHINES.map((m) => m.name);
  const rows = aggs
    .filter((a) => a.machine.name !== gpu && a.mode === "now")
    .map((a) => {
      let asked = 0;
      let yes = 0;
      for (const r of noIdx) {
        const c = a.src[r] ?? null;
        if (c === null || c === -1) continue;
        asked += 1;
        if (c > 0) yes += 1;
      }
      return { name: a.machine.name, pp: sh(yes, asked) };
    })
    .sort((a, b) => (b.pp ?? 0) - (a.pp ?? 0) || order.indexOf(a.name) - order.indexOf(b.name))
    .slice(0, SUBS_SHOWN)
    .map(({ name, pp }): SubRow => ({ name, w: pp ?? 0, big: ps(pp), pp, tone: tone(pp) }));

  const intro =
    focus === undefined || focus.mode !== "now"
      ? `${gpu} has no on-demand asks in this window, so there is nothing to substitute for.`
      : noIdx.length > 0
        ? `In the ${plural(noIdx.length, "round")} when no place handed us a ${gpu}, how often did some place hand us one of these instead? Share of those rounds.`
        : `${gpu} never had a round with no yes, so there was nothing to substitute.`;

  return {
    section: "substitutes",
    empty: false,
    title: `When ${gpu} said no, what said yes?`,
    gpu,
    noRounds: noIdx.length,
    intro,
    rows,
    footer: SUBS_FOOTER,
  };
}
