// Acceptance for the discriminated-union scene model.
// Run: node --test test_shapes.ts
//
// Post-refactor surface: plain-data shapes tagged by `kind`, lower-case
// constructor helpers, an exhaustive matchShape helper, and pure
// functions replacing the old methods. All geometry, description strings
// and the saved-scene JSON format are pinned to the class-based
// implementation's output — old boards on disk must still load.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  rect,
  circle,
  segment,
  label,
  matchShape,
  area,
  bounds,
  moved,
  describe,
  totalInkArea,
  serializeScene,
  parseScene,
} from './shapes.ts';

// Captured from a board saved by the class-based implementation.
const LEGACY_SAVE =
  '[{"kind":"rect","x":1,"y":2,"width":3,"height":4},' +
  '{"kind":"circle","cx":10,"cy":10,"r":2},' +
  '{"kind":"segment","x1":4,"y1":8,"x2":1,"y2":4},' +
  '{"kind":"label","x":2,"y":3,"text":"Hi there"}]';

const HANDLERS = {
  rect: () => 'rect',
  circle: () => 'circle',
  segment: () => 'segment',
  label: () => 'label',
};

test('constructors build plain tagged objects, not class instances', () => {
  assert.deepEqual(rect(1, 2, 3, 4), { kind: 'rect', x: 1, y: 2, width: 3, height: 4 });
  assert.deepEqual(circle(10, 10, 2), { kind: 'circle', cx: 10, cy: 10, r: 2 });
  assert.deepEqual(segment(4, 8, 1, 4), { kind: 'segment', x1: 4, y1: 8, x2: 1, y2: 4 });
  assert.deepEqual(label(2, 3, 'Hi there'), { kind: 'label', x: 2, y: 3, text: 'Hi there' });
});

test('shapes survive structuredClone unchanged — the worker handoff', () => {
  const scene = [rect(1, 2, 3, 4), circle(10, 10, 2), segment(4, 8, 1, 4), label(2, 3, 'Hi there')];
  assert.deepEqual(structuredClone(scene), scene);
});

test('matchShape routes each variant to its handler with the shape', () => {
  for (const [shape, want] of [
    [rect(0, 0, 1, 1), 'rect'],
    [circle(0, 0, 1), 'circle'],
    [segment(0, 0, 1, 1), 'segment'],
    [label(0, 0, 'x'), 'label'],
  ] as Array<[any, string]>) {
    assert.equal(matchShape(shape, HANDLERS), want);
  }
  const got = matchShape(circle(5, 6, 2), {
    rect: () => 'no',
    circle: (c) => `r=${c.r} at (${c.cx}, ${c.cy})`,
    segment: () => 'no',
    label: () => 'no',
  });
  assert.equal(got, 'r=2 at (5, 6)');
});

test('matchShape rejects unknown kinds at runtime', () => {
  assert.throws(
    () => matchShape({ kind: 'hexagon' } as any, HANDLERS),
    /unknown shape kind: hexagon/,
  );
});

test('area matches the old methods', () => {
  assert.equal(area(rect(1, 2, 3, 4)), 12);
  assert.equal(area(circle(10, 10, 2)), Math.PI * 4);
  assert.equal(area(segment(0, 0, 9, 9)), 0);
  assert.equal(area(label(0, 0, 'wide label')), 0);
});

test('bounds match the old methods, segment normalized, label metrics kept', () => {
  assert.deepEqual(bounds(rect(1, 2, 3, 4)), { x: 1, y: 2, width: 3, height: 4 });
  assert.deepEqual(bounds(circle(10, 10, 2)), { x: 8, y: 8, width: 4, height: 4 });
  assert.deepEqual(bounds(segment(4, 8, 1, 4)), { x: 1, y: 4, width: 3, height: 4 });
  assert.deepEqual(bounds(label(2, 3, 'Hi there')), { x: 2, y: 3, width: 64, height: 16 });
});

test('moved returns a fresh shape and never mutates the input', () => {
  const r0 = rect(1, 2, 3, 4);
  assert.deepEqual(moved(r0, 5, 5), rect(6, 7, 3, 4));
  assert.deepEqual(r0, rect(1, 2, 3, 4));
  assert.notEqual(moved(r0, 0, 0), r0);
  assert.deepEqual(moved(segment(4, 8, 1, 4), 1, 1), segment(5, 9, 2, 5));
  assert.deepEqual(moved(circle(10, 10, 2), -10, -10), circle(0, 0, 2));
  assert.deepEqual(moved(label(2, 3, 'Hi there'), 0, 7), label(2, 10, 'Hi there'));
});

test('describe strings are unchanged — the sidebar snapshot tests upstream rely on them', () => {
  assert.equal(describe(rect(1, 2, 3, 4)), 'rect 3x4 at (1, 2)');
  assert.equal(describe(circle(10, 10, 2)), 'circle r=2 at (10, 10)');
  assert.equal(describe(segment(4, 8, 1, 4)), 'segment (4, 8) -> (1, 4) length 5');
  assert.equal(describe(segment(0, 0, 3, 4)), 'segment (0, 0) -> (3, 4) length 5');
  assert.equal(describe(label(2, 3, 'Hi there')), 'label "Hi there" at (2, 3)');
});

test('totalInkArea still sums rect and circle ink only', () => {
  const scene = [rect(1, 2, 3, 4), segment(0, 0, 5, 5), label(0, 0, 'note'), circle(10, 10, 2)];
  assert.equal(totalInkArea(scene), 12 + Math.PI * 4);
  assert.equal(totalInkArea([]), 0);
});

test('scenes saved by the class implementation still parse', () => {
  const scene = parseScene(LEGACY_SAVE);
  assert.deepEqual(scene, [
    rect(1, 2, 3, 4),
    circle(10, 10, 2),
    segment(4, 8, 1, 4),
    label(2, 3, 'Hi there'),
  ]);
});

test('serializeScene writes the same on-disk format', () => {
  const scene = parseScene(LEGACY_SAVE);
  assert.deepEqual(JSON.parse(serializeScene(scene)), JSON.parse(LEGACY_SAVE));
});

test('parseScene rejects unknown kinds and non-array documents', () => {
  assert.throws(() => parseScene('[{"kind":"hexagon"}]'), /unknown shape kind: hexagon/);
  assert.throws(() => parseScene('{"kind":"rect"}'), /scene must be an array/);
});
