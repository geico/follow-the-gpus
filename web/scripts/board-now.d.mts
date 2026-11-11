export type NowSweep = {
  h: number;
  t: number;
  cloud: string;
  region: string;
  sku: string;
  probe: string;
  state: number;
  got: number;
  asked: number;
  reason: string | null;
};
export type NowPlace = {
  sku: string;
  cloud: string;
  region: string;
  probe: string;
  n: number;
  na: number;
  yes: number;
  nu: number;
  no: number;
  lastYesH: number | null;
};
export type NowCb = {
  sku: string;
  region: string;
  h: number | null;
  leadH: number | null;
  durH: number | null;
  start: string | null;
  longestH: number | null;
};
export type MetaNow = { sweeps: NowSweep[]; places: NowPlace[]; cb: NowCb[] };
export function buildMetaNow(D: {
  meta: { t0: string; cadenceH?: { launch?: number | null } };
  sweeps: unknown[];
  cb: unknown[];
}): MetaNow;
