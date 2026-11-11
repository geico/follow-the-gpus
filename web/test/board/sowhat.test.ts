// @vitest-environment jsdom

import { cleanup, render } from "@testing-library/react";
import { createElement } from "react";
import { afterEach, describe, expect, it } from "vitest";
import type { BlockPlace, MachineAgg } from "../../src/board/derive/aggregate";
import type { SawtoothResult } from "../../src/board/derive/sawtooth";
import { bestBlock, blockText, deriveSowhat } from "../../src/board/derive/sowhat";
import type { BoardData, CbPlace, Sweep, ViewState } from "../../src/board/derive/types";
import { DEFAULT_STATE } from "../../src/board/derive/viewstate";
import { SowhatView } from "../../src/board/view/sowhat";
import { stubBoardData, stubMeta, stubWindow } from "../../src/data/stub";

// Branches tested from the text of spec 0006 section 11.10.
const ALL: ViewState = { hz: "all", page: null, tier: "All", gpu: "H100 ×1" };

function sweep(
  h: number,
  sku: string,
  region: string,
  state: number,
  got = 0,
  cloud = "aws",
): Sweep {
  return {
    h,
    t: h,
    cloud,
    region,
    sku,
    probe: cloud === "aws" ? "aws_odcr" : "az_capacity_reservation",
    asked: 1,
    gotZoneMax: got,
    gotZoneSum: got,
    state,
    nMax: got,
    reason: state === -1 ? "quota" : null,
    err: null,
    zones: 1,
    zonesYes: got > 0 ? 1 : 0,
  };
}

/** Round r of a series: a sweep of 1 place, yes ("Y") or no ("N"). */
const series = (sku: string, pattern: string) =>
  [...pattern].map((c, r) =>
    c === "Y" ? sweep(2 * r, sku, "us-east-1", 2, 1) : sweep(2 * r, sku, "us-east-1", 0),
  );

function synth(sweeps: Sweep[], cb: CbPlace[] = []): BoardData {
  return { meta: stubMeta(), window: { ...stubWindow(), sweeps, cb } };
}

function planOf(data: BoardData, state: ViewState, name: string): string | undefined {
  const res = deriveSowhat(data, state);
  if (res.empty) throw new Error("empty");
  return res.plans.find((p) => p.name === name)?.text;
}

const NO_CB = "We check no Capacity Block for it, so there is no sure-thing path we could probe.";

describe("so what card, branches from the spec text", () => {
  it("mode none names the not-offered and not-available places, earliest first (item 1)", () => {
    const data = synth([
      sweep(6, "p5.4xlarge", "us-east-1", -4),
      sweep(4, "Standard_NC40ads_H100_v5", "eastus", -3, 0, "azure"),
      sweep(2, "p5.4xlarge", "us-east-1", -4),
      sweep(8, "p5.4xlarge", "us-west-2", -3),
    ]);
    expect(planOf(data, ALL, "H100 ×1")).toBe(
      "No capacity answer in the window shown: AWS us-east-1 not offered, Azure eastus not available, AWS us-west-2 not available.",
    );
    const reordered = synth([
      sweep(6, "p5.4xlarge", "us-east-1", -4),
      sweep(4, "Standard_NC40ads_H100_v5", "eastus", -3, 0, "azure"),
    ]);
    expect(planOf(reordered, ALL, "H100 ×1")).toBe(
      "No capacity answer in the window shown: Azure eastus not available, AWS us-east-1 not offered.",
    );
  });

  it("no yes in any round: do not plan on demand, and no retry text (item 2, share 0)", () => {
    const data = synth(series("g6.xlarge", "NNNNNNNN"));
    expect(planOf(data, ALL, "L4")).toBe(
      `Nobody handed us one in 8 rounds; do not plan on demand. ${NO_CB}`,
    );
  });

  it("a long shot with a wait of 0 prints the round length as the drought (item 2)", () => {
    const data = synth(series("g6.xlarge", "NYN"));
    expect(planOf(data, ALL, "L4")).toBe(
      `On demand is a long shot: 33% of rounds even asking everywhere; longest drought 2 h. ${NO_CB}`,
    );
  });

  it("P(yes after no) of 10 says wait, not retry; the yes line needs a − b >= 20 (item 3)", () => {
    // After a yes: 3 of 4; after a no: 1 of 10. Yes-streaks 4 and 1: the median is 4 rounds.
    const data = synth(series("g6.xlarge", "YYYYNNNNNNNNNNY"));
    expect(planOf(data, ALL, "L4")).toBe(
      "On demand is a long shot: 33% of rounds even asking everywhere; longest drought 22 h." +
        " After a no, only 10% of next rounds said yes: a no means wait, not retry — check back next day, not next hour." +
        " A yes 2 h ago is worth more than any score: when you get one, take it — 75% of the time it is still there next round, median streak 8 h." +
        ` ${NO_CB}`,
    );
  });

  it("share 0.9 launches on demand with no Capacity Block line; under 3 rounds after a no, no retry text", () => {
    const data = synth(series("g6.xlarge", "YYYYYYYNYY"));
    expect(planOf(data, ALL, "L4")).toBe(
      "Launch on demand; AWS us-east-1 handed us 90% of asks. Reserve nothing.",
    );
  });

  describe("Capacity Block (item 4)", () => {
    // [h, found, leadDays, durationH, fee, horizonDays, nRequested, leadH]
    const check = (h: number, lead: number | null, found = 1, leadH: number | null = null) => [
      h,
      found,
      lead,
      24,
      null,
      14,
      1,
      leadH ?? (lead === null ? null : lead * 24),
    ];
    const cb: CbPlace[] = [
      {
        sku: "p6-b200.48xlarge",
        region: "us-east-1",
        checks: [check(0, 5), check(24, 4), check(48, 10)],
        longest: [],
      },
      {
        sku: "p6-b200.48xlarge",
        region: "us-west-2",
        checks: [check(0, 5), check(24, 4), check(48, 3)],
        longest: [],
      },
      { sku: "p5.48xlarge", region: "us-east-2", checks: [check(0, null, 0)], longest: [] },
      { sku: "p4d.24xlarge", region: "us-east-1", checks: [check(0, null)], longest: [] },
    ];
    const state: ViewState = { hz: "all", page: null, tier: "Frontier node", gpu: "B200 ×8" };
    const res = deriveSowhat(synth([], cb), state);
    if (res.empty) throw new Error("empty");
    const text = (name: string) => res.plans.find((p) => p.name === name)?.text;

    it("names the best place by share, then by the lowest lead; a jump in j of k regions", () => {
      expect(text("B200 ×8")).toBe(
        "No Capacity Block started within 1 h of a check. Book ahead: 100% of checks in us-west-2 had a block, starting ~4 d out." +
          " The next free slot got bought out from under us in 1 of 2 regions — book the moment you see a date you can live with; it will not wait.",
      );
    });

    it("a block place with no found check: none found within the horizon", () => {
      expect(text("H100 ×8")).toBe("No check found a Capacity Block within 14 days in any region.");
    });

    it("a found check with no lead prints – for the start", () => {
      expect(text("A100 ×8")).toBe(
        "No Capacity Block started within 1 h of a check. Book ahead: 100% of checks in us-east-1 had a block, starting ~– out.",
      );
    });

    it("states the within-1-h rate, the same as the strip and the matrix", () => {
      // 1 of 4 checks starts 30 minutes out: 25% now, though every check found a block.
      const near: CbPlace[] = [
        {
          sku: "p6-b200.48xlarge",
          region: "us-east-1",
          checks: [check(0, 0.02, 1, 0.5), check(24, 4), check(48, 4), check(72, 4)],
          longest: [],
        },
      ];
      const r = deriveSowhat(synth([], near), state);
      if (r.empty) throw new Error("empty");
      expect(r.plans.find((p) => p.name === "B200 ×8")?.text).toMatch(
        /^For a sure thing, a Capacity Block in us-east-1 started within 1 h on 25% of checks\./,
      );
    });

    it("a machine with nothing in the window", () => {
      expect(text("H200 ×8")).toBe("No probes for this machine in the window shown.");
    });
  });
});

afterEach(cleanup);

describe("so what view", () => {
  it("renders an empty labelled section on an empty store", () => {
    const { container } = render(
      createElement(SowhatView, { data: stubBoardData(), state: DEFAULT_STATE }),
    );
    const s = container.querySelector("section[data-section=sowhat]");
    expect(s?.getAttribute("aria-label")).toBe("So what");
    expect(s?.childElementCount).toBe(0);
  });
});

describe("so what card, edge cases", () => {
  const place = (region: string, n: number, found: number) =>
    ({ region, sku: "p5.4xlarge", n, found, medLead: 1 }) as unknown as BlockPlace;

  it("a block place with n = 0 is not a block place (item 4)", () => {
    const real = place("us-west-2", 4, 1);
    expect(bestBlock([place("us-east-1", 0, 0), real])).toBe(real);
    expect(bestBlock([place("us-east-1", 0, 0)])).toBeNull();
  });

  it("mode now with tried = 0 and no block place has no Capacity Block line (item 4)", () => {
    const agg = {
      mode: "now",
      blockPlaces: [],
      counts: { tried: 0, yes: 0, waitH: 0 },
      machine: { name: "H100 ×1" },
    } as unknown as MachineAgg;
    const saw = { empty: true } as unknown as SawtoothResult;
    expect(blockText(agg, saw, 14)).toBeNull();
    const asked = { ...agg, counts: { tried: 4, yes: 1, waitH: 0 } } as unknown as MachineAgg;
    expect(blockText(asked, saw, 14)).toBe(NO_CB);
  });
});
