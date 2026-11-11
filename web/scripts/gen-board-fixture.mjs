// Write the synthetic, seeded events fixture of the board (spec 0006 section 10.4).
//
// Usage: node web/scripts/gen-board-fixture.mjs [OUT]
// OUT defaults to web/test/fixtures/board/events.json. The output has the design's data shape
// { meta, sweeps, cb, spot, azspot, price } plus the 4 meta fields of spec 0006 section 7.3.
// Each sweep carries the per-zone fields of spec 0006 section 5.3 (`gotZoneMax`, `gotZoneSum`,
// `zones`, `zonesYes`) and the design's `got`, which the join of section 7.4 sets to `gotZoneMax`.
// It is deterministic: a fixed seed, no clock, no environment. All values are invented.
import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const out = resolve(process.argv[2] ?? resolve(here, "../test/fixtures/board/events.json"));

const SEED = 533;
const HOUR = 3600e3;
// 07:00 UTC on a Tuesday: not an America/New_York midnight, so the hour rules (R6, C1, C2) show.
const T0 = Date.parse("2026-10-13T07:00:00Z");
const TEND = T0 + 503.5 * HOUR; // 3 weeks; US daylight time ends on 2026-11-01 inside the span
const NH = Math.ceil((TEND - T0) / HOUR) + 1;
const CAD = 2;
const AZURE_START = 70; // hours after t0: Azure probes begin 3 days in
const ZONE_START = 120; // AWS asks every zone from here on
const UP_START = 150; // the search goes up after a full success from here on
const LATE_START = 330; // a place flagged "late" is asked from here on (a machine unasked in the early weeks)
const CB_HORIZON = 14;
// The first hour of the latest round of the sweeps (the last sweep is at h 502; a round is 2 h).
const LATEST_H = 502;
// The check hour of the latest-round Capacity Block scenarios; it falls in the hour before the round of LATEST_H, as on real data.
const CB_LATEST_H = 501;
// The lead in hours of the 1-day check at CB_LATEST_H, or null for a check that found no block
// (spec 0006 section 11.9 best offer): under 1 h, exactly 1 h (not now: the rule is "under"), between 1 h
// and 24 h, over 24 h, and no offer.
const CB_LATEST = {
  "p6-b200.48xlarge|us-east-1": 0.5,
  "p6-b200.48xlarge|us-west-2": null,
  "p5.48xlarge|us-east-1": 26,
  "p5.48xlarge|us-east-2": null,
  "p5.4xlarge|us-east-1": 1,
  "p5.4xlarge|us-west-2": 12,
};

function mulberry32(a) {
  let s = a >>> 0;
  return () => {
    s = (s + 0x6d2b79f5) >>> 0;
    let t = s;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
const rnd = mulberry32(SEED);
// The per-zone counts use their own stream, so they leave every other draw of the fixture as it was.
const rndZone = mulberry32(SEED + 1);
const r2 = (v) => Math.round(v * 100) / 100;
const r4 = (v) => Math.round(v * 10000) / 10000;
const pick = (a) => a[Math.floor(rnd() * a.length)];
const pick2 = (a) => a[Math.floor(rndZone() * a.length)];

const COAST = {
  "us-east-1": "East",
  "us-east-2": "East",
  eastus: "East",
  eastus2: "East",
  centralus: "Central",
  northcentralus: "Central",
  southcentralus: "Central",
  "us-west-2": "West",
  westus: "West",
  westus2: "West",
  westus3: "West",
};

// One entry per machine of the catalogue; each place is [cloud, region, base yes rate or a mode].
// A mode is "quota", "restricted" (Azure size restriction), "notoffered" (state -4, an aws_odcr Region
// `restricted` row, written only since the per-zone probe, spec 0006 section 6.2), or "skunotavail" (state -3).
// A number is the base chance of a yes in a round; "nth" is the share of rounds that go wrong (state -2).
const FIXTURE = [
  // Frontier node
  {
    sku: "p6-b200.48xlarge",
    cb: [
      ["us-east-1", "jump"],
      ["us-west-2", "creep"],
    ],
  },
  {
    sku: "p5en.48xlarge",
    asked: 1,
    places: [
      ["aws", "us-east-1", "quota"],
      ["aws", "us-east-2", "quota"],
      ["aws", "us-west-2", "quota"],
    ],
  },
  {
    sku: "p5.48xlarge",
    cb: [
      ["us-east-1", "creep"],
      ["us-east-2", "creep"],
    ],
    bigChecks: true,
  },
  // p4d.24xlarge is last in the list (see the end), so the phase of every other place stays as it was
  // Single big GPU
  {
    sku: "p5.4xlarge",
    asked: 2,
    places: [
      ["aws", "us-east-1", 0.42],
      ["aws", "us-east-2", 0.3],
      ["aws", "us-west-2", 0.55],
    ],
    cb: [
      ["us-east-1", "jump"],
      ["us-west-2", "jump"],
    ],
    spot: true,
  },
  {
    sku: "Standard_NC40ads_H100_v5",
    asked: 2,
    places: [
      ["azure", "eastus", 0.2],
      ["azure", "westus3", 0.35],
      ["azure", "centralus", "restricted"],
    ],
    probe: "az_capacity_reservation",
    azspot: true,
  },
  {
    sku: "Standard_NC24ads_A100_v4",
    asked: 2,
    places: [
      ["azure", "eastus", 0.12],
      ["azure", "southcentralus", 0.08],
      ["azure", "westus2", "restricted"],
    ],
    probe: "az_capacity_reservation",
    azspot: true,
  },
  {
    sku: "g7e.2xlarge",
    asked: 1,
    places: [
      ["aws", "us-east-1", "notoffered"],
      ["aws", "us-east-2", 0.5],
      ["aws", "us-west-2", 0.45, "nth"],
    ],
  },
  // Mid
  {
    sku: "g6e.xlarge",
    asked: 2,
    places: [
      ["aws", "us-east-1", 0.14],
      ["aws", "us-east-2", 0.1],
      ["aws", "us-west-2", 0.2],
    ],
  },
  // Small
  {
    sku: "g5.xlarge",
    asked: 4,
    places: [
      ["aws", "us-east-1", 0.97],
      ["aws", "us-east-2", 0.98],
      ["aws", "us-west-2", 0.95],
    ],
    spot: true,
  },
  {
    sku: "Standard_NV36ads_A10_v5",
    asked: 2,
    places: [
      ["azure", "southcentralus", "quota"],
      ["azure", "eastus2", "skunotavail"],
      ["azure", "westus", 0.4, "nth"],
      ["azure", "northcentralus", 0.6],
    ],
    probe: "az_vmss_flex",
    azspot: true,
  },
  {
    sku: "g6.xlarge",
    asked: 4,
    places: [
      ["aws", "us-east-1", 0.6],
      ["aws", "us-east-2", 0.55],
      ["aws", "us-west-2", 0.7],
    ],
    spot: true,
  },
  {
    sku: "g4dn.xlarge",
    asked: 4,
    places: [
      ["aws", "us-east-1", 0.8],
      ["aws", "us-east-2", 0.75],
      ["aws", "us-west-2", 0.85],
    ],
    spot: true,
  },
  {
    sku: "Standard_NC4as_T4_v3",
    asked: 2,
    places: [
      ["azure", "eastus", 0.7],
      ["azure", "westus2", 0.65],
      ["azure", "northcentralus", 0.5],
    ],
    probe: "az_vmss_flex",
    azspot: true,
  },
  // p4d.24xlarge: only Region `restricted` rows (state -4), and only from LATE_START: the early weeks show
  // "not asked" and the later pages show "asked; no capacity answer" (spec 0006 sections 11.3 item 5, 11.9, 11.10)
  {
    sku: "p4d.24xlarge",
    asked: 1,
    places: [["aws", "us-east-1", "notoffered", "late"]],
  },
];

const wdET = new Intl.DateTimeFormat("en-US", { weekday: "short", timeZone: "America/New_York" });
const isWeekendET = (ms) => ["Sat", "Sun"].includes(wdET.format(new Date(ms)));
const hourUTC = (ms) => new Date(ms).getUTCHours();

// Business hours drain East and Central capacity; weekends ease it. Real UTC hour of t0 + t (R6).
function dayFactor(region, ms) {
  const hr = hourUTC(ms);
  const coast = COAST[region];
  const peak = coast === "West" ? hr >= 16 || hr === 0 : hr >= 13 && hr < 21;
  let f = peak ? 0.55 : 1;
  if (isWeekendET(ms)) f = Math.min(1.25, f * 1.5);
  return f;
}

const sweeps = [];
const firstH = {};
let placeNo = 0;
for (const m of FIXTURE) {
  for (const [cloud, region, rate, flag] of m.places ?? []) {
    placeNo++;
    const probe = cloud === "aws" ? "aws_odcr" : m.probe;
    const phase = r2(0.43 + (placeNo % 7) * 0.01);
    const start = flag === "late" ? LATE_START : cloud === "aws" ? 0 : AZURE_START;
    let last = false;
    for (let r = 0; ; r++) {
      const t = r2(start + phase + r * CAD);
      if (t > (TEND - T0) / HOUR) break;
      const h = Math.floor(t);
      const ms = T0 + t * HOUR;
      const asked = m.asked;
      const hasZones = cloud === "aws" && h >= ZONE_START;
      // An old per-zone `restricted` row is "not asked" and makes no sweep; a Region row exists since the per-zone probe.
      if (rate === "notoffered" && !hasZones) continue;
      const zones = cloud !== "aws" ? 1 : hasZones ? (region === "us-west-2" ? 2 : 3) : 1;
      const s = {
        h,
        t,
        cloud,
        region,
        sku: m.sku,
        probe,
        asked,
        got: 0,
        gotZoneMax: 0,
        gotZoneSum: 0,
        state: 0,
        nMax: 0,
        reason: null,
        err: null,
        zones,
        zonesYes: 0,
      };
      if (rate === "quota") {
        Object.assign(s, {
          state: -1,
          reason: "quota",
          err: cloud === "aws" ? "VcpuLimitExceeded" : "QuotaExceeded",
        });
      } else if (rate === "restricted") {
        Object.assign(s, { state: -1, reason: "restricted", err: "SkuNotAvailable" });
      } else if (rate === "notoffered") {
        Object.assign(s, { state: -4, zones: 0, err: "Unsupported" });
      } else if (rate === "skunotavail" && rnd() < 0.8) {
        Object.assign(s, { state: -3, err: "SkuNotAvailable" });
      } else if (flag === "nth" && rnd() < 0.12) {
        Object.assign(s, { state: -2, err: pick(["Timeout", "RequestLimitExceeded"]) });
        // An aws_odcr Region `error` row asks no zone (spec 0006 section 5.3 rule 6).
        if (hasZones) s.zones = 0;
      } else {
        const base = typeof rate === "number" ? rate : 0.3;
        const p = Math.min(0.99, (last ? 0.5 + 0.5 * base : 0.75 * base) * dayFactor(region, ms));
        const yes = rnd() < p;
        last = yes;
        if (yes) {
          const full = rnd() < 0.7;
          if (!full && asked < 2) {
            // A halving yes needs an ask of 2 or more: with 1 machine the best zone placed all it asked,
            // so the rows give state 2 (spec 0006 section 5.3 rule 4). No draw is used here,
            // so every other value of the fixture stays as it was.
            s.state = 2;
            s.got = asked;
            s.nMax = asked;
          } else if (full) {
            s.state = 2;
            s.got = asked;
            s.nMax = h >= UP_START && rnd() < 0.5 ? asked * pick([2, 4, 8]) : asked;
          } else {
            // a halving yes: the first ask failed, a smaller one worked
            s.state = 1;
            s.got = Math.max(1, Math.floor(asked / 2));
            s.nMax = s.got;
          }
          s.zonesYes = zones > 1 ? 1 + Math.floor(rnd() * zones) : 1;
          // The zones that said yes: the best one is the headline `got` (gotZoneMax), the sum is a lower bound.
          const others = Array.from({ length: s.zonesYes - 1 }, () =>
            s.state === 2
              ? pick2([asked, Math.max(1, Math.floor(asked / 2))])
              : 1 + Math.floor(rndZone() * s.got),
          );
          s.gotZoneMax = s.got;
          s.gotZoneSum = s.got + others.reduce((a, b) => a + b, 0);
        }
      }
      sweeps.push(s);
      if (s.state !== -4) firstH[m.sku] = Math.min(firstH[m.sku] ?? Infinity, h);
    }
  }
}
const cmp = (a, b) => (a < b ? -1 : a > b ? 1 : 0);
// The latest round of 1 machine is all state -1 with reason "restricted" (spec 0006 section 11.9, plan of the
// quota answer, correction C12). It draws no random value, so every other value of the fixture stays as it was.
for (const s of sweeps)
  if (s.sku === "Standard_NC24ads_A100_v4" && s.h >= LATEST_H)
    Object.assign(s, {
      state: -1,
      reason: "restricted",
      err: "SkuNotAvailable",
      got: 0,
      gotZoneMax: 0,
      gotZoneSum: 0,
      nMax: 0,
      zonesYes: 0,
    });
// The zoneList of spec 0006 section 5.3: an aws_odcr sweep that asked zones names them, sorted by
// zone. The zones that said yes come first in a rotating order; the best one holds gotZoneMax and the others
// share the rest of gotZoneSum. It draws no random value, so every other value of the fixture stays as it was.
for (const s of sweeps) {
  if (s.cloud !== "aws" || s.probe !== "aws_odcr" || s.h < ZONE_START || s.zones < 1) continue;
  const names = Array.from({ length: s.zones }, (_, i) => `${s.region}${"abc"[i]}`);
  const got = names.map(() => 0);
  if (s.zonesYes > 0) {
    const rest = s.gotZoneSum - s.gotZoneMax;
    const others = s.zonesYes - 1;
    for (let j = 0; j < s.zonesYes; j++) {
      const at = (s.h + j) % s.zones;
      got[at] = j === 0 ? s.gotZoneMax : Math.floor(rest / others) + (j <= rest % others ? 1 : 0);
    }
  }
  s.zoneList = names.map((zone, i) => ({ zone, got: got[i] }));
}
// The cases of spec 0006 section 8.4 item 2 that the sweeps show: the run fails if one is missing.
const CASES = {
  "a multi-zone Region where 2 or more zones placed (gotZoneSum > gotZoneMax)": (s) =>
    s.zones > 1 && s.zonesYes > 1 && s.gotZoneSum > s.gotZoneMax,
  "a multi-zone Region with 1 zone that placed and another that did not (mixed zones)": (s) =>
    s.zones > 1 && s.zonesYes === 1 && s.state > 0,
  "a multi-zone Region where no zone placed": (s) => s.zones > 1 && s.state === 0,
  "a row before the per-zone probe (1 zone)": (s) =>
    s.cloud === "aws" &&
    s.probe === "aws_odcr" &&
    s.h < ZONE_START &&
    s.zones === 1 &&
    s.state >= 0,
  "an aws_odcr Region restricted row (state -4, zones 0)": (s) => s.state === -4 && s.zones === 0,
  "an aws_odcr Region error row (state -2, zones 0)": (s) =>
    s.state === -2 && s.cloud === "aws" && s.zones === 0,
  "an Azure restricted row (state -1)": (s) => s.state === -1 && s.reason === "restricted",
  "a quota row (state -1)": (s) => s.state === -1 && s.reason === "quota",
  "a restricted_or_no_capacity row (state -3)": (s) => s.state === -3,
};
for (const [name, test] of Object.entries(CASES))
  if (!sweeps.some(test)) throw new Error(`gen-board-fixture: no sweep for the case: ${name}`);
sweeps.sort(
  (a, b) =>
    a.h - b.h ||
    a.t - b.t ||
    cmp(a.cloud, b.cloud) ||
    cmp(a.region, b.region) ||
    cmp(a.sku, b.sku) ||
    cmp(a.probe, b.probe),
);
for (const m of FIXTURE)
  if (m.places) firstH[m.sku] = Math.min(...sweeps.filter((s) => s.sku === m.sku).map((s) => s.h));

// Capacity Block checks: the next free 1-day slot is a fixed calendar day. "creep": it gets closer all
// window. "jump": somebody buys it now and then and the next free slot is later.
const cb = [];
const cbFirstH = {};
const longestSteps = [24, 48, 72, 168, 336];
for (const m of FIXTURE) {
  if (!m.cb) continue;
  for (const [region, kind] of m.cb) {
    const checks = [];
    const longest = [];
    let slotDay = 5 + rnd() * 4; // absolute day (from t0) of the next free slot
    const stagger = Math.floor(rnd() * 3);
    for (let h = stagger; h < NH - 1; h += 3) {
      if (kind === "jump" && rnd() < 0.045) slotDay += 1.5 + rnd() * 3;
      if (slotDay - h / 24 < 0.3) slotDay = h / 24 + 3 + rnd() * 6;
      const found = r2(slotDay - h / 24) <= CB_HORIZON ? 1 : 0;
      // Every 40th check of a place offers a block that starts 30 minutes after it: a
      // start-now offer. The slot day is not changed. Else the lead is the slot's lead.
      const startNow = found && (h - stagger) % 40 === 0;
      const lead = startNow ? r2(0.5 / 24) : r2(slotDay - h / 24);
      checks.push([
        h,
        found,
        found ? lead : null,
        24,
        found ? r2(100 + rnd() * 60) : null,
        CB_HORIZON,
        1,
        found ? (startNow ? 0.5 : Math.round((slotDay - h / 24) * 24 * 1e4) / 1e4) : null,
      ]);
      if (m.bigChecks && h % 6 === stagger % 6) {
        const f2 = rnd() < 0.5 ? 1 : 0;
        const lead2 = f2 ? r2(lead + 2 + rnd() * 2) : null;
        checks.push([
          h,
          f2,
          lead2,
          48,
          f2 ? r2(260 + rnd() * 80) : null,
          CB_HORIZON,
          2,
          lead2 === null ? null : Math.round(lead2 * 24 * 1e4) / 1e4,
        ]);
      }
      if (h % 12 === stagger % 12) {
        const f3 = rnd() < 0.85 ? 1 : 0;
        const lh = pick(longestSteps);
        const ld = f3 ? r2(lead + rnd() * 2) : null;
        longest.push([
          h,
          f3,
          ld,
          f3 ? lh : null,
          f3 ? 24 : null,
          f3 ? new Date(T0 + (h + ld * 24) * HOUR).toISOString().slice(0, 10) : null,
        ]);
      }
    }
    checks.sort((a, b) => a[0] - b[0] || a[3] - b[3] || a[6] - b[6]);
    cb.push({ sku: m.sku, region, checks, longest });
    const one = checks.filter((c) => c[3] === 24 && c[6] === 1).map((c) => c[0]);
    cbFirstH[m.sku] = Math.min(cbFirstH[m.sku] ?? Infinity, ...one);
  }
}
// The latest-round scenarios: no random value is drawn, so the earlier checks stay as they were.
for (const p of cb) {
  const key = `${p.sku}|${p.region}`;
  if (!(key in CB_LATEST)) continue;
  const lead = CB_LATEST[key];
  p.checks = p.checks.filter((c) => !(c[0] >= CB_LATEST_H && c[3] === 24 && c[6] === 1));
  p.checks.push([
    CB_LATEST_H,
    lead === null ? 0 : 1,
    lead === null ? null : r2(lead / 24),
    24,
    lead === null ? null : 120.5,
    CB_HORIZON,
    1,
    lead,
  ]);
  p.checks.sort((a, b) => a[0] - b[0] || a[3] - b[3] || a[6] - b[6]);
}
cb.sort((a, b) => cmp(a.sku, b.sku) || cmp(a.region, b.region));

// The span the rows give (spec 0006 section 5.2 item 3, section 7.3): tEnd is the latest row that makes a sweep
// or a Capacity Block record. An error sweep makes no anchor row of its own.
// A sweep with a yes also makes a second row 1 s later (the zone results of spec 0006 section 5.3).
const lastMs = Math.max(
  ...sweeps
    .filter((s) => s.state !== -2)
    .map((s) => Math.round(s.t * HOUR) + (s.state > 0 ? 1000 : 0)),
  ...cb.flatMap((c) => [...c.checks, ...c.longest].map((r) => Math.round(r[0] * HOUR))),
);
const SPAN_TEND = T0 + lastMs;
const SPAN_NH = Math.ceil((SPAN_TEND - T0) / HOUR) + 1;

// Spot placement scores. AWS: [h, mean score] every 2 hours, with gaps. Azure: [h, high, medium, low, other].
const yesAt = new Map();
for (const s of sweeps)
  yesAt.set(`${s.sku}|${s.region}|${Math.floor(s.h / 2)}`, s.state > 0 ? 1 : 0);
const spot = {};
const azspot = {};
for (const m of FIXTURE) {
  for (const [cloud, region] of m.places ?? []) {
    if (cloud === "aws" && m.spot) {
      const pts = [];
      for (let h = 0; h < NH; h += 2) {
        if (h > 200 && h < 224) continue; // a gap
        const y = yesAt.get(`${m.sku}|${region}|${Math.floor(h / 2) + 1}`) ?? 0;
        pts.push([h, r2(Math.min(10, Math.max(1, 3.2 + 4.5 * y + rnd() * 2.5)))]);
      }
      spot[`${m.sku}|${region}`] = pts;
    }
    if (cloud === "azure" && m.azspot) {
      const pts = [];
      for (let h = AZURE_START - 2; h < NH; h += 2) {
        const y = yesAt.get(`${m.sku}|${region}|${Math.floor(h / 2) + 1}`) ?? 0;
        const hi = y ? 2 + Math.floor(rnd() * 3) : 0;
        const lo = y ? 0 : 3 + Math.floor(rnd() * 2);
        pts.push([h, hi, y ? 1 : 0, lo, rnd() < 0.05 ? 1 : 0]);
      }
      azspot[`${m.sku}|${region}`] = pts;
    }
  }
}

// Spot prices: one value per UTC date; Azure changes once, on the first of the month.
const dates = [];
for (let k = 0, d0 = Math.floor(T0 / (24 * HOUR)) * 24 * HOUR; d0 + k * 24 * HOUR <= SPAN_TEND; k++)
  dates.push(new Date(d0 + k * 24 * HOUR).toISOString().slice(0, 10));
const priceSeries = {};
const PRICES = [
  ["p5.4xlarge", "aws", 4.05],
  ["g5.xlarge", "aws", 0.42],
  ["g6.xlarge", "aws", 0.31],
  ["g6e.xlarge", "aws", 0.83],
  ["g7e.2xlarge", "aws", 1.9],
  ["g4dn.xlarge", "aws", 0.2],
  ["Standard_NC40ads_H100_v5", "azure", 3.2],
  ["Standard_NC24ads_A100_v4", "azure", 1.1],
  ["Standard_NV36ads_A10_v5", "azure", 0.95],
  ["Standard_NC4as_T4_v3", "azure", 0.18],
];
for (const [sku, cloud, base] of PRICES) {
  let level = base;
  priceSeries[`${sku}|${cloud}`] = dates.map((d, i) => {
    if (cloud === "azure") return d >= "2026-11-01" ? r4(base * 1.04) : base;
    if (i === 3 && sku === "p5.4xlarge") return null; // a date with no row
    level = Math.max(base * 0.7, level * (1 + (rnd() - 0.5) * 0.04));
    return r4(level);
  });
}

const weekends = dates.filter((d) => wdET.format(new Date(`${d}T12:00:00Z`)) === "Sat").length;
const probeStartH = {};
for (const s of sweeps) {
  const key = `${s.cloud}:${s.probe}`;
  probeStartH[key] = Math.min(probeStartH[key] ?? Infinity, s.t);
}
const regions = [
  ...new Set([...sweeps.map((s) => `${s.cloud}|${s.region}`), ...cb.map((c) => `aws|${c.region}`)]),
].sort();
const nPrice = Object.values(priceSeries).reduce(
  (n, a) => n + a.filter((v) => v != null).length,
  0,
);
const nPts = (o) => Object.values(o).reduce((n, a) => n + a.length, 0);
const nRows =
  sweeps.length +
  cb.reduce((n, c) => n + c.checks.length + c.longest.length, 0) +
  nPts(spot) +
  nPts(azspot) +
  nPrice;
const upStartH = Math.min(...sweeps.filter((s) => s.nMax > s.asked).map((s) => s.h));
const zoneStartH = Math.min(
  ...sweeps.filter((s) => s.cloud === "aws" && s.zones > 1).map((s) => s.h),
);
const sortedKeys = (o) => Object.fromEntries(Object.entries(o).sort(([a], [b]) => cmp(a, b)));

const meta = {
  t0: new Date(T0).toISOString(),
  tEnd: new Date(SPAN_TEND).toISOString(),
  nh: SPAN_NH,
  dates,
  weekends,
  cadenceH: { launch: CAD, cb: 3 },
  probeStartH: sortedKeys(probeStartH),
  zones: ["us-east-1a", "us-east-2a", "us-west-2a"],
  cbHorizonDays: [CB_HORIZON],
  regions,
  rows: nRows,
  sweeps: sweeps.length,
  firstH: sortedKeys(firstH),
  cbFirstH: sortedKeys(cbFirstH),
  upStartH,
  zoneStartH,
  simulated: true, // the rows are invented; the board then shows its banner
};

const lines = (a) => `[\n${a.map((v) => `    ${JSON.stringify(v)}`).join(",\n")}\n  ]`;
const dict = (o) =>
  `{\n${Object.entries(o)
    .map(([k, v]) => `    ${JSON.stringify(k)}: ${JSON.stringify(v)}`)
    .join(",\n")}\n  }`;
const text = `{
  "meta": ${JSON.stringify(meta)},
  "sweeps": ${lines(sweeps)},
  "cb": ${lines(cb)},
  "spot": ${dict(sortedKeys(spot))},
  "azspot": ${dict(sortedKeys(azspot))},
  "price": {
    "dates": ${JSON.stringify(dates)},
    "series": ${dict(sortedKeys(priceSeries))}
  }
}
`;
mkdirSync(dirname(out), { recursive: true });
writeFileSync(out, text);
console.log(`gen-board-fixture: wrote ${out} (${text.length} bytes, ${sweeps.length} sweeps)`);
