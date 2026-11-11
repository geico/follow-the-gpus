// Section "Can I get one right now, anywhere?" (spec 0006 section 11.9): the answer of
// each machine from the latest round, the suggestion, the window column, and the open rows. The
// answer and the suggestion read `meta.now` only; the window column and the open rows read the
// window. The text and the numbers follow spec 0006 section 11.9.
// The zone rows are not built. The view only draws what is computed here.

import { aggregateMachine, type BlockPlace, countsOf, type Place } from "./aggregate";
import {
  CLOUD_NAMES,
  cbHorizon,
  gpusPerMachine,
  MACHINES,
  type Machine,
  machinesOfTier,
  TIER_SUBS,
  TIERS,
} from "./catalogue";
import { dateLabel, hrs, median, plural, ps, tone, weekdayName } from "./format";
import { joinData } from "./join";
import { cellLabel, type Frame, hourInstant, makeFrame, NEW_YORK, zoneParts } from "./time";
import type {
  BoardData,
  EmptySection,
  JoinedData,
  JoinedMeta,
  JoinedSweep,
  NowCb,
  NowPlace,
  ViewState,
} from "./types";

export const NOW_TITLE = "Can I get one right now, anywhere?";

const HOUR = 3600e3;
const NO_LEAD = 1e9;

export type NowKind = "yes" | "soon" | "book" | "quota" | "no" | "none";

/** The icon classes of the design (Phosphor, spec 0006 decision R12). */
const ICON: Readonly<Record<string, string>> = {
  yes: "ph-fill ph-check-circle",
  no: "ph ph-x-circle",
  soon: "ph ph-clock",
  book: "ph ph-calendar-blank",
  quota: "ph ph-lock-simple",
  none: "ph ph-minus-circle",
};

const WORD: Readonly<Record<NowKind, string>> = {
  yes: "Yes",
  no: "No",
  soon: "Not now",
  book: "Book ahead",
  quota: "Our quota",
  none: "Not asked",
};

const PROBE_SUB: Readonly<Record<string, string>> = {
  aws_odcr: "reservation for now",
  az_capacity_reservation: "reservation for now",
  az_vmss_flex: "scale set · VMs really start",
};

export type Tip = { readonly title: string; readonly lines: readonly string[] };

/** How a cell is drawn: the view maps it to a fill. */
export type CellFill = "all" | "some" | "no" | "hatch" | "none";

/** One strip cell: its height, its fill, and its tooltip. */
export type NowCell = { readonly h: string; readonly fill: CellFill; readonly tip: Tip };

export type NowPlaceRow = {
  readonly cloud: string;
  readonly region: string;
  readonly sub: string;
  readonly first: boolean;
  readonly nowIcon: string;
  readonly nowTxt: string;
  readonly cells: readonly NowCell[];
  readonly rate: string;
  /** The colour token of the rate (section 11.9, `tone`). */
  readonly rateTone: string;
  readonly rateTxt: string;
  readonly last: string;
};

export type NowBlockRow = {
  readonly region: string;
  readonly first: boolean;
  readonly icon: string;
  readonly word: string;
  readonly offer: string;
  readonly rate: string;
  /** The colour token of the rate: block accent at 60% or more, else neutral. */
  readonly rateTone: string;
  readonly rateTxt: string;
  readonly lead: string;
};

export type NowChip = { readonly label: string; readonly rate: string };

export type NowRow = {
  readonly name: string;
  readonly tier: string;
  readonly kind: NowKind;
  /** 100 × yes / tried over the window, or null. */
  readonly pct: number | null;
  /** The lead of the best offer in hours, or 1e9 with none. */
  readonly leadH: number;
  readonly shape: string;
  readonly icon: string;
  readonly word: string;
  readonly qual: string;
  readonly plan: string;
  readonly planSub: string;
  readonly chips: readonly NowChip[];
  readonly hasChips: boolean;
  readonly depBig: string;
  readonly depTxt: string;
  readonly depW: number;
  /** The colour tokens of the big number and the bar (block-only machines use the block accent). */
  readonly depTone: string;
  readonly depBar: string;
  readonly depWait: string;
  readonly open: boolean;
  readonly chev: "▸" | "▾";
  readonly places: readonly NowPlaceRow[];
  readonly blocks: readonly NowBlockRow[];
  readonly nb: number;
  readonly axis: readonly { readonly l: number; readonly label: string }[];
  readonly marks: readonly { readonly l: number; readonly label: string }[];
  readonly note: string;
};

export type NowGroup = {
  readonly name: string;
  readonly sub: string;
  readonly show: boolean;
  readonly rows: readonly NowRow[];
};

export type NowResult =
  | EmptySection<"now">
  | {
      readonly section: "now";
      readonly empty: false;
      readonly title: string;
      readonly asOf: string;
      readonly cadence: string;
      readonly winLabel: string;
      readonly groups: readonly NowGroup[];
    };

/** `hrs` of section 11.4 with a value under 1 h printed as minutes (section 11.9). */
function nhrs(h: number): string {
  return h < 1 ? `${Math.max(1, Math.round(60 * h))} min` : hrs(h);
}

/** "Wed Nov 4" (weekday short, month short, day, UTC). */
const wdate = (ms: number) => `${weekdayName(ms)} ${dateLabel(ms)}`;

const place = (cloud: string, region: string) => `${CLOUD_NAMES[cloud] ?? cloud} ${region}`;

/** The state of a place in the latest round, from the sweeps of `meta.now` (section 11.9, "Latest round"). */
type NowPlaceState = {
  readonly cloud: string;
  readonly region: string;
  readonly probe: string;
  /** Over the whole data. */
  readonly allN: number;
  readonly allNa: number;
  readonly allYes: number;
  readonly lastYesH: number | null;
  /** The now state: the highest state >= 0, else -1, else null. */
  nowState: number | null;
  nowGot: number;
  nowAsked: number;
  nowReason: string | null;
  /** A latest-round sweep with state -1 and reason "restricted". */
  anyRestricted: boolean;
  /** -3 or -4 when a latest-round sweep has that state, else null. */
  nowU: number | null;
  /** The window place, when it has a sweep in the window. */
  win: Place | undefined;
};

type RoundCell = { got: number; asked: number; zi: JoinedSweep | undefined };

/** Per place and round of the window: the most `got` and `asked`, and the sweep that set the cell. */
function roundCells(d: JoinedData, f: Frame, types: readonly string[]) {
  const out = new Map<string, RoundCell[]>();
  for (const s of d.sweeps) {
    if (s.h < f.H0 || s.h >= f.H1 || s.state < 0 || !types.includes(s.sku)) continue;
    const key = `${s.cloud}|${s.region}`;
    let cells = out.get(key);
    if (cells === undefined) {
      cells = Array.from({ length: f.NR }, () => ({ got: 0, asked: 0, zi: undefined }));
      out.set(key, cells);
    }
    const c = cells[Math.floor((s.h - f.H0) / f.cad)];
    if (c === undefined) continue;
    if (c.zi === undefined || s.state > c.zi.state) c.zi = s;
    c.got = Math.max(c.got, s.got);
    c.asked = Math.max(c.asked, s.asked);
  }
  return out;
}

function cellTip(v: number | null, got: number, asked: number): string {
  if (v === null) return "not asked";
  if (v === -1) return "our quota blocked the ask";
  if (v === 0) return "asked, cloud said no capacity";
  const of = asked ? ` (${got} of ${asked})` : "";
  return v === 2 ? `got all we asked${of}` : `got some${of}`;
}

/** The zone lines of a round tooltip (spec 0006 section 11.9 tooltip). */
function zoneLines(z: JoinedSweep | undefined): string[] {
  const out: string[] = [];
  if (z === undefined) return out;
  if (z.zones > 1) {
    const across =
      z.zonesYes > 1
        ? ` ${z.gotZoneSum} across ${plural(z.zonesYes, "zone")}, at least: each zone stops at the count asked.`
        : "";
    out.push(`Yes in ${z.zonesYes} of ${plural(z.zones, "zone")}.${across}`);
  }
  if (z.zoneList !== undefined && z.zoneList.length > 0) {
    out.push(
      `By zone: ${z.zoneList.map((q) => `${q.zone} ${q.got === null ? "no answer" : q.got}`).join(", ")}.`,
    );
  }
  return out;
}

/** The label of the window (section 11.9, "Window"). */
function winLabelOf(f: Frame, nDates: number): string {
  const pg = f.pages[f.page];
  const start = pg?.start ?? f.t0;
  if (f.hz === "all") return plural(nDates, "day").replace(/^/, "all ");
  if (f.hz === "day") return wdate(start);
  if (f.hz === "week") {
    const p = zoneParts(start, NEW_YORK);
    return `the week of ${dateLabel(Date.UTC(p.year, p.month - 1, p.day))}`;
  }
  return new Intl.DateTimeFormat("en-US", { month: "long", timeZone: "UTC" }).format(
    new Date(start),
  );
}

/** "Tue Nov 3 05:00 UTC" (weekday short, month short, day, 24-hour HH:MM, UTC). */
function asOfOf(ms: number): string {
  const d = new Date(ms);
  const hh = String(d.getUTCHours()).padStart(2, "0");
  const mm = String(d.getUTCMinutes()).padStart(2, "0");
  return `${wdate(ms)} ${hh}:${mm} UTC`;
}

type Best = {
  readonly cb: NowCb;
  readonly leadH: number;
  readonly durH: number;
  readonly start: string;
};

const startTxt = (o: Best) =>
  o.leadH < 1
    ? "within 1 h"
    : o.leadH < 24
      ? `in ${nhrs(o.leadH)}`
      : `${wdate(Date.parse(o.start))} (in ${nhrs(o.leadH)})`;

const lenTxt = (o: Best) =>
  `${nhrs(o.durH)} long${o.cb.longestH !== null && o.cb.longestH > o.durH ? `, up to ${nhrs(o.cb.longestH)} later` : ""}`;

/** The rows of the section for 1 machine. */
function rowOf(
  machine: Machine,
  d: JoinedData,
  meta: JoinedMeta,
  f: Frame,
  ctx: {
    readonly nowH: number;
    readonly lr: number;
    readonly winLabel: string;
    readonly grain: number;
    readonly nb: number;
    readonly axis: NowRow["axis"];
    readonly marks: NowRow["marks"];
    readonly note: string;
  },
  isOpen: boolean,
): NowRow {
  const now = meta.now;
  const types = [...machine.aws, ...machine.azure];
  const cad = f.cad;
  const { nowH, winLabel } = ctx;
  const agg = aggregateMachine(d, f, machine);
  const ago = (h: number) => (nowH - h < cad ? "this round" : `${nhrs(nowH - h)} earlier`);

  // The latest round of the machine, and the places with their now state.
  const sw = (now?.sweeps ?? []).filter((s) => types.includes(s.sku));
  const lastR = sw.length > 0 ? Math.max(...sw.map((s) => Math.floor(s.h / cad))) : null;
  const byKey = new Map<string, NowPlaceState>();
  for (const p of (now?.places ?? []).filter((q) => types.includes(q.sku))) {
    const key = `${p.cloud}|${p.region}`;
    const cur: NowPlaceState = byKey.get(key) ?? {
      cloud: p.cloud,
      region: p.region,
      probe: p.probe,
      allN: 0,
      allNa: 0,
      allYes: 0,
      lastYesH: null,
      nowState: null,
      nowGot: 0,
      nowAsked: 0,
      nowReason: null,
      anyRestricted: false,
      nowU: null,
      win: agg.places.find((q) => q.cloud === p.cloud && q.region === p.region),
    };
    byKey.set(key, {
      ...cur,
      allN: cur.allN + p.n,
      allNa: cur.allNa + p.na,
      allYes: cur.allYes + p.yes,
      lastYesH:
        p.lastYesH === null ? cur.lastYesH : Math.max(cur.lastYesH ?? p.lastYesH, p.lastYesH),
    });
  }
  const PL = [...byKey.values()].sort(
    (a, b) =>
      (a.cloud < b.cloud ? -1 : a.cloud > b.cloud ? 1 : 0) ||
      (a.region < b.region ? -1 : a.region > b.region ? 1 : 0),
  );
  // The tie order (section 11.9): cloud name, then the places of the latest round in the order of
  // `meta.now.sweeps`, then the other places by region. `full` ranks a full answer over a partial one.
  const pos = new Map<string, number>();
  for (const s of sw)
    if (Math.floor(s.h / cad) === lastR && !pos.has(`${s.cloud}|${s.region}`))
      pos.set(`${s.cloud}|${s.region}`, pos.size);
  const posOf = (P: NowPlaceState) => pos.get(`${P.cloud}|${P.region}`) ?? pos.size;
  const byCloud = (a: NowPlaceState, b: NowPlaceState) =>
    a.cloud < b.cloud ? -1 : a.cloud > b.cloud ? 1 : 0;
  const rankOf = (a: NowPlaceState, b: NowPlaceState) => byCloud(a, b) || posOf(a) - posOf(b);
  const full = (P: NowPlaceState) => Math.max(P.nowState ?? 0, 0);
  for (const s of sw) {
    if (Math.floor(s.h / cad) !== lastR) continue;
    const p = byKey.get(`${s.cloud}|${s.region}`);
    const P = PL.find((q) => q.cloud === s.cloud && q.region === s.region);
    if (p === undefined || P === undefined) continue;
    if (s.state <= -3) {
      P.nowU = P.nowU === -3 || s.state === -3 ? -3 : -4;
    } else if (P.nowState === null || P.nowState === -1 || s.state > P.nowState) {
      P.nowState = s.state;
      P.nowGot = s.got;
      P.nowAsked = s.asked;
      P.nowReason = s.reason;
    }
    if (s.state === -1 && s.reason === "restricted") P.anyRestricted = true;
  }

  // The yes-rate of a place: over the window, else over the whole data, else 0.
  // The span the rate counts: the window, or the whole data when the window has no asked round.
  const rateSpan = (P: NowPlaceState) =>
    P.win !== undefined && P.win.n - P.win.na > 0 ? winLabel : "all the data";
  const rate = (P: NowPlaceState) => {
    const a = (P.win?.n ?? 0) - (P.win?.na ?? 0);
    if (P.win !== undefined && a > 0) return (100 * P.win.yes) / a;
    const a2 = P.allN - P.allNa;
    return a2 > 0 ? (100 * P.allYes) / a2 : 0;
  };

  // The union over the window, and its counts.
  const union = agg.union;
  const { tried, yes, waitH, waitKind } = countsOf(union, cad);
  const quotaRounds = union.filter((v) => v === -1).length;
  const pct = tried > 0 ? (100 * yes) / tried : null;

  // The Capacity Block places of the machine, and the best offer.
  const horizon = cbHorizon(meta.cbHorizonDays);
  const blocks = (now?.cb ?? [])
    .filter((c) => machine.aws.includes(c.sku))
    .map((c) => {
      const win: BlockPlace | undefined = agg.blockPlaces.find(
        (b) => b.sku === c.sku && b.region === c.region,
      );
      // Fresh (section 11.9): the Capacity Block checks run in the hour before
      // the provisioning round; the latest check counts when it is less than 2 x CAD old.
      const fresh = c.h !== null && nowH - c.h < 2 * cad;
      // The lead shown is relative to now: the lead at the check, less the age of the check, 0 at least.
      // The age is 0 at least too: a check newer than nowH must not lengthen the lead.
      const best: Best | null =
        fresh && c.h !== null && c.leadH !== null && c.durH !== null && c.start !== null
          ? {
              cb: c,
              leadH: Math.max(0, c.leadH - Math.max(0, nowH - c.h)),
              durH: c.durH,
              start: c.start,
            }
          : null;
      return { cb: c, checkH: c.h, best, fresh, win };
    });
  const offers = blocks
    .filter((b) => b.best !== null)
    .sort((a, b) => (a.best?.leadH ?? 0) - (b.best?.leadH ?? 0));
  const BB = offers[0]?.best ?? null;

  const nowAsked = PL.filter((P) => P.nowState !== null);
  const nowOD = nowAsked.filter((P) => (P.nowState ?? -1) >= 0);
  const nowYes = PL.filter((P) => (P.nowState ?? 0) > 0).sort(
    (a, b) => rate(b) - rate(a) || full(b) - full(a) || rankOf(a, b),
  );

  let kind: NowKind;
  if (nowYes.length > 0) kind = "yes";
  else if (BB !== null && BB.leadH < 1) kind = "yes";
  else if (BB !== null && BB.leadH < 24) kind = "soon";
  else if (BB !== null) kind = "book";
  else if (nowAsked.length > 0 && nowOD.length === 0) kind = "quota";
  else if (nowAsked.length > 0) kind = "no";
  else kind = "none";
  const viaBlock = kind === "yes" && nowYes.length === 0;

  const odNone =
    nowOD.length > 0
      ? `On demand: none of ${plural(nowOD.length, "place")} in the latest round${pct !== null ? `; a yes in ${ps(pct)} of rounds over ${winLabel}` : ""}.`
      : "We ask for it only as a Capacity Block.";

  let word: string = WORD[kind];
  let qual = "";
  let plan = "";
  let planSub = "";
  let chips: NowChip[] = [];
  const first = nowYes[0];
  if (kind === "yes" && !viaBlock && first !== undefined) {
    qual = `${nowYes.length} of ${plural(nowAsked.length, "place")} said yes`;
    plan =
      nowYes.length === 1
        ? `Only ${place(first.cloud, first.region)} — go there.`
        : `Try ${place(first.cloud, first.region)} first.`;
    planSub = `It said yes in ${ps(rate(first))} of the rounds it was asked over ${rateSpan(first)}${
      nowYes.length > 1 ? `; ${plural(nowYes.length - 1, "more place")} also said yes.` : "."
    }`;
    chips = nowYes.slice(0, 6).map((P) => ({ label: place(P.cloud, P.region), rate: ps(rate(P)) }));
    if (nowYes.length > 6) chips.push({ label: `+${nowYes.length - 6} more`, rate: "" });
  } else if (kind === "yes" && BB !== null) {
    qual = "a block starts within 1 h";
    plan = `Buy the Capacity Block in AWS ${BB.cb.region} — starts ${startTxt(BB)}, ${lenTxt(BB)}.`;
    planSub = odNone;
  } else if (kind === "soon" && BB !== null) {
    qual = `block starts in ${nhrs(BB.leadH)}`;
    plan = `Wait for the Capacity Block in AWS ${BB.cb.region} — starts ${startTxt(BB)}, ${lenTxt(BB)}.`;
    planSub = odNone;
  } else if (kind === "book" && BB !== null) {
    qual = `earliest ${wdate(Date.parse(BB.start))}`;
    plan = `Book a Capacity Block in AWS ${BB.cb.region} — starts ${startTxt(BB)}, ${lenTxt(BB)}.`;
    planSub = odNone;
  } else if (kind === "quota") {
    qual = `all ${plural(nowAsked.length, "place")} at our limit`;
    plan = nowAsked.some((P) => P.anyRestricted)
      ? "Ask for a quota increase or a size access request first."
      : "Raise our quota first.";
    planSub =
      "Every place we asked stopped at our own account limit, so the cloud was never asked about capacity.";
  } else if (kind === "no") {
    qual = `none of ${plural(nowOD.length, "place")}`;
    const ly = PL.filter((P) => P.lastYesH !== null).sort(
      (a, b) => (b.lastYesH ?? 0) - (a.lastYesH ?? 0) || rankOf(a, b),
    )[0];
    plan = `Nothing anywhere.${
      ly !== undefined && ly.lastYesH !== null
        ? ` Last yes ${ago(ly.lastYesH)} in ${place(ly.cloud, ly.region)}.`
        : " Never a yes in the data."
    }`;
    planSub =
      pct === null
        ? ""
        : pct >= 40
          ? `A yes in ${ps(pct)} of rounds over ${winLabel} — ask everywhere again in ${cad} h.`
          : pct > 0
            ? `A yes in only ${ps(pct)} of rounds over ${winLabel} — do not plan on it.`
            : `Never a yes in ${plural(tried, "round")} over ${winLabel}.`;
  } else if (PL.length > 0 && PL.every((P) => P.nowU !== null)) {
    // 7a: every place said -3 or -4 in the latest round.
    word = PL.every((P) => P.nowU === -4) ? "Not offered" : "Not available";
    qual = `${plural(PL.length, "place")} said so in the latest round`;
    plan = "Not offered or not available in the latest round.";
  } else if (PL.length === 0 && blocks.length > 0) {
    // 7b: a block-only machine with no offer in the latest round.
    word = "No block now";
    qual = "no Capacity Block offered in the latest round";
    plan = "We ask for it only as a Capacity Block.";
  } else {
    // 7c: not asked.
    qual = PL.length > 0 ? "not asked in the latest round" : "no round yet";
    plan = "Not asked in the latest round.";
  }

  // The window column.
  const blockOnly = PL.length === 0 && blocks.length > 0;
  let depBig: string;
  let depTxt: string;
  let depW = 0;
  let depWait: string;
  if (blockOnly) {
    const bn = blocks.reduce((a, b) => a + (b.win?.n ?? 0), 0);
    const bf = blocks.reduce((a, b) => a + (b.win?.nowFound ?? 0), 0);
    const bp = bn > 0 ? (100 * bf) / bn : null;
    const ml = median(
      blocks.map((b) => b.win?.medLead ?? null).filter((v): v is number => v !== null),
    );
    depBig = ps(bp);
    depTxt = `${bf} of ${plural(bn, "check")} had a block starting within 1 h`;
    depW = bp ?? 0;
    depWait =
      ml !== null
        ? `earliest start usually ${nhrs(ml * 24)} out`
        : `no block within ${horizon} days`;
  } else if (tried === 0 && quotaRounds > 0) {
    depBig = "0%";
    depTxt = `quota blocked all ${plural(quotaRounds, "round")}`;
    depWait = "no capacity answer possible";
  } else if (tried === 0) {
    depBig = "–";
    depTxt = PL.some((P) => (P.win?.nu ?? 0) > 0 || (P.win?.no ?? 0) > 0)
      ? `asked; no capacity answer over ${winLabel}`
      : `not asked over ${winLabel}`;
    depWait = "";
  } else {
    depBig = ps(pct);
    depTxt = `${yes} of ${plural(tried, "round")} had a yes`;
    depW = pct ?? 0;
    depWait =
      yes === 0
        ? `never, in ${nhrs(tried * cad)} of asking`
        : waitH > 0
          ? `longest wait ${nhrs(waitH)} ${waitKind ?? ""}`
          : "never more than one round without";
  }

  const clouds = [...new Set(PL.map((P) => P.cloud).sort())].map((c) => CLOUD_NAMES[c] ?? c);
  const gp = gpusPerMachine(machine);
  const shape = `${plural(gp, "GPU")} · ${
    blockOnly ? "AWS, Capacity Block only" : `${clouds.join(" + ")}, ${plural(PL.length, "place")}`
  }`;

  // The open row: the places.
  const roundCellsOf = roundCells(d, f, types);
  const firstKey =
    nowYes.length > 0 && first !== undefined ? `${first.cloud}|${first.region}` : null;
  const placeRows: NowPlaceRow[] = PL.slice()
    .sort(
      (a, b) =>
        Number((b.nowState ?? 0) > 0) - Number((a.nowState ?? 0) > 0) ||
        rate(b) - rate(a) ||
        rankOf(a, b),
    )
    .map((P) => {
      const w = P.win;
      const a = (w?.n ?? 0) - (w?.na ?? 0);
      const ns = P.nowState;
      const nk = ns === null ? "none" : ns === -1 ? "quota" : ns > 0 ? "yes" : "no";
      const key = `${P.cloud}|${P.region}`;
      const rc = roundCellsOf.get(key);
      const cells: NowCell[] = [];
      for (let b = 0; b < ctx.nb; b++) {
        cells.push(
          ctx.grain === cad
            ? roundCell(f, b, w?.cells[b] ?? null, rc?.[b], P)
            : dayCell(f, ctx.grain, b, w?.cells ?? []),
        );
      }
      return {
        cloud: CLOUD_NAMES[P.cloud] ?? P.cloud,
        region: P.region,
        sub: PROBE_SUB[P.probe] ?? P.probe,
        first: key === firstKey,
        nowIcon: ICON[nk] ?? "",
        nowTxt:
          ns === null
            ? P.nowU === -3
              ? "not available"
              : P.nowU === -4
                ? "not offered"
                : "not asked"
            : ns === -1
              ? "our quota"
              : ns > 0
                ? ns === 2
                  ? `yes · all ${P.nowGot}`
                  : `yes · ${P.nowGot} of ${P.nowAsked}`
                : "no capacity",
        cells,
        rate: a > 0 && w !== undefined ? ps((100 * w.yes) / a) : (w?.na ?? 0) > 0 ? "quota" : "–",
        rateTone: tone(a > 0 && w !== undefined ? (100 * w.yes) / a : null),
        rateTxt:
          a > 0 && w !== undefined
            ? `${w.yes} of ${plural(a, "round")}`
            : (w?.na ?? 0) > 0
              ? `blocked all ${plural(w?.na ?? 0, "round")}`
              : "not asked",
        last: P.lastYesH !== null ? `last yes ${ago(P.lastYesH)}` : "never a yes in the data",
      };
    });

  // The open row: the Capacity Block Regions.
  const blockRows: NowBlockRow[] = [...offers, ...blocks.filter((b) => b.best === null)].map(
    (b, i) => {
      const o = b.best;
      const bk = o === null ? "no" : o.leadH < 1 ? "yes" : o.leadH < 24 ? "soon" : "book";
      const n = b.win?.n ?? 0;
      const bp = n > 0 ? (100 * (b.win?.nowFound ?? 0)) / n : null;
      const medLead = b.win?.medLead ?? null;
      return {
        region: b.cb.region,
        first: o !== null && i === 0 && (kind === "soon" || kind === "book" || viaBlock),
        icon: ICON[o === null && b.cb.leadH !== null ? "none" : bk] ?? "",
        word:
          o === null
            ? b.cb.leadH !== null
              ? b.fresh
                ? "start unknown"
                : "too old to count"
              : `none within ${horizon} days`
            : o.leadH < 1
              ? "starts within 1 h"
              : `in ${nhrs(o.leadH)}`,
        offer:
          o !== null
            ? `Earliest start ${startTxt(o)} · ${lenTxt(o)}${b.checkH !== null ? ` · checked ${ago(b.checkH)}` : ""}`
            : b.cb.leadH !== null && b.fresh
              ? `The latest check found an offer without a start or a length${b.checkH !== null ? ` (${ago(b.checkH)})` : ""}.`
              : b.cb.leadH !== null
                ? `The latest check is too old to count${b.checkH !== null ? ` (${ago(b.checkH)})` : ""}.`
                : `No Capacity Block offered within ${horizon} days at the latest check${b.checkH !== null ? ` (${ago(b.checkH)})` : ""}.`,
        rate: n > 0 ? ps(bp) : "–",
        rateTone: bp !== null && bp >= 60 ? "--color-accent-2-300" : "--color-neutral-200",
        rateTxt:
          n > 0
            ? `${b.win?.nowFound ?? 0} of ${plural(n, "check")} had a block starting within 1 h`
            : `no checks over ${winLabel}`,
        lead: medLead !== null ? `earliest start usually ${nhrs(medLead * 24)} out` : "",
      };
    },
  );

  return {
    name: machine.name,
    tier: machine.tier,
    kind,
    pct,
    leadH: BB !== null ? BB.leadH : NO_LEAD,
    shape,
    icon: ICON[kind] ?? "",
    word,
    qual,
    plan,
    planSub,
    chips,
    hasChips: chips.length > 0,
    depBig,
    depTxt,
    depW: Math.max(0, Math.min(100, depW)),
    depTone: blockOnly ? "--color-accent-2-300" : tone(pct),
    depBar: blockOnly ? "--color-accent-2-500" : "--color-accent-500",
    depWait,
    open: isOpen,
    chev: isOpen ? "▾" : "▸",
    places: placeRows,
    blocks: blockRows,
    nb: ctx.nb,
    axis: ctx.axis,
    marks: ctx.marks,
    note: ctx.note,
  };
}

/** A cell at round grain: height, fill, and the tooltip with the zone lines of the sweep that set it. */
function roundCell(
  f: Frame,
  b: number,
  v: number | null,
  rc: RoundCell | undefined,
  _p: NowPlaceState,
): NowCell {
  const h = v === null ? "0%" : v === -1 ? "100%" : v === 0 ? "2px" : v === 2 ? "100%" : "50%";
  const fill: CellFill =
    v === -1 ? "hatch" : v === 0 ? "no" : v === 2 ? "all" : v !== null ? "some" : "none";
  const lines = [cellTip(v, rc?.got ?? 0, rc?.asked ?? 0), ...zoneLines(rc?.zi)];
  return {
    h,
    fill,
    tip: { title: cellLabel({ ...f, grain: f.cad, grainName: "round" }, b), lines },
  };
}

/** A cell at day grain, folding the rounds of the day (section 11.9 strip). */
function dayCell(f: Frame, grain: number, b: number, cells: readonly (number | null)[]): NowCell {
  let y = 0;
  let n = 0;
  let q = 0;
  const k0 = Math.round((b * grain) / f.cad);
  const k1 = Math.min(f.NR, Math.round(((b + 1) * grain) / f.cad));
  for (let k = k0; k < k1; k++) {
    const c = cells[k] ?? null;
    if (c === null) continue;
    if (c === -1) {
      q += 1;
      continue;
    }
    n += 1;
    if (c > 0) y += 1;
  }
  const title = wdate(hourInstant(f, f.H0 + b * grain));
  const text =
    n === 0 && q > 0
      ? "our quota blocked every ask"
      : n === 0
        ? "not asked"
        : `${y} of ${plural(n, "round")} had a yes`;
  const h =
    n === 0 && q > 0
      ? "100%"
      : n === 0
        ? "0%"
        : y > 0
          ? `${Math.max(12, Math.round((100 * y) / n))}%`
          : "2px";
  const fill: CellFill = n === 0 && q > 0 ? "hatch" : n === 0 ? "none" : y > 0 ? "all" : "no";
  return { h, fill, tip: { title, lines: [text] } };
}

/**
 * The section for the window in `data` (the payload of the page in view). `open` holds the names of
 * the open rows: view state that is not stored (section 11.5). `meta.now` absent (an old events
 * file) gives the empty result, never a thrown error.
 */
export function deriveNow(
  data: BoardData,
  state: ViewState,
  open: ReadonlySet<string> = new Set(),
): NowResult {
  const j = joinData(data);
  const now = j.meta.now;
  if (now === undefined) return { section: "now", empty: true };
  const meta = j.meta;
  const f = makeFrame(data.meta, data.window.hz, data.window.page);
  const cad = f.cad;
  const answered = now.sweeps;
  const maxH = answered.length > 0 ? Math.max(...answered.map((s) => s.h)) : 0;
  const lr = Math.floor(maxH / cad);
  const nowH = lr * cad;
  const nDates = meta.dates.length;
  const winLabel = winLabelOf(f, nDates);

  // The strip grain: 1 cell per round on a day or week page, else 1 cell per UTC day (correction C10).
  const span = f.H1 - f.H0;
  const grain = f.hz === "day" || f.hz === "week" ? cad : 24;
  const nb = Math.ceil(span / grain);
  const step = grain === cad ? (span <= 24 ? 6 : 24) : 168;
  const tickMs = step % 24 === 0 ? 864e5 : step * HOUR;
  const axis: { l: number; label: string }[] = [];
  for (
    let t = (Math.ceil((f.t0 + f.H0 * HOUR) / tickMs) * tickMs - f.t0) / HOUR;
    t < f.H1;
    t += step
  ) {
    if ((t - f.H0) / span > 0.93) continue;
    const ms = f.t0 + t * HOUR;
    const label =
      grain === cad
        ? span <= 24
          ? `${String(new Date(ms).getUTCHours()).padStart(2, "0")}:00`
          : `${new Date(ms).getUTCDate()} ${weekdayName(ms)}`
        : dateLabel(ms);
    axis.push({ l: ((t - f.H0) / span) * 100, label });
  }
  const z = meta.zoneStartH;
  const marks =
    z !== null && z >= f.H0 && z < f.H1
      ? [{ l: ((z - f.H0) / span) * 100, label: "AWS asks every zone from here" }]
      : [];
  const note =
    z === null
      ? ""
      : z >= f.H1
        ? `Before ${dateLabel(f.t0 + z * HOUR)} AWS was asked in one zone per Region.`
        : z >= f.H0
          ? "Dashed line: AWS asks every zone from here."
          : "";
  const ctx = { nowH, lr, winLabel, grain, nb, axis, marks, note };

  const gl = machinesOfTier(state.tier);
  const groups = TIERS.map((tier) => ({
    name: tier,
    sub: TIER_SUBS[tier] ?? "",
    show: true,
    rows: MACHINES.filter((m) => m.tier === tier && gl.includes(m.name)).map((m) =>
      rowOf(m, j, meta, f, ctx, open.has(m.name)),
    ),
  })).filter((g) => g.rows.length > 0);

  return {
    section: "now",
    empty: false,
    title: NOW_TITLE,
    asOf: asOfOf(f.t0 + nowH * HOUR),
    cadence: cad === 1 ? "hour" : `${cad} hours`,
    winLabel,
    groups,
  };
}

export type { NowPlace };
