// Section "Intro and headline chips" (spec 0006 section 11.8). Pure: no DOM, no React.

import { aggregateAll, type MachineAgg } from "./aggregate";
import { machinesOfTier, TIER_SUBS } from "./catalogue";
import { dateLabel, hrs, plural, ps } from "./format";
import { hasWindowData } from "./inwindow";
import { joinData } from "./join";
import { makeFrame } from "./time";
import type { BoardData, EmptySection, ViewState } from "./types";

const DAY = 86_400_000;
const HOUR = 3_600_000;

export const ALL_SUB =
  "Rows are machines, not chips: an 8-GPU node and a single card are different products with different buyers.";

export const FOOTNOTE =
  "A yes means the cloud actually handed us the capacity — never a forecast, never a listing. Definitions at the bottom. Every caption on this board is computed from the data in view, not written for one run.";

export const SIMULATED_BANNER = "No real rows in this store. Showing simulated rows.";

export type IntroChip = {
  readonly name: string;
  /** True for the focus machine: its name takes the accent colour token. */
  readonly focus: boolean;
  readonly big: string;
  /** The colour token of the big number. */
  readonly bigColor: string;
  readonly sub: string;
  /** The bar width in percent; the bar shows on rate chips only. */
  readonly w: number;
  readonly barOn: boolean;
};

export type IntroGroup = {
  readonly tag: string;
  /** The colour token of the tag. */
  readonly tagColor: string;
  readonly chips: readonly IntroChip[];
};

export type IntroFull = {
  readonly section: "intro";
  readonly empty: false;
  /** The banner line for simulated rows, or null. */
  readonly banner: string | null;
  /** "Every <cadence> hours we ask ... Showing 6 days, Oct 13 – Oct 19 (a partial week: ...)." */
  readonly intro: string;
  /** The tier subtitle, or the "Rows are machines" line for All. */
  readonly sub: string;
  readonly groups: readonly IntroGroup[];
  readonly footnote: string;
};

export type IntroResult = EmptySection<"intro"> | IntroFull;

const GROUP_TAGS = [
  { tag: "Yes almost always", color: "--color-accent-300" },
  { tag: "Yes, sometimes", color: "--color-neutral-300" },
  { tag: "Never, anywhere", color: "--color-neutral-400" },
  { tag: "Quota-blocked", color: "--color-neutral-500" },
  { tag: "Not asked in this window", color: "--color-neutral-600" },
] as const;

type Kind = 0 | 1 | 2 | 3 | 4;

/** The group of a machine, or null: a machine of mode `block` has no group (section 11.9 answers it). */
function kindOf(a: MachineAgg): Kind | null {
  const { tried, yes } = a.counts;
  if (a.mode === "now" && tried > 0) {
    if (yes === 0) return 2;
    return yes / tried >= 0.9 ? 0 : 1;
  }
  if (a.mode === "block") return null;
  if (a.mode === "quota") return 3;
  return 4;
}

function chipOf(a: MachineAgg, kind: Kind, focusName: string): IntroChip {
  const { tried, yes } = a.counts;
  const name = a.machine.name;
  const focus = name === focusName;
  const rate = tried > 0 ? (100 * yes) / tried : 0;
  const color = (c: string) => `var(${c})`;
  if (kind === 0 || kind === 1) {
    return {
      name,
      focus,
      big: ps(rate),
      bigColor: color(GROUP_TAGS[kind].color),
      sub: `${yes} of ${plural(tried, "round")} had a yes somewhere`,
      w: rate,
      barOn: true,
    };
  }
  if (kind === 2) {
    return {
      name,
      focus,
      big: "0%",
      bigColor: color("--color-neutral-400"),
      sub: `0 of ${plural(tried, "round")}; no place handed us one`,
      w: 0,
      barOn: false,
    };
  }
  if (kind === 3) {
    const n = a.places.length;
    return {
      name,
      focus,
      big: "quota",
      bigColor: color("--color-neutral-400"),
      sub: `our own limit refused every ask in ${plural(n, "region")}; not a capacity answer`,
      w: 0,
      barOn: false,
    };
  }
  return {
    name,
    focus,
    big: "–",
    bigColor: color("--color-neutral-500"),
    sub: "no probes for this machine in the days shown",
    w: 0,
    barOn: false,
  };
}

export function deriveIntro(data: BoardData, state: ViewState): IntroResult {
  const { meta } = data;
  // A window with no sweep and no Capacity Block check in H0..H1 has nothing to describe.
  if (!hasWindowData(data)) return { section: "intro", empty: true };
  const f = makeFrame(meta, state.hz, state.page);
  const cadText = f.cad === 1 ? "hour" : hrs(f.cad).replace(/ h$/, " hours");
  let span: string;
  let partial = "";
  if (f.H1 - f.H0 === f.nh) {
    span = `all ${plural(meta.dates.length, "day")}`;
  } else {
    const start = Math.max(f.t0, f.t0 + f.H0 * HOUR);
    const end = Math.min(f.tEnd, f.t0 + f.H1 * HOUR - 1);
    const d = Math.max(1, Math.ceil((end - start) / DAY));
    const from = dateLabel(start);
    const to = dateLabel(end);
    span = `${plural(d, "day")}, ${from === to ? from : `${from} – ${to}`}`;
    const pg = f.pages[f.page];
    if (state.hz !== "all" && pg !== undefined && (pg.start < f.t0 || pg.end - 1 > f.tEnd)) {
      partial = ` (a partial ${state.hz}: the data starts or ends inside it)`;
    }
  }
  const intro = `Every ${cadText} we ask AWS and Azure, in ${meta.regions.length} US regions, to hand us a real GPU machine. Showing ${span}${partial}.`;
  const sub = state.tier === "All" ? ALL_SUB : (TIER_SUBS[state.tier] ?? "");

  const gl = new Set(machinesOfTier(state.tier));
  const aggs = aggregateAll(joinData(data), f).filter((a) => gl.has(a.machine.name));
  const groups: IntroGroup[] = [];
  GROUP_TAGS.forEach((g, i) => {
    const kind = i as Kind;
    const members = aggs.filter((a) => kindOf(a) === kind);
    if (members.length === 0) return;
    const chips = members.map((a) => ({ a, chip: chipOf(a, kind, state.gpu) }));
    if (kind <= 1) chips.sort((x, y) => y.chip.w - x.chip.w);
    groups.push({ tag: g.tag, tagColor: `var(${g.color})`, chips: chips.map((c) => c.chip) });
  });
  return {
    section: "intro",
    empty: false,
    banner: meta.simulated ? SIMULATED_BANNER : null,
    intro,
    sub,
    groups,
    footnote: FOOTNOTE,
  };
}
