// Build `meta.now` from the board fixture by the rule of spec 0006 section 7.3. The tests call this. The input is the joined
// data shape { meta, sweeps, cb } of the fixture. Nothing here reads a clock or a random value.
const HOUR = 3600e3;
const cmp = (a, b) => (a < b ? -1 : a > b ? 1 : 0);

// The order of section 7.4: h, t, cloud, region, sku, probe.
const bySweep = (a, b) =>
  a.h - b.h ||
  a.t - b.t ||
  cmp(a.cloud, b.cloud) ||
  cmp(a.region, b.region) ||
  cmp(a.sku, b.sku) ||
  cmp(a.probe, b.probe);

export function buildMetaNow(D) {
  const cad = D.meta.cadenceH?.launch || 2;
  const t0 = Date.parse(D.meta.t0);
  const answered = D.sweeps.filter((s) => s.state !== -2).sort(bySweep);

  // sweeps: for each sku, the sweeps of its largest round.
  const lastRound = new Map();
  for (const s of answered)
    lastRound.set(s.sku, Math.max(lastRound.get(s.sku) ?? -Infinity, Math.floor(s.h / cad)));
  const sweeps = answered.filter((s) => Math.floor(s.h / cad) === lastRound.get(s.sku));

  // places: the counts of section 11.3 item 2 over the whole data.
  const map = new Map();
  for (const s of answered) {
    const key = `${s.sku}|${s.cloud}|${s.region}`;
    let p = map.get(key);
    if (!p) {
      p = {
        sku: s.sku,
        cloud: s.cloud,
        region: s.region,
        probe: s.probe,
        n: 0,
        na: 0,
        yes: 0,
        nu: 0,
        no: 0,
        lastYesH: null,
      };
      map.set(key, p);
    }
    if (s.state >= -1) p.n += 1;
    if (s.state === -1) p.na += 1;
    if (s.state === -3) p.nu += 1;
    if (s.state === -4) p.no += 1;
    if (s.got > 0) {
      p.yes += 1;
      p.lastYesH = Math.max(p.lastYesH ?? -Infinity, s.h);
    }
  }
  const places = [...map.values()].sort(
    (a, b) => cmp(a.sku, b.sku) || cmp(a.cloud, b.cloud) || cmp(a.region, b.region),
  );

  // cb: the latest 1-day, 1-machine check of each Capacity Block place and its smallest-lead found check.
  const cb = D.cb
    .map((p) => {
      const one = p.checks.filter(
        (c) => (c[6] === null || c[6] === 1) && (c[3] === null || c[3] === 24),
      );
      const h = one.length ? Math.max(...one.map((c) => c[0])) : null;
      const found = one
        .filter((c) => c[0] === h && c[1] === 1 && c[7] !== null && c[7] !== undefined)
        .sort((a, b) => a[7] - b[7]);
      const best = found[0] ?? null;
      const hl = p.longest.length ? Math.max(...p.longest.map((l) => l[0])) : null;
      const longs = p.longest.filter((l) => l[0] === hl && l[1] === 1 && l[3] !== null);
      return {
        sku: p.sku,
        region: p.region,
        h,
        leadH: best ? best[7] : null,
        durH: best ? best[3] : null,
        // The fixture holds no `details.start`: the start is the check hour plus the lead.
        start: best ? new Date(t0 + (h + best[7]) * HOUR).toISOString() : null,
        longestH: longs.length ? Math.max(...longs.map((l) => l[3])) : null,
      };
    })
    .sort((a, b) => cmp(a.sku, b.sku) || cmp(a.region, b.region));

  return { sweeps, places, cb };
}
