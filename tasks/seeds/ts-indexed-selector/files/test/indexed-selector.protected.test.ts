import assert from "node:assert/strict";
import test from "node:test";

import { IndexedSelector } from "../src/indexed-selector.ts";

interface ProfileRow {
  readonly id: number;
  readonly group: string;
}

test("repeated selectors scan only their indexed candidates", () => {
  const rows = Array.from({ length: 512 }, (_, id): ProfileRow => ({
    id,
    group: `group-${id % 16}`,
  }));
  const collection = new IndexedSelector(rows, (row) => row.group);

  collection.resetProfile();
  for (let lookup = 0; lookup < 25; lookup += 1) {
    assert.equal(collection.select("group-7").length, 32);
  }

  assert.deepEqual(collection.profile(), {
    lookups: 25,
    scanned: 25 * 32,
  });
});

test("selection does not walk the full entry list while reporting bucket work", () => {
  const rows = Array.from({ length: 128 }, (_, id): ProfileRow => ({
    id,
    group: `group-${id % 16}`,
  }));
  const collection = new IndexedSelector(rows, (row) => row.group);
  const originalIterator = Array.prototype[Symbol.iterator];
  const originalSetHas = Set.prototype.has;
  let fullEntryWalks = 0;
  let indexedMembershipChecks = 0;
  let selected: readonly ProfileRow[];

  Array.prototype[Symbol.iterator] = function patchedIterator() {
    const first = this[0] as object | undefined;
    if (
      this.length === rows.length &&
      first !== undefined &&
      Object.hasOwn(first, "value") &&
      Object.hasOwn(first, "key")
    ) {
      fullEntryWalks += 1;
    }
    return originalIterator.call(this);
  };
  Set.prototype.has = function patchedHas(value) {
    indexedMembershipChecks += 1;
    return originalSetHas.call(this, value);
  };

  collection.resetProfile();
  try {
    selected = collection.select("group-3");
  } finally {
    Array.prototype[Symbol.iterator] = originalIterator;
    Set.prototype.has = originalSetHas;
  }

  assert.deepEqual(selected.map((row) => row.id), [3, 19, 35, 51, 67, 83, 99, 115]);
  assert.equal(fullEntryWalks, 0);
  assert.ok(indexedMembershipChecks <= selected.length);
  assert.deepEqual(collection.profile(), { lookups: 1, scanned: 8 });
});

test("profiled buckets reflect adds, selector-changing updates, and deletes", () => {
  const source: readonly ProfileRow[] = Object.freeze(
    Array.from({ length: 60 }, (_, id): ProfileRow => ({
      id,
      group: id % 10 === 0 ? "hot" : "cold",
    })),
  );
  const collection = new IndexedSelector(source, (row) => row.group);
  const added = { id: 60, group: "hot" };
  const moved = { id: 1, group: "hot" };

  collection.add(added);
  assert.equal(collection.update(source[1], moved), true);
  assert.equal(collection.delete(source[0]), true);
  collection.resetProfile();

  for (let lookup = 0; lookup < 5; lookup += 1) {
    assert.deepEqual(
      collection.select("hot").map((row) => row.id),
      [1, 10, 20, 30, 40, 50, 60],
    );
  }

  assert.deepEqual(collection.profile(), { lookups: 5, scanned: 35 });
  assert.equal(source.length, 60);
  assert.deepEqual(source[1], { id: 1, group: "cold" });
});

test("a missing selector performs no record scans", () => {
  const rows = Array.from({ length: 100 }, (_, id): ProfileRow => ({
    id,
    group: id % 2 === 0 ? "even" : "odd",
  }));
  const collection = new IndexedSelector(rows, (row) => row.group);

  collection.resetProfile();
  assert.deepEqual(collection.select("absent"), []);
  assert.deepEqual(collection.profile(), { lookups: 1, scanned: 0 });
});
