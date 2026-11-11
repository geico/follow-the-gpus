// Turns the events fixture of the board (web/test/fixtures/board/events.json) into fixture
// rows. The direction is events to rows: the board views are made from the events, so the rows must
// carry the events, and the server then has to give them back. The converter shares no code with
// web/sql/. All rows are synthetic.

import type { FixtureRow } from "./fixture";

export type EventSweep = {
  h: number;
  t: number;
  cloud: string;
  region: string;
  sku: string;
  probe: string;
  asked: number;
  got?: number;
  gotZoneMax?: number;
  gotZoneSum?: number;
  state: number;
  nMax: number;
  reason: string | null;
  err: string | null;
  zones: number;
  zonesYes: number;
};

export type Events = {
  meta: Record<string, unknown> & { t0: string; zoneStartH: number | null };
  sweeps: EventSweep[];
  cb: {
    sku: string;
    region: string;
    checks: (number | null)[][];
    longest: (number | string | null)[][];
  }[];
  spot: Record<string, [number, number][]>;
  azspot: Record<string, number[][]>;
  price: { dates: string[]; series: Record<string, (number | null)[]> };
};

const HOUR_MS = 3_600_000;
const iso = (ms: number): string => new Date(ms).toISOString().replace(".000Z", "Z");

/**
 * A sweep that no rows can make: the SQL rule gives state 1 only when the best zone placed less
 * than it asked (spec 0006 section 5.3 rule 4). The generator makes none.
 */
export function isUnrealizable(s: EventSweep): boolean {
  return s.state === 1 && (s.gotZoneMax ?? s.got ?? 0) >= s.asked;
}

/** The per-zone results of a yes: the best zone first, the sum as given or `got` for each zone. */
function zoneResults(got: number, yes: number, sum: number): number[] {
  const out = [got];
  let rest = sum - got;
  for (let i = 1; i < yes; i++) {
    const left = yes - 1 - i;
    const v = Math.max(1, Math.min(got, rest - left));
    out.push(v);
    rest -= v;
  }
  return out;
}

/** The rows of 1 sweep (spec 0006 section 5.3): 1 run, 1 zone search for each zone. */
function sweepRows(
  s: EventSweep,
  i: number,
  t0Ms: number,
  zoneStartH: number | null,
): FixtureRow[] {
  const got = s.gotZoneMax ?? s.got ?? 0;
  const ts = iso(t0Ms + Math.round(s.t * HOUR_MS));
  const later = iso(t0Ms + Math.round(s.t * HOUR_MS) + 1000);
  const base = {
    provider: s.cloud as "aws" | "azure",
    probe: s.probe,
    sku: s.sku,
    region: s.region,
    run: `s${i}`,
  };
  const perZone = s.cloud === "aws" && zoneStartH !== null && s.h >= zoneStartH;
  const zoneOf = (j: number) =>
    s.cloud !== "aws" ? null : perZone ? `${s.region}${"abcdef"[j]}` : `${s.region}a`;
  const idOf = (j: number) => (perZone ? { zone_id: `${s.region}-az${j + 1}` } : {});
  if (s.zones === 0) {
    // an aws_odcr Region row: no zone, no attempt
    const outcome = s.state === -4 ? "restricted" : "error";
    return [{ ...base, ts, zone: null, n: s.asked, outcome, err: s.err }];
  }
  const rows: FixtureRow[] = [];
  const yes = s.state > 0 ? zoneResults(got, s.zonesYes, s.gotZoneSum ?? got * s.zonesYes) : [];
  for (let j = 0; j < s.zones; j++) {
    const zone = zoneOf(j);
    const details = idOf(j);
    const v = yes[j];
    if (v !== undefined) {
      if (v < s.asked) {
        rows.push({ ...base, ts, zone, n: s.asked, outcome: "no_capacity", details });
        rows.push({
          ...base,
          ts: later,
          zone,
          n: v,
          placed: v,
          outcome: "success",
          details: { ...details, search_step: 2 },
        });
      } else {
        rows.push({ ...base, ts, zone, n: s.asked, placed: s.asked, outcome: "success", details });
        if (j === 0 && s.nMax > s.asked) {
          rows.push({
            ...base,
            ts: later,
            zone,
            n: s.nMax,
            placed: s.nMax,
            outcome: "success",
            details: { ...details, search_step: 2, search_direction: "up" },
          });
        }
      }
      continue;
    }
    const outcome =
      s.state >= 0
        ? "no_capacity"
        : s.state === -1
          ? (s.reason ?? "quota")
          : s.state === -3
            ? "restricted_or_no_capacity"
            : "error";
    rows.push({ ...base, ts, zone, n: s.asked, outcome, err: s.err, details });
  }
  return rows;
}

/** All the rows of the events: the sweeps, the Capacity Block records, Spot scores, prices. */
export function eventsToRows(e: Events): FixtureRow[] {
  const t0Ms = Date.parse(e.meta.t0);
  const rows: FixtureRow[] = [];
  e.sweeps.forEach((s, i) => {
    rows.push(...sweepRows(s, i, t0Ms, e.meta.zoneStartH));
  });
  for (const p of e.cb) {
    const at = (h: number) => t0Ms + h * HOUR_MS;
    const none = <T>(x: T | null | undefined) => x ?? null;
    const base = { provider: "aws" as const, sku: p.sku, region: p.region };
    for (const c of p.checks) {
      const [h0, found, lead, dur = null, fee = null, horizon = null, nReq = null, leadH = null] =
        c;
      const h = h0 ?? 0;
      const details: Record<string, unknown> = {};
      // The lead in hours (check element 7) is exact; the lead in days is rounded to 2 decimals.
      if (found === 1 && leadH !== null) details.start = iso(at(h) + leadH * 3_600_000);
      else if (found === 1 && lead !== null && lead !== undefined)
        details.start = iso(at(h) + lead * 86_400_000);
      if (dur !== null) details.duration_hours = dur;
      if (fee !== null) details.upfront_fee = fee;
      if (horizon !== null) details.start_window_days = horizon;
      rows.push({
        ...base,
        probe: "aws_cb_offering",
        ts: iso(at(h)),
        n: none(nReq),
        outcome: found === 1 ? "success" : "none",
        details,
      });
    }
    for (const l of p.longest) {
      const [h0, found, lead, longestH = null, earliestH = null] = l as (number | null)[];
      const h = h0 ?? 0;
      const details: Record<string, unknown> = {};
      if (lead !== null && lead !== undefined)
        details.earliest_start = iso(at(h) + lead * 86_400_000);
      if (longestH !== null) details.longest_duration_hours = longestH;
      if (earliestH !== null) details.earliest_duration_hours = earliestH;
      rows.push({
        ...base,
        probe: "aws_cb_longest",
        ts: iso(at(h)),
        outcome: found === 1 ? "success" : "none",
        details,
      });
    }
  }
  for (const [key, pts] of Object.entries(e.spot)) {
    const [sku = "", region = ""] = key.split("|");
    for (const [h = 0, score = 0] of pts) {
      rows.push({
        provider: "aws",
        probe: "aws_spot_score",
        sku,
        region,
        ts: iso(t0Ms + h * HOUR_MS),
        outcome: "success",
        details: { score: Math.round(score) },
      });
    }
  }
  for (const [key, pts] of Object.entries(e.azspot)) {
    const [sku = "", region = ""] = key.split("|");
    for (const [h = 0, ...counts] of pts) {
      ["High", "Medium", "Low", "Odd"].forEach((score, k) => {
        for (let n = 0; n < (counts[k] ?? 0); n++) {
          rows.push({
            provider: "azure",
            probe: "az_spot_score",
            sku,
            region,
            ts: iso(t0Ms + h * HOUR_MS),
            outcome: "success",
            details: { score },
          });
        }
      });
    }
  }
  for (const [key, vals] of Object.entries(e.price.series)) {
    const [sku = "", cloud = ""] = key.split("|");
    vals.forEach((price, i) => {
      if (price === null) return;
      rows.push({
        provider: cloud as "aws" | "azure",
        probe: cloud === "aws" ? "aws_spot_price" : "az_spot",
        sku,
        region: cloud === "aws" ? "us-east-1" : "eastus",
        ts: `${e.price.dates[i]}T12:00:00Z`,
        outcome: "success",
        details: { price },
      });
    });
  }
  // The fixture says its rows are invented; the server then reports `simulated`.
  if (e.meta.simulated) for (const r of rows) r.sim = true;
  return rows;
}

/** The events a server can give back: the spot scores are integers. */
export function realizable(e: Events): Events {
  return {
    ...e,
    sweeps: e.sweeps.map((s) => {
      const got = s.gotZoneMax ?? s.got ?? 0;
      return {
        ...s,
        gotZoneMax: got,
        gotZoneSum: s.gotZoneSum ?? got * s.zonesYes,
      };
    }),
    spot: Object.fromEntries(
      Object.entries(e.spot).map(([k, pts]) => [
        k,
        pts.map(([h = 0, v = 0]) => [h, Math.round(v)] as [number, number]),
      ]),
    ),
  };
}

const ANCHORS = new Set([
  "aws_odcr",
  "az_capacity_reservation",
  "az_vmss_flex",
  "aws_cb_offering",
  "aws_cb_longest",
]);

/**
 * The span the rows give (spec 0006 section 5.2 item 3, section 7.3): tEnd is the latest row that
 * makes a sweep or a Capacity Block record, and nh counts the hours up to it. The generator sets
 * both the same way.
 */
export function spanOf(rows: readonly FixtureRow[], t0: string): { tEnd: string; nh: number } {
  const last = Math.max(
    ...rows
      .filter(
        (r) =>
          ANCHORS.has(r.probe) &&
          r.outcome !== "error" &&
          !(r.probe === "aws_odcr" && r.outcome === "restricted" && r.zone),
      )
      .map((r) => Date.parse(r.ts)),
  );
  return {
    tEnd: new Date(last).toISOString(),
    nh: Math.ceil((last - Date.parse(t0)) / HOUR_MS) + 1,
  };
}
