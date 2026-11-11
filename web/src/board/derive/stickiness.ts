// Section "Is capacity sticky? What the last round tells you about the next" (spec 0006 section
// 11.11). The `StickyRow` list is also the input of the "so what" retry text (section 11.10 item 3).

import { aggregateAll, type Cell } from "./aggregate";
import { machinesOfTier } from "./catalogue";
import { hrs, plural, ps, sh } from "./format";
import { hasWindowSweep } from "./inwindow";
import { joinData } from "./join";
import { makeFrame } from "./time";
import type { BoardData, EmptySection, ViewState } from "./types";

export const STICKY_INTRO =
  "For consecutive rounds in view: how often the next round had a yes somewhere, split by whether the previous round did. A big gap means your own last probe is the best forecast you have.";

export const STICKY_HEADS = {
  afterYes: "yes again, after a yes",
  afterNo: "yes, after a no",
  streak: "yes-streak median · p90",
} as const;

export const STICKY_NONE = "Not enough consecutive rounds in view to measure stickiness.";

export type StickyRow = {
  readonly name: string;
  /** "{ps(a)}" of the next rounds after a yes; "–" when n is 0. */
  readonly a: string;
  readonly an: number;
  /** The same after a no. */
  readonly b: string;
  readonly bn: number;
  /** P(yes after yes) and P(yes after no) in percent, unrounded; null when n is 0. */
  readonly aP: number | null;
  readonly bP: number | null;
  /** Bar widths in percent (0 when empty). */
  readonly aW: number;
  readonly bW: number;
  /** Yes-streak median and p90, printed with `hrs`; "–" when there is no streak. */
  readonly streak: string;
  readonly streakP90: string;
  /** The streak cell caption "{streak} · {p90}"; "–" when there is no streak. */
  readonly streakText: string;
  /** Yes-streak median in hours; null when there is no streak. */
  readonly streakH: number | null;
  /** a − b in points; null when either n is 0. */
  readonly gap: number | null;
  readonly tip: { readonly title: string; readonly lines: readonly string[] };
};

export type StickinessResult =
  | EmptySection<"stickiness">
  | {
      readonly section: "stickiness";
      readonly empty: false;
      readonly intro: string;
      readonly heads: typeof STICKY_HEADS;
      readonly rows: readonly StickyRow[];
      readonly verdict: string;
      /** The round length in hours, printed in the verdict. */
      readonly cadH: number;
    };

/** The counts and streaks of a series (section 11.11 rules). */
export function stickCounts(src: readonly Cell[]) {
  let an = 0;
  let ay = 0;
  let bn = 0;
  let by = 0;
  const runs: number[] = [];
  let run = 0;
  let prev: Cell = null;
  let prevIdx = -2;
  src.forEach((c, i) => {
    if (c === null || c === -1) return;
    if (prev !== null && prevIdx === i - 1) {
      if (prev > 0) {
        an += 1;
        if (c > 0) ay += 1;
      } else {
        bn += 1;
        if (c > 0) by += 1;
      }
    }
    if (c > 0) run += 1;
    else if (run > 0) {
      runs.push(run);
      run = 0;
    }
    prev = c;
    prevIdx = i;
  });
  if (run > 0) runs.push(run);
  return { an, ay, bn, by, runs };
}

export function deriveStickiness(data: BoardData, state: ViewState): StickinessResult {
  if (!hasWindowSweep(data)) return { section: "stickiness", empty: true };
  const d = joinData(data);
  const f = makeFrame(data.meta, state.hz, state.page);
  const gl = machinesOfTier(state.tier);
  const rows: StickyRow[] = [];
  for (const agg of aggregateAll(d, f)) {
    if (!gl.includes(agg.machine.name) || agg.mode !== "now" || agg.counts.tried < 6) continue;
    const { an, ay, bn, by, runs } = stickCounts(agg.src);
    const aP = sh(ay, an);
    const bP = sh(by, bn);
    const sorted = [...runs].sort((x, y) => x - y);
    const med = sorted.length > 0 ? (sorted[Math.floor(sorted.length / 2)] ?? 0) : null;
    const p90 = sorted.length > 0 ? (sorted[Math.floor(0.9 * sorted.length)] ?? 0) : null;
    const streak = med === null ? "–" : hrs(med * f.cad);
    const streakP90 = p90 === null ? "–" : hrs(p90 * f.cad);
    rows.push({
      name: agg.machine.name,
      a: ps(aP),
      an,
      b: ps(bP),
      bn,
      aP,
      bP,
      aW: aP ?? 0,
      bW: bP ?? 0,
      streak,
      streakP90,
      streakText: med === null ? "–" : `${streak} · ${streakP90}`,
      streakH: med === null ? null : med * f.cad,
      gap: aP !== null && bP !== null ? aP - bP : null,
      tip: {
        title: `${agg.machine.name} · capacity is sticky?`,
        lines: [
          `After a yes: ${ay} of ${plural(an, "next round")} ${an === 1 ? "was" : "were"} also a yes`,
          `After a no: ${by} of ${plural(bn, "next round")} ${bn === 1 ? "was" : "were"} a yes`,
          `Yes-streaks: median ${streak}, p90 ${streakP90}`,
        ],
      },
    });
  }
  let verdict: string;
  if (rows.length === 0) verdict = STICKY_NONE;
  else {
    const names = rows.filter((r) => r.gap !== null && r.gap >= 20).map((r) => r.name);
    verdict = "";
    if (names.length > 0) {
      verdict += `Your last probe is the best forecast you have for ${names.join(", ")}: a yes ${hrs(f.cad)} ago is worth far more than any published score. `;
    }
    verdict += `If you were just told no, the odds ${hrs(f.cad)} later are the right-hand number — plan the retry interval from that, not from hope.`;
  }
  return {
    section: "stickiness",
    empty: false,
    intro: STICKY_INTRO,
    heads: STICKY_HEADS,
    rows,
    verdict,
    cadH: f.cad,
  };
}
