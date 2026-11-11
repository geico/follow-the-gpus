// View state (spec 0006 section 11.5): the defaults, the hash codec, and the snap rules. Pure:
// the storage and the location are read by the caller.

import { machinesOfTier, TIERS } from "./catalogue";
import type { Horizon, ViewState } from "./types";

export const DEFAULT_STATE: ViewState = { hz: "all", page: null, tier: "All", gpu: "H100 ×1" };

const HORIZONS: readonly string[] = ["day", "week", "month", "all"];

/** Fix a state: a focus machine outside the tier snaps to the first machine of the tier. */
export function normalize(s: ViewState): ViewState {
  const gl = machinesOfTier(s.tier);
  const gpu = gl.includes(s.gpu) ? s.gpu : (gl[0] ?? s.gpu);
  return gpu === s.gpu ? s : { ...s, gpu };
}

/** Split `a=b&c=d` into decoded pairs. A bad escape drops the pair. */
function parsePairs(query: string): ReadonlyMap<string, string> {
  const m = new Map<string, string>();
  for (const part of query.split("&")) {
    const i = part.indexOf("=");
    if (i < 0) continue;
    try {
      m.set(decodeURIComponent(part.slice(0, i)), decodeURIComponent(part.slice(i + 1)));
    } catch {
      // a bad escape: leave the pair out
    }
  }
  return m;
}

/** The one page validator: a canonical non-negative integer string gives the number, else null. */
export function parsePage(v: string | null): number | null {
  return v !== null && /^(0|[1-9]\d*)$/.test(v) ? Number(v) : null;
}

/** Apply the keys of `p` that hold a valid value over `base`. Unknown values are ignored. */
function overlay(base: ViewState, p: ReadonlyMap<string, string>): ViewState {
  const hz = p.get("hz") ?? null;
  // A hash with an unsupported hz drops its page too: the page belongs to that hz.
  const hzBad = hz !== null && !HORIZONS.includes(hz);
  const page = hzBad ? null : parsePage(p.get("page") ?? null);
  const tier = p.get("tier") ?? null;
  const gpu = p.get("gpu") ?? null;
  const gpuOk = gpu !== null && machinesOfTier("All").includes(gpu);
  const tierOk = tier !== null && (tier === "All" || (TIERS as readonly string[]).includes(tier));
  // A machine with no tier of its own is not snapped by a base tier that does not hold it.
  const baseTier = gpuOk && !tierOk && !machinesOfTier(base.tier).includes(gpu) ? "All" : base.tier;
  const next: ViewState = {
    hz: hz !== null && HORIZONS.includes(hz) ? (hz as Horizon) : base.hz,
    page: page ?? base.page,
    tier: tierOk ? tier : baseTier,
    gpu: gpuOk ? gpu : base.gpu,
  };
  return normalize(next);
}

/** Parse `#hz=week&page=1&tier=All&gpu=...`. */
export function fromHash(hash: string, base: ViewState = DEFAULT_STATE): ViewState {
  const p = parsePairs(hash.replace(/^#/, ""));
  const next = overlay(base, p);
  // A change of hz clears page (section 11.5): a hash with a new hz and no valid page does not keep the old page.
  const pageOk = parsePage(p.get("page") ?? null) !== null;
  return next.hz !== base.hz && !pageOk ? { ...next, page: null } : next;
}

/** Write the hash: values URI-encoded, absent keys left out. */
export function toHash(s: ViewState): string {
  const parts = [`hz=${encodeURIComponent(s.hz)}`];
  if (s.page !== null) parts.push(`page=${s.page}`);
  parts.push(`tier=${encodeURIComponent(s.tier)}`, `gpu=${encodeURIComponent(s.gpu)}`);
  return `#${parts.join("&")}`;
}

/** Read the stored JSON (the localStorage value); a bad value gives the default. */
export function fromStored(json: string | null): ViewState {
  if (json === null) return DEFAULT_STATE;
  try {
    const o: unknown = JSON.parse(json);
    if (typeof o !== "object" || o === null) return DEFAULT_STATE;
    const r = o as Record<string, unknown>;
    const p = new Map<string, string>();
    for (const k of ["hz", "tier", "gpu"]) if (typeof r[k] === "string") p.set(k, r[k] as string);
    if (typeof r.page === "number") p.set("page", String(r.page));
    return overlay(DEFAULT_STATE, p);
  } catch {
    return DEFAULT_STATE;
  }
}

/** The storage JSON of a state. The open strip rows are not part of it. */
export function toStored(s: ViewState): string {
  return JSON.stringify(s);
}

/** A change of `hz` clears `page`. */
export function withHorizon(s: ViewState, hz: Horizon): ViewState {
  return { ...s, hz, page: null };
}

export function withTier(s: ViewState, tier: string): ViewState {
  return normalize({ ...s, tier });
}
