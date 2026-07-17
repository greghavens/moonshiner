import { test } from "node:test";
import assert from "node:assert/strict";
import {
  round,
  tripKm,
  per100,
  costPerKm,
  fleetPer100,
  thriftiest,
} from "./fuellog.ts";

const TRIPS = [
  { date: "2026-07-01", odoStart: 48210, odoEnd: 48530, litres: 27.4, cents: 4384 },
  { date: "2026-07-03", odoStart: 48530, odoEnd: 48950, litres: 31.5, cents: 5040 },
  { date: "2026-07-08", odoStart: 48950, odoEnd: 49010, litres: 6.9, cents: 1104 },
];

test("round is receipt-style half-up at the digit", () => {
  assert.equal(round(8.5625, 2), 8.56);
  assert.equal(round(8.565, 2), 8.57);
  assert.equal(round(73.05, 1), 73.1);
});

test("tripKm comes straight off the odometer", () => {
  assert.equal(tripKm(TRIPS[0]), 320);
  assert.equal(tripKm(TRIPS[2]), 60);
});

test("per100 is litres per hundred km, 2dp", () => {
  assert.equal(per100(TRIPS[0]), 8.56);
  assert.equal(per100(TRIPS[1]), 7.5);
  assert.equal(per100(TRIPS[2]), 11.5);
});

test("per100 refuses a stuck odometer", () => {
  const stuck = { date: "2026-07-09", odoStart: 500, odoEnd: 500, litres: 4, cents: 640 };
  assert.ok(Number.isNaN(per100(stuck)));
});

test("costPerKm is cents per km, 1dp", () => {
  assert.equal(costPerKm(TRIPS[0]), 13.7);
  assert.equal(costPerKm(TRIPS[1]), 12);
});

test("fleetPer100 pools litres and km across the log", () => {
  assert.equal(fleetPer100(TRIPS), 8.23);
  assert.ok(Number.isNaN(fleetPer100([])));
});

test("thriftiest names the lowest-consumption trip", () => {
  assert.equal(thriftiest(TRIPS), "2026-07-03");
  assert.equal(thriftiest([]), "");
});
