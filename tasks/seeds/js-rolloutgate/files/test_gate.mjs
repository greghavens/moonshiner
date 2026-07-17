// Rollout-gate behavior suite — protected file.
import test from "node:test";
import assert from "node:assert/strict";

import { parseWindow, minSupported, qualifies, ringFor } from "./gate.mjs";

const POLICY = { supported: ">=3.2.0 <4", maxStaleness: "2w" };
const NOW = "2026-07-10T00:00:00Z";

test("parseWindow understands the ops shorthand, weeks included", () => {
  assert.equal(parseWindow("45m"), 2700000);
  assert.equal(parseWindow("36h"), 129600000);
  assert.equal(parseWindow("2w"), 1209600000);
});

test("parseWindow rejects prose", () => {
  assert.throws(() => parseWindow("fortnight"),
    (e) => e instanceof Error && e.message === "unrecognized window: fortnight");
  assert.throws(() => parseWindow(""), /unrecognized window/);
});

test("minSupported reports the floor of the supported range", () => {
  assert.equal(minSupported(POLICY), "3.2.0");
  assert.equal(minSupported({ supported: "^2.4.1" }), "2.4.1");
});

test("a fresh agent inside the range qualifies", () => {
  const agent = { id: "a-1", version: "3.5.2", lastSeen: "2026-07-08T12:00:00Z" };
  assert.deepEqual(qualifies(agent, POLICY, NOW), { ok: true, reasons: [] });
});

test("an out-of-range version is called out", () => {
  const agent = { id: "a-2", version: "2.9.0", lastSeen: "2026-07-08T12:00:00Z" };
  assert.deepEqual(qualifies(agent, POLICY, NOW), {
    ok: false,
    reasons: ["version 2.9.0 outside >=3.2.0 <4"],
  });
});

test("a heartbeat older than the staleness window is called out", () => {
  const agent = { id: "a-3", version: "3.5.2", lastSeen: "2026-06-20T00:00:00Z" };
  assert.deepEqual(qualifies(agent, POLICY, NOW), {
    ok: false,
    reasons: ["last seen 2026-06-20T00:00:00Z is older than 2w"],
  });
});

test("multiple problems stack, version first", () => {
  const agent = { id: "a-4", version: "4.2.0", lastSeen: "2026-06-01T00:00:00Z" };
  assert.deepEqual(qualifies(agent, POLICY, NOW), {
    ok: false,
    reasons: [
      "version 4.2.0 outside >=3.2.0 <4",
      "last seen 2026-06-01T00:00:00Z is older than 2w",
    ],
  });
});

test("garbage versions are unparseable, not out-of-range", () => {
  const agent = { id: "a-5", version: "three-two", lastSeen: "2026-07-08T12:00:00Z" };
  assert.deepEqual(qualifies(agent, POLICY, NOW), {
    ok: false,
    reasons: ["unparseable version three-two"],
  });
});

test("ringFor picks the first matching ring, null when none fit", () => {
  const rings = [
    { name: "canary", range: "^4.0.0" },
    { name: "stable", range: "^3.2.0" },
  ];
  assert.equal(ringFor("4.1.0", rings), "canary");
  assert.equal(ringFor("3.5.0", rings), "stable");
  assert.equal(ringFor("2.9.9", rings), null);
});

test("boundary freshness: exactly at the window is still fresh", () => {
  const agent = { id: "a-6", version: "3.2.0", lastSeen: "2026-06-26T00:00:00Z" };
  // 2026-06-26 -> 2026-07-10 is exactly 14 days.
  assert.deepEqual(qualifies(agent, POLICY, NOW), { ok: true, reasons: [] });
});
