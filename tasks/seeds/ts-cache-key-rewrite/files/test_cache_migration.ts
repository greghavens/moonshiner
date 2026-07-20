import assert from "node:assert/strict";
import test from "node:test";

import {
  canonicalCacheKey,
  legacyCacheKey,
  migrateCacheEntry,
  type CacheEntry,
  type CacheIdentity,
  type CacheStorage,
} from "./cache_migration.ts";

class RecordingCache implements CacheStorage {
  readonly entries = new Map<string, CacheEntry>();
  readonly mutations: string[] = [];
  failWritesTo: string | undefined;

  get(key: string): CacheEntry | undefined {
    return this.entries.get(key);
  }

  set(key: string, entry: CacheEntry): void {
    this.mutations.push(`set:${key}`);
    if (key === this.failWritesTo) {
      throw new Error("simulated cache write failure");
    }
    this.entries.set(key, entry);
  }

  delete(key: string): void {
    this.mutations.push(`delete:${key}`);
    this.entries.delete(key);
  }
}

const browserIdentity: CacheIdentity = {
  runtime: "browser",
  tenantId: "acme/eu",
  resourceId: "profile:7",
};

test("canonical key is compound, escaped, and runtime-specific", () => {
  assert.equal(
    canonicalCacheKey(browserIdentity),
    "cache:v2:browser:acme%2Feu:profile%3A7",
  );
  assert.equal(
    canonicalCacheKey({ ...browserIdentity, runtime: "server" }),
    "cache:v2:server:acme%2Feu:profile%3A7",
  );
  assert.equal(legacyCacheKey(browserIdentity.resourceId), "cache:profile:7");
});

test("an unexpired entry reaches v2 unchanged and remains readable after rollback", () => {
  const cache = new RecordingCache();
  const sourceKey = legacyCacheKey(browserIdentity.resourceId);
  const targetKey = canonicalCacheKey(browserIdentity);
  const entry = {
    value: { displayName: "Ada", flags: ["beta"] },
    expiresAt: 50_000,
  };
  cache.entries.set(sourceKey, entry);

  const result = migrateCacheEntry(cache, browserIdentity, 40_000);

  assert.equal(result.status, "migrated");
  assert.deepEqual(cache.get(targetKey), entry, "v2 reader lost the value or TTL");
  assert.deepEqual(
    cache.get(sourceKey),
    entry,
    "the previous reader must still work during rollback",
  );
  assert.deepEqual(cache.mutations, [`set:${targetKey}`]);
});

test("stale legacy data is removed without being replayed", () => {
  for (const expiresAt of [39_999, 40_000]) {
    const cache = new RecordingCache();
    const sourceKey = legacyCacheKey(browserIdentity.resourceId);
    const targetKey = canonicalCacheKey(browserIdentity);
    cache.entries.set(sourceKey, { value: "stale", expiresAt });

    const result = migrateCacheEntry(cache, browserIdentity, 40_000);

    assert.equal(result.status, "expired");
    assert.equal(cache.get(sourceKey), undefined);
    assert.equal(cache.get(targetKey), undefined);
    assert.deepEqual(cache.mutations, [`delete:${sourceKey}`]);
  }
});

test("an existing current canonical value wins without disabling rollback", () => {
  const cache = new RecordingCache();
  const sourceKey = legacyCacheKey(browserIdentity.resourceId);
  const targetKey = canonicalCacheKey(browserIdentity);
  const legacy = { value: "v1", expiresAt: 45_000 };
  const current = { value: "v2", expiresAt: 60_000 };
  cache.entries.set(sourceKey, legacy);
  cache.entries.set(targetKey, current);

  const result = migrateCacheEntry(cache, browserIdentity, 40_000);

  assert.equal(result.status, "already-current");
  assert.deepEqual(cache.get(targetKey), current, "legacy data replayed over v2");
  assert.deepEqual(cache.get(sourceKey), legacy, "rollback source was discarded");
  assert.deepEqual(cache.mutations, []);
});

test("an expired canonical slot is replaced without extending legacy TTL", () => {
  const cache = new RecordingCache();
  const serverIdentity: CacheIdentity = {
    runtime: "server",
    tenantId: "tenant-a",
    resourceId: "report-9",
  };
  const sourceKey = legacyCacheKey(serverIdentity.resourceId);
  const targetKey = canonicalCacheKey(serverIdentity);
  const legacy = { value: "usable", expiresAt: 55_000 };
  cache.entries.set(sourceKey, legacy);
  cache.entries.set(targetKey, { value: "stale-v2", expiresAt: 40_000 });

  const result = migrateCacheEntry(cache, serverIdentity, 40_000);

  assert.equal(result.status, "migrated");
  assert.deepEqual(cache.get(targetKey), legacy);
  assert.deepEqual(cache.get(sourceKey), legacy);
});

test("a failed canonical write cannot lose the unexpired source", () => {
  const cache = new RecordingCache();
  const sourceKey = legacyCacheKey(browserIdentity.resourceId);
  const targetKey = canonicalCacheKey(browserIdentity);
  const legacy = { value: "keep-me", expiresAt: 50_000 };
  cache.entries.set(sourceKey, legacy);
  cache.failWritesTo = targetKey;

  assert.throws(
    () => migrateCacheEntry(cache, browserIdentity, 40_000),
    /simulated cache write failure/,
  );
  assert.deepEqual(cache.get(sourceKey), legacy);
  assert.equal(cache.get(targetKey), undefined);
  assert.deepEqual(cache.mutations, [`set:${targetKey}`]);
});
