// test_keyed_recon.ts — spec suite for the keyed reconciler.
// Protected test file: do not modify. Run with: node --test test_keyed_recon.ts
//
// Plain TypeScript under node's type stripping: no JSX, no React import.
// The module under test is ./keyed_recon.ts, exporting diff(oldRoot, newRoot).
// Every expected op list below is exact: op order, field names, indices.

import { test } from "node:test";
import assert from "node:assert/strict";
import { diff } from "./keyed_recon.ts";

type Props = Record<string, string | number | boolean>;

const el = (type: string, key: string | null, props: Props = {}, children: any[] = []): any => ({
  type,
  key,
  props,
  children,
});

const li = (key: string, text: string) => el("li", key, { text });

// ------------------------------------------------------------ no-change

test("identical trees produce no ops; root keys are ignored", () => {
  const a = el("div", "x", { id: "app" }, [el("p", null, { text: "hi" })]);
  const b = el("div", "y", { id: "app" }, [el("p", null, { text: "hi" })]);
  assert.deepEqual(diff(a, b), []);
});

// -------------------------------------------------------------- updates

test("prop changes emit one update op with set and sorted remove", () => {
  const a = el("div", null, { a: 1, b: "x", c: true, z: 0 });
  const b = el("div", null, { a: 2, b: "x", d: false });
  assert.deepEqual(diff(a, b), [
    { op: "update", path: [], set: { a: 2, d: false }, remove: ["c", "z"] },
  ]);
});

test("prop values compare with Object.is: NaN to NaN is no change", () => {
  assert.deepEqual(diff(el("i", null, { v: NaN }), el("i", null, { v: NaN })), []);
  assert.deepEqual(diff(el("i", null, { v: 1 }), el("i", null, { v: NaN })), [
    { op: "update", path: [], set: { v: NaN }, remove: [] },
  ]);
});

// ------------------------------------------------------ keyed insert/remove

test("keyed insert in the middle", () => {
  const a = el("ul", null, {}, [li("a", "1"), li("b", "2")]);
  const b = el("ul", null, {}, [li("a", "1"), li("x", "9"), li("b", "2")]);
  assert.deepEqual(diff(a, b), [
    { op: "insert", parent: [], index: 1, node: li("x", "9") },
  ]);
});

test("keyed remove from the middle uses the old index", () => {
  const a = el("ul", null, {}, [li("a", "1"), li("x", "9"), li("b", "2")]);
  const b = el("ul", null, {}, [li("a", "1"), li("b", "2")]);
  assert.deepEqual(diff(a, b), [{ op: "remove", parent: [], index: 1 }]);
});

test("emptying a list removes in descending old-index order", () => {
  const a = el("ul", null, {}, [li("a", "1"), li("b", "2")]);
  const b = el("ul", null, {}, []);
  assert.deepEqual(diff(a, b), [
    { op: "remove", parent: [], index: 1 },
    { op: "remove", parent: [], index: 0 },
  ]);
});

test("filling an empty list inserts left to right", () => {
  const a = el("ul", null, {}, []);
  const b = el("ul", null, {}, [li("a", "1"), li("b", "2")]);
  assert.deepEqual(diff(a, b), [
    { op: "insert", parent: [], index: 0, node: li("a", "1") },
    { op: "insert", parent: [], index: 1, node: li("b", "2") },
  ]);
});

// ---------------------------------------------------------- minimal moves

test("moving the last child to the front is exactly one move", () => {
  const a = el("ul", null, {}, [li("a", "1"), li("b", "2"), li("c", "3"), li("d", "4")]);
  const b = el("ul", null, {}, [li("d", "4"), li("a", "1"), li("b", "2"), li("c", "3")]);
  assert.deepEqual(diff(a, b), [{ op: "move", parent: [], from: 3, to: 0 }]);
});

test("rotating a block moves only the smaller side", () => {
  const a = el("ul", null, {}, [li("a", "1"), li("b", "2"), li("c", "3"), li("d", "4"), li("e", "5")]);
  const b = el("ul", null, {}, [li("c", "3"), li("d", "4"), li("e", "5"), li("a", "1"), li("b", "2")]);
  assert.deepEqual(diff(a, b), [
    { op: "move", parent: [], from: 0, to: 3 },
    { op: "move", parent: [], from: 1, to: 4 },
  ]);
});

test("full reversal keeps the earliest new child and moves the rest", () => {
  const a = el("ul", null, {}, [li("a", "1"), li("b", "2"), li("c", "3"), li("d", "4")]);
  const b = el("ul", null, {}, [li("d", "4"), li("c", "3"), li("b", "2"), li("a", "1")]);
  assert.deepEqual(diff(a, b), [
    { op: "move", parent: [], from: 2, to: 1 },
    { op: "move", parent: [], from: 1, to: 2 },
    { op: "move", parent: [], from: 0, to: 3 },
  ]);
});

test("remove, insert and move interleave: removes first, then new-order walk", () => {
  const a = el("ul", null, {}, [li("a", "1"), li("b", "2"), li("c", "3"), li("d", "4")]);
  const b = el("ul", null, {}, [li("b", "2"), li("c", "3"), li("e", "5"), li("a", "1")]);
  assert.deepEqual(diff(a, b), [
    { op: "remove", parent: [], index: 3 },
    { op: "insert", parent: [], index: 2, node: li("e", "5") },
    { op: "move", parent: [], from: 0, to: 3 },
  ]);
});

test("a moved child still gets its update op, addressed by its new path", () => {
  const a = el("ul", null, {}, [li("a", "1"), li("b", "2")]);
  const b = el("ul", null, {}, [li("b", "2"), li("a", "9")]);
  assert.deepEqual(diff(a, b), [
    { op: "move", parent: [], from: 0, to: 1 },
    { op: "update", path: [1], set: { text: "9" }, remove: [] },
  ]);
});

// ----------------------------------------------------- type/key matching

test("same key with a different type is remove + insert, not a move", () => {
  const a = el("ul", null, {}, [li("a", "1"), li("b", "2")]);
  const b = el("ul", null, {}, [li("a", "1"), el("div", "b", { text: "2" })]);
  assert.deepEqual(diff(a, b), [
    { op: "remove", parent: [], index: 1 },
    { op: "insert", parent: [], index: 1, node: el("div", "b", { text: "2" }) },
  ]);
});

test("unkeyed children pair up positionally", () => {
  const a = el("div", null, {}, [el("p", null, { text: "x" }), el("span", null, {})]);
  const b = el("div", null, {}, [el("p", null, { text: "y" }), el("span", null, {}), el("em", null, {})]);
  assert.deepEqual(diff(a, b), [
    { op: "insert", parent: [], index: 2, node: el("em", null, {}) },
    { op: "update", path: [0], set: { text: "y" }, remove: [] },
  ]);
});

test("an unkeyed positional pair with different types is remove + insert", () => {
  const a = el("div", null, {}, [el("p", null, { text: "x" }), el("span", null, {})]);
  const b = el("div", null, {}, [el("blockquote", null, { text: "x" }), el("span", null, {})]);
  assert.deepEqual(diff(a, b), [
    { op: "remove", parent: [], index: 0 },
    { op: "insert", parent: [], index: 0, node: el("blockquote", null, { text: "x" }) },
  ]);
});

// ------------------------------------------------------- nested recursion

test("parent child-list ops come before descendant ops; paths use new indices", () => {
  const a = el("section", null, {}, [
    el("header", null, { title: "t" }),
    el("ul", null, {}, [li("a", "1"), li("b", "2")]),
  ]);
  const b = el("section", null, {}, [
    el("header", null, { title: "t" }),
    el("ul", null, {}, [li("b", "2!"), li("a", "1")]),
  ]);
  assert.deepEqual(diff(a, b), [
    { op: "move", parent: [1], from: 0, to: 1 },
    { op: "update", path: [1, 0], set: { text: "2!" }, remove: [] },
  ]);
});

test("a node's own update precedes its child-list ops", () => {
  const a = el("ul", null, { class: "old" }, [li("a", "1")]);
  const b = el("ul", null, { class: "new" }, [li("x", "9"), li("a", "1")]);
  assert.deepEqual(diff(a, b), [
    { op: "update", path: [], set: { class: "new" }, remove: [] },
    { op: "insert", parent: [], index: 0, node: li("x", "9") },
  ]);
});

test("inserted subtrees are carried whole; no ops are emitted inside them", () => {
  const child = el("li", "x", { text: "9" }, [el("em", null, { text: "!" })]);
  const a = el("ul", null, {}, [li("a", "1")]);
  const b = el("ul", null, {}, [li("a", "1"), child]);
  assert.deepEqual(diff(a, b), [{ op: "insert", parent: [], index: 1, node: child }]);
});

// ---------------------------------------------------------------- errors

test("duplicate keys in one child list throw", () => {
  const a = el("ul", null, {}, [li("a", "1")]);
  const b = el("ul", null, {}, [li("a", "1"), li("a", "2")]);
  assert.throws(() => diff(a, b), { message: "duplicate key: a" });
});

test("a root type change throws", () => {
  assert.throws(() => diff(el("div", null, {}), el("span", null, {})), {
    message: "root type changed: expected div, got span",
  });
});
