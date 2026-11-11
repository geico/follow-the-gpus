// Section "How many at once?" (spec 0006 section 11.14): per machine, the share of asks that
// handed us at least 1, 2, 4, 8, 16, or 32 machines in one go. Pure: no DOM, no React.

import { MACHINES, type Machine, machinesOfTier } from "./catalogue";
import { dateLabel, ps, sh } from "./format";
import { joinData } from "./join";
import { hourInstant, makeFrame } from "./time";
import type { BoardData, EmptySection, JoinedSweep, ViewState } from "./types";

/** The rungs of the ladder: 1, 2, 4, 8, 16, 32 (section 11.14). */
const RUNGS = [1, 2, 4, 8, 16, 32] as const;

/** The least height of a bar with a count above 0, in percent. */
const MIN_BAR = 3;

const NOTE =
  "After each full yes we ask again for double, up to a ceiling. Bars: share of asks (one per place per round) that handed us at least that many machines in one go.";

export type HowmanyRung = {
  readonly label: number;
  /** The bar height in percent of the row (0 to 100). */
  readonly h: number;
  readonly title: string;
  readonly line: string;
};

export type HowmanyRow = {
  readonly name: string;
  /** True for the focus machine: its name takes the accent colour token. */
  readonly focus: boolean;
  readonly sub: string;
  readonly rungs: readonly HowmanyRung[];
};

export type HowmanyFull = {
  readonly section: "howmany";
  readonly empty: false;
  readonly note: string;
  readonly rows: readonly HowmanyRow[];
};

export type HowmanyResult = EmptySection<"howmany"> | HowmanyFull;

function rowOf(m: Machine, sweeps: readonly JoinedSweep[], focus: string): HowmanyRow | null {
  const types = [...m.aws, ...m.azure];
  const sw = sweeps.filter((s) => s.state >= 0 && types.includes(s.sku));
  if (sw.length === 0) return null;
  const asks = sw.length;
  const largest = Math.max(...sw.map((s) => s.nMax));
  const top = Math.max(largest, ...sw.map((s) => s.asked));
  const rungs = RUNGS.filter((n) => n <= top).map((n) => {
    const k = sw.filter((s) => s.nMax >= n).length;
    const share = sh(k, asks);
    return {
      label: n,
      h: k > 0 ? Math.max(MIN_BAR, share ?? 0) : 0,
      title: `${m.name} · at least ${n} machine${n === 1 ? "" : "s"} at once`,
      line: `${k} of ${asks} asks handed us at least ${n} · ${ps(share)}`,
    };
  });
  return {
    name: m.name,
    focus: m.name === focus,
    sub: `${asks} asks · most at once: ${largest}`,
    rungs,
  };
}

export function deriveHowmany(data: BoardData, state: ViewState): HowmanyResult {
  const d = joinData(data);
  const f = makeFrame(d.meta, d.hz, d.page);
  const inWindow = d.sweeps.filter((s) => s.h >= f.H0 && s.h < f.H1);
  if (inWindow.length === 0) return { section: "howmany", empty: true };

  let note = NOTE;
  const up = d.meta.upStartH;
  if (up !== null) {
    const date = dateLabel(hourInstant(f, up));
    if (!inWindow.some((s) => s.nMax > s.asked)) {
      note += ` Doubling only began ${date}, after this window: every round here asked the base size.`;
    } else if (up > f.H0) {
      note += ` Doubling began ${date}, so earlier rounds in view only asked the base size.`;
    }
  }

  const gl = new Set(machinesOfTier(state.tier));
  const rows: HowmanyRow[] = [];
  for (const m of MACHINES) {
    if (!gl.has(m.name)) continue;
    const row = rowOf(m, inWindow, state.gpu);
    if (row !== null) rows.push(row);
  }
  return { section: "howmany", empty: false, note, rows };
}
