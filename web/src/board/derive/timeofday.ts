// Section "Does the time of day matter?" (spec 0006 section 11.18): the focus machine's sweeps
// grouped by the real UTC hour of T0 + t (decision R6), by coast, and by weekday or weekend, with
// the ET and PT business-hour brackets placed by the zones' UTC offsets at the window midpoint
// (correction C3). Pure: no DOM, React, or server import.

import { coastOf, MIN_ASKS, machineByName } from "./catalogue";
import { ps } from "./format";
import { joinData } from "./join";
import { cadence, isWeekendEt, LOS_ANGELES, NEW_YORK, utcOffsetHours } from "./time";
import type { BoardData, EmptySection, ViewState } from "./types";

const HOUR = 3600e3;
const COAST_ORDER = ["East", "Central", "West"] as const;
const RESOLUTIONS = [1, 2, 4, 6] as const;

export type TodCell = {
  readonly got: number;
  readonly asked: number;
  /** The big value: ps(100 × got / asked), or "–" with nothing asked. */
  readonly big: string;
  /** "{got} of {asked}" or "not asked". */
  readonly sub: string;
  /** Percent for the heat fill; null with nothing asked. */
  readonly p: number | null;
  /** Asked, but fewer than `minAsks`; a cell with nothing asked is also drawn thin. */
  readonly thin: boolean;
  readonly tip: { readonly title: string; readonly lines: readonly string[] };
};

export type TodRow = {
  readonly label: string;
  readonly sub: string;
  /** The weekend row: its label is drawn in `--color-accent-300`. */
  readonly weekend: boolean;
  readonly cells: readonly TodCell[];
};

/** A bracket over the head row: columns are hours, so `start` and `len` are in hours of day. */
export type TodBand = {
  readonly label: string;
  readonly zone: "ET" | "PT";
  /** Start hour in UTC, 0..23. */
  readonly start: number;
  /** The printed hour range, "13–21" or "17–01". */
  readonly range: string;
  /** Left and width in percent of the head row; a bracket that wraps midnight has 2 pieces. */
  readonly pieces: readonly { readonly l: number; readonly w: number }[];
};

export type TimeofdayContent = {
  readonly section: "timeofday";
  readonly empty: false;
  readonly gpu: string;
  readonly title: string;
  readonly res: number;
  readonly minAsks: number;
  readonly heads: readonly string[];
  readonly bands: readonly TodBand[];
  readonly rows: readonly TodRow[];
  readonly intro: string;
  readonly verdict: string;
  readonly legend: readonly string[];
};

export type TimeofdayResult = EmptySection<"timeofday"> | TimeofdayContent;

const pad = (n: number) => String(n).padStart(2, "0");

function head(start: number, res: number): string {
  return res === 1 ? pad(start) : `${pad(start)}–${pad(start + res)}`;
}

/** The business hours 09–17 of a zone in UTC, from the offset at the window midpoint. */
export function businessBand(zone: string, mid: number): { start: number; range: string } {
  const off = utcOffsetHours(mid, zone);
  const start = (((9 - off) % 24) + 24) % 24;
  const end = start + 8;
  return { start, range: `${pad(start)}–${pad(end > 24 ? end - 24 : end)}` };
}

function band(zone: "ET" | "PT", mid: number): TodBand {
  const { start, range } = businessBand(zone === "ET" ? NEW_YORK : LOS_ANGELES, mid);
  const pct = (h: number) => (100 * h) / 24;
  const over = start + 8 - 24;
  const pieces =
    over > 0
      ? [
          { l: pct(start), w: pct(24 - start) },
          { l: 0, w: pct(over) },
        ]
      : [{ l: pct(start), w: pct(8) }];
  return { label: `${zone} 09–17`, zone, start, range, pieces };
}

type Group = { got: number; asked: number };

export function deriveTimeofday(data: BoardData, state: ViewState): TimeofdayResult {
  const machine = machineByName(state.gpu);
  if (machine === undefined || data.meta.sweeps === 0) return { section: "timeofday", empty: true };
  const d = joinData(data);
  const cad = cadence(data.meta);
  const types = [...machine.aws, ...machine.azure];
  const sweeps = d.sweeps.filter(
    (s) => s.h >= d.H0 && s.h < d.H1 && s.state >= 0 && types.includes(s.sku),
  );
  const t0 = Date.parse(data.meta.t0);
  const facts = sweeps.map((s) => ({
    s,
    hour: new Date(t0 + s.t * HOUR).getUTCHours(),
    weekend: isWeekendEt(t0 + s.h * HOUR),
    coast: coastOf(s.region),
  }));

  // Resolution: the first of 1, 2, 4, 6 h that is >= CAD where 80% of the groups hold enough asks.
  // A sweep in a region with no coast has no row, so it forms no group (spec 11.18).
  const grouped = facts.filter((x) => x.coast !== null);
  let res = grouped.length === 0 ? 4 : 6;
  if (grouped.length > 0) {
    for (const r of RESOLUTIONS) {
      if (r < cad) continue;
      const groups = new Map<string, number>();
      for (const x of grouped) {
        const key = `${Math.floor(x.hour / r)}|${x.coast}|${x.weekend}`;
        groups.set(key, (groups.get(key) ?? 0) + x.s.asked);
      }
      const enough = [...groups.values()].filter((a) => a >= MIN_ASKS).length;
      if (enough >= 0.8 * groups.size) {
        res = r;
        break;
      }
    }
  }
  const nCols = 24 / res;
  const heads = Array.from({ length: nCols }, (_, i) => head(i * res, res));

  const rowOf = (label: string, sub: string, weekend: boolean, pick: typeof facts): TodRow => {
    const groups: Group[] = Array.from({ length: nCols }, () => ({ got: 0, asked: 0 }));
    for (const x of pick) {
      const g = groups[Math.floor(x.hour / res)];
      if (g === undefined) continue;
      g.got += x.s.got;
      g.asked += x.s.asked;
    }
    const cells = groups.map((g, i): TodCell => {
      const p = g.asked > 0 ? (100 * g.got) / g.asked : null;
      const thin = g.asked < MIN_ASKS;
      const lines =
        g.asked === 0
          ? ["Not asked in this block."]
          : [
              `${g.got} of ${g.asked} machines asked for were handed to us · ${ps(p)}`,
              ...(thin ? [`Fewer than ${MIN_ASKS} asked: too thin to read.`] : []),
            ];
      return {
        got: g.got,
        asked: g.asked,
        big: ps(p),
        sub: g.asked > 0 ? `${g.got} of ${g.asked}` : "not asked",
        p,
        thin,
        tip: { title: `${machine.name} · ${label} · ${heads[i]} UTC`, lines },
      };
    });
    return { label, sub, weekend, cells };
  };

  const rows: TodRow[] = [];
  for (const coast of COAST_ORDER) {
    const pick = facts.filter((x) => !x.weekend && x.coast === coast);
    if (pick.length > 0) rows.push(rowOf(`${coast} regions`, "weekdays", false, pick));
  }
  rows.push(
    rowOf(
      "All regions",
      `weekends · ${data.meta.weekends}`,
      true,
      facts.filter((x) => x.weekend),
    ),
  );

  const mid = t0 + ((d.H0 + d.H1) / 2) * HOUR;
  const bands = [band("ET", mid), band("PT", mid)];

  // Verdict: coast rows with at least 3 readable cells; printed percents compare.
  const printed = (p: number) => (p > 0 && p < 1 ? 1 : p > 99 && p < 100 ? 99 : Math.round(p));
  const parts: string[] = [];
  for (const row of rows) {
    if (row.weekend) continue;
    const ok = row.cells
      .map((c, i) => ({ c, i }))
      .filter(({ c }) => !c.thin && c.p !== null)
      .map(({ c, i }) => ({ v: printed(c.p ?? 0), p: c.p, i }));
    if (ok.length < 3) continue;
    let best = ok[0];
    let worst = ok[0];
    for (const x of ok) {
      if (best === undefined || worst === undefined) break;
      if (x.v > best.v) best = x;
      if (x.v < worst.v) worst = x;
    }
    if (best === undefined || worst === undefined) continue;
    if (best.v === 0) parts.push(`${row.label}: never, at any hour`);
    else if (best.v - worst.v >= 15) {
      parts.push(
        `${row.label}: best ${heads[best.i]} UTC (${ps(best.p)}), worst ${heads[worst.i]} UTC (${ps(worst.p)})`,
      );
    } else parts.push(`${row.label}: flat within ${best.v - worst.v} points`);
  }
  const et = bands[0];
  const pt = bands[1];
  const verdict =
    parts.length === 0 || et === undefined || pt === undefined
      ? ""
      : `${parts.join(" · ")}. ET business hours are ${et.range} UTC, PT ${pt.range} UTC.`;

  const intro =
    facts.length === 0
      ? `${machine.name} has no on-demand asks in this window.`
      : `Columns are hours of the day in UTC; rows split the regions by coast so a business-day drain shows up where it happens. Resolution is chosen from the data: ${res === 1 ? "hourly" : `${res}-hour blocks`}, the finest at which at least 80% of cells hold ${MIN_ASKS}+ asks. Value = machines handed to us ÷ machines asked for.`;

  return {
    section: "timeofday",
    empty: false,
    gpu: machine.name,
    title: `Does the time of day matter? ${machine.name}`,
    res,
    minAsks: MIN_ASKS,
    heads,
    bands,
    rows,
    intro,
    verdict,
    legend: [
      "0%…100% of machines asked for were handed to us",
      `fewer than ${MIN_ASKS} asked: too thin to read`,
      "columns are UTC · brackets mark 09–17 local business hours on each coast",
    ],
  };
}
