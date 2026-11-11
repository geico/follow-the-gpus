// Section "Where did the yes come from?" (spec 0006 section 11.21): one cell for each machine and
// region, the share of machines handed to us (GOT IT) or the share of checks with a Capacity Block
// on offer (BLOCK), with mini bars over the window. Pure: the view only draws what is returned.

import { aggregateAll, type MachineAgg, NOW_LEAD_H, type Place } from "./aggregate";
import { CLOUD_NAMES, TIER_SUBS, TIERS } from "./catalogue";
import { count, dateLabel, plural, ps } from "./format";
import { hasWindowData } from "./inwindow";
import { joinData } from "./join";
import { type Frame, hourInstant, makeFrame } from "./time";
import type { BoardData, EmptySection, JoinedData, ViewState } from "./types";

export const MATRIX_TITLE = "Where did the yes come from?";

/** The sentence that a GOT IT cell of AWS adds (section 5.3 rule 5). */
const AWS_NOTE =
  "Each round counts the most that 1 zone handed us, what a single request gets; the strip gives the sum across zones.";

export const MATRIX_LEGEND: readonly { readonly term: string; readonly text: string }[] = [
  { term: "GOT IT", text: "filled: share of machines asked for that were handed to us right now" },
  { term: "BLOCK", text: "outlined: share of checks with a 1-day block starting within 1 h" },
  { term: "quota", text: "our quota blocked the question" },
  { term: "not offered", text: "AWS does not sell this type in the region" },
  { term: "not available", text: "Azure said SkuNotAvailable" },
  { term: "never asked", text: "" },
];

export type MatrixKind = "got" | "block" | "quota" | "notOffered" | "notAvailable" | "never";

export type MatrixCell = {
  readonly kind: MatrixKind;
  /** The big label: `ps` of the share, "quota", "not offered", "not available", or "". */
  readonly big: string;
  /** "GOT IT", "BLOCK", or "". */
  readonly tag: string;
  /** The share in percent for GOT IT and BLOCK; null otherwise. */
  readonly p: number | null;
  /** The mix percent of the BLOCK ring colour, round(25 + 75 p / 100); null for other kinds. */
  readonly ringPct: number | null;
  /** The number of mini bars (1 when the cell has none). */
  readonly nd: number;
  /** Mini bars: `h` is the height in percent, 0 for an empty step. */
  readonly days: readonly { readonly h: number }[];
  readonly title: string;
  readonly lines: readonly string[];
  /** The accessible name: the title and the lines. */
  readonly label: string;
};

export type MatrixRow = {
  readonly name: string;
  /** The clouds of the machine joined by " + ", or "–". */
  readonly sub: string;
  /** The types of the machine joined by ", ". */
  readonly skus: string;
  readonly focus: boolean;
  readonly cells: readonly MatrixCell[];
};

export type MatrixGroup = {
  readonly tier: string;
  readonly sub: string;
  readonly rows: readonly MatrixRow[];
};

export type MatrixColumn = { readonly cloud: string; readonly region: string };

export type MatrixData = {
  readonly section: "matrix";
  readonly empty: false;
  readonly title: string;
  readonly intro: string;
  readonly legend: typeof MATRIX_LEGEND;
  readonly columns: readonly MatrixColumn[];
  /** The group heads over the columns: "AWS", then "Azure", each with its column count. */
  readonly heads: readonly { readonly cloud: string; readonly n: number }[];
  readonly groups: readonly MatrixGroup[];
};

export type MatrixResult = EmptySection<"matrix"> | MatrixData;

const HOUR_DAY = 24;

/** The step size in days: groups of ceil(days / 16) days when there are more than 16. */
function stepDays(f: Frame): number {
  const days = Math.ceil((f.H1 - f.H0) / HOUR_DAY);
  return days > 16 ? Math.ceil(days / 16) : 1;
}

type Step = { readonly a: number; readonly b: number };

function stepsOf(f: Frame): Step[] {
  const size = stepDays(f) * HOUR_DAY;
  const out: Step[] = [];
  for (let a = f.H0; a < f.H1; a += size) out.push({ a, b: Math.min(f.H1, a + size) });
  return out;
}

/** The mini bars and the "Over time" line of a series of (hour, hit, weight) records. */
function bars(f: Frame, steps: readonly Step[], share: (s: Step) => number | null) {
  const shares = steps.map(share);
  const days = shares.map((p) => ({ h: p === null ? 0 : Math.max(8, p) }));
  const over = shares
    .map((p, k) =>
      p === null ? null : `${dateLabel(hourInstant(f, steps[k]?.a ?? f.H0))} ${ps(p)}`,
    )
    .filter((x): x is string => x !== null);
  return { days, over: over.length > 0 ? `Over time: ${over.join(" · ")}` : null };
}

const rounds = (n: number) => plural(n, "round", count(n));

function sweepBars(d: JoinedData, f: Frame, steps: readonly Step[], agg: MachineAgg, pl: Place) {
  const types = [...agg.machine.aws, ...agg.machine.azure];
  const own = d.sweeps.filter(
    (s) =>
      s.state >= 0 &&
      s.cloud === pl.cloud &&
      s.region === pl.region &&
      types.includes(s.sku) &&
      s.h >= f.H0 &&
      s.h < f.H1,
  );
  return bars(f, steps, (st) => {
    let got = 0;
    let asked = 0;
    for (const s of own) {
      if (s.h < st.a || s.h >= st.b) continue;
      got += s.got;
      asked += s.asked;
    }
    return asked > 0 ? (100 * got) / asked : null;
  });
}

function blockBars(
  d: JoinedData,
  f: Frame,
  steps: readonly Step[],
  agg: MachineAgg,
  region: string,
) {
  const checks = d.cb
    .filter((c) => c.region === region && agg.machine.aws.includes(c.sku))
    .flatMap((c) => c.checks)
    .filter((c) => {
      const h = c[0] ?? -1;
      const nReq = c[6] ?? null;
      const dur = c[3] ?? null;
      return h >= f.H0 && h < f.H1 && (nReq === null || nReq === 1) && (dur === null || dur === 24);
    });
  return bars(f, steps, (st) => {
    let n = 0;
    let found = 0;
    for (const c of checks) {
      const h = c[0] ?? -1;
      if (h < st.a || h >= st.b) continue;
      n += 1;
      if (c[1] === 1 && c[7] !== null && c[7] !== undefined && c[7] < NOW_LEAD_H) found += 1;
    }
    return n > 0 ? (100 * found) / n : null;
  });
}

function cellOf(
  d: JoinedData,
  f: Frame,
  steps: readonly Step[],
  agg: MachineAgg,
  col: MatrixColumn,
): MatrixCell {
  const name = agg.machine.name;
  const cloudName = CLOUD_NAMES[col.cloud] ?? col.cloud;
  const pl = agg.places.find((p) => p.cloud === col.cloud && p.region === col.region);
  const bp =
    col.cloud === "aws" && agg.mode === "block"
      ? agg.blockPlaces.find((b) => b.region === col.region)
      : undefined;
  const plain = (kind: MatrixKind, big: string, line: string): MatrixCell =>
    finish(
      { kind, big, tag: "", p: null, ringPct: null, nd: 1, days: [] },
      `${name} · ${col.region}`,
      [line],
    );

  if (pl !== undefined && pl.n > pl.na) {
    const p = pl.asked > 0 ? (100 * pl.got) / pl.asked : 0;
    const b = sweepBars(d, f, steps, agg, pl);
    const lines = [
      `${count(pl.got)} of ${count(pl.asked)} machines asked for were handed to us · ${ps(p)}`,
    ];
    if (b.over !== null) lines.push(b.over);
    if (pl.no > 0) lines.push(`Not offered in ${rounds(pl.no)}.`);
    if (pl.nu > 0) lines.push(`SkuNotAvailable in ${rounds(pl.nu)}.`);
    if (col.cloud === "aws") lines.push(AWS_NOTE);
    return finish(
      { kind: "got", big: ps(p), tag: "GOT IT", p, ringPct: null, nd: steps.length, days: b.days },
      `${name} · ${cloudName} ${col.region}`,
      lines,
    );
  }
  if (bp !== undefined) {
    const p = bp.n > 0 ? (100 * bp.nowFound) / bp.n : 0;
    const b = blockBars(d, f, steps, agg, col.region);
    const lines = [
      `${count(bp.nowFound)} of ${plural(bp.n, "check", count(bp.n))} had a 1-day block starting within ${NOW_LEAD_H} h · ${ps(p)}`,
    ];
    if (b.over !== null) lines.push(b.over);
    return finish(
      {
        kind: "block",
        big: ps(p),
        tag: "BLOCK",
        p,
        ringPct: Math.round(25 + 75 * (p / 100)),
        nd: steps.length,
        days: b.days,
      },
      `${name} · ${cloudName} ${col.region}`,
      lines,
    );
  }
  if (pl !== undefined && pl.n > 0 && pl.na === pl.n) {
    return plain(
      "quota",
      "quota",
      `Our account could not ask here: ${pl.reason ?? "quota"}. Not a capacity answer.`,
    );
  }
  if (pl !== undefined && pl.no > 0) {
    return plain(
      "notOffered",
      "not offered",
      "AWS does not offer this type in this Region. Not a quota wall and not a capacity answer.",
    );
  }
  if (pl !== undefined && pl.nu > 0) {
    return plain(
      "notAvailable",
      "not available",
      `Azure answered SkuNotAvailable in ${rounds(pl.nu)}: a size restriction or no capacity, which the probe cannot tell apart. Not a quota wall.`,
    );
  }
  return plain("never", "", "Never asked.");
}

function finish(
  c: Omit<MatrixCell, "title" | "lines" | "label">,
  title: string,
  lines: readonly string[],
): MatrixCell {
  return { ...c, title, lines, label: `${title}. ${lines.join(" ")}` };
}

function cloudsOf(agg: MachineAgg): string {
  const names: string[] = [];
  for (const p of agg.places) {
    const n = CLOUD_NAMES[p.cloud] ?? p.cloud;
    if (!names.includes(n)) names.push(n);
  }
  if (agg.blockPlaces.length > 0 && !names.includes("AWS")) names.push("AWS");
  return names.length > 0 ? names.join(" + ") : "–";
}

export function deriveMatrix(data: BoardData, state: ViewState): MatrixResult {
  if (!hasWindowData(data)) {
    return { section: "matrix", empty: true };
  }
  const f = makeFrame(data.meta, data.window.hz, data.window.page);
  const d = joinData(data);
  const aggs = aggregateAll(d, f);
  const steps = stepsOf(f);

  const columns: MatrixColumn[] = data.meta.regions
    .map((r) => {
      const [cloud = "", region = ""] = r.split("|");
      return { cloud, region };
    })
    .sort((a, b) => Number(a.cloud !== "aws") - Number(b.cloud !== "aws"));
  const heads = ["aws", "azure"]
    .map((c) => ({ cloud: CLOUD_NAMES[c] ?? c, n: columns.filter((x) => x.cloud === c).length }))
    .filter((h) => h.n > 0);

  const shown = aggs.filter((a) => state.tier === "All" || a.machine.tier === state.tier);
  const groups: MatrixGroup[] = TIERS.map((tier) => ({
    tier,
    sub: TIER_SUBS[tier] ?? "",
    rows: shown
      .filter((a) => a.machine.tier === tier)
      .map(
        (a): MatrixRow => ({
          name: a.machine.name,
          sub: cloudsOf(a),
          skus: [...a.machine.aws, ...a.machine.azure].join(", "),
          focus: a.machine.name === state.gpu,
          cells: columns.map((col) => cellOf(d, f, steps, a, col)),
        }),
      ),
  })).filter((g) => g.rows.length > 0);

  const size = stepDays(f);
  const intro =
    "Rows are grouped by tier. Each cell: one machine in one region. Filled cells: of the machines we asked for there right now, the share handed to us. " +
    `Outlined cells: a different question — the share of checks with a 1-machine, 1-day Capacity Block starting within ${NOW_LEAD_H} h. ` +
    `The small bars are the same share ${size > 1 ? `in ${size}-day steps` : "day by day"} across the window.`;

  return {
    section: "matrix",
    empty: false,
    title: MATRIX_TITLE,
    intro,
    legend: MATRIX_LEGEND,
    columns,
    heads,
    groups,
  };
}
