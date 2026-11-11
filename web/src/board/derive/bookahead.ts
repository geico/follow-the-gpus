// Section "Book ahead: how long a block, how soon" (spec 0006 section 11.16): per machine and
// region, the longest Capacity Block AWS listed and how soon it starts. Pure: no DOM, no React.

import { aggregateMachine, type BlockPlace } from "./aggregate";
import { cbHorizon, MACHINES, type Machine, machineByName, machinesOfTier } from "./catalogue";
import { hrs, median, plural, ps, sh } from "./format";
import { joinData } from "./join";
import { makeFrame } from "./time";
import type { BoardData, EmptySection, JoinedData, ViewState } from "./types";

const NOTE =
  "AWS Capacity Blocks, read per region for 1 machine. “Longest” is the longest duration AWS listed at any point in the window. Azure has no equivalent we can probe.";

export type BookRow = {
  readonly name: string;
  /** True for the focus machine: its name takes the accent colour token. */
  readonly focus: boolean;
  readonly region: string;
  /** The bar width in percent of the scale. */
  readonly w: number;
  readonly lab: string;
  /** The colour token of the bar label. */
  readonly labTone: "--color-bg" | "--color-neutral-200";
  readonly lead: string;
  readonly title: string;
  readonly lines: readonly string[];
};

export type BookaheadFull = {
  readonly section: "bookahead";
  readonly empty: false;
  readonly note: string;
  readonly rows: readonly BookRow[];
  readonly foot: string;
};

export type BookaheadResult = EmptySection<"bookahead"> | BookaheadFull;

/** Days of hours: "{round(days)} days" from 48 h, else "{h} hours". */
function span(h: number): string {
  return h >= 48 ? `${Math.round(h / 24)} days` : `${Math.round(h)} hours`;
}

function rowOf(
  m: Machine,
  p: BlockPlace,
  longestH: number,
  scale: number,
  d: JoinedData,
  focus: string,
): BookRow {
  const days = longestH / 24;
  const w = (100 * days) / scale;
  const lead = p.longestLead === null ? null : hrs(p.longestLead * 24);
  const horizon = cbHorizon(d.meta.cbHorizonDays);
  const lines = [
    `Longest block listed for 1 machine: ${span(longestH)} · typical listing ${span(p.longestMedH ?? longestH)}${
      lead === null ? "" : ` · starts ~${lead} out (median of ${plural(p.nLong, "check")})`
    }`,
    `${ps(sh(p.found, p.n))} of ${plural(p.n, "check")} had a 1-day block starting within ${plural(horizon, "day")}${
      p.medLead === null ? "" : `, soonest ~${hrs(p.medLead * 24)} out`
    }`,
    p.fee === null
      ? "Fee not listed."
      : `Listed upfront fee for that 1-day block: $${Math.round(p.fee)}`,
  ];
  return {
    name: m.name,
    focus: m.name === focus,
    region: p.region,
    w,
    lab: days >= 2 ? `${Math.round(days)} d` : `${longestH} h`,
    labTone: w > 25 ? "--color-bg" : "--color-neutral-200",
    lead: lead === null ? "–" : `~${lead}`,
    title: `${m.name} · ${p.region}`,
    lines,
  };
}

/** The "Bigger 1-day blocks" tail of the footer: in-window 24 h checks for more than 1 machine. */
function bigger(d: JoinedData, machines: readonly Machine[], H0: number, H1: number): string {
  const items: [string, string][] = [];
  for (const m of machines) {
    const byK = new Map<number, (readonly (number | null)[])[]>();
    for (const place of d.cb) {
      if (!m.aws.includes(place.sku)) continue;
      for (const c of place.checks) {
        const h = c[0] ?? -1;
        const k = c[6] ?? null;
        const dur = c[3] ?? null;
        if (h < H0 || h >= H1 || k === null || k <= 1 || (dur !== null && dur !== 24)) continue;
        const list = byK.get(k) ?? [];
        list.push(c);
        byK.set(k, list);
      }
    }
    for (const [k, checks] of byK) {
      const n = checks.length;
      const found = checks.filter((c) => c[1] === 1);
      const key = `${m.name} ×${k}`;
      const leads = found.map((c) => c[2]).filter((x): x is number => typeof x === "number");
      const lead = median(leads);
      items.push([
        key,
        found.length === 0
          ? `${key} machines: never offered in ${plural(n, "check")}`
          : `${key} machines: ${ps(sh(found.length, n))} of ${plural(n, "check")}${
              lead === null ? "" : `, starting ~${hrs(lead * 24)} out`
            }`,
      ]);
    }
  }
  if (items.length === 0) return "";
  items.sort((a, b) => (a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : 0));
  return ` Bigger 1-day blocks were also asked for — ${items.map((i) => i[1]).join(" · ")}.`;
}

export function deriveBookahead(data: BoardData, state: ViewState): BookaheadResult {
  const d = joinData(data);
  const f = makeFrame(d.meta, d.hz, d.page);
  const inTier = machinesOfTier(state.tier);
  const focus = machineByName(state.gpu);
  const ordered: Machine[] = [];
  if (focus !== undefined && inTier.includes(focus.name)) ordered.push(focus);
  for (const m of MACHINES) {
    if (inTier.includes(m.name) && m.name !== state.gpu) ordered.push(m);
  }

  const picked: { m: Machine; p: BlockPlace; longestH: number }[] = [];
  for (const m of ordered) {
    const places = aggregateMachine(d, f, m).blockPlaces.flatMap((p) =>
      p.longestH === null ? [] : [{ m, p, longestH: p.longestH }],
    );
    places.sort((a, b) => b.longestH - a.longestH);
    picked.push(...places);
  }
  const anyCheck = d.cb.some((p) =>
    p.checks.some((c) => (c[0] ?? -1) >= f.H0 && (c[0] ?? -1) < f.H1),
  );
  if (!anyCheck) return { section: "bookahead", empty: true };

  const scale = Math.max(1, Math.ceil(Math.max(24, ...picked.map((r) => r.longestH)) / 24));
  const rows = picked.map((r) => rowOf(r.m, r.p, r.longestH, scale, d, state.gpu));
  const foot =
    `Bar scale: 0 to ${plural(scale, "day")}. “It starts in” = median days from a check to the start of that longest block. ` +
    (state.tier === "All"
      ? "Focus machine first."
      : "Only this tier; switch Tier to All for the rest.") +
    bigger(d, ordered, f.H0, f.H1);
  return { section: "bookahead", empty: false, note: NOTE, rows, foot };
}
