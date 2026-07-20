export type CacheRuntime = "browser" | "server";

export interface CacheIdentity {
  runtime: CacheRuntime;
  tenantId: string;
  resourceId: string;
}

export interface CacheEntry<T = unknown> {
  value: T;
  expiresAt: number;
}

export interface CacheStorage {
  get(key: string): CacheEntry | undefined;
  set(key: string, entry: CacheEntry): void;
  delete(key: string): void;
}

export type MigrationStatus =
  | "missing"
  | "expired"
  | "migrated"
  | "already-current";

export interface MigrationResult {
  status: MigrationStatus;
  legacyKey: string;
  canonicalKey: string;
}

/** The resource-only key consumed by the reader in the previous release. */
export function legacyCacheKey(resourceId: string): string {
  return `cache:${resourceId}`;
}

/** The v2 key is safe even when compound-key fields contain separators. */
export function canonicalCacheKey(identity: CacheIdentity): string {
  const tenant = encodeURIComponent(identity.tenantId);
  const resource = encodeURIComponent(identity.resourceId);
  return `cache:v2:${identity.runtime}:${tenant}:${resource}`;
}

/**
 * Lazily migrate one entry when it is encountered by the v2 reader.
 *
 * Cache adapters may throw from set(), so the source is not touched until the
 * canonical write has succeeded.
 */
export function migrateCacheEntry(
  cache: CacheStorage,
  identity: CacheIdentity,
  nowMs: number,
): MigrationResult {
  const legacyKey = legacyCacheKey(identity.resourceId);
  const canonicalKey = canonicalCacheKey(identity);
  const legacy = cache.get(legacyKey);

  if (legacy === undefined) {
    return { status: "missing", legacyKey, canonicalKey };
  }

  if (legacy.expiresAt <= nowMs) {
    cache.delete(legacyKey);
    return { status: "expired", legacyKey, canonicalKey };
  }

  const canonical = cache.get(canonicalKey);
  let status: MigrationStatus;

  if (canonical !== undefined && canonical.expiresAt > nowMs) {
    // A v2 writer has already produced a current value; do not replay v1 data.
    status = "already-current";
  } else {
    // Preserve the exact deadline rather than granting a new TTL on migration.
    cache.set(canonicalKey, legacy);
    status = "migrated";
  }

  // Once v2 can read the entry, the source key is no longer needed.
  cache.delete(legacyKey);
  return { status, legacyKey, canonicalKey };
}
