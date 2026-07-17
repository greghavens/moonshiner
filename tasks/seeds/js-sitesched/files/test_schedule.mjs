// Portal scheduling suite — protected file.
// Covers the existing helpers (must keep passing) and the two new features.
import test from "node:test";
import assert from "node:assert/strict";

import * as sched from "./schedule.mjs";

// ---------------------------------------------------------------- existing

test("fmtLocal renders the site wall clock", () => {
  assert.equal(sched.fmtLocal("2026-07-20T14:00:00Z", "America/Denver"),
    "2026-07-20 08:00");
  assert.equal(sched.fmtLocal("2026-07-20T14:00:00Z", "Asia/Tokyo"),
    "2026-07-20 23:00");
});

test("isSameLocalDay answers in the site zone, not UTC", () => {
  assert.equal(
    sched.isSameLocalDay("2026-07-19T10:00:00Z", "2026-07-19T16:00:00Z", "Asia/Tokyo"),
    false);
  assert.equal(
    sched.isSameLocalDay("2026-07-19T10:00:00Z", "2026-07-19T16:00:00Z", "Europe/London"),
    true);
});

test("nextVisitDue adds whole-day cadences and rejects junk", () => {
  assert.equal(sched.nextVisitDue("2026-07-01T09:00:00.000Z", 30),
    "2026-07-31T09:00:00.000Z");
  for (const bad of [0, -3, 1.5, "30"]) {
    assert.throws(() => sched.nextVisitDue("2026-07-01T09:00:00.000Z", bad),
      /cadenceDays must be a positive integer/);
  }
});

// ------------------------------------------------------------- weekWindow

test("weekWindow: Monday-start local week as half-open UTC instants", () => {
  assert.deepEqual(sched.weekWindow("2026-07-19T16:30:00Z", "Asia/Tokyo"), {
    start: "2026-07-19T15:00:00.000Z",
    end: "2026-07-26T15:00:00.000Z",
  });
});

test("weekWindow: the same instant falls in different weeks per zone", () => {
  // 16:30Z on Sunday 2026-07-19 is already Monday in Tokyo, still Sunday in London.
  assert.deepEqual(sched.weekWindow("2026-07-19T16:30:00Z", "Europe/London"), {
    start: "2026-07-12T23:00:00.000Z",
    end: "2026-07-19T23:00:00.000Z",
  });
});

test("weekWindow: spring-forward week is 167 real hours", () => {
  const w = sched.weekWindow("2026-03-08T20:00:00Z", "America/Denver");
  assert.deepEqual(w, {
    start: "2026-03-02T07:00:00.000Z",
    end: "2026-03-09T06:00:00.000Z",
  });
  assert.equal((Date.parse(w.end) - Date.parse(w.start)) / 3600000, 167);
});

test("weekWindow: fall-back week is 169 real hours", () => {
  const w = sched.weekWindow("2026-10-28T12:00:00Z", "America/Denver");
  assert.deepEqual(w, {
    start: "2026-10-26T06:00:00.000Z",
    end: "2026-11-02T07:00:00.000Z",
  });
  assert.equal((Date.parse(w.end) - Date.parse(w.start)) / 3600000, 169);
});

test("weekWindow: weekStartsOn 0 opens the week on Sunday", () => {
  assert.deepEqual(sched.weekWindow("2026-07-15T12:00:00Z", "America/Denver", 0), {
    start: "2026-07-12T06:00:00.000Z",
    end: "2026-07-19T06:00:00.000Z",
  });
});

test("weekWindow contains its instant, half-open", () => {
  const instant = "2026-03-08T20:00:00Z";
  const w = sched.weekWindow(instant, "America/Denver");
  const t = Date.parse(instant);
  assert.ok(Date.parse(w.start) <= t && t < Date.parse(w.end));
});

// ------------------------------------------------------- expandRecurrence

test("weekly rule holds the site wall clock across a DST change", () => {
  const out = sched.expandRecurrence(
    { freq: "weekly", byDay: ["MO"], time: "09:00", tz: "America/New_York" },
    "2026-03-01T00:00:00Z", "2026-03-15T00:00:00Z");
  assert.deepEqual(out, [
    "2026-03-02T14:00:00.000Z",
    "2026-03-09T13:00:00.000Z",
  ]);
});

test("weekly rule with several byDay codes comes back sorted", () => {
  const out = sched.expandRecurrence(
    { freq: "weekly", byDay: ["WE", "MO"], time: "08:30", tz: "Europe/Berlin" },
    "2026-07-13T00:00:00Z", "2026-07-20T00:00:00Z");
  assert.deepEqual(out, [
    "2026-07-13T06:30:00.000Z",
    "2026-07-15T06:30:00.000Z",
  ]);
});

test("duplicate byDay codes count once", () => {
  const out = sched.expandRecurrence(
    { freq: "weekly", byDay: ["MO", "MO"], time: "08:30", tz: "Europe/Berlin" },
    "2026-07-13T00:00:00Z", "2026-07-20T00:00:00Z");
  assert.deepEqual(out, ["2026-07-13T06:30:00.000Z"]);
});

test("window is half-open: start instant in, end instant out", () => {
  const out = sched.expandRecurrence(
    { freq: "weekly", byDay: ["FR"], time: "09:00", tz: "Etc/UTC" },
    "2026-07-10T09:00:00Z", "2026-07-17T09:00:00Z");
  assert.deepEqual(out, ["2026-07-10T09:00:00.000Z"]);
});

test("monthly nth weekday follows the site zone through DST", () => {
  const out = sched.expandRecurrence(
    { freq: "monthly", nth: 2, day: "TU", time: "10:00", tz: "America/Chicago" },
    "2026-01-01T00:00:00Z", "2026-04-01T00:00:00Z");
  assert.deepEqual(out, [
    "2026-01-13T16:00:00.000Z",
    "2026-02-10T16:00:00.000Z",
    "2026-03-10T15:00:00.000Z",
  ]);
});

test("monthly nth -1 picks the last such weekday", () => {
  const out = sched.expandRecurrence(
    { freq: "monthly", nth: -1, day: "FR", time: "16:00", tz: "Europe/Berlin" },
    "2026-02-01T00:00:00Z", "2026-04-01T00:00:00Z");
  assert.deepEqual(out, [
    "2026-02-27T15:00:00.000Z",
    "2026-03-27T15:00:00.000Z",
  ]);
});

test("months without a 5th occurrence are skipped, not invented", () => {
  const out = sched.expandRecurrence(
    { freq: "monthly", nth: 5, day: "FR", time: "12:00", tz: "Etc/UTC" },
    "2026-01-01T00:00:00Z", "2026-04-01T00:00:00Z");
  assert.deepEqual(out, ["2026-01-30T12:00:00.000Z"]);
});

test("junk rules and windows are rejected with the agreed messages", () => {
  const win = ["2026-07-01T00:00:00Z", "2026-08-01T00:00:00Z"];
  assert.throws(() => sched.expandRecurrence(
    { freq: "daily", byDay: ["MO"], time: "09:00", tz: "Etc/UTC" }, ...win),
    (e) => e instanceof Error && e.message === "unsupported freq: daily");
  assert.throws(() => sched.expandRecurrence(
    { freq: "weekly", byDay: ["MO", "XX"], time: "09:00", tz: "Etc/UTC" }, ...win),
    (e) => e.message === "unknown weekday: XX");
  assert.throws(() => sched.expandRecurrence(
    { freq: "weekly", byDay: [], time: "09:00", tz: "Etc/UTC" }, ...win),
    (e) => e.message === "byDay must not be empty");
  assert.throws(() => sched.expandRecurrence(
    { freq: "monthly", nth: 0, day: "FR", time: "09:00", tz: "Etc/UTC" }, ...win),
    (e) => e.message === "invalid nth: 0");
  assert.throws(() => sched.expandRecurrence(
    { freq: "monthly", nth: 6, day: "FR", time: "09:00", tz: "Etc/UTC" }, ...win),
    (e) => e.message === "invalid nth: 6");
  assert.throws(() => sched.expandRecurrence(
    { freq: "weekly", byDay: ["MO"], time: "9am", tz: "Etc/UTC" }, ...win),
    (e) => e.message === "invalid time: 9am");
  assert.throws(() => sched.expandRecurrence(
    { freq: "weekly", byDay: ["MO"], time: "09:00", tz: "Etc/UTC" },
    "2026-08-01T00:00:00Z", "2026-07-01T00:00:00Z"),
    (e) => e.message === "invalid window");
  assert.throws(() => sched.expandRecurrence(
    { freq: "weekly", byDay: ["MO"], time: "09:00", tz: "Etc/UTC" },
    "not-a-date", "2026-08-01T00:00:00Z"),
    (e) => e.message === "invalid window");
});
