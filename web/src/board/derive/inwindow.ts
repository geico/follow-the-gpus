// The empty-window tests of the sections (spec 0006 section 11.2 item 8). Pure: no DOM, no React.
// The payload also holds the lookback range L..H1; only a record with H0 <= h < H1 counts.

import type { BoardData } from "./types";

function inWindow(data: BoardData, h: number | null | undefined): boolean {
  const { H0, H1 } = data.window;
  return h !== null && h !== undefined && h >= H0 && h < H1;
}

/** The sweeps with H0 <= h < H1; the lookback range is not in it. */
export function windowSweeps(data: BoardData) {
  return data.window.sweeps.filter((s) => inWindow(data, s.h));
}

/** True when the window holds at least 1 sweep with H0 <= h < H1. */
export function hasWindowSweep(data: BoardData): boolean {
  return data.window.sweeps.some((s) => inWindow(data, s.h));
}

/** True when the window holds at least 1 sweep or 1 Capacity Block check with H0 <= h < H1. */
export function hasWindowData(data: BoardData): boolean {
  return (
    hasWindowSweep(data) || data.window.cb.some((p) => p.checks.some((c) => inWindow(data, c[0])))
  );
}
