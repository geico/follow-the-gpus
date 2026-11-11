// Section "Better or worse?" (spec 0006 section 11.13): per machine, 1 sparkline of each day's
// share of rounds with a yes, and the rate of this range against the one before it.

import { aggregateAll, type MachineAgg, NOW_LEAD_H } from "./aggregate";
import { machinesOfTier } from "./catalogue";
import { dateLabel, plural, ps } from "./format";
import { joinData } from "./join";
import { type Frame, hourInstant, isWeekendEt, makeFrame } from "./time";
import type { BoardData, EmptySection, JoinedData, ViewState } from "./types";

const DAY_H = 24;

export type TrendBand = { readonly l: number; readonly w: number };
export type TrendTick = { readonly l: number; readonly label: string };
export type TrendTip = { readonly title: string; readonly lines: readonly string[] };

export type TrendRow = {
  readonly name: string;
  /** "yes somewhere", or "block within 1 h" for mode `block`. */
  readonly sub: string;
  readonly block: boolean;
  /** The SVG paths in the 1000 x 100 box. */
  readonly path: string;
  readonly area: string;
  /** With 1 slice (a `day` page): the rate in percent of the single dot; absent otherwise or with no rate. */
  readonly dot?: number;
  /** Previous range, this range, and change ("–" when missing). */
  readonly a: string;
  readonly b: string;
  readonly d: string;
  /** The colour token of the change. */
  readonly tone: string;
  /** One tooltip per day, in order. */
  readonly tips: readonly TrendTip[];
};

export type TrendFull = {
  readonly section: "trend";
  readonly empty: false;
  readonly intro: string;
  readonly heads: { readonly prev: string; readonly this: string; readonly change: string };
  readonly note: string;
  readonly days: number;
  readonly axis: readonly TrendTick[];
  /** Weekend bands in percent of the window width. */
  readonly weekend: readonly TrendBand[];
  /** The method-change marker (dashed line), when it is inside the window. */
  readonly starts: readonly TrendTick[];
  readonly rows: readonly TrendRow[];
};

export type TrendResult = EmptySection<"trend"> | TrendFull;

type Rate = { readonly y: number; readonly n: number };

const fix1 = (x: number) => x.toFixed(1);

/** The yes rounds and rounds of a machine in the hours [a, b) (section 11.13, "Rate in a range"). */
function rateIn(d: JoinedData, f: Frame, agg: MachineAgg, a: number, b: number): Rate | null {
  const rounds = new Map<number, boolean>();
  const put = (h: number, yes: boolean) => {
    const r = Math.floor(h / f.cad);
    rounds.set(r, (rounds.get(r) ?? false) || yes);
  };
  if (agg.mode === "block") {
    for (const p of d.cb) {
      if (!agg.machine.aws.includes(p.sku)) continue;
      for (const c of p.checks) {
        const h = c[0] ?? -1;
        const nReq = c[6] ?? null;
        const dur = c[3] ?? null;
        if (h < a || h >= b || (nReq !== null && nReq !== 1) || (dur !== null && dur !== 24))
          continue;
        put(h, c[1] === 1 && c[7] !== null && c[7] !== undefined && c[7] < NOW_LEAD_H);
      }
    }
  } else {
    const types = [...agg.machine.aws, ...agg.machine.azure];
    for (const s of d.sweeps) {
      if (s.h < a || s.h >= b || s.state < 0 || !types.includes(s.sku)) continue;
      put(s.h, s.got > 0);
    }
  }
  if (rounds.size === 0) return null;
  return { y: [...rounds.values()].filter(Boolean).length, n: rounds.size };
}

const pct = (r: Rate | null): number | null => (r === null ? null : (100 * r.y) / r.n);

/** The weekend bands (Saturday 00:00 to Monday 00:00 in New York), cut to the window. */
function weekendBands(f: Frame): TrendBand[] {
  const span = f.H1 - f.H0;
  const out: TrendBand[] = [];
  let start: number | null = null;
  for (let h = f.H0; h <= f.H1; h++) {
    const on = h < f.H1 && isWeekendEt(hourInstant(f, h));
    if (on && start === null) start = h;
    if (!on && start !== null) {
      out.push({ l: ((start - f.H0) / span) * 100, w: ((h - start) / span) * 100 });
      start = null;
    }
  }
  return out;
}

export function deriveTrend(data: BoardData, state: ViewState): TrendResult {
  const d = joinData(data);
  const f = makeFrame(data.meta, state.hz, state.page);
  const gl = machinesOfTier(state.tier);
  const days = Math.ceil((f.H1 - f.H0) / DAY_H);
  const hasPrev = f.H0 > 0 && f.H0 - f.L >= DAY_H;
  const all = state.hz === "all";
  const span = all ? "span" : state.hz;

  const aggs = aggregateAll(d, f).filter((a) => gl.includes(a.machine.name) && a.counts.tried > 0);
  if (aggs.length === 0) return { section: "trend", empty: true };

  const dayStart = (i: number) => f.H0 + i * DAY_H;
  const dayMs = (i: number) => hourInstant(f, dayStart(i));
  const rows: TrendRow[] = aggs.map((agg) => {
    let path = "";
    let area = "";
    let seg: { x: number; y: number }[] = [];
    const close = () => {
      if (seg.length > 0) {
        const first = seg[0];
        const last = seg[seg.length - 1];
        if (first !== undefined && last !== undefined) {
          area += `M${fix1(first.x)} 100`;
          for (const p of seg) area += `L${fix1(p.x)} ${fix1(p.y)}`;
          area += `L${fix1(last.x)} 100Z`;
        }
      }
      seg = [];
    };
    const tips: TrendTip[] = [];
    let broke = true;
    let dot: number | undefined;
    for (let i = 0; i < days; i++) {
      const r = rateIn(d, f, agg, dayStart(i), Math.min(dayStart(i) + DAY_H, f.H1));
      const title = `${agg.machine.name} · ${dateLabel(dayMs(i))}`;
      const rate = pct(r);
      if (r === null || rate === null) {
        tips.push({ title, lines: ["Not asked that day."] });
        broke = true;
        close();
        continue;
      }
      const x = ((i + 0.5) / days) * 1000;
      const y = 100 - rate;
      path += `${broke ? "M" : "L"}${fix1(x)} ${fix1(y)}`;
      broke = false;
      if (days === 1) dot = rate;
      seg.push({ x, y });
      tips.push({ title, lines: [`${r.y} of ${plural(r.n, "round")} · ${ps(rate)}`] });
    }
    close();
    const prev = hasPrev ? pct(rateIn(d, f, agg, f.L, f.H0)) : null;
    const cur = pct(rateIn(d, f, agg, f.H0, f.H1));
    let change = "–";
    let tone = "--color-neutral-500";
    if (prev !== null && cur !== null) {
      const diff = cur - prev;
      change = `${diff < 0 ? "−" : "+"}${Math.round(Math.abs(diff))} pt`;
      if (Math.abs(diff) >= 5) tone = diff > 0 ? "--color-accent-300" : "--color-neutral-200";
    }
    return {
      name: agg.machine.name,
      sub: agg.mode === "block" ? `block within ${NOW_LEAD_H} h` : "yes somewhere",
      block: agg.mode === "block",
      // One slice draws a dot and no line and no area (spec 0006 section 11.13).
      path: days === 1 ? "" : path,
      area: days === 1 ? "" : area,
      ...(dot === undefined ? {} : { dot }),
      a: ps(prev),
      b: ps(cur),
      d: change,
      tone,
      tips,
    };
  });

  // Days with a sweep (any SKU, any state).
  const withData = new Set<number>();
  for (const s of d.sweeps) {
    if (s.h >= f.H0 && s.h < f.H1) withData.add(Math.floor((s.h - f.H0) / DAY_H));
  }
  let note =
    withData.size === days
      ? `${plural(days, "day")} in view`
      : `${days}-day ${span}, ${withData.size} with data`;
  if (!hasPrev && !all) note += ` · no earlier ${state.hz} to compare with`;
  const zs = data.meta.zoneStartH;
  const methodChange = zs !== null && f.L <= zs && zs < f.H1;
  if (methodChange) {
    note += ` · AWS asks every zone from ${dateLabel(hourInstant(f, zs))}: AWS rates before and after it come from different methods`;
  }
  const starts: TrendTick[] = [];
  if (methodChange && zs >= f.H0) {
    starts.push({
      l: Number((((zs - f.H0) / (f.H1 - f.H0)) * 100).toFixed(4)),
      label: `AWS asks every zone from ${dateLabel(hourInstant(f, zs))}`,
    });
  }

  const step = Math.ceil(days / 6);
  const axis: TrendTick[] = [];
  for (let i = 0; i < days; i += step) {
    axis.push({ l: (i / days) * 100, label: dateLabel(dayMs(i)) });
  }

  return {
    section: "trend",
    empty: false,
    intro: `The line is each day’s share of rounds with a yes somewhere; shaded bands are weekends. The columns compare the whole ${span} in view with the one before it.`,
    heads: {
      prev: all ? "—" : `previous ${state.hz}`,
      this: all ? "all data" : `this ${state.hz}`,
      change: "change",
    },
    note,
    days,
    axis,
    weekend: weekendBands(f),
    starts,
    rows,
  };
}
