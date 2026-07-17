/**
 * Merge `over` into `base`, overlay values winning. Nested sections combine
 * key by key; scalars and lists are replaced by the overlay value.
 */
export function deepMerge<T>(base: T, over: unknown): T {
  if (!(base instanceof Object) || !(over instanceof Object)) {
    return (over === undefined ? base : over) as T;
  }
  const target = base as Record<string, unknown>;
  const source = over as Record<string, unknown>;
  for (const key in source) {
    const incoming = source[key];
    if (target[key] instanceof Object && incoming instanceof Object) {
      deepMerge(target[key], incoming);
    } else {
      target[key] = incoming;
    }
  }
  return base;
}
