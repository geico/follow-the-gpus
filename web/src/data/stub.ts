// A stub source for tests: the shapes of spec 0006 sections 7.3 and 7.4 with no rows.

import type { BoardData, Meta, WindowHorizon, WindowPayload } from "../board/derive/types";
import type { DataSource, MetaResponse } from "./source";

export function stubMeta(): Meta {
  return {
    t0: "2026-10-01T07:00:00Z",
    tEnd: "2026-10-22T07:00:00Z",
    nh: 505,
    dates: ["2026-10-01", "2026-10-02"],
    weekends: 0,
    cadenceH: { launch: 2, cb: 2 },
    probeStartH: {},
    zones: [],
    cbHorizonDays: [14],
    regions: [],
    rows: 0,
    sweeps: 0,
    firstH: {},
    cbFirstH: {},
    upStartH: null,
    zoneStartH: null,
    simulated: true,
    now: { sweeps: [], places: [], cb: [] },
  };
}

export function stubWindow(hz: WindowHorizon = "all", page = 0): WindowPayload {
  return {
    hz,
    page,
    H0: 0,
    H1: 505,
    L: 0,
    sweeps: [],
    cb: [],
    spot: {},
    azspot: {},
    price: { dates: [], series: {} },
  };
}

export function stubBoardData(): BoardData {
  return { meta: stubMeta(), window: stubWindow() };
}

export class StubDataSource implements DataSource {
  constructor(private readonly metaValue: MetaResponse = stubMeta()) {}

  meta(): Promise<MetaResponse> {
    return Promise.resolve(this.metaValue);
  }

  window(hz: WindowHorizon, page: number): Promise<WindowPayload> {
    return Promise.resolve(stubWindow(hz, page));
  }
}
