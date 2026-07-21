import assert from "node:assert/strict";
import test from "node:test";

import {
  VersionedTenantCache,
  type ResourceIdentity,
  type ResourceSnapshot,
} from "../src/versioned-tenant-cache.ts";

interface Profile {
  displayName: string;
}

function key(identity: ResourceIdentity): string {
  return JSON.stringify([identity.tenantId, identity.resourceId]);
}

function makeHarness() {
  const source = new Map<string, ResourceSnapshot<Profile>>();
  const loadCounts = new Map<string, number>();

  const cache = new VersionedTenantCache<Profile>(async (identity) => {
    const identityKey = key(identity);
    loadCounts.set(identityKey, (loadCounts.get(identityKey) ?? 0) + 1);
    const snapshot = source.get(identityKey);
    if (snapshot === undefined) {
      throw new Error(`missing source record: ${identityKey}`);
    }
    return { version: snapshot.version, value: { ...snapshot.value } };
  });

  return {
    cache,
    put(identity: ResourceIdentity, version: number, displayName: string) {
      source.set(key(identity), { version, value: { displayName } });
    },
    loads(identity: ResourceIdentity) {
      return loadCounts.get(key(identity)) ?? 0;
    },
  };
}

test("repeat reads remain cache hits", async () => {
  const harness = makeHarness();
  const identity = { tenantId: "acme", resourceId: "profile-7" };
  harness.put(identity, 12, "Ada");

  assert.deepEqual(await harness.cache.read(identity), {
    version: 12,
    value: { displayName: "Ada" },
  });

  harness.put(identity, 13, "Ada Lovelace");
  assert.deepEqual(await harness.cache.read(identity), {
    version: 12,
    value: { displayName: "Ada" },
  });
  assert.equal(harness.loads(identity), 1);
});

test("a write invalidation is correlated through its request trace", async () => {
  for (const targetTraceFirst of [true, false]) {
    const harness = makeHarness();
    const identity = { tenantId: "acme", resourceId: "profile-7" };
    const unrelated = { tenantId: "globex", resourceId: "profile-8" };
    harness.put(identity, 12, "Ada");
    harness.put(unrelated, 12, "Grace");
    await harness.cache.read(identity);
    await harness.cache.read(unrelated);

    harness.put(identity, 13, "Ada Lovelace");
    harness.put(unrelated, 13, "Grace Hopper");
    const targetTrace = {
      traceId: "trace-write-13",
      operation: "write" as const,
      ...identity,
      version: 13,
    };
    const unrelatedTrace = {
      traceId: "invalidate-event-99",
      operation: "write" as const,
      ...unrelated,
      version: 13,
    };
    const traces = targetTraceFirst
      ? [targetTrace, unrelatedTrace]
      : [unrelatedTrace, targetTrace];
    for (const trace of traces) {
      harness.cache.recordRequestTrace(trace);
    }

    assert.equal(
      harness.cache.handleInvalidation({
        eventId: "invalidate-event-99",
        causedByTraceId: "trace-write-13",
        version: 13,
      }),
      true,
    );
    assert.deepEqual(await harness.cache.read(identity), {
      version: 13,
      value: { displayName: "Ada Lovelace" },
    });
    assert.deepEqual(await harness.cache.read(unrelated), {
      version: 12,
      value: { displayName: "Grace" },
    });
    assert.equal(harness.loads(identity), 2);
    assert.equal(harness.loads(unrelated), 1);
  }
});

test("correlated invalidations do not cross tenant boundaries", async () => {
  const harness = makeHarness();
  const acme = { tenantId: "acme", resourceId: "profile-7" };
  const globex = { tenantId: "globex", resourceId: "profile-7" };
  harness.put(acme, 4, "Acme user");
  harness.put(globex, 8, "Globex user");
  await harness.cache.read(acme);
  await harness.cache.read(globex);

  harness.put(globex, 9, "Updated Globex user");
  harness.cache.recordRequestTrace({
    traceId: "globex-write-9",
    operation: "write",
    ...globex,
    version: 9,
  });
  assert.equal(
    harness.cache.handleInvalidation({
      eventId: "invalidate-globex-9",
      causedByTraceId: "globex-write-9",
      version: 9,
    }),
    true,
  );

  assert.equal((await harness.cache.read(acme)).version, 4);
  assert.equal((await harness.cache.read(globex)).version, 9);
  assert.equal(harness.loads(acme), 1);
  assert.equal(harness.loads(globex), 2);
});

test("delayed and mismatched invalidations cannot evict a newer entry", async () => {
  const harness = makeHarness();
  const identity = { tenantId: "acme", resourceId: "profile-7" };
  harness.put(identity, 15, "Current profile");
  await harness.cache.read(identity);

  harness.cache.recordRequestTrace({
    traceId: "old-write-14",
    operation: "write",
    ...identity,
    version: 14,
  });
  assert.equal(
    harness.cache.handleInvalidation({
      eventId: "delayed-event-14",
      causedByTraceId: "old-write-14",
      version: 14,
    }),
    false,
  );

  harness.cache.recordRequestTrace({
    traceId: "write-16",
    operation: "write",
    ...identity,
    version: 16,
  });
  assert.equal(
    harness.cache.handleInvalidation({
      eventId: "mismatched-event",
      causedByTraceId: "write-16",
      version: 15,
    }),
    false,
  );

  assert.equal((await harness.cache.read(identity)).version, 15);
  assert.equal(harness.loads(identity), 1);
});
