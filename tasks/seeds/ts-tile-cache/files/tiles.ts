export type TileKey = { z: number; x: number; y: number };
export type FetchTile = (key: TileKey) => Promise<string>;

/**
 * Read-through cache in front of the tile server. Every viewport pan/zoom
 * asks for tiles by key; anything already fetched must come from memory.
 */
export class TileCache {
  private tiles: Map<TileKey, string>;
  private fetchTile: FetchTile;

  constructor(fetchTile: FetchTile) {
    this.tiles = new Map();
    this.fetchTile = fetchTile;
  }

  async get(key: TileKey): Promise<string> {
    const cached = this.tiles.get(key);
    if (cached !== undefined) {
      return cached;
    }
    const data = await this.fetchTile(key);
    this.tiles.set(key, data);
    return data;
  }

  size(): number {
    return this.tiles.size;
  }
}

/** Drop duplicate keys from a pan/zoom burst, keeping first-occurrence order. */
export function dedupeKeys(keys: TileKey[]): TileKey[] {
  return [...new Set(keys)];
}

/** Warm the cache for a viewport: fetch each distinct tile once. */
export async function prefetch(cache: TileCache, keys: TileKey[]): Promise<number> {
  const distinct = dedupeKeys(keys);
  for (const key of distinct) {
    await cache.get(key);
  }
  return distinct.length;
}
