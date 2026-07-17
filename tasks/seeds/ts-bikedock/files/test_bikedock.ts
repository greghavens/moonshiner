import { test } from "node:test";
import assert from "node:assert/strict";

import { DockState, describeState } from "./stations.ts";
import type { Station, Report } from "./stations.ts";
import {
  deficitOf,
  overCapacity,
  pullCandidates,
  totalShortfall,
} from "./rebalance.ts";
import { rankStations, summaryLines, vanLoad } from "./report.ts";

function st(
  id: string,
  name: string,
  capacity: number,
  state: string,
  lastReport?: Report,
): Station {
  return { id, name, capacity, state, lastReport };
}

function fleet(): Station[] {
  return [
    st("d1", "Canal & 5th", 20, "active", { bikes: 3, broken: 0 }),
    st("d2", "Museum Loop", 12, "active", { bikes: 6, broken: 1 }),
    st("d3", "Depot Yard", 30, "reserve", { bikes: 22, broken: 2 }),
    st("d4", "Hilltop", 16, "offline", { bikes: 2, broken: 0 }),
    st("d5", "Ferry Plaza", 10, "active", { bikes: 14, broken: 0 }),
    st("d6", "Stadium West", 24, "active"),
    st("d7", "Old Mill", 9, "active", { bikes: 4, broken: 0 }),
  ];
}

test("dock state vocabulary and labels", () => {
  assert.equal(DockState.Active, "active");
  assert.equal(DockState.Reserve, "reserve");
  assert.equal(DockState.Offline, "offline");
  assert.equal(describeState(DockState.Active), "in service");
  assert.equal(describeState(DockState.Reserve), "reserve pool");
  assert.equal(describeState(DockState.Offline), "out of service");
});

test("deficit targets half the racks, rounded up, active docks only", () => {
  const [d1, d2, d3, d4, d5, d6, d7] = fleet();
  assert.equal(deficitOf(d1), 7); // target 10, 3 on the ground
  assert.equal(deficitOf(d2), 0); // exactly at target
  assert.equal(deficitOf(d3), 0); // reserve pool is not planned
  assert.equal(deficitOf(d4), 0); // offline is not planned
  assert.equal(deficitOf(d5), -9); // surplus: pick up
  assert.equal(deficitOf(d6), 0); // no field report yet
  assert.equal(deficitOf(d7), 1); // target ceil(9/2)=5, 4 on the ground
});

test("over-capacity means strictly more bikes than racks", () => {
  const [d1, , , , d5, d6] = fleet();
  assert.equal(overCapacity(d5), true); // 14 bikes on 10 racks
  assert.equal(overCapacity(d1), false);
  assert.equal(overCapacity(d6), false); // no report -> not over capacity
  const full = st("dx", "Edge", 8, "active", { bikes: 8, broken: 0 });
  assert.equal(overCapacity(full), false); // exactly full is not over
});

test("pull candidates skip offline docks and the reserve pool", () => {
  const ids = pullCandidates(fleet()).map((s) => s.id);
  assert.deepEqual(ids, ["d1", "d2", "d5", "d6", "d7"]);
});

test("total shortfall sums only the positive deficits", () => {
  const rows = [
    { label: "a", deficit: 7 },
    { label: "b", deficit: -9 },
    { label: "c", deficit: 1 },
    { label: "d", deficit: 0 },
  ];
  assert.equal(totalShortfall(rows), 8);
  assert.equal(totalShortfall([]), 0);
});

test("ranking is worst-deficit-first with stable ties", () => {
  const snapshot = fleet();
  const ranked = rankStations(snapshot).map((s) => s.id);
  assert.deepEqual(ranked, ["d1", "d7", "d2", "d3", "d4", "d6", "d5"]);
});

test("ranking must not reorder the dispatcher's snapshot", () => {
  const snapshot = fleet();
  rankStations(snapshot);
  assert.deepEqual(
    snapshot.map((s) => s.id),
    ["d1", "d2", "d3", "d4", "d5", "d6", "d7"],
  );
});

test("summary lines cover only docks needing a drop-off", () => {
  assert.deepEqual(summaryLines(fleet()), [
    "Canal & 5th [d1] needs 7 bikes",
    "Old Mill [d7] needs 1 bike",
  ]);
});

test("van load is the sum of drop-offs for the route", () => {
  assert.equal(vanLoad(fleet()), 8);
  assert.equal(vanLoad([]), 0);
});
