// The time model, the pages, and the rounds (spec 0006 section 11.2). Every hour of day is the
// real UTC hour of T0 + h (decision R6). Week pages and weekends follow America/New_York with its
// daylight-time change (correction C2); offsets of America/New_York and America/Los_Angeles come
// from `Intl` and the tz database (correction C3).

import { dateLabel, dayLabel, plural, weekdayName } from "./format.ts";
import type { Meta, WindowHorizon } from "./types.ts";

const HOUR = 3600e3;
const DAY = 24 * HOUR;

export const NEW_YORK = "America/New_York";
export const LOS_ANGELES = "America/Los_Angeles";

const MONTH_NAMES = [
  "January",
  "February",
  "March",
  "April",
  "May",
  "June",
  "July",
  "August",
  "September",
  "October",
  "November",
  "December",
];

const formatters = new Map<string, Intl.DateTimeFormat>();

function formatter(zone: string): Intl.DateTimeFormat {
  let f = formatters.get(zone);
  if (f === undefined) {
    f = new Intl.DateTimeFormat("en-US", {
      timeZone: zone,
      hourCycle: "h23",
      year: "numeric",
      month: "numeric",
      day: "numeric",
      hour: "numeric",
      minute: "numeric",
      second: "numeric",
    });
    formatters.set(zone, f);
  }
  return f;
}

/** The wall-clock fields of an instant in a zone. */
export function zoneParts(ms: number, zone: string) {
  const v: Record<string, number> = {};
  for (const p of formatter(zone).formatToParts(new Date(ms))) {
    if (p.type !== "literal") v[p.type] = Number(p.value);
  }
  return {
    year: v.year ?? 0,
    month: v.month ?? 1,
    day: v.day ?? 1,
    hour: v.hour ?? 0,
    minute: v.minute ?? 0,
    second: v.second ?? 0,
  };
}

/** The UTC offset of a zone at an instant, in hours (for example -4 or -5). */
export function utcOffsetHours(ms: number, zone: string): number {
  const p = zoneParts(ms, zone);
  const asUtc = Date.UTC(p.year, p.month - 1, p.day, p.hour, p.minute, p.second);
  return (asUtc - Math.floor(ms / 1000) * 1000) / HOUR;
}

/** The instant of 00:00 on a local date of a zone. `day` may overflow the month. */
function localMidnight(year: number, month: number, day: number, zone: string): number {
  const wall = Date.UTC(year, month - 1, day);
  let t = wall - utcOffsetHours(wall, zone) * HOUR;
  t = wall - utcOffsetHours(t, zone) * HOUR;
  return t;
}

/** Day of week of an instant in America/New_York, 0 = Sunday. */
export function etDayOfWeek(ms: number): number {
  const p = zoneParts(ms, NEW_YORK);
  return new Date(Date.UTC(p.year, p.month - 1, p.day)).getUTCDay();
}

/** Saturday or Sunday in America/New_York (section 11.2 item 7). */
export function isWeekendEt(ms: number): boolean {
  const d = etDayOfWeek(ms);
  return d === 0 || d === 6;
}

/** The UTC midnight at or before an instant. */
function utcMidnight(ms: number): number {
  return Math.floor(ms / DAY) * DAY;
}

export type Page = { readonly start: number; readonly end: number };

/** The page list of a horizon (section 11.2 item 2), as instants in milliseconds. */
export function pageList(hz: WindowHorizon, t0: number, tEnd: number): readonly Page[] {
  if (hz === "all") return [{ start: utcMidnight(t0), end: tEnd + 1 }];
  const pages: Page[] = [];
  if (hz === "day") {
    for (let start = utcMidnight(t0); start <= tEnd; start += DAY) {
      pages.push({ start, end: start + DAY });
    }
    return pages;
  }
  if (hz === "week") {
    const p = zoneParts(t0, NEW_YORK);
    const dow = new Date(Date.UTC(p.year, p.month - 1, p.day)).getUTCDay();
    let day = p.day - ((dow + 6) % 7);
    let start = localMidnight(p.year, p.month, day, NEW_YORK);
    while (start <= tEnd) {
      day += 7;
      const end = localMidnight(p.year, p.month, day, NEW_YORK);
      pages.push({ start, end });
      start = end;
    }
    return pages;
  }
  const d = new Date(t0);
  let y = d.getUTCFullYear();
  let m = d.getUTCMonth();
  let start = Date.UTC(y, m, 1);
  while (start <= tEnd) {
    m += 1;
    if (m > 11) {
      m = 0;
      y += 1;
    }
    const end = Date.UTC(y, m, 1);
    pages.push({ start, end });
    start = end;
  }
  return pages;
}

/** The default page index (section 11.2 item 3). */
export function defaultPage(
  hz: WindowHorizon,
  pages: readonly Page[],
  t0: number,
  tEnd: number,
): number {
  const coverage = (p: Page) => (Math.min(p.end, tEnd) - Math.max(p.start, t0)) / (p.end - p.start);
  let i = pages.length - 1;
  const last = pages[i];
  if (hz !== "day" && last !== undefined && coverage(last) < 0.5 && i > 0) i -= 1;
  return Math.max(0, i);
}

/** The window [H0, H1) in hours after T0 of a page (section 11.2 item 4). `nh` is `meta.nh`. */
export function pageWindow(pg: Page, t0: number, nh: number): { H0: number; H1: number } {
  const H0 = Math.round((pg.start - t0) / HOUR) || 0;
  let H1 = Math.min(nh, Math.ceil((pg.end - t0) / HOUR));
  if (H1 <= H0) H1 = H0 + 1;
  return { H0, H1 };
}

export type Frame = {
  readonly hz: WindowHorizon;
  readonly t0: number;
  readonly tEnd: number;
  readonly nh: number;
  /** The round length in hours: `meta.cadenceH.launch`, or 2 when null or 0. */
  readonly cad: number;
  readonly pages: readonly Page[];
  /** The 0-based index of the page shown. */
  readonly page: number;
  readonly H0: number;
  readonly H1: number;
  /** The start of the range before the page that the trend compares with (section 7.4). */
  readonly L: number;
  /** The cell length in hours and its name. */
  readonly grain: number;
  readonly grainName: "round" | "day" | "week";
  /** The number of cells and of rounds. */
  readonly NB: number;
  readonly NR: number;
  readonly pageLabel: string;
  readonly noPrev: boolean;
  readonly noNext: boolean;
};

export function cadence(meta: Meta): number {
  const c = meta.cadenceH.launch;
  return c === null || c === 0 ? 2 : c;
}

/** The frame of a horizon and page (section 11.2). `page` null selects the default page. */
export function makeFrame(meta: Meta, hz: WindowHorizon, page: number | null): Frame {
  const t0 = Date.parse(meta.t0);
  const tEnd = Date.parse(meta.tEnd);
  const cad = cadence(meta);
  const pages = pageList(hz, t0, tEnd);
  const idx = Math.min(Math.max(page ?? defaultPage(hz, pages, t0, tEnd), 0), pages.length - 1);
  const pg = pages[idx] ?? { start: t0, end: tEnd + 1 };
  const { H0, H1 } = pageWindow(pg, t0, meta.nh);
  const L = Math.min(H0, Math.max(0, 2 * H0 - H1));
  let grain: number;
  let grainName: Frame["grainName"];
  if (hz === "day" || hz === "week") {
    grain = cad;
    grainName = "round";
  } else if (hz === "month" || (H1 - H0) / 24 <= 60) {
    grain = 24;
    grainName = "day";
  } else {
    grain = 168;
    grainName = "week";
  }
  let pageLabel: string;
  if (hz === "all") pageLabel = `all ${plural(meta.dates.length, "day")}`;
  else if (hz === "day") pageLabel = `${weekdayName(pg.start)} ${dateLabel(pg.start)} (UTC)`;
  else if (hz === "week") {
    const p = zoneParts(pg.start, NEW_YORK);
    const mon = new Date(Date.UTC(p.year, p.month - 1, p.day));
    pageLabel = `Week of Mon ${dateLabel(mon.getTime())} (ET)`;
  } else {
    const d = new Date(pg.start);
    pageLabel = `${MONTH_NAMES[d.getUTCMonth()]} ${d.getUTCFullYear()}`;
  }
  return {
    hz,
    t0,
    tEnd,
    nh: meta.nh,
    cad,
    pages,
    page: idx,
    H0,
    H1,
    L,
    grain,
    grainName,
    NB: Math.ceil((H1 - H0) / grain),
    NR: Math.ceil((H1 - H0) / cad),
    pageLabel,
    noPrev: idx <= 0,
    noNext: idx >= pages.length - 1,
  };
}

/** The instant of hour `h` after T0. */
export function hourInstant(f: Pick<Frame, "t0">, h: number): number {
  return f.t0 + h * HOUR;
}

/** "04:00" of the real UTC hour of T0 + h (decision R6, correction C1). */
export function utcHourLabel(f: Pick<Frame, "t0">, h: number): string {
  const hh = new Date(hourInstant(f, h)).getUTCHours();
  return `${String(hh).padStart(2, "0")}:00`;
}

/** The start hour (h) of cell `k`. */
export function cellStartH(f: Pick<Frame, "H0" | "grain">, k: number): number {
  return f.H0 + k * f.grain;
}

/** The label of cell `k` in a tooltip title: "26 Mon 04:00 UTC" at round grain, else "13 Tue Oct 13". */
export function cellLabel(f: Pick<Frame, "t0" | "H0" | "grain" | "grainName">, k: number): string {
  const ms = hourInstant(f, cellStartH(f, k));
  if (f.grainName === "round") return `${dayLabel(ms)} ${utcHourLabel(f, cellStartH(f, k))} UTC`;
  return `${dayLabel(ms)} ${dateLabel(ms)}`;
}

/** The round index of hour `h` in the window: floor((h − H0) / CAD). */
export function roundIndex(f: Pick<Frame, "H0" | "cad">, h: number): number {
  return Math.floor((h - f.H0) / f.cad);
}
