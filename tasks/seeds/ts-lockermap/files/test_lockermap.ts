import test from "node:test";
import assert from "node:assert/strict";
import { doorLabel, nextState, storageFee, slaBucket } from "./lockermap.ts";

test("every known door state has a kiosk label", () => {
  assert.equal(doorLabel("empty"), "Available");
  assert.equal(doorLabel("reserved"), "Reserved");
  assert.equal(doorLabel("loaded"), "Parcel inside");
  assert.equal(doorLabel("overdue"), "Awaiting return pickup");
  assert.equal(doorLabel("jammed"), "Out of service");
});

test("an unknown state must blow up, not render a blank tile", () => {
  assert.throws(() => doorLabel("hallway"));
  assert.throws(() => nextState("hallway", "pickup"));
  assert.throws(() => storageFee("hallway", 1));
});

test("the empty/reserved handshake", () => {
  assert.equal(nextState("empty", "reserve"), "reserved");
  assert.equal(nextState("empty", "pickup"), "empty");
  assert.equal(nextState("reserved", "load"), "loaded");
  assert.equal(nextState("reserved", "cancel"), "empty");
  assert.equal(nextState("reserved", "expire"), "reserved");
});

test("loaded doors: pickup, expiry, jams", () => {
  assert.equal(nextState("loaded", "pickup"), "empty");
  assert.equal(nextState("loaded", "expire"), "overdue");
  assert.equal(nextState("loaded", "report-jam"), "jammed");
  assert.equal(nextState("loaded", "service"), "loaded");
  assert.equal(nextState("loaded", "reserve"), "loaded");
});

test("overdue doors behave like loaded ones, plus the service clear-out", () => {
  assert.equal(nextState("overdue", "service"), "empty");
  assert.equal(nextState("overdue", "pickup"), "empty");
  assert.equal(nextState("overdue", "report-jam"), "jammed");
  assert.equal(nextState("overdue", "expire"), "overdue");
  assert.equal(nextState("overdue", "load"), "overdue");
  assert.equal(nextState("overdue", "cancel"), "overdue");
});

test("jammed doors only leave via service", () => {
  assert.equal(nextState("jammed", "service"), "empty");
  assert.equal(nextState("jammed", "pickup"), "jammed");
  assert.equal(nextState("jammed", "reserve"), "jammed");
});

test("storage fees: flat while loaded, surcharge past the grace window", () => {
  assert.equal(storageFee("loaded", 1), 25);
  assert.equal(storageFee("loaded", 30), 25);
  assert.equal(storageFee("overdue", 2), 25);
  assert.equal(storageFee("overdue", 3), 25);
  assert.equal(storageFee("overdue", 4), 75);
  assert.equal(storageFee("overdue", 5), 125);
  assert.equal(storageFee("empty", 9), 0);
  assert.equal(storageFee("reserved", 9), 0);
  assert.equal(storageFee("jammed", 9), 0);
});

test("sla buckets cover the whole clock", () => {
  assert.equal(slaBucket(0), "fresh");
  assert.equal(slaBucket(59), "fresh");
  assert.equal(slaBucket(60), "same-day");
  assert.equal(slaBucket(1439), "same-day");
  assert.equal(slaBucket(1440), "aging");
  assert.equal(slaBucket(4319), "aging");
  assert.equal(slaBucket(4320), "stale");
  assert.equal(slaBucket(999999), "stale");
  assert.equal(slaBucket(-5), "fresh");
});
