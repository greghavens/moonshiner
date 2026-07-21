import assert from "node:assert/strict";
import test from "node:test";

import { IndexedSelector } from "../src/indexed-selector.ts";

interface RecordValue {
  readonly id: string;
  readonly lane: string;
}

test("select preserves collection order and duplicate selector values", () => {
  const records: readonly RecordValue[] = [
    { id: "a", lane: "blue" },
    { id: "b", lane: "red" },
    { id: "c", lane: "blue" },
    { id: "d", lane: "green" },
  ];
  const collection = new IndexedSelector(records, (record) => record.lane);

  assert.deepEqual(collection.select("blue").map((record) => record.id), ["a", "c"]);
  assert.deepEqual(collection.select("missing"), []);
  assert.equal(collection.size, 4);
});

test("add, update, and delete keep selector results synchronized", () => {
  const a = { id: "a", lane: "blue" };
  const b = { id: "b", lane: "red" };
  const c = { id: "c", lane: "blue" };
  const d = { id: "d", lane: "green" };
  const e = { id: "e", lane: "blue" };
  const bMoved = { id: "b", lane: "blue" };
  const cMoved = { id: "c", lane: "red" };
  const collection = new IndexedSelector([a, b, c, d], (record) => record.lane);

  collection.add(e);
  assert.equal(collection.update(b, bMoved), true);
  assert.equal(collection.update(c, cMoved), true);
  assert.equal(collection.delete(a), true);

  assert.deepEqual(collection.select("blue").map((record) => record.id), ["b", "e"]);
  assert.deepEqual(collection.select("red").map((record) => record.id), ["c"]);
  assert.deepEqual(collection.snapshot().map((record) => record.id), ["b", "c", "d", "e"]);
  assert.equal(collection.update(a, bMoved), false);
  assert.equal(collection.delete(a), false);
});

test("duplicate object references remain distinct entries", () => {
  const shared = { id: "shared", lane: "blue" };
  const replacement = { id: "replacement", lane: "red" };
  const collection = new IndexedSelector([shared, shared], (record) => record.lane);

  assert.equal(collection.select("blue").length, 2);
  assert.equal(collection.update(shared, replacement), true);
  assert.deepEqual(collection.select("blue"), [shared]);
  assert.deepEqual(collection.select("red"), [replacement]);
  assert.equal(collection.delete(shared), true);
  assert.deepEqual(collection.select("blue"), []);
  assert.equal(collection.size, 1);
});

test("the caller's array and returned arrays are never retained for mutation", () => {
  const first = { id: "first", lane: "blue" };
  const second = { id: "second", lane: "red" };
  const input: RecordValue[] = [first, second];
  const collection = new IndexedSelector(input, (record) => record.lane);

  collection.add({ id: "third", lane: "blue" });
  collection.update(second, { id: "second", lane: "blue" });
  assert.deepEqual(input, [first, second]);

  input.splice(0, input.length, { id: "outside", lane: "red" });
  assert.deepEqual(collection.snapshot().map((record) => record.id), [
    "first",
    "second",
    "third",
  ]);

  const result = collection.select("blue") as RecordValue[];
  result.splice(0, result.length);

  assert.deepEqual(collection.select("blue").map((record) => record.id), [
    "first",
    "second",
    "third",
  ]);
});
