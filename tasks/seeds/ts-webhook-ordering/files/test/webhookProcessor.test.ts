import assert from "node:assert/strict";
import test from "node:test";

import { VersionedWebhookProcessor } from "../src/webhookProcessor.ts";

test("applies in-order events and rejects replayed or stale versions", () => {
  const processor = new VersionedWebhookProcessor<string>();

  assert.equal(
    processor.process({ entityId: "order-1", version: 1, value: "created" })
      .disposition,
    "applied",
  );
  processor.process({ entityId: "order-1", version: 2, value: "paid" });

  const duplicate = processor.process({
    entityId: "order-1",
    version: 2,
    value: "duplicate must not win",
  });
  const stale = processor.process({
    entityId: "order-1",
    version: 1,
    value: "stale must not win",
  });

  assert.equal(duplicate.disposition, "duplicate");
  assert.equal(stale.disposition, "stale");
  assert.deepEqual(processor.getState("order-1"), {
    version: 2,
    value: "paid",
    pendingVersions: [],
  });
});

test("replays every contiguous pending event when a gap closes", () => {
  const processor = new VersionedWebhookProcessor<string>();

  processor.process({ entityId: "order-2", version: 4, value: "shipped" });
  processor.process({ entityId: "order-2", version: 2, value: "paid" });
  processor.process({ entityId: "order-2", version: 3, value: "packed" });

  const result = processor.process({
    entityId: "order-2",
    version: 1,
    value: "created",
  });

  assert.deepEqual(result.appliedVersions, [1, 2, 3, 4]);
  assert.deepEqual(processor.getState("order-2"), {
    version: 4,
    value: "shipped",
    pendingVersions: [],
  });
});

test("keeps entity streams independent and ignores duplicate buffered deliveries", () => {
  const processor = new VersionedWebhookProcessor<string>();

  processor.process({ entityId: "alpha", version: 3, value: "first-v3" });
  assert.equal(
    processor.process({ entityId: "alpha", version: 3, value: "second-v3" })
      .disposition,
    "duplicate",
  );
  processor.process({ entityId: "beta", version: 1, value: "beta-v1" });
  processor.process({ entityId: "alpha", version: 1, value: "alpha-v1" });
  processor.process({ entityId: "alpha", version: 2, value: "alpha-v2" });

  assert.deepEqual(processor.getState("alpha"), {
    version: 3,
    value: "first-v3",
    pendingVersions: [],
  });
  assert.deepEqual(processor.getState("beta"), {
    version: 1,
    value: "beta-v1",
    pendingVersions: [],
  });
});

test("bounds pending retention and keeps the nearest future versions", () => {
  const processor = new VersionedWebhookProcessor<string>(2);

  assert.equal(
    processor.process({ entityId: "order-3", version: 10, value: "v10" })
      .disposition,
    "buffered",
  );
  processor.process({ entityId: "order-3", version: 4, value: "v4" });
  const result = processor.process({
    entityId: "order-3",
    version: 3,
    value: "v3",
  });

  assert.equal(processor.pendingCount("order-3"), 2);
  assert.deepEqual(result.pendingVersions, [3, 4]);
  assert.equal(
    processor.process({ entityId: "order-3", version: 20, value: "v20" })
      .disposition,
    "dropped",
  );
  assert.equal(processor.pendingCount("order-3"), 2);
});

test("rejects malformed versions without creating visible state", () => {
  const processor = new VersionedWebhookProcessor<string>();

  assert.throws(
    () => processor.process({ entityId: "order-4", version: 1.5, value: "bad" }),
    /positive safe integer/,
  );
  assert.throws(
    () => processor.process({ entityId: "", version: 1, value: "bad" }),
    /entityId must not be empty/,
  );
  assert.equal(processor.getState("order-4"), undefined);
});
