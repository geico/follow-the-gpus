// Formatting and colour helpers (spec 0006 section 11.4). Pure; no locale of the host: the
// separators are fixed to `en-US`.

const COUNT = new Intl.NumberFormat("en-US");

/** Count with `en-US` grouping; null prints "–". */
export function count(n: number | null): string {
  return n === null ? "–" : COUNT.format(n);
}

/**
 * "{label} {noun}", the noun singular when the count is 1 (spec 0006 section 10, "Singular"). The
 * label defaults to the plain count; pass `count(n)` for the grouped form.
 */
export function plural(n: number, noun: string, label: string = String(n)): string {
  return `${label} ${noun}${n === 1 ? "" : "s"}`;
}

/** Percent `ps(p)`. */
export function ps(p: number | null): string {
  if (p === null) return "–";
  if (p > 0 && p < 1) return "<1%";
  if (p > 99 && p < 100) return "99%";
  return `${Math.round(p)}%`;
}

/** Share `sh(a, b)`: 100 × a / b when b > 0, else null. */
export function sh(a: number, b: number): number | null {
  return b > 0 ? (100 * a) / b : null;
}

/** Hours `hrs(h)`: under 48 h as hours, else as days with 1 decimal. */
export function hrs(h: number): string {
  if (h < 48) return `${Math.round(h)} h`;
  return `${Math.round((h / 24) * 10) / 10} d`;
}

/** Median: the element floor(n / 2) of the sorted list; null for an empty list. */
export function median(xs: readonly number[]): number | null {
  if (xs.length === 0) return null;
  const s = [...xs].sort((a, b) => a - b);
  return s[Math.floor(s.length / 2)] ?? null;
}

const WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"] as const;
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** Day label "13 Tue" of an instant (UTC). */
export function dayLabel(ms: number): string {
  const d = new Date(ms);
  return `${d.getUTCDate()} ${WEEKDAYS[d.getUTCDay()]}`;
}

/** Weekday short name "Tue" of an instant (UTC). */
export function weekdayName(ms: number): string {
  return WEEKDAYS[new Date(ms).getUTCDay()] ?? "";
}

/** Date label "Oct 13" of an instant (UTC). */
export function dateLabel(ms: number): string {
  const d = new Date(ms);
  return `${MONTHS[d.getUTCMonth()]} ${d.getUTCDate()}`;
}

/** `heat(p)`: the heat fill; transparent for null. */
export function heat(p: number | null): string {
  if (p === null) return "transparent";
  const pct = Math.round(8 + 92 * (p / 100) ** 0.8);
  return `color-mix(in oklch, var(--color-accent-400) ${pct}%, var(--color-neutral-900))`;
}

/** `tone(p)`: the colour token of a share. */
export function tone(p: number | null): string {
  if (p === null) return "--color-neutral-500";
  if (p >= 60) return "--color-accent-300";
  if (p >= 25) return "--color-neutral-200";
  return "--color-neutral-400";
}

/** The hatch of a not-asked cell. */
export const HATCH =
  "repeating-linear-gradient(45deg, var(--color-neutral-700) 0 1.5px, transparent 1.5px 4px)";

/** Text on a heat fill flips to the background colour at p >= 45. */
export function flipsOnHeat(p: number | null): boolean {
  return p !== null && p >= 45;
}
