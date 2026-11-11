// Section "So what" card (spec 0006 section 11.10): 1 line per machine of the tier. It reads the
// typed results of the stickiness (`e.sticky`), substitutes (`e.subs`), and sawtooth (`e.saw`)
// sections and does not compute their numbers again.

import {
  aggregateAll,
  type BlockPlace,
  type MachineAgg,
  NOW_LEAD_H,
  type Place,
} from "./aggregate";
import { CLOUD_NAMES, cbHorizon, machinesOfTier } from "./catalogue";
import { hrs, plural, ps, sh } from "./format";
import { hasWindowData } from "./inwindow";
import { joinData } from "./join";
import { deriveSawtooth, type SawtoothResult } from "./sawtooth";
import { deriveStickiness, type StickyRow } from "./stickiness";
import { deriveSubstitutes, type SubstitutesResult } from "./substitutes";
import { type Frame, makeFrame } from "./time";
import type { BoardData, EmptySection, JoinedData, ViewState } from "./types";

export const SOWHAT_KICKER = "So what — one line per machine, from the window in view";

export type Plan = { readonly name: string; readonly text: string };

export type SowhatResult =
  | EmptySection<"sowhat">
  | {
      readonly section: "sowhat";
      readonly empty: false;
      readonly kicker: string;
      /** `e.plans` of the design: 1 line per machine of the tier, in catalogue order. */
      readonly plans: readonly Plan[];
    };

const cloudName = (cloud: string) => CLOUD_NAMES[cloud] ?? cloud;

/** The number of a printed percent: "<1%" is 1, "62%" is 62 (section 11.10 item 3). */
const printed = (s: string) => Number(s.replace(/[<%]/g, ""));

/** Item 1: modes `none` and `quota`, which give the whole line. */
function onlyLine(agg: MachineAgg, d: JoinedData, f: Frame): string | null {
  if (agg.mode === "none") {
    // The places in the order of their first sweep of state -3 or -4 in the window.
    const types = [...agg.machine.aws, ...agg.machine.azure];
    const first = new Map<string, number>();
    for (const s of d.sweeps) {
      if (s.h < f.H0 || s.h >= f.H1 || !types.includes(s.sku)) continue;
      if (s.state !== -3 && s.state !== -4) continue;
      const key = `${s.cloud}|${s.region}`;
      first.set(key, Math.min(first.get(key) ?? s.h, s.h));
    }
    const at = (p: Place) => first.get(`${p.cloud}|${p.region}`) ?? Number.POSITIVE_INFINITY;
    const silent = agg.places.filter((p) => p.no > 0 || p.nu > 0).sort((a, b) => at(a) - at(b));
    if (silent.length === 0) return "No probes for this machine in the window shown.";
    const words = silent.map(
      (p) => `${cloudName(p.cloud)} ${p.region} ${p.no > 0 ? "not offered" : "not available"}`,
    );
    return `No capacity answer in the window shown: ${words.join(", ")}.`;
  }
  if (agg.mode === "quota") {
    const regions = agg.places.filter((p) => p.n > 0).length;
    return `Ask for a quota increase first; every probe stopped at our own limit (${plural(regions, "region")}).`;
  }
  return null;
}

/** The place with the highest `yes` / (`n` − `na`); the first on a tie. */
function bestPlace(places: readonly Place[]): { place: Place; p: number } | null {
  let best: { place: Place; p: number } | null = null;
  for (const place of places) {
    const p = sh(place.yes, place.n - place.na);
    if (p === null) continue;
    if (best === null || p > best.p) best = { place, p };
  }
  return best;
}

/** Item 3: the retry text from the stickiness row, each value used only with n >= 3. */
function retryText(row: StickyRow | undefined, cad: number): string {
  if (row === undefined || row.bn < 3) return "";
  const b = printed(row.b);
  let out: string;
  if (b <= 10)
    out = ` After a no, only ${row.b} of next rounds said yes: a no means wait, not retry — check back next day, not next hour.`;
  else if (b >= 50)
    out = ` After a no, ${row.b} of next rounds said yes anyway: a no is noise, retry everywhere ${hrs(cad)} later.`;
  else
    out = ` After a no, ${row.b} of next rounds said yes: retry everywhere every ${hrs(cad)}, but keep a fallback.`;
  if (row.an >= 3 && printed(row.a) - b >= 20)
    out += ` A yes ${hrs(cad)} ago is worth more than any score: when you get one, take it — ${row.a} of the time it is still there next round, median streak ${row.streak}.`;
  return out;
}

/** Item 2: the on-demand verdict with its retry text; null outside mode `now`. */
function onDemand(agg: MachineAgg, row: StickyRow | undefined, cad: number): string | null {
  const { tried, yes, waitH } = agg.counts;
  if (agg.mode !== "now" || tried === 0) return null;
  const share = yes / tried;
  if (share === 0)
    return `Nobody handed us one in ${plural(tried, "round")}; do not plan on demand.`;
  const best = bestPlace(agg.places);
  const where = best === null ? "–" : `${cloudName(best.place.cloud)} ${best.place.region}`;
  const bestPs = ps(best?.p ?? null);
  let head: string;
  if (share >= 0.9)
    head = `Launch on demand; ${where} handed us ${bestPs} of asks. Reserve nothing.`;
  else if (share >= 0.4)
    head = `On demand works ${ps(100 * share)} of rounds if you ask everywhere; best single place ${where} only ${bestPs}.`;
  else
    head = `On demand is a long shot: ${ps(100 * share)} of rounds even asking everywhere; longest drought ${hrs(waitH === 0 ? cad : waitH)}.`;
  return head + retryText(row, cad);
}

/**
 * The block place with the highest `nowFound` / `n` (the now rule), then the highest
 * `found` / `n`, then the lowest `medLead` (null as 99).
 */
export function bestBlock(blocks: readonly BlockPlace[]): BlockPlace | null {
  let best: BlockPlace | null = null;
  for (const b of blocks) {
    if (b.n === 0) continue;
    if (best === null) {
      best = b;
      continue;
    }
    const rank = (x: BlockPlace) => [x.nowFound / x.n, x.found / x.n, -(x.medLead ?? 99)];
    const p = rank(b);
    const q = rank(best);
    for (let i = 0; i < p.length; i++) {
      if ((p[i] ?? 0) === (q[i] ?? 0)) continue;
      if ((p[i] ?? 0) > (q[i] ?? 0)) best = b;
      break;
    }
  }
  return best;
}

/** The sawtooth note of item 4, for the sawtooth machine only. */
function sawNote(saw: SawtoothResult, name: string): string {
  if (saw.empty || saw.machine !== name) return "";
  const rows = saw.rows.filter((r) => r.found > 0);
  if (rows.length === 0) return "";
  const j = rows.filter((r) => r.jumps > 0).length;
  if (j === 0)
    return " The next free slot crept closer all window without anyone buying it: you can wait for a nearer date without losing it.";
  const where =
    j === rows.length ? "in every region" : `in ${j} of ${plural(rows.length, "region")}`;
  return ` The next free slot got bought out from under us ${where} — book the moment you see a date you can live with; it will not wait.`;
}

/** Item 4: the Capacity Block text; null when there is nothing to say. */
export function blockText(
  agg: MachineAgg,
  saw: SawtoothResult,
  horizonDays: number,
): string | null {
  const best = bestBlock(agg.blockPlaces);
  if (best !== null && best.found > 0) {
    if (best.nowFound > 0)
      return `For a sure thing, a Capacity Block in ${best.region} started within ${NOW_LEAD_H} h on ${ps(sh(best.nowFound, best.n))} of checks.${sawNote(saw, agg.machine.name)}`;
    const lead = best.medLead === null ? "–" : hrs(best.medLead * 24);
    return `No Capacity Block started within ${NOW_LEAD_H} h of a check. Book ahead: ${ps(sh(best.found, best.n))} of checks in ${best.region} had a block, starting ~${lead} out.${sawNote(saw, agg.machine.name)}`;
  }
  if (best !== null)
    return `No check found a Capacity Block within ${plural(horizonDays, "day")} in any region.`;
  const { tried, yes } = agg.counts;
  if (agg.mode === "now" && tried > 0 && yes / tried < 0.9)
    return "We check no Capacity Block for it, so there is no sure-thing path we could probe.";
  return null;
}

/** Item 5: the substitute text, for the focus machine only. */
function subText(subs: SubstitutesResult, name: string, focus: string): string | null {
  if (name !== focus || subs.empty || subs.noRounds === 0) return null;
  const top = subs.rows[0];
  if (top === undefined || top.pp === null || top.pp <= 0) return null;
  return `When it says no, ${top.name} was available in ${top.big} of those rounds — a different machine, if the job can take it.`;
}

export function deriveSowhat(data: BoardData, state: ViewState): SowhatResult {
  if (!hasWindowData(data)) return { section: "sowhat", empty: true };
  const f = makeFrame(data.meta, state.hz, state.page);
  const gl = machinesOfTier(state.tier);
  const sticky = deriveStickiness(data, state);
  const subs = deriveSubstitutes(data, state);
  const saw = deriveSawtooth(data, state);
  const horizonDays = cbHorizon(data.meta.cbHorizonDays);
  const plans: Plan[] = [];
  const d = joinData(data);
  for (const agg of aggregateAll(d, f)) {
    const name = agg.machine.name;
    if (!gl.includes(name)) continue;
    const only = onlyLine(agg, d, f);
    if (only !== null) {
      plans.push({ name, text: only });
      continue;
    }
    const row = sticky.empty ? undefined : sticky.rows.find((r) => r.name === name);
    const parts = [
      onDemand(agg, row, f.cad),
      blockText(agg, saw, horizonDays),
      subText(subs, name, state.gpu),
    ].filter((p): p is string => p !== null);
    plans.push({ name, text: parts.join(" ") });
  }
  return { section: "sowhat", empty: false, kicker: SOWHAT_KICKER, plans };
}
