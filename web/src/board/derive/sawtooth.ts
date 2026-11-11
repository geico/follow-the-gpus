// Section "When could a block start? {machine}, check by check" (spec 0006 section 11.17): for the
// sawtooth machine, 1 line per Capacity Block place, 1 point per in-window 1-day check.

import { aggregateMachine } from "./aggregate";
import { type Machine, machineByName, machinesOfTier } from "./catalogue";
import { dateLabel, dayLabel, plural } from "./format";
import { joinData } from "./join";
import { type Frame, hourInstant, makeFrame } from "./time";
import type { BoardData, CbPlace, EmptySection, JoinedData, ViewState } from "./types";

/** A check of the line: its place in the 1000-wide chart and its tooltip text. */
export type SawPoint = {
  /** x in the 0..1000 chart space. */
  readonly x: number;
  /** y in the 0..100 chart space; null for a break (no block, or no lead). */
  readonly y: number | null;
  readonly title: string;
  readonly lines: readonly string[];
};

export type SawRow = {
  readonly region: string;
  /** The SVG path of the line, with 1 break (`M`) after each check that has no lead. */
  readonly path: string;
  readonly jumps: number;
  readonly found: number;
  readonly n: number;
  /** "{found} of {n} checks found one". */
  readonly count: string;
  readonly note: string;
  /** Tooltip shown with no check under the cursor: title and "No checks here.". */
  readonly empty: { readonly title: string; readonly lines: readonly string[] };
  readonly points: readonly SawPoint[];
};

export type SawYLabel = { readonly label: string; readonly top: number };

export type SawtoothFull = {
  readonly section: "sawtooth";
  readonly empty: false;
  /** The sawtooth machine's name. */
  readonly machine: string;
  readonly intro: string;
  readonly heading: string;
  /** The y scale in days: a multiple of 7. */
  readonly scale: number;
  readonly yLabels: readonly SawYLabel[];
  readonly rows: readonly SawRow[];
};

/** `e.saw` of the design: the result of this section; the "so what" card reads it. */
export type SawtoothResult = EmptySection<"sawtooth"> | SawtoothFull;

const NO_BLOCK = "no block within the horizon";

type Check = readonly (number | null)[];

function oneDayChecks(place: CbPlace, f: Frame): Check[] {
  return place.checks.filter((c) => {
    const h = c[0] ?? -1;
    const nReq = c[6] ?? null;
    const dur = c[3] ?? null;
    return h >= f.H0 && h < f.H1 && (nReq === null || nReq === 1) && (dur === null || dur === 24);
  });
}

/** The machine of the section: the focus machine with a place in the window, else the first of `gl`. */
export function sawMachine(d: JoinedData, f: Frame, state: ViewState): Machine | null {
  const hasPlace = (m: Machine) => aggregateMachine(d, f, m).blockPlaces.length > 0;
  const focus = machineByName(state.gpu);
  if (focus !== undefined && hasPlace(focus)) return focus;
  for (const name of machinesOfTier(state.tier)) {
    const m = machineByName(name);
    if (m !== undefined && hasPlace(m)) return m;
  }
  return null;
}

const fix1 = (x: number) => x.toFixed(1);

export function deriveSawtooth(data: BoardData, state: ViewState): SawtoothResult {
  const d = joinData(data);
  const f = makeFrame(data.meta, state.hz, state.page);
  const machine = sawMachine(d, f, state);
  if (machine === null) return { section: "sawtooth", empty: true };

  const places = d.cb.filter((p) => machine.aws.includes(p.sku));
  const checksOf = places.map((p) => oneDayChecks(p, f));
  let maxLead = 1;
  for (const cs of checksOf)
    for (const c of cs) if (c[1] === 1 && c[2] != null && c[2] > maxLead) maxLead = c[2];
  const scale = Math.ceil(maxLead / 7) * 7;
  const top = (days: number) => 100 - (days / scale) * 92;
  const yLabels: SawYLabel[] = [
    { label: "0 d", top: top(0) },
    { label: `${Math.round(scale / 2)} d`, top: top(Math.round(scale / 2)) },
    { label: `${scale} d`, top: top(scale) },
  ];

  const rows: SawRow[] = places.map((place, i) => {
    const checks = checksOf[i] ?? [];
    const title = `${machine.name} · ${place.region}`;
    let path = "";
    let started = false;
    let prev: number | null = null;
    let jumps = 0;
    let found = 0;
    const points: SawPoint[] = [];
    for (const c of checks) {
      const h = c[0] ?? 0;
      const lead = c[1] === 1 ? (c[2] ?? null) : null;
      const x = ((h - f.H0) / (f.H1 - f.H0)) * 1000;
      const ms = hourInstant(f, h);
      const when = `${dayLabel(ms)} ${dateLabel(ms)}`;
      if (c[1] === 1) found += 1;
      let y: number | null = null;
      let line: string;
      if (lead !== null) {
        y = 100 - (lead / scale) * 92;
        path += `${started ? "L" : "M"}${fix1(x)} ${fix1(y)}`;
        started = true;
        if (prev !== null && lead > prev + 2) jumps += 1;
        prev = lead;
        const fee = c[4] ?? null;
        line = `${when}: next free 1-day block starts in ${lead} days${fee === null ? "" : ` · $${Math.round(fee)} upfront`}`;
      } else {
        started = false;
        line = `${when}: ${NO_BLOCK}`;
      }
      points.push({ x, y, title, lines: [line] });
    }
    const note =
      jumps > 0
        ? `${jumps} jump${jumps === 1 ? "" : "s"} out — the slot was bought`
        : found > 0
          ? "the slot crept closer all window"
          : NO_BLOCK;
    return {
      region: place.region,
      path,
      jumps,
      found,
      n: checks.length,
      count: `${found} of ${plural(checks.length, "check")} found one`,
      note,
      empty: { title, lines: ["No checks here."] },
      points,
    };
  });

  return {
    section: "sawtooth",
    empty: false,
    machine: machine.name,
    heading: `When could a block start? ${machine.name}, check by check`,
    intro: `Days from each check to the earliest 1-day block AWS would sell for ${machine.name}, per region. The "next free day" is a fixed calendar slot: the line creeps down as it approaches, then jumps when somebody buys it.`,
    scale,
    yLabels,
    rows,
  };
}

/** The tooltip at x (0..1000) of a row: the nearest check, or "No checks here." with none. */
export function sawTipAt(row: SawRow, x: number): { title: string; lines: readonly string[] } {
  let best: SawPoint | null = null;
  for (const p of row.points)
    if (best === null || Math.abs(p.x - x) < Math.abs(best.x - x)) best = p;
  return best ?? row.empty;
}
