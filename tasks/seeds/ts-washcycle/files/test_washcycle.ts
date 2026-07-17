import { test } from "node:test";
import assert from "node:assert/strict";

import type { MachineDriver } from "./types.ts";
import { PRESETS, findPreset, estimateEnd } from "./machines.ts";
import { startQueue } from "./schedule.ts";
import { priceLoad } from "./pricing.ts";
import { parseCycleRow, receiptLine } from "./receipts.ts";

test("preset table survived the C-40 migration", () => {
  assert.deepEqual(
    PRESETS.map((p) => [p.code, p.minutes, p.temp]),
    [
      ["Q20", 20, "cold"],
      ["N34", 34, "warm"],
      ["H45", 45, "hot"],
    ],
  );
  assert.equal(findPreset("H45").label, "Heavy duty");
  assert.throws(() => findPreset("Z99"), /unknown cycle code: Z99/);
});

test("finish times use the cycle runtime and wrap past midnight", () => {
  const normal = findPreset("N34");
  assert.equal(estimateEnd(normal, 600), 634);
  assert.equal(estimateEnd(normal, 1420), 14); // 23:40 start wraps to 00:14
  assert.equal(estimateEnd(findPreset("Q20"), 0), 20);
});

interface StartCall {
  code: string;
  minutes: number;
}

function fakeDriver(log: string[], starts: StartCall[]): MachineDriver {
  return {
    start(code: string, minutes: number) {
      starts.push({ code, minutes });
      log.push(`start:${code}`);
    },
    lockDoor() {
      log.push("lock");
    },
  };
}

test("queue start passes each controller its runtime up front", () => {
  const log: string[] = [];
  const starts: StartCall[] = [];
  const drivers = new Map<string, MachineDriver>([
    ["W1", fakeDriver(log, starts)],
    ["W2", fakeDriver(log, starts)],
  ]);
  const started = startQueue(
    [
      { machine: "W1", cycleCode: "N34" },
      { machine: "W9", cycleCode: "H45" }, // no driver: stays queued
      { machine: "W2", cycleCode: "Q20" },
    ],
    drivers,
  );
  assert.deepEqual(started, ["N34", "Q20"]);
  assert.deepEqual(starts, [
    { code: "N34", minutes: 34 },
    { code: "Q20", minutes: 20 },
  ]);
  assert.deepEqual(log, ["lock", "start:N34", "lock", "start:Q20"]);
});

test("vend pricing applies every matching rule, floored at zero", () => {
  assert.equal(priceLoad({ kg: 6, member: false, cycleCode: "N34" }), 450);
  assert.equal(priceLoad({ kg: 6, member: true, cycleCode: "Q20" }), 300);
  assert.equal(priceLoad({ kg: 11, member: false, cycleCode: "H45" }), 700);
  assert.equal(priceLoad({ kg: 12, member: true, cycleCode: "H45" }), 650);
});

test("cycle rows parse into the new spec shape", () => {
  assert.deepEqual(parseCycleRow("D60|Delicates|60|cold"), {
    code: "D60",
    label: "Delicates",
    minutes: 60,
    temp: "cold",
  });
  assert.equal(
    receiptLine(parseCycleRow("N34|Normal|34|warm")),
    "N34 Normal — 34 min, warm",
  );
});

test("rows with an unknown temperature are rejected, not smuggled through", () => {
  assert.throws(() => parseCycleRow("B90|Boil|90|boiling"));
  assert.throws(() => parseCycleRow("N34|Normal|34|Warm")); // case matters
  assert.throws(() => parseCycleRow("N34|Normal|34|"));
});

test("malformed rows are rejected", () => {
  assert.throws(() => parseCycleRow("N34|Normal|34"), /bad cycle row/);
  assert.throws(() => parseCycleRow("N34|Normal|soon|hot"), /bad cycle row/);
  assert.throws(() => parseCycleRow("N34|Normal|0|hot"), /bad cycle row/);
  assert.throws(() => parseCycleRow("|Normal|34|hot"), /bad cycle row/);
});
