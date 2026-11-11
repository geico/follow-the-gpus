// Section "Does a high score predict a yes?" (spec 0006 section 11.19). For every ask in view, the
// cloud's published Spot score for the same size and region in the CAD hours before the ask.

import { machineByName, machinesOfTier } from "./catalogue";
import { hrs, ps, sh } from "./format";
import { hasWindowSweep } from "./inwindow";
import { joinData } from "./join";
import { makeFrame } from "./time";
import type { BoardData, JoinedData, ViewState } from "./types";

export const FORECAST_KICKER =
  "Forecast only — the clouds’ published Spot scores. Nothing in this box is a machine we got.";
export const FORECAST_HEAD_LIKELY = "got the capacity when score said likely";
export const FORECAST_HEAD_UNLIKELY = "got it when score said not likely";
const VERDICT_TAIL =
  "A low score was never a reliable “no”: it is a forecast for Spot, not a measurement of on-demand capacity. AWS: placement score ≥ 6 counts as likely; Azure: a regional score of Medium or High.";
const AWS_LIKELY = 6;
const USABLE = 15;

export type ForecastRow = {
  readonly name: string;
  /** Bar widths in percent; 0 with no ask in the bucket. */
  readonly hiW: number;
  readonly loW: number;
  /** "{ps} of {n}", or "never ≥ 6" with no ask in the bucket. */
  readonly hi: string;
  readonly lo: string;
  /** hiW − loW. */
  readonly d: number;
  /** Both buckets hold an ask, so the verdict counts the row. */
  readonly both: boolean;
};

export type ForecastResult = {
  readonly section: "forecast";
  /** True when the window holds no sweep: nothing to say yet. */
  readonly empty: boolean;
  readonly kicker: string;
  readonly intro: string;
  readonly headHi: string;
  readonly headLo: string;
  readonly rows: readonly ForecastRow[];
  readonly verdict: string;
};

type Points = readonly (readonly number[])[];

/** The index of the first point with `h` >= x (points are sorted by `h`). */
function lowerBound(pts: Points, x: number): number {
  let lo = 0;
  let hi = pts.length;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if ((pts[mid]?.[0] ?? 0) < x) lo = mid + 1;
    else hi = mid;
  }
  return lo;
}

/** True/false: the forecast said likely; null: no point in [h − cad, h). */
function likelyAws(d: JoinedData, key: string, h: number, cad: number): boolean | null {
  const pts = d.spot[key];
  if (pts === undefined) return null;
  let sum = 0;
  let n = 0;
  for (let i = lowerBound(pts, h - cad); i < pts.length; i++) {
    const p = pts[i];
    if (p === undefined || p[0] === undefined || p[0] >= h) break;
    sum += p[1] ?? 0;
    n += 1;
  }
  return n === 0 ? null : sum / n >= AWS_LIKELY;
}

function likelyAzure(d: JoinedData, key: string, h: number, cad: number): boolean | null {
  const pts = d.azspot[key];
  if (pts === undefined) return null;
  let good = 0;
  let low = 0;
  let n = 0;
  for (let i = lowerBound(pts, h - cad); i < pts.length; i++) {
    const p = pts[i];
    if (p === undefined || p[0] === undefined || p[0] >= h) break;
    good += (p[1] ?? 0) + (p[2] ?? 0);
    low += p[3] ?? 0;
    n += 1;
  }
  return n === 0 ? null : good > low;
}

function names(list: readonly string[]): string {
  return list.join(", ");
}

export function deriveForecast(data: BoardData, state: ViewState): ForecastResult {
  const d = joinData(data);
  const f = makeFrame(d.meta, state.hz, state.page);
  const intro = `For every ask in view, the cloud’s Spot score for the same size and region in the ${hrs(f.cad)} before. AWS: placement score ≥ 6 is “likely”. Azure: a regional score of Medium or High.`;
  const rows: ForecastRow[] = [];
  for (const name of machinesOfTier(state.tier)) {
    const m = machineByName(name);
    if (m === undefined) continue;
    const aws = new Set(m.aws);
    const azure = new Set(m.azure);
    const c = { hiN: 0, hiFull: 0, loN: 0, loFull: 0 };
    for (const s of d.sweeps) {
      if (s.h < f.H0 || s.h >= f.H1 || s.state < 0) continue;
      let likely: boolean | null;
      if (s.cloud === "aws" && aws.has(s.sku)) {
        likely = likelyAws(d, `${s.sku}|${s.region}`, s.h, f.cad);
      } else if (s.cloud === "azure" && azure.has(s.sku)) {
        likely = likelyAzure(d, `${s.sku}|${s.region}`, s.h, f.cad);
      } else continue;
      if (likely === null) continue;
      if (likely) {
        c.hiN += 1;
        if (s.state === 2) c.hiFull += 1;
      } else {
        c.loN += 1;
        if (s.state === 2) c.loFull += 1;
      }
    }
    if (c.hiN + c.loN === 0) continue;
    const hiW = sh(c.hiFull, c.hiN) ?? 0;
    const loW = sh(c.loFull, c.loN) ?? 0;
    rows.push({
      name,
      hiW,
      loW,
      hi: c.hiN === 0 ? "never ≥ 6" : `${ps(hiW)} of ${c.hiN}`,
      lo: c.loN === 0 ? "never < 6" : `${ps(loW)} of ${c.loN}`,
      d: hiW - loW,
      both: c.hiN > 0 && c.loN > 0,
    });
  }
  const counted = rows.filter((r) => r.both);
  const usable = counted.filter((r) => r.d >= USABLE).map((r) => r.name);
  const little = counted.filter((r) => Math.abs(r.d) < USABLE).map((r) => r.name);
  const wrong = counted.filter((r) => r.d <= -USABLE).map((r) => r.name);
  let verdict = "";
  if (usable.length > 0) verdict += `A high score was a usable “yes” for ${names(usable)}. `;
  if (little.length > 0) verdict += `For ${names(little)} the score made little difference. `;
  if (wrong.length > 0) verdict += `For ${names(wrong)} it pointed the wrong way. `;
  verdict += VERDICT_TAIL;
  return {
    section: "forecast",
    empty: !hasWindowSweep(data),
    kicker: FORECAST_KICKER,
    intro,
    headHi: FORECAST_HEAD_LIKELY,
    headLo: FORECAST_HEAD_UNLIKELY,
    rows,
    verdict,
  };
}
