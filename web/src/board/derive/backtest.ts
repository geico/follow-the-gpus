// Section "Stay or follow? One region vs asking everywhere" (spec 0006 section 11.12).

import { aggregateAll } from "./aggregate";
import { CLOUD_NAMES, machinesOfTier } from "./catalogue";
import { plural, ps } from "./format";
import { hasWindowSweep } from "./inwindow";
import { joinData } from "./join";
import { makeFrame } from "./time";
import type { BoardData, EmptySection, ViewState } from "./types";

export const BACKTEST_INTRO =
  "Replayed round by round. “Stay” parks in the single best region — picked with hindsight, the kindest possible case for staying put. “Follow” asks every region each round and takes any yes. Share of rounds with a machine in hand.";

export const BACKTEST_HEADS = {
  stay: "stay · best single region",
  follow: "follow · any region",
  gain: "gain · regions to cover all",
} as const;

export const BACKTEST_NONE = "Not enough rounds in view to backtest.";

export type BacktestRow = {
  readonly name: string;
  /** "{cloud} {region}" of the stay place. */
  readonly place: string;
  readonly stay: string;
  readonly follow: string;
  /** Bar widths in percent. */
  readonly sW: number;
  readonly fW: number;
  /** follow − stay in points, and in rounds. */
  readonly gain: number;
  readonly gainRounds: number;
  /** "+{k} round{s}" or "none". */
  readonly gainTxt: string;
  readonly cover: number;
  readonly sole: number;
  readonly n: number;
  readonly nPlaces: number;
  readonly tip: { readonly title: string; readonly lines: readonly string[] };
};

export type BacktestResult =
  | EmptySection<"backtest">
  | {
      readonly section: "backtest";
      readonly empty: false;
      readonly intro: string;
      readonly heads: typeof BACKTEST_HEADS;
      readonly rows: readonly BacktestRow[];
      readonly verdict: string;
    };

export function deriveBacktest(data: BoardData, state: ViewState): BacktestResult {
  if (!hasWindowSweep(data)) return { section: "backtest", empty: true };
  const d = joinData(data);
  const f = makeFrame(data.meta, state.hz, state.page);
  const gl = machinesOfTier(state.tier);
  const rows: BacktestRow[] = [];
  for (const agg of aggregateAll(d, f)) {
    if (!gl.includes(agg.machine.name) || agg.mode !== "now") continue;
    const types = [...agg.machine.aws, ...agg.machine.azure];
    // round -> place key -> 1 when a sweep got > 0
    const rounds = new Map<number, Map<string, number>>();
    const placeInfo = new Map<string, string>();
    for (const s of d.sweeps) {
      if (s.h < f.H0 || s.h >= f.H1 || s.state < 0 || !types.includes(s.sku)) continue;
      const r = Math.floor(s.h / f.cad);
      const key = `${s.cloud}|${s.region}`;
      if (!placeInfo.has(key)) placeInfo.set(key, `${CLOUD_NAMES[s.cloud] ?? s.cloud} ${s.region}`);
      let m = rounds.get(r);
      if (m === undefined) {
        m = new Map();
        rounds.set(r, m);
      }
      m.set(key, Math.max(m.get(key) ?? 0, s.got > 0 ? 1 : 0));
    }
    const n = rounds.size;
    if (n < 6) continue;
    const keys = [...placeInfo.keys()];
    const yesRounds: Set<string>[] = [];
    const yesCount = new Map<string, number>(keys.map((k) => [k, 0]));
    let sole = 0;
    for (const m of rounds.values()) {
      const ys = new Set([...m].filter(([, v]) => v > 0).map(([k]) => k));
      if (ys.size === 0) continue;
      yesRounds.push(ys);
      if (ys.size === 1) sole += 1;
      for (const k of ys) yesCount.set(k, (yesCount.get(k) ?? 0) + 1);
    }
    let stayKey = keys[0] ?? "";
    let stay = -1;
    for (const k of keys) {
      const c = yesCount.get(k) ?? 0;
      if (c > stay) {
        stay = c;
        stayKey = k;
      }
    }
    const follow = yesRounds.length;
    const rescued = yesRounds.filter((ys) => !ys.has(stayKey)).length;
    // Greedy set cover of the yes rounds.
    let left = yesRounds;
    let cover = 0;
    while (left.length > 0) {
      let best = "";
      let bestN = 0;
      for (const k of keys) {
        const c = left.filter((ys) => ys.has(k)).length;
        if (c > bestN) {
          bestN = c;
          best = k;
        }
      }
      if (bestN === 0) break;
      cover += 1;
      left = left.filter((ys) => !ys.has(best));
    }
    const gainRounds = follow - stay;
    const place = placeInfo.get(stayKey) ?? "";
    const nPlaces = keys.length;
    rows.push({
      name: agg.machine.name,
      place,
      stay: ps((100 * stay) / n),
      follow: ps((100 * follow) / n),
      sW: (100 * stay) / n,
      fW: (100 * follow) / n,
      gain: (100 * gainRounds) / n,
      gainRounds,
      gainTxt: gainRounds > 0 ? `+${plural(gainRounds, "round")}` : "none",
      cover,
      sole,
      n,
      nPlaces,
      tip: {
        title: `${agg.machine.name} · stay vs follow · ${plural(n, "round")}`,
        lines: [
          `Stay: ${place} alone (the best single place, picked with hindsight) said yes in ${stay} of ${n}`,
          `Follow: asking all ${plural(nPlaces, "place")} and taking any yes gave ${follow} of ${n} — ${plural(rescued, "round")} rescued by somewhere else`,
          `${plural(sole, "round")} had exactly one place with capacity; ${plural(cover, "place")} ${cover === 1 ? "was" : "were"} enough to cover every yes`,
        ],
      },
    });
  }
  return {
    section: "backtest",
    empty: false,
    intro: BACKTEST_INTRO,
    heads: BACKTEST_HEADS,
    rows,
    verdict: verdictOf(rows),
  };
}

function verdictOf(rows: readonly BacktestRow[]): string {
  if (rows.length === 0) return BACKTEST_NONE;
  const winners = rows.filter((r) => r.gain >= 10);
  let v: string;
  if (winners.length > 0) {
    v = `Asking everywhere beat the best single region, even one chosen after the fact, for ${winners
      .map((r) => `${r.name} (${r.gainTxt})`)
      .join(", ")}. Nobody picks that region in advance, so the real gap is wider. `;
  } else {
    const most = Math.max(...rows.map((r) => r.gainRounds));
    v = `In this window, asking everywhere did not beat parking in the right region for any machine — the most it added was ${plural(most, "round")}. The catch is knowing which region in advance: `;
  }
  const rest = rows.filter((r) => r.gain < 10);
  const names = (rs: readonly BacktestRow[]) => rs.map((r) => r.name).join(", ");
  const sat = rest.filter((r) => r.fW >= 90);
  const mid = rest.filter((r) => r.fW >= 5 && r.fW < 90);
  const one = mid.filter((r) => r.cover === 1);
  const many = mid.filter((r) => r.cover > 1);
  const none = rest.filter((r) => r.fW < 5);
  if (sat.length > 0) {
    v += `${names(sat)} said yes nearly every round everywhere, so where you ask barely matters. `;
  }
  if (one.length > 0) {
    const only = one.length === 1 && one[0] !== undefined ? ` (${one[0].place})` : "";
    v += `For ${names(one)} a single region supplied every yes${only} — only one place ever had it, so the other regions added nothing but also cost nothing to ask. `;
  }
  if (many.length > 0) {
    const covers = [...new Set(many.map((r) => r.cover))].sort((x, y) => x - y).join("–");
    v += `${names(many)} needed ${covers} regions to cover every yes, but the best one alone got most of them. `;
  }
  if (none.length > 0) v += `${names(none)}: nobody had it, anywhere. `;
  return v;
}
