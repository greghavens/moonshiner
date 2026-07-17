import { test } from "node:test";
import assert from "node:assert/strict";
import { tally, invert, mergeCounts, groupBy, topK } from "./mapkit.ts";

test("tally counts occurrences", () => {
  const t = tally(["howto", "news", "howto", "howto", "review"]);
  assert.deepEqual(
    [...t.entries()],
    [
      ["howto", 3],
      ["news", 1],
      ["review", 1],
    ]
  );
});

test("tally of nothing is an empty map", () => {
  assert.equal(tally([]).size, 0);
});

test("invert swaps keys and values, last write wins", () => {
  const m = new Map([
    ["a", 1],
    ["b", 2],
    ["c", 2],
  ]);
  const inv = invert(m);
  assert.equal(inv.get(1), "a");
  assert.equal(inv.get(2), "c");
  assert.equal(inv.size, 2);
});

test("mergeCounts adds overlapping keys and keeps the rest", () => {
  const a = new Map([
    ["howto", 3],
    ["news", 1],
  ]);
  const b = new Map([
    ["news", 4],
    ["review", 2],
  ]);
  const m = mergeCounts(a, b);
  assert.deepEqual(
    [...m.entries()],
    [
      ["howto", 3],
      ["news", 5],
      ["review", 2],
    ]
  );
  assert.equal(a.get("news"), 1, "inputs must not be mutated");
});

test("groupBy collects values per key in arrival order", () => {
  const g = groupBy([
    ["ada", 10],
    ["bo", 7],
    ["ada", 4],
  ]);
  assert.deepEqual(g.get("ada"), [10, 4]);
  assert.deepEqual(g.get("bo"), [7]);
  assert.equal(g.size, 2);
});

test("topK ranks by count desc, ties alphabetical", () => {
  const counts = new Map([
    ["news", 4],
    ["howto", 9],
    ["review", 4],
    ["meta", 1],
  ]);
  assert.deepEqual(topK(counts, 3), ["howto", "news", "review"]);
  assert.deepEqual(topK(counts, 0), []);
  assert.deepEqual(topK(new Map(), 5), []);
});
