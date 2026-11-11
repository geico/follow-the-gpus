// Section "Definitions, quota walls, not measured" (spec 0006 section 11.22): what a "yes" is, the
// places our quota or a size restriction refused first, and what the board does not measure.

import { aggregateAll } from "./aggregate";
import { CLOUD_NAMES, cbHorizon, machinesOfTier } from "./catalogue";
import { dateLabel, plural } from "./format";
import { hasWindowSweep, windowSweeps } from "./inwindow";
import { joinData } from "./join";
import { hourInstant, makeFrame } from "./time";
import type { BoardData, EmptySection, ViewState } from "./types";

export const DEFS_ERROR_CODE = "InsufficientInstanceCapacity";

export const WALLS_KICKER = "For capacity planning: the quota walls";
export const NOT_MEASURED_KICKER = "Not measured — do not read it into this board";

export const WALLS_LEAD =
  "Never reached the capacity question — our account’s quota or a size restriction on our subscription refused first. Each is a request to the cloud (a quota increase or a size access request), not a capacity fact.";
export const WALLS_NONE =
  "No quota walls for this tier: every place we asked gave a capacity answer.";

const AWS_ZONES_NOW =
  "each zone of the region that offers the type, one zone after the other; a yes means at least 1 zone handed us the machines, and the count is the most that 1 zone handed us, what a single request gets; the strip tooltip also gives the sum across the zones, a lower bound";
const NOT_ZONES_NOW =
  "All that the zones of a region hold: each zone stops at the count we ask, so the sum across zones is only a lower bound";

export type Definition = {
  /** The bold lead, for example "AWS, right now". */
  readonly lead: string;
  /** The text after the lead and its dash. */
  readonly text: string;
};

export type DefinitionsData = {
  readonly section: "definitions";
  readonly empty: false;
  readonly title: string;
  /** The 3 definitions, in order. */
  readonly defs: readonly Definition[];
  readonly wallsKicker: string;
  /** The walls text, or the no-wall sentence. */
  readonly walls: string;
  /** True when at least 1 place is a quota wall. */
  readonly hasWalls: boolean;
  readonly notMeasuredKicker: string;
  readonly notMeasured: readonly string[];
};

export type DefinitionsResult = EmptySection<"definitions"> | DefinitionsData;

/** The distinct zone letters of `meta.zones` joined by "/": "us-east-1a" gives "a". */
function zoneLetters(zones: readonly string[]): string {
  return [...new Set(zones.map((z) => z.slice(-1)))].join("/");
}

export function deriveDefinitions(data: BoardData, state: ViewState): DefinitionsResult {
  if (!hasWindowSweep(data)) return { section: "definitions", empty: true };
  const { meta, window } = data;
  const f = makeFrame(meta, window.hz, window.page);
  const zsh = meta.zoneStartH;
  const oneZone = zsh === null || zsh >= f.H1;
  const perZone = zsh !== null && zsh <= f.H0;
  const oneZoneText = `a capacity reservation in zone ${zoneLetters(meta.zones)} of each region`;
  const zoneDate = zsh === null ? "" : dateLabel(hourInstant(f, zsh));

  let method: string;
  let zonesItem: string;
  if (oneZone) {
    method = oneZoneText;
    zonesItem = `Other availability zones: AWS reservations were asked in ${meta.zones.join(", ")} only`;
  } else if (perZone) {
    method = `a capacity reservation in ${AWS_ZONES_NOW}`;
    zonesItem = NOT_ZONES_NOW;
  } else {
    method = `a capacity reservation in ${AWS_ZONES_NOW}. Before ${zoneDate}: in zone ${zoneLetters(meta.zones)} of each region only`;
    zonesItem = `Other availability zones before ${zoneDate}: AWS reservations were asked in ${meta.zones.join(", ")} only`;
  }
  const horizon = plural(cbHorizon(meta.cbHorizonDays), "day");
  const defs: Definition[] = [
    {
      lead: "AWS, right now",
      text: `${method}. Granted means the machines were ours to start; we release it within a minute. Refused means ${DEFS_ERROR_CODE}.`,
    },
    {
      lead: "Azure, right now",
      text: "a capacity reservation, granted in full or not at all, or a scale set that really starts the VMs; both are deleted at once.",
    },
    {
      lead: "AWS, days ahead",
      text: `a Capacity Block check: is a 1-machine, 1-day block on offer, and does it start within 1 h (a yes for now)? Blocks that start later are in Book ahead, up to ${horizon} out. An offer, not a purchase. Only in Book ahead and the outlined matrix cells.`,
    },
  ];

  // The quota walls: places with n > 0 and na = n, per machine of the tier, grouped by cloud.
  const gl = new Set(machinesOfTier(state.tier));
  const regionOrder = meta.regions.map((r) => r.split("|")[1] ?? r);
  const items: string[] = [];
  for (const a of aggregateAll(joinData(data), f)) {
    if (!gl.has(a.machine.name)) continue;
    const groups: string[] = [];
    for (const cloud of ["aws", "azure"]) {
      const walls = a.places
        .filter((p) => p.cloud === cloud && p.n > 0 && p.na === p.n)
        .sort((x, y) => regionOrder.indexOf(x.region) - regionOrder.indexOf(y.region));
      if (walls.length === 0) continue;
      const list = walls
        .map((p) => `${p.region}${p.reason === "restricted" ? " (restricted)" : ""}`)
        .join(", ");
      groups.push(`${CLOUD_NAMES[cloud] ?? cloud} ${list}`);
    }
    if (groups.length > 0) items.push(`${a.machine.name}: ${groups.join("; ")}`);
  }

  const nMax = windowSweeps(data).reduce((m, s) => Math.max(m, s.asked, s.nMax), 0);
  return {
    section: "definitions",
    empty: false,
    title: "Definitions, quota walls, not measured",
    defs,
    wallsKicker: WALLS_KICKER,
    walls: items.length > 0 ? `${WALLS_LEAD} ${items.join(" · ")}.` : WALLS_NONE,
    hasWalls: items.length > 0,
    notMeasuredKicker: NOT_MEASURED_KICKER,
    notMeasured: [
      "How long you keep a machine once it starts",
      "What a yes would have cost: On-Demand and Capacity Block prices were never probed, only Spot",
      `Parties over ${nMax} machines; blocks for more than 1 machine`,
      "Interconnected multi-node capacity (placement groups never varied)",
      zonesItem,
      "Spot forecasts as availability — shown only in the dashed box",
    ],
  };
}
