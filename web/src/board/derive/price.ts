// Section "Did price move with scarcity?" (spec 0006 section 11.20). Daily mean Spot price of the
// first type of each machine and cloud over the page, against the on-demand yes rate.

import { aggregateAll } from "./aggregate";
import { CLOUD_NAMES, machineByName, machinesOfTier } from "./catalogue";
import { dateLabel, ps, sh } from "./format";
import { joinData } from "./join";
import { makeFrame } from "./time";
import type { BoardData, ViewState } from "./types";

const DAY = 24 * 3600e3;
const HOUR = 3600e3;
const FLAT_BELOW = 3;
const MOVED = 10;

export const PRICE_INTRO =
  "Spot price for one fixed machine size per machine and cloud, daily mean in $ per machine-hour across the regions watched, over the horizon in view. If price tracked availability, the machines that said no would be climbing.";
const PRICE_TAIL =
  " Scarcity did not show up in Spot prices. Azure prices carry a monthly effective date — a list price, not a market. On-Demand and Capacity Block prices were not probed.";

export type PriceRow = {
  readonly name: string;
  readonly cloud: string;
  readonly type: string;
  readonly first: string;
  readonly last: string;
  /** "+4.2%" or "−0.1%". */
  readonly delta: string;
  /** The raw change in percent. */
  readonly d: number;
  /** The colour token of the change. */
  readonly deltaTone: string;
  /** The sparkline path in a 1000 × 100 box. */
  readonly path: string;
  /** The on-demand yes rate of the machine in percent, or null with no ask. */
  readonly yesP: number | null;
  /** The cloud tag colours: background and text tokens. */
  readonly tagBg: string;
  readonly tagFg: string;
  readonly tip: { readonly title: string; readonly lines: readonly string[] };
};

export type PriceResult = {
  readonly section: "price";
  /** True when the window holds no price date: nothing to say yet. */
  readonly empty: boolean;
  readonly intro: string;
  /** First and last date of the page, "Oct 26". */
  readonly first: string;
  readonly last: string;
  readonly rows: readonly PriceRow[];
  readonly verdict: string;
};

function money(v: number): string {
  return `$${v.toFixed(2)}`;
}

function pct(d: number): string {
  return `${d >= 0 ? "+" : "−"}${Math.abs(d).toFixed(1)}%`;
}

export function derivePrice(data: BoardData, state: ViewState): PriceResult {
  const d = joinData(data);
  const f = makeFrame(d.meta, state.hz, state.page);
  const dates = d.price.dates;
  const firstMs = Date.parse(dates[0] ?? "");
  const raw0 = Math.round((f.t0 + f.H0 * HOUR - firstMs) / DAY);
  const raw1 = Math.round((f.t0 + f.H1 * HOUR - firstMs) / DAY);
  const d0 = Math.max(0, raw0);
  const d1 = Math.max(d0 + 1, Math.min(dates.length, raw1));
  // Spec section 11.20: empty when no price date falls in the window.
  const noDateInWindow = dates.length === 0 || raw0 >= dates.length || raw1 <= 0;
  const dateText = (i: number) => {
    const s = dates[i];
    return s === undefined ? "" : dateLabel(Date.parse(s));
  };
  const aggs = new Map(aggregateAll(d, f).map((a) => [a.machine.name, a]));
  const rows: PriceRow[] = [];
  for (const name of machinesOfTier(state.tier)) {
    const m = machineByName(name);
    if (m === undefined) continue;
    const yes = aggs.get(name)?.counts;
    const yesP = yes === undefined ? null : sh(yes.yes, yes.tried);
    for (const [cloud, type] of [
      ["aws", m.aws[0]],
      ["azure", m.azure[0]],
    ] as const) {
      if (type === undefined) continue;
      const series = d.price.series[`${type}|${cloud}`]?.slice(d0, d1);
      if (series === undefined) continue;
      const vals = series.filter((v): v is number => v !== null);
      const firstV = vals[0];
      const lastV = vals[vals.length - 1];
      if (vals.length < 2 || firstV === undefined || lastV === undefined) continue;
      const change = (100 * (lastV - firstV)) / firstV;
      const low = Math.min(...vals);
      const high = Math.max(...vals);
      const n = series.length;
      const path = series
        .flatMap((v, i) => {
          if (v === null) return [];
          const y = high === low ? 50 : 8 + (100 - (100 * (v - low)) / (high - low)) * 0.84;
          return [`${(((i + 0.5) * 1000) / n).toFixed(1)} ${y.toFixed(1)}`];
        })
        .map((p, i) => (i === 0 ? `M${p}` : `L${p}`))
        .join("");
      const cloudName = CLOUD_NAMES[cloud] ?? cloud;
      const listed = series
        .flatMap((v, i) => (v === null ? [] : [`${dateText(d0 + i)} ${money(v)}`]))
        .join(" · ");
      rows.push({
        name,
        cloud: cloudName,
        type,
        first: money(firstV),
        last: money(lastV),
        delta: pct(change),
        d: change,
        deltaTone: Math.abs(change) < FLAT_BELOW ? "--color-neutral-500" : "--color-text",
        path,
        yesP,
        tagBg: cloud === "aws" ? "--color-neutral-900" : "--color-accent-900",
        tagFg: cloud === "aws" ? "--color-neutral-300" : "--color-accent-200",
        tip: {
          title: `${name} · ${cloudName} Spot price · ${type}`,
          lines: [
            `Daily mean, $ per machine-hour across the regions watched: ${listed}`,
            `Low ${money(low)} · high ${money(high)}`,
            yesP === null
              ? "No on-demand asks for this machine."
              : `Meanwhile ${ps(yesP)} of our on-demand rounds got a yes.`,
          ],
        },
      });
    }
  }
  const byMove = [...rows].sort((a, b) => Math.abs(b.d) - Math.abs(a.d));
  const top = byMove[0];
  let verdict: string;
  if (top === undefined) {
    verdict = "No Spot price series for this tier in the window.";
  } else {
    const span = state.hz === "all" ? "span" : state.hz;
    const largest = Math.abs(top.d);
    let head: string;
    if (largest < MOVED) {
      head = `No. Every Spot price here ended the ${span} within ${Math.ceil(largest)}% of where it started`;
    } else {
      const next = byMove[1] === undefined ? 0 : Math.abs(byMove[1].d);
      head = `Mostly no. The largest move was ${top.name} on ${top.cloud} at ${top.delta}; every other price stayed within ${Math.ceil(next)}%`;
    }
    const rates = rows.flatMap((r) => (r.yesP === null ? [] : [r.yesP]));
    const tail =
      rates.length > 0
        ? `, while on-demand yes rates for the same machines ran from ${ps(Math.min(...rates))} to ${ps(Math.max(...rates))}.`
        : ".";
    verdict = head + tail + PRICE_TAIL;
  }
  return {
    section: "price",
    empty: noDateInWindow,
    intro: PRICE_INTRO,
    first: dateText(d0),
    last: dateText(d1 - 1),
    rows,
    verdict,
  };
}
