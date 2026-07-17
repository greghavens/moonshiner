// Contract tests for the service-record validation layer — protected file.
import test from "node:test";
import assert from "node:assert/strict";

import { recordSchema, parseRecord, parseBatch } from "./schemas.mjs";

function service(overrides = {}) {
  return {
    type: "service",
    vehicle: { plate: "AB-123", fleet: "north" },
    odometerKm: 128450,
    date: "2026-06-30",
    intervalKm: 15000,
    checklist: ["oil", "filters"],
    ...overrides,
  };
}

function inspection(overrides = {}) {
  return {
    type: "inspection",
    vehicle: { plate: "AB-123", fleet: "north" },
    odometerKm: 90210,
    date: "2026-06-30",
    passed: true,
    ...overrides,
  };
}

function repair(overrides = {}) {
  return {
    type: "repair",
    vehicle: { plate: "AB-123", fleet: "north" },
    odometerKm: 55000,
    date: "2026-06-30",
    failureCode: "BRK",
    workshop: "north depot",
    costCents: 41900,
    parts: [{ sku: "P-00417", qty: 2 }],
    ...overrides,
  };
}

function shape(issues) {
  return issues.map((i) => [i.path, i.code]);
}

test("a clean service record parses, normalizing plate and coercing odometer", () => {
  const r = parseRecord(service({
    vehicle: { plate: "  ab-123 ", fleet: "north" },
    odometerKm: "128450",
  }));
  assert.equal(r.ok, true);
  assert.deepEqual(r.record, service());
});

test("parsed records are plain JSON data (round-trip clean)", () => {
  const r = parseRecord(repair());
  assert.equal(r.ok, true);
  assert.deepEqual(JSON.parse(JSON.stringify(r.record)), repair());
});

test("recordSchema itself is a zod schema usable with safeParse", () => {
  const out = recordSchema.safeParse(service());
  assert.equal(out.success, true);
});

test("parseRecord never throws: non-objects come back as a single root issue", () => {
  for (const junk of [null, 42, "hello", []]) {
    const r = parseRecord(junk);
    assert.equal(r.ok, false);
    assert.deepEqual(shape(r.issues), [["", "invalid_type"]]);
  }
});

test("a many-problems record reports everything at once, sorted by path", () => {
  const r = parseRecord(service({
    vehicle: { plate: "nope", fleet: "east" },
    odometerKm: "12k",
    date: "2026-02-30",
    intervalKm: 5001,
    checklist: [],
  }));
  assert.equal(r.ok, false);
  assert.deepEqual(shape(r.issues), [
    ["checklist", "too_small"],
    ["date", "custom"],
    ["intervalKm", "custom"],
    ["odometerKm", "invalid_type"],
    ["vehicle.fleet", "invalid_value"],
    ["vehicle.plate", "custom"],
  ]);
  const byPath = Object.fromEntries(r.issues.map((i) => [i.path, i.message]));
  assert.equal(byPath["date"], "date must be a real YYYY-MM-DD day");
  assert.equal(byPath["intervalKm"], "intervalKm must be a multiple of 5000");
  assert.equal(byPath["vehicle.plate"], "plate must look like ABC-123");
});

test("unknown record type is a single issue at path type", () => {
  const r = parseRecord({ ...service(), type: "detail" });
  assert.equal(r.ok, false);
  assert.equal(r.issues.length, 1);
  assert.equal(r.issues[0].path, "type");
  assert.equal(r.issues[0].code, "invalid_union");
});

test("typo'd field names bounce: unknown keys are errors at the owning object", () => {
  const top = parseRecord(inspection({ odometer: 5 }));
  assert.equal(top.ok, false);
  assert.deepEqual(shape(top.issues), [["", "unrecognized_keys"]]);
  const nested = parseRecord(inspection({
    vehicle: { plate: "AB-123", fleet: "north", color: "red" },
  }));
  assert.equal(nested.ok, false);
  assert.deepEqual(shape(nested.issues), [["vehicle", "unrecognized_keys"]]);
});

test("repair problems pin nested array paths like parts.0.qty", () => {
  const r = parseRecord(repair({
    failureCode: "FOO",
    workshop: "   ",
    costCents: -1,
    parts: [{ sku: "P-123", qty: 0 }],
  }));
  assert.equal(r.ok, false);
  assert.deepEqual(shape(r.issues), [
    ["costCents", "too_small"],
    ["failureCode", "invalid_value"],
    ["parts.0.qty", "too_small"],
    ["parts.0.sku", "custom"],
    ["workshop", "custom"],
  ]);
  const byPath = Object.fromEntries(r.issues.map((i) => [i.path, i.message]));
  assert.equal(byPath["workshop"], "workshop is required");
  assert.equal(byPath["parts.0.sku"], "sku must look like P-12345");
});

test("a repair with no parts is fine", () => {
  const r = parseRecord(repair({ parts: [] }));
  assert.equal(r.ok, true);
  assert.deepEqual(r.record.parts, []);
});

test("missing required fields report per-field, not a blanket failure", () => {
  const bad = inspection();
  delete bad.odometerKm;
  delete bad.passed;
  const r = parseRecord(bad);
  assert.equal(r.ok, false);
  assert.deepEqual(shape(r.issues), [
    ["odometerKm", "invalid_type"],
    ["passed", "invalid_type"],
  ]);
});

test("odometer coerces from numeric strings but stays an integer >= 0", () => {
  assert.equal(parseRecord(inspection({ odometerKm: "-5" })).ok, false);
  assert.deepEqual(shape(parseRecord(inspection({ odometerKm: "-5" })).issues),
    [["odometerKm", "too_small"]]);
  const ok = parseRecord(inspection({ odometerKm: "90210" }));
  assert.equal(ok.ok, true);
  assert.equal(ok.record.odometerKm, 90210);
});

test("calendar dates are validated for real: leap days behave", () => {
  assert.equal(parseRecord(inspection({ date: "2028-02-29" })).ok, true);
  const r = parseRecord(inspection({ date: "2027-02-29" }));
  assert.equal(r.ok, false);
  assert.deepEqual(shape(r.issues), [["date", "custom"]]);
});

test("interval must land on the 5000 km schedule", () => {
  assert.equal(parseRecord(service({ intervalKm: 20000 })).ok, true);
  const r = parseRecord(service({ intervalKm: 12345 }));
  assert.equal(r.ok, false);
  assert.deepEqual(shape(r.issues), [["intervalKm", "custom"]]);
});

test("failed inspections need notes; passed ones default notes to empty", () => {
  const failed = parseRecord(inspection({ passed: false }));
  assert.equal(failed.ok, false);
  assert.deepEqual(failed.issues, [{
    path: "notes",
    code: "custom",
    message: "failed inspection needs notes",
  }]);
  const documented = parseRecord(inspection({ passed: false, notes: "brake pads at 20%" }));
  assert.equal(documented.ok, true);
  const passed = parseRecord(inspection());
  assert.equal(passed.ok, true);
  assert.equal(passed.record.notes, "");
});

test("parseBatch splits accepted and rejected, keeping original indexes", () => {
  const out = parseBatch([
    service(),
    inspection({ passed: false }),
    repair(),
  ]);
  assert.equal(out.accepted.length, 2);
  assert.deepEqual(out.accepted[0], service());
  assert.equal(out.accepted[1].type, "repair");
  assert.equal(out.rejected.length, 1);
  assert.equal(out.rejected[0].index, 1);
  assert.deepEqual(shape(out.rejected[0].issues), [["notes", "custom"]]);
});

test("parseBatch refuses non-arrays with a TypeError", () => {
  for (const junk of ["batch", { records: [] }, null]) {
    assert.throws(() => parseBatch(junk), (err) =>
      err instanceof TypeError && err.message === "batch must be an array");
  }
});
