import { test } from "node:test";
import assert from "node:assert/strict";

import { zoneOf, ZONES } from "./plot.ts";
import { parseRoster } from "./parse.ts";
import { seasonFee, formatFee } from "./fees.ts";
import { assignPlots } from "./assign.ts";
import { loadSeason } from "./roster.ts";

const ROSTER = [
  "# 2026 season",
  "p1, A3, 120, yes",
  "p2, C1, 200, no",
  "",
  "p3, E2, 80, no",
  "p4, B4, 60, no",
  "p5, F1, 300, yes",
].join("\n");

test("beds map to watering zones by row letter", () => {
  assert.deepEqual([...ZONES], ["north", "south", "creek"]);
  assert.equal(zoneOf("A3"), "north");
  assert.equal(zoneOf("b7"), "north");
  assert.equal(zoneOf("C1"), "south");
  assert.equal(zoneOf("E2"), "creek");
  assert.throws(() => zoneOf("K9"), /unknown bed row: K9/);
});

test("roster export parses, skipping comments and blanks", () => {
  const plots = parseRoster(ROSTER);
  assert.deepEqual(
    plots.map((p) => [p.id, p.bed, p.sqft, p.raised]),
    [
      ["p1", "A3", 120, true],
      ["p2", "C1", 200, false],
      ["p3", "E2", 80, false],
      ["p4", "B4", 60, false],
      ["p5", "F1", 300, true],
    ],
  );
  assert.deepEqual(parseRoster("# nothing yet\n\n"), []);
});

test("bad roster lines are reported with their 1-based line number", () => {
  assert.throws(
    () => parseRoster("# hdr\np1, A3, 120, yes\np2, C1, lots, no"),
    /roster line 3: bad sqft/,
  );
  assert.throws(() => parseRoster("p1, A3, 120"), /line 1: expected 4 fields/);
  assert.throws(
    () => parseRoster("p1, A3, 120, sure"),
    /raised must be yes or no/,
  );
});

test("season fees: base, per-sqft, lumber fund, creek discount", () => {
  const plots = parseRoster(ROSTER);
  assert.deepEqual(
    plots.map((p) => seasonFee(p)),
    [85, 90, 50, 55, 120],
  );
  assert.equal(formatFee(260), "$260.00");
  assert.equal(formatFee(50.5), "$50.50");
});

test("assignment seats each applicant on the smallest fitting plot", () => {
  const plots = parseRoster(ROSTER);
  const { assigned, waitlist } = assignPlots(plots, [
    { name: "Ana", minSqft: 100 },
    { name: "Ben", minSqft: 50 },
    { name: "Cleo", minSqft: 250 },
    { name: "Dev", minSqft: 400 },
  ]);
  assert.deepEqual(
    [...assigned.entries()],
    [
      ["Ana", "p1"],
      ["Ben", "p4"],
      ["Cleo", "p5"],
    ],
  );
  assert.deepEqual(waitlist, ["Dev"]);
});

test("season summary totals only the seated beds", () => {
  const summary = loadSeason(ROSTER, [
    { name: "Ana", minSqft: 100 },
    { name: "Ben", minSqft: 50 },
    { name: "Cleo", minSqft: 250 },
    { name: "Dev", minSqft: 400 },
  ]);
  assert.deepEqual(summary, {
    plots: 5,
    seated: 3,
    totalDue: "$260.00",
    waitlist: ["Dev"],
  });
});

test("empty season", () => {
  assert.deepEqual(loadSeason("", []), {
    plots: 0,
    seated: 0,
    totalDue: "$0.00",
    waitlist: [],
  });
});
