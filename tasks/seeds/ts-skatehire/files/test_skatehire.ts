import test from "node:test";
import assert from "node:assert/strict";
import { HireCounter, isKidsSize } from "./skatehire.ts";

test("kids sizes are 3 and under", () => {
  assert.equal(isKidsSize("2"), true);
  assert.equal(isKidsSize("3"), true);
  assert.equal(isKidsSize("4"), false);
  assert.equal(isKidsSize("7.5"), false);
  assert.equal(isKidsSize("youth"), false);
});

test("shelf listing shows only sizes with stock, sorted", () => {
  const counter = new HireCounter({ "7": 2, "2": 1, "9": 0 });
  assert.deepEqual(counter.sizesOnShelf(), ["2", "7"]);
});

test("hiring out issues sequential slips with the right deposit", () => {
  const counter = new HireCounter({ "7": 2, "2": 1 });
  const a = counter.hireOut("7");
  assert.ok(a);
  assert.equal(a.tag, 100);
  assert.equal(a.deposit, 5);
  assert.equal(a.note, undefined);

  const b = counter.hireOut("2", "left toe pick chipped");
  assert.ok(b);
  assert.equal(b.tag, 101);
  assert.equal(b.deposit, 2);
  assert.equal(b.note, "left toe pick chipped");
});

test("unknown and exhausted sizes are refused", () => {
  const counter = new HireCounter({ "7": 1, "9": 0 });
  assert.equal(counter.hireOut("13"), null);
  assert.equal(counter.hireOut("9"), null);
  assert.ok(counter.hireOut("7"));
  assert.equal(counter.hireOut("7"), null);
});

test("hand-back refunds the deposit and restocks the shelf", () => {
  const counter = new HireCounter({ "7": 1 });
  const slip = counter.hireOut("7");
  assert.ok(slip);
  assert.deepEqual(counter.sizesOnShelf(), []);
  assert.equal(counter.handBack(slip.tag, false), 5);
  assert.deepEqual(counter.sizesOnShelf(), ["7"]);
  assert.equal(counter.handBack(slip.tag, false), null, "same tag twice");
  assert.equal(counter.handBack(555, false), null, "unknown tag");
});

test("dull blades go to the sharpening queue, not the shelf", () => {
  const counter = new HireCounter({ "7": 1, "8": 1 });
  const seven = counter.hireOut("7");
  const eight = counter.hireOut("8");
  assert.ok(seven);
  assert.ok(eight);
  assert.equal(counter.handBack(seven.tag, true), 5);
  assert.equal(counter.handBack(eight.tag, true), 5);
  assert.deepEqual(counter.sizesOnShelf(), []);
  assert.deepEqual(counter.sharpeningQueue(), ["7", "8"]);

  assert.equal(counter.sharpenDone("7", "back bench"), true);
  assert.deepEqual(counter.sharpeningQueue(), ["8"]);
  assert.deepEqual(counter.sizesOnShelf(), ["7"]);
  assert.equal(counter.sharpenDone("7", "back bench"), false, "not in the queue");
});

test("noted slips are reported with their notes trimmed", () => {
  const counter = new HireCounter({ "6": 3 });
  counter.hireOut("6", "  lace hook bent ");
  counter.hireOut("6");
  counter.hireOut("6", "scuffed heel");
  assert.deepEqual(counter.notedSlips(), [
    "#100: lace hook bent",
    "#102: scuffed heel",
  ]);
});

test("receipt lines and deposits held", () => {
  const counter = new HireCounter({ "7": 2, "2": 1 });
  const a = counter.hireOut("7");
  const b = counter.hireOut("2");
  assert.ok(a);
  assert.ok(b);
  assert.equal(counter.receiptLine(a), "#100 size 7 — deposit $5");
  assert.equal(counter.depositsHeld(), 7);
  counter.handBack(a.tag, false);
  assert.equal(counter.depositsHeld(), 2);
});
