// Per-machine aggregation (spec 0006 section 11.3): the places, the union, the Capacity Block
// places, the mode, the series, and the counts that every section reads. A section's own numbers
// (captions, bars, verdicts) are in the section files, not here.

import { MACHINES, type Machine } from "./catalogue";
import { median } from "./format";
import type { Frame } from "./time";
import type { CbPlace, JoinedData, JoinedSweep } from "./types";

/** A round cell: null = not asked, -1 = quota, 0 = no, 1 = some, 2 = all. */
export type Cell = number | null;

export type Place = {
  readonly cloud: string;
  readonly region: string;
  readonly probe: string;
  readonly cells: Cell[];
  /** Sweeps of state -1 or more. */
  n: number;
  /** Sweeps with state -1. */
  na: number;
  /** Sweeps with `got` > 0. */
  yes: number;
  /** Sweeps with state 2. */
  full: number;
  /** Sums over sweeps with state >= 0. */
  got: number;
  asked: number;
  reason: "quota" | "restricted" | null;
  /** Window sweeps of state -3 and -4. */
  nu: number;
  no: number;
};

/** A Capacity Block offer is a yes to "right now" only when it starts less than this many hours after the check. */
export const NOW_LEAD_H = 1;

export type BlockPlace = {
  readonly region: string;
  readonly sku: string;
  readonly n: number;
  readonly found: number;
  /** The found checks that start less than `NOW_LEAD_H` hours after the check (section 11.3 item 4). */
  readonly nowFound: number;
  /** Per round: the earliest start of a found check, in hours after that check; null with none. */
  readonly leadH: (number | null)[];
  /** Per round: the length in hours of the block with that earliest start; null with none. */
  readonly durH: (number | null)[];
  readonly medLead: number | null;
  readonly cells: Cell[];
  readonly longestH: number | null;
  readonly longestMedH: number | null;
  readonly longestLead: number | null;
  readonly nLong: number;
  readonly fee: number | null;
};

export type Mode = "now" | "quota" | "block" | "none";

export type WaitKind = "between two yeses" | "since the last yes" | "before the first yes";

export type Counts = {
  readonly tried: number;
  readonly yes: number;
  /** The longest wait in hours (0 when the largest value is <= 1 round), and its name. */
  readonly waitH: number;
  readonly waitKind: WaitKind | null;
};

export type MachineAgg = {
  readonly machine: Machine;
  readonly places: readonly Place[];
  readonly union: readonly Cell[];
  readonly blockPlaces: readonly BlockPlace[];
  readonly blockUnion: readonly Cell[];
  readonly mode: Mode;
  readonly src: readonly Cell[];
  readonly counts: Counts;
  /** The distinct `asked` of the sweeps with state >= 0, ascending. */
  readonly reqs: readonly number[];
  /** First-ask hour; Infinity when absent. */
  readonly famStart: number;
};

/** The max of the cell and the state, a null or -1 cell counting as 0. */
function bump(cell: Cell, state: number): number {
  return Math.max(state, cell === null || cell === -1 ? 0 : cell);
}

/** Per round, the max over the series that have a cell; -1 only when all such are -1. */
export function unionOf(series: readonly (readonly Cell[])[], nr: number): Cell[] {
  const out: Cell[] = [];
  for (let r = 0; r < nr; r++) {
    let seen = false;
    let best = -1;
    for (const s of series) {
      const c = s[r] ?? null;
      if (c === null) continue;
      seen = true;
      if (c > best) best = c;
    }
    out.push(seen ? best : null);
  }
  return out;
}

function placesOf(sw: readonly JoinedSweep[], all: readonly JoinedSweep[], f: Frame): Place[] {
  const map = new Map<string, Place>();
  const get = (s: JoinedSweep): Place => {
    const key = `${s.cloud}|${s.region}`;
    let p = map.get(key);
    if (p === undefined) {
      p = {
        cloud: s.cloud,
        region: s.region,
        probe: s.probe,
        cells: new Array<Cell>(f.NR).fill(null),
        n: 0,
        na: 0,
        yes: 0,
        full: 0,
        got: 0,
        asked: 0,
        reason: null,
        nu: 0,
        no: 0,
      };
      map.set(key, p);
    }
    return p;
  };
  for (const s of sw) {
    const p = get(s);
    p.n += 1;
    if (s.state === -1) {
      p.na += 1;
      if (p.reason === null) p.reason = s.reason;
    }
    if (s.got > 0) p.yes += 1;
    if (s.state === 2) p.full += 1;
    if (s.state >= 0) {
      p.got += s.got;
      p.asked += s.asked;
    }
    const r = Math.floor((s.h - f.H0) / f.cad);
    if (r < 0 || r >= f.NR) continue;
    if (s.state === -1) {
      if (p.cells[r] === null || p.cells[r] === undefined) p.cells[r] = -1;
    } else {
      p.cells[r] = bump(p.cells[r] ?? null, s.state);
    }
  }
  // A place with only state -3 or -4 sweeps has n = 0 and no cell.
  for (const s of all) {
    if (s.state !== -3 && s.state !== -4) continue;
    const p = get(s);
    if (s.state === -3) p.nu += 1;
    else p.no += 1;
  }
  return [...map.values()];
}

function blockPlacesOf(cb: readonly CbPlace[], types: readonly string[], f: Frame): BlockPlace[] {
  const out: BlockPlace[] = [];
  for (const place of cb) {
    if (!types.includes(place.sku)) continue;
    const checks = place.checks.filter((c) => {
      const h = c[0] ?? -1;
      const nReq = c[6] ?? null;
      const dur = c[3] ?? null;
      return h >= f.H0 && h < f.H1 && (nReq === null || nReq === 1) && (dur === null || dur === 24);
    });
    if (checks.length === 0) continue;
    const found = checks.filter((c) => c[1] === 1);
    const isNow = (c: readonly (number | null)[]) =>
      c[1] === 1 && c[7] !== null && c[7] !== undefined && c[7] < NOW_LEAD_H;
    const cells: Cell[] = new Array<Cell>(f.NR).fill(null);
    const leadH: (number | null)[] = new Array<number | null>(f.NR).fill(null);
    const durH: (number | null)[] = new Array<number | null>(f.NR).fill(null);
    for (const c of checks) {
      const r = Math.floor(((c[0] ?? 0) - f.H0) / f.cad);
      if (r < 0 || r >= f.NR) continue;
      cells[r] = Math.max(cells[r] ?? 0, isNow(c) ? 1 : 0);
      const lead = c[1] === 1 ? (c[7] ?? null) : null;
      const cur = leadH[r] ?? null;
      if (lead !== null && (cur === null || lead < cur)) {
        leadH[r] = lead;
        durH[r] = c[3] ?? null;
      }
    }
    const longRows = place.longest.filter(
      (l) => (l[0] as number) >= f.H0 && (l[0] as number) < f.H1 && l[1] === 1,
    );
    const nums = (xs: readonly (number | string | null | undefined)[]) =>
      xs.filter((x): x is number => typeof x === "number");
    const longestVals = nums(longRows.map((l) => l[3]));
    out.push({
      region: place.region,
      sku: place.sku,
      n: checks.length,
      found: found.length,
      nowFound: found.filter(isNow).length,
      leadH,
      durH,
      medLead: median(nums(found.map((c) => c[2]))),
      cells,
      longestH: longestVals.length > 0 ? Math.max(...longestVals) : null,
      longestMedH: median(longestVals),
      longestLead: median(nums(longRows.map((l) => l[2]))),
      nLong: longRows.length,
      fee: median(nums(found.map((c) => c[4]))),
    });
  }
  return out;
}

/** The counts of section 11.3 item 7 over a series. */
export function countsOf(src: readonly Cell[], cad: number): Counts {
  let tried = 0;
  let yes = 0;
  let first = -1;
  let last = -1;
  let firstYes = -1;
  let lastYes = -1;
  let bestGap = 0;
  let prevYes = -1;
  let noBetween = false;
  src.forEach((c, i) => {
    if (c === null || c === -1) return;
    tried += 1;
    if (first < 0) first = i;
    last = i;
    if (c > 0) {
      yes += 1;
      if (firstYes < 0) firstYes = i;
      lastYes = i;
      if (prevYes >= 0 && noBetween) bestGap = Math.max(bestGap, i - prevYes);
      prevYes = i;
      noBetween = false;
    } else if (prevYes >= 0) {
      noBetween = true;
    }
  });
  if (yes === 0) return { tried, yes, waitH: 0, waitKind: null };
  const lead = firstYes - first;
  const trail = last - lastYes;
  // In order of preference when equal: between, since the last yes, before the first yes.
  let value = bestGap;
  let kind: WaitKind = "between two yeses";
  if (trail > value) {
    value = trail;
    kind = "since the last yes";
  }
  if (lead > value) {
    value = lead;
    kind = "before the first yes";
  }
  return value <= 1
    ? { tried, yes, waitH: 0, waitKind: kind }
    : { tried, yes, waitH: value * cad, waitKind: kind };
}

/** The aggregation of 1 machine over the window of the frame. */
export function aggregateMachine(d: JoinedData, f: Frame, machine: Machine): MachineAgg {
  const types = [...machine.aws, ...machine.azure];
  const inWindow = d.sweeps.filter((s) => s.h >= f.H0 && s.h < f.H1 && types.includes(s.sku));
  const sw = inWindow.filter((s) => s.state >= -1);
  const places = placesOf(sw, inWindow, f);
  const union = unionOf(
    places.map((p) => p.cells),
    f.NR,
  );
  const blockPlaces = blockPlacesOf(d.cb, machine.aws, f);
  const blockUnion = unionOf(
    blockPlaces.map((b) => b.cells),
    f.NR,
  );
  let mode: Mode = "none";
  if (sw.some((s) => s.state >= 0)) mode = "now";
  else if (sw.length > 0) mode = "quota";
  else if (blockPlaces.length > 0) mode = "block";
  const src: Cell[] = mode === "block" ? blockUnion.map((c) => (c === 1 ? 2 : c)) : union.slice();
  const reqs = [...new Set(sw.filter((s) => s.state >= 0).map((s) => s.asked))].sort(
    (a, b) => a - b,
  );
  let famStart = Number.POSITIVE_INFINITY;
  if (mode === "block") {
    const v = d.meta.cbFirstH[machine.aws[0] ?? ""];
    if (v !== undefined) famStart = v;
  } else {
    for (const t of types) {
      const v = d.meta.firstH[t];
      if (v !== undefined && v < famStart) famStart = v;
    }
  }
  return {
    machine,
    places,
    union,
    blockPlaces,
    blockUnion,
    mode,
    src,
    counts: countsOf(src, f.cad),
    reqs,
    famStart,
  };
}

/** Every machine of the catalogue (all tiers, because the substitutes read every machine). */
export function aggregateAll(d: JoinedData, f: Frame): readonly MachineAgg[] {
  return MACHINES.map((m) => aggregateMachine(d, f, m));
}

/** A cell is pre-probe when it is not asked and ends at or before the first ask (item 9). */
export function isPreProbe(f: Frame, famStart: number, k: number): boolean {
  return f.H0 + (k + 1) * f.grain <= famStart;
}

export type Folded = {
  /** Rounds not null and not -1. */
  readonly n: number;
  /** Of those, value > 0. */
  readonly y: number;
  readonly share: number | null;
  /** With n = 0: true when a round was -1 (shown as quota), else the cell is not asked. */
  readonly quota: boolean;
};

/** The cells at the grain of the frame, each folding its rounds (item 10). */
export function foldCells(series: readonly Cell[], f: Frame): Folded[] {
  const out: Folded[] = [];
  for (let k = 0; k < f.NB; k++) {
    let n = 0;
    let y = 0;
    let quota = false;
    for (let r = 0; r < series.length; r++) {
      if (Math.floor((r * f.cad) / f.grain) !== k) continue;
      const c = series[r] ?? null;
      if (c === -1) quota = true;
      if (c === null || c === -1) continue;
      n += 1;
      if (c > 0) y += 1;
    }
    out.push({ n, y, share: n > 0 ? (100 * y) / n : null, quota: n === 0 && quota });
  }
  return out;
}
