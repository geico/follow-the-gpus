// The join of meta and a window payload into the design's data shape (spec 0006 section 7.4,
// last paragraph). Each sweep, and each sweep of `meta.now`, gets the design's field `got` = `gotZoneMax`.

import type { BoardData, JoinedData, JoinedMeta } from "./types";

export function joinData(data: BoardData): JoinedData {
  const w = data.window;
  // An older events-file meta has no `now`: leave it out of the join and do not crash.
  const meta = (
    data.meta.now
      ? {
          ...data.meta,
          now: {
            ...data.meta.now,
            sweeps: data.meta.now.sweeps.map((s) => ({ ...s, got: s.gotZoneMax })),
          },
        }
      : data.meta
  ) as JoinedMeta;
  return {
    meta,
    hz: w.hz,
    page: w.page,
    H0: w.H0,
    H1: w.H1,
    L: w.L,
    sweeps: w.sweeps.map((s) => ({ ...s, got: s.gotZoneMax })),
    cb: w.cb,
    spot: w.spot,
    azspot: w.azspot,
    price: w.price,
  };
}
