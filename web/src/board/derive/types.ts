// Shared types of the derive layer: the parsed payloads (spec 0006 sections 7.3 and 7.4), the view
// state (section 11.5), and the empty result of a section stub. The data client imports these
// types; the derive layer imports nothing from it (section 10.3).

/** The horizons of the board (section 11.5). */
export type Horizon = "day" | "week" | "month" | "all";

/** The horizons of a window (section 7.1): the same 4. */
export type WindowHorizon = Horizon;

export type ViewState = {
  readonly hz: Horizon;
  /** 0-based page index, or null for the default page (section 11.2 item 3). */
  readonly page: number | null;
  /** "All" or a tier name (section 11.1). */
  readonly tier: string;
  /** A machine name of the catalogue. */
  readonly gpu: string;
};

/** `meta.json` of a store with rows (section 7.3). Keys are in the order of the spec. */
export type Meta = {
  readonly t0: string;
  readonly tEnd: string;
  readonly nh: number;
  readonly dates: readonly string[];
  readonly weekends: number;
  readonly cadenceH: { readonly launch: number | null; readonly cb: number | null };
  readonly probeStartH: Readonly<Record<string, number>>;
  readonly zones: readonly string[];
  readonly cbHorizonDays: readonly number[];
  readonly regions: readonly string[];
  readonly rows: number;
  readonly sweeps: number;
  readonly firstH: Readonly<Record<string, number>>;
  readonly cbFirstH: Readonly<Record<string, number>>;
  readonly upStartH: number | null;
  /** The smallest `h` of an `aws_odcr` row with a zone id: the start of the per-zone probe. */
  readonly zoneStartH: number | null;
  readonly simulated: boolean;
  /** The latest answer of every machine over the whole data, for section 11.9 (section 7.3). */
  /** Absent in an events file made before `meta.now` existed: section 11.9 then has no answer. */
  readonly now?: Now;
};

/** `meta.now` (section 7.3): what section 11.9 reads outside any window. */
export type Now = {
  readonly sweeps: readonly Sweep[];
  readonly places: readonly NowPlace[];
  readonly cb: readonly NowCb[];
};

/** One (sku, cloud, region) place of `meta.now.places`, counted over the whole data. */
export type NowPlace = {
  readonly sku: string;
  readonly cloud: string;
  readonly region: string;
  readonly probe: string;
  /** Sweeps with state >= -1. */
  readonly n: number;
  /** Sweeps with state -1. */
  readonly na: number;
  /** Sweeps with `got` > 0. */
  readonly yes: number;
  /** Sweeps with state -3. */
  readonly nu: number;
  /** Sweeps with state -4. */
  readonly no: number;
  readonly lastYesH: number | null;
};

/** One Capacity Block place of `meta.now.cb`: its latest 1-day check and longest block. */
export type NowCb = {
  readonly sku: string;
  readonly region: string;
  /** The latest hour of the place's 1-day, 1-machine checks, or null. */
  readonly h: number | null;
  /** Of the found checks at `h`, the smallest lead; null with no found check at `h`. */
  readonly leadH: number | null;
  readonly durH: number | null;
  readonly start: string | null;
  readonly longestH: number | null;
};

/** `meta.json` of a database with no row of the 9 probe types (section 7.3). */
export type EmptyMeta = { readonly t0: null; readonly simulated: boolean };

/** A sweep record (section 5.3). */
export type Sweep = {
  readonly h: number;
  readonly t: number;
  readonly cloud: string;
  readonly region: string;
  readonly sku: string;
  readonly probe: string;
  readonly asked: number;
  /** The most that 1 zone placed (the headline `got` after the join, section 7.4). */
  readonly gotZoneMax: number;
  /** The sum over the answered zones: a lower bound, shown in the place tooltip only. */
  readonly gotZoneSum: number;
  readonly state: number;
  readonly nMax: number;
  readonly reason: "quota" | "restricted" | null;
  readonly err: string | null;
  /** The number of zone searches of the group that asked a zone (section 5.3 rule 6). */
  readonly zones: number;
  /** The number of zone searches of the group with a yes. */
  readonly zonesYes: number;
  /**
   * The zones of an `aws_odcr` sweep, in zone order (section 5.3 rule 6); `got` is the zone's
   * result, null when the zone did not answer. Absent on every other sweep.
   */
  readonly zoneList?: readonly { readonly zone: string; readonly got: number | null }[];
};

/** A sweep after the join: the design's field `got` is `gotZoneMax` (section 7.4). */
export type JoinedSweep = Sweep & { readonly got: number };

/** `meta` after the join: `meta.now.sweeps` carry `got` too (sections 7.4 and 11.9). */
export type JoinedMeta = Omit<Meta, "now"> & {
  readonly now?: Omit<Now, "sweeps"> & { readonly sweeps: readonly JoinedSweep[] };
};

/** One Capacity Block place (section 5.4 items 1 and 2). */
export type CbPlace = {
  readonly sku: string;
  readonly region: string;
  readonly checks: readonly (readonly (number | null)[])[];
  readonly longest: readonly (readonly (number | string | null)[])[];
};

/** `window/<hz>/<page>.json` (section 7.4). Keys are in the order of the spec. */
export type WindowPayload = {
  readonly hz: WindowHorizon;
  readonly page: number;
  readonly H0: number;
  readonly H1: number;
  readonly L: number;
  readonly sweeps: readonly Sweep[];
  readonly cb: readonly CbPlace[];
  readonly spot: Readonly<Record<string, readonly (readonly [number, number])[]>>;
  readonly azspot: Readonly<Record<string, readonly (readonly number[])[]>>;
  readonly price: {
    readonly dates: readonly string[];
    readonly series: Readonly<Record<string, readonly (number | null)[]>>;
  };
};

/** The joined data that every derive function reads (section 7.4, last paragraph). */
export type BoardData = { readonly meta: Meta; readonly window: WindowPayload };

/**
 * The joined data every derive function reads: the design's shape `{meta, sweeps, cb, spot,
 * azspot, price}` plus the window range (section 7.4).
 */
export type JoinedData = {
  readonly meta: JoinedMeta;
  readonly hz: WindowHorizon;
  readonly page: number;
  readonly H0: number;
  readonly H1: number;
  readonly L: number;
  readonly sweeps: readonly JoinedSweep[];
  readonly cb: readonly CbPlace[];
  readonly spot: WindowPayload["spot"];
  readonly azspot: WindowPayload["azspot"];
  readonly price: WindowPayload["price"];
};

/** What a section stub returns until its task fills it in. */
export type EmptySection<Name extends string> = { readonly section: Name; readonly empty: true };
