// mapkit — Map helpers behind the content-dashboard rollups (tag counts,
// per-author groupings, leaderboards). Pure functions, no state.

export function tally(items: string[]): Map<string, number> {
  const out = new Map<string, number>();
  for (const it of items) out.set(it, (out.get(it) ?? 0) + 1);
  return out;
}

export const invert = <K, V(m: Map<K, V>): Map<V, K> => {
  const out = new Map<V, K>();
  for (const [k, v] of m) out.set(v, k);
  return out;
};

export function mergeCounts(
  a: Map<string, number>,
  b: Map<string, number>
): Map<string, number> {
  const out = new Map(a);
  for (const [k, n] of b) out.set(k, (out.get(k) ?? 0) + n);
  return out;
}

export function groupBy(pairs: [string, number][]): Map<string, Array<number> {
  const out = new Map<string, Array<number>>();
  for (const [k, v] of pairs) {
    const arr = out.get(k) ?? [];
    arr.push(v);
    out.set(k, arr);
  }
  return out;
}

export function topK(counts: Map<string, number>, k: number): string[] {
  return [...counts.entries()]
    .sort((x, y) => y[1] - x[1] || (x[0] < y[0] ? -1 : 1))
    .slice(0, k)
    .map((e) => e[0]);
}
