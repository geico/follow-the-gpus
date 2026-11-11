import { describe, expect, it } from "vitest";
import { joinData } from "../../src/board/derive/join";
import type { Sweep } from "../../src/board/derive/types";
import { stubBoardData } from "../../src/data/stub";

describe("joinData carries meta.now (spec 0006 sections 7.3 and 7.4)", () => {
  it("gives each now sweep got = gotZoneMax and passes places and cb through", () => {
    const d = stubBoardData();
    const base = d.window.sweeps[0] as Sweep;
    const now = {
      sweeps: [
        { ...base, gotZoneMax: 3 },
        { ...base, gotZoneMax: 0 },
      ],
      places: [],
      cb: [],
    };
    const j = joinData({ ...d, meta: { ...d.meta, now } }).meta.now;
    expect(j?.sweeps.map((s) => s.got)).toEqual([3, 0]);
    expect(j?.places).toBe(now.places);
    expect(j?.cb).toBe(now.cb);
  });

  it("does not crash when the meta has no now", () => {
    const d = stubBoardData();
    const { now: _now, ...old } = d.meta;
    expect(() => joinData({ ...d, meta: old as typeof d.meta })).not.toThrow();
  });
});
