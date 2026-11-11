// The machine catalogue and the tier rules (spec 0006 sections 11.1, 11.5, and 11.7).

export const TIERS = [
  "Frontier node",
  "Single big GPU",
  "Mid · 48 GB",
  "Small · 16–24 GB",
] as const;

export type Machine = {
  readonly name: string;
  readonly tier: string;
  /** AWS instance types, in order; the first sets the GPU count. */
  readonly aws: readonly string[];
  /** Azure sizes, in order. */
  readonly azure: readonly string[];
};

const M = (name: string, tier: string, aws: string[], azure: string[]): Machine => ({
  name,
  tier,
  aws,
  azure,
});

/** The catalogue of spec 0006 section 11.1, in order. */
export const MACHINES: readonly Machine[] = [
  M("B200 ×8", "Frontier node", ["p6-b200.48xlarge"], []),
  M("H200 ×8", "Frontier node", ["p5en.48xlarge"], []),
  M("H100 ×8", "Frontier node", ["p5.48xlarge"], []),
  M("A100 ×8", "Frontier node", ["p4d.24xlarge"], []),
  M("H100 ×1", "Single big GPU", ["p5.4xlarge"], ["Standard_NC40ads_H100_v5"]),
  M("A100 ×1", "Single big GPU", [], ["Standard_NC24ads_A100_v4"]),
  M("RTX PRO 6000", "Single big GPU", ["g7e.2xlarge"], []),
  M("L40S", "Mid · 48 GB", ["g6e.xlarge"], []),
  M("A10G", "Small · 16–24 GB", ["g5.xlarge"], []),
  M("A10", "Small · 16–24 GB", [], ["Standard_NV36ads_A10_v5"]),
  M("L4", "Small · 16–24 GB", ["g6.xlarge"], []),
  M("T4", "Small · 16–24 GB", ["g4dn.xlarge"], ["Standard_NC4as_T4_v3"]),
];

/** Tier subtitles (section 11.1). */
export const TIER_SUBS: Readonly<Record<string, string>> = {
  "Frontier node": "8 HBM GPUs, NVLink — 400B+ models, multi-node training",
  "Single big GPU": "80–96 GB — 70B quantised, 30B at FP16, fine-tuning",
  "Mid · 48 GB": "7–30B models",
  "Small · 16–24 GB": "≤13B models",
};

export type Coast = "East" | "Central" | "West";

const COASTS: Readonly<Record<string, Coast>> = {
  "us-east-1": "East",
  "us-east-2": "East",
  eastus: "East",
  eastus2: "East",
  centralus: "Central",
  northcentralus: "Central",
  southcentralus: "Central",
  "us-west-2": "West",
  westus: "West",
  westus2: "West",
  westus3: "West",
};

/** The coast of a region (section 11.1); null for a region the spec does not list. */
export function coastOf(region: string): Coast | null {
  return COASTS[region] ?? null;
}

/** `minAsks` (decision R8). */
export const MIN_ASKS = 8;

/** The Capacity Block horizon in days: `meta.cbHorizonDays[0]`, or 14 (section 11.1). */
export function cbHorizon(cbHorizonDays: readonly number[]): number {
  return cbHorizonDays[0] ?? 14;
}

/** The AWS 8-GPU types; every other type has 1 GPU (section 11.1). */
const EIGHT_GPU = new Set(["p6-b200.48xlarge", "p5en.48xlarge", "p5.48xlarge", "p4d.24xlarge"]);

/** GPUs per machine, read from the first type of the machine. */
export function gpusPerMachine(m: Machine): number {
  const first = m.aws[0] ?? m.azure[0];
  return first !== undefined && EIGHT_GPU.has(first) ? 8 : 1;
}

/** Cloud print names (section 11.1). */
export const CLOUD_NAMES: Readonly<Record<string, string>> = { aws: "AWS", azure: "Azure" };

/** The machine of a name, or undefined. */
export function machineByName(name: string): Machine | undefined {
  return MACHINES.find((m) => m.name === name);
}

/** The machines of a tier, in catalogue order; "All" gives every machine. */
export function machinesOfTier(tier: string): readonly string[] {
  return MACHINES.filter((m) => tier === "All" || m.tier === tier).map((m) => m.name);
}
