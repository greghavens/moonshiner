// Acceptance suite for the integer-physics pong engine.
// Run: node --test test_pong.ts
//
// Everything is a pure function of the state: paddles chase the ball's
// pre-move row, the ball advances by its velocity, walls reflect, paddles
// return by hit offset, goals reset to a pinned serve. Ball coordinates
// over tick sequences and full rally transcripts are pinned exactly.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  WIDTH,
  HEIGHT,
  LEFT_COL,
  RIGHT_COL,
  newMatch,
  tick,
  tickN,
  trace,
  render,
} from './pong.ts';

import type { State } from './pong.ts';

function st(ball: [number, number], vel: [number, number], left = 4, right = 4, score: [number, number] = [0, 0]): State {
  return { ball, vel, left, right, score } as State;
}

test('court constants and the fresh match are pinned', () => {
  assert.equal(WIDTH, 20);
  assert.equal(HEIGHT, 9);
  assert.equal(LEFT_COL, 1);
  assert.equal(RIGHT_COL, 18);
  const s = newMatch();
  assert.deepEqual(s.ball, [10, 4]);
  assert.deepEqual(s.vel, [1, 1]);
  assert.equal(s.left, 4);
  assert.equal(s.right, 4);
  assert.deepEqual(s.score, [0, 0]);
});

test('the fresh court renders exactly', () => {
  assert.equal(render(newMatch()), [
    '0 : 0',
    '....................',
    '....................',
    '....................',
    '.|................|.',
    '.|........o.......|.',
    '.|................|.',
    '....................',
    '....................',
    '....................',
  ].join('\n'));
});

test('one tick: ball advances by its velocity, centered paddles hold', () => {
  const s = tick(newMatch());
  assert.deepEqual(s.ball, [11, 5]);
  assert.deepEqual(s.vel, [1, 1]);
  assert.equal(s.left, 4);
  assert.equal(s.right, 4);
});

test('paddles chase the ball one row per tick using its PRE-move row', () => {
  const s = tick(st([10, 7], [1, -1], 4, 6));
  assert.equal(s.left, 5);
  assert.equal(s.right, 7); // chased toward row 7 even though the ball moved to 6
  assert.deepEqual(s.ball, [11, 6]);
});

test('paddles clamp to the court and stand still when aligned', () => {
  const held = tick(st([5, 0], [1, -1], 1, 1));
  assert.equal(held.left, 1);
  assert.equal(held.right, 1);
  const mixed = tick(st([5, 0], [1, 1], 2, 7));
  assert.equal(mixed.left, 1);
  assert.equal(mixed.right, 6);
});

test('the bottom wall reflects with integer fold-back', () => {
  const s = tick(st([14, 8], [1, 1]));
  assert.deepEqual(s.ball, [15, 7]);
  assert.deepEqual(s.vel, [1, -1]);
});

test('the top wall reflects with integer fold-back', () => {
  const s = tick(st([5, 0], [1, -1]));
  assert.deepEqual(s.ball, [6, 1]);
  assert.deepEqual(s.vel, [1, 1]);
});

test('a paddle return sets vy to the hit offset', () => {
  const up = tick(st([2, 5], [-1, 1]));
  assert.deepEqual(up.ball, [1, 6]);
  assert.deepEqual(up.vel, [1, 1]); // hit one below the center: offset +1
  assert.equal(up.left, 5);
  const down = tick(st([2, 3], [-1, -1]));
  assert.deepEqual(down.ball, [1, 2]);
  assert.deepEqual(down.vel, [1, -1]); // offset -1
  const flat = tick(st([2, 4], [-1, 0]));
  assert.deepEqual(flat.ball, [1, 4]);
  assert.deepEqual(flat.vel, [1, 0]); // dead-center: offset 0
});

test('the right paddle returns symmetrically', () => {
  const s = tick(st([17, 5], [1, 1]));
  assert.deepEqual(s.ball, [18, 6]);
  assert.deepEqual(s.vel, [-1, 1]);
  assert.equal(s.right, 5);
});

test('a dead-center return shuttles flat forever without scoring', () => {
  const log = trace(st([2, 4], [-1, 0]), 36);
  for (const line of log.split('\n')) {
    assert.match(line, /ball=\(\d+,4\) v=\((-1|1),0\) 0:0$/);
  }
});

test('tick is pure: the input state is never modified', () => {
  const s = st([10, 7], [1, -1], 4, 6);
  tick(s);
  assert.deepEqual(s.ball, [10, 7]);
  assert.deepEqual(s.vel, [1, -1]);
  assert.equal(s.right, 6);
});

test('a missed ball scores and serves toward the scorer (odd total: vy -1)', () => {
  // Right paddle is far out of position; the ball escapes on the right.
  assert.equal(trace(st([16, 7], [1, 1], 4, 1), 4), [
    't=1 ball=(17,8) v=(1,1) 0:0',
    't=2 ball=(18,7) v=(1,-1) 0:0',
    't=3 ball=(10,4) v=(-1,-1) 1:0',
    't=4 ball=(9,3) v=(-1,-1) 1:0',
  ].join('\n'));
});

test('a goal on the left gives the right player the point', () => {
  assert.equal(trace(st([3, 7], [-1, 1], 1, 4), 4), [
    't=1 ball=(2,8) v=(-1,1) 0:0',
    't=2 ball=(1,7) v=(-1,-1) 0:0',
    't=3 ball=(10,4) v=(1,-1) 0:1',
    't=4 ball=(11,3) v=(1,-1) 0:1',
  ].join('\n'));
});

test('an even point total serves with vy +1', () => {
  const s = tickN(st([16, 7], [1, 1], 4, 1, [0, 1]), 3);
  assert.deepEqual(s.score, [1, 1]);
  assert.deepEqual(s.ball, [10, 4]);
  assert.deepEqual(s.vel, [-1, 1]); // toward the left scorer, vy +1 at 2 points
  assert.equal(s.left, 4);
  assert.equal(s.right, 4);
});

test('the serve resets both paddles to center', () => {
  const s = tickN(st([16, 7], [1, 1], 7, 1), 3);
  assert.equal(s.left, 4);
  assert.equal(s.right, 4);
});

test('the opening rally is pinned for thirty ticks (perfect AI keeps it alive)', () => {
  assert.equal(trace(newMatch(), 30), [
    't=1 ball=(11,5) v=(1,1) 0:0',
    't=2 ball=(12,6) v=(1,1) 0:0',
    't=3 ball=(13,7) v=(1,1) 0:0',
    't=4 ball=(14,8) v=(1,1) 0:0',
    't=5 ball=(15,7) v=(1,-1) 0:0',
    't=6 ball=(16,6) v=(1,-1) 0:0',
    't=7 ball=(17,5) v=(1,-1) 0:0',
    't=8 ball=(18,4) v=(-1,-1) 0:0',
    't=9 ball=(17,3) v=(-1,-1) 0:0',
    't=10 ball=(16,2) v=(-1,-1) 0:0',
    't=11 ball=(15,1) v=(-1,-1) 0:0',
    't=12 ball=(14,0) v=(-1,-1) 0:0',
    't=13 ball=(13,1) v=(-1,1) 0:0',
    't=14 ball=(12,2) v=(-1,1) 0:0',
    't=15 ball=(11,3) v=(-1,1) 0:0',
    't=16 ball=(10,4) v=(-1,1) 0:0',
    't=17 ball=(9,5) v=(-1,1) 0:0',
    't=18 ball=(8,6) v=(-1,1) 0:0',
    't=19 ball=(7,7) v=(-1,1) 0:0',
    't=20 ball=(6,8) v=(-1,1) 0:0',
    't=21 ball=(5,7) v=(-1,-1) 0:0',
    't=22 ball=(4,6) v=(-1,-1) 0:0',
    't=23 ball=(3,5) v=(-1,-1) 0:0',
    't=24 ball=(2,4) v=(-1,-1) 0:0',
    't=25 ball=(1,3) v=(1,-1) 0:0',
    't=26 ball=(2,2) v=(1,-1) 0:0',
    't=27 ball=(3,1) v=(1,-1) 0:0',
    't=28 ball=(4,0) v=(1,-1) 0:0',
    't=29 ball=(5,1) v=(1,1) 0:0',
    't=30 ball=(6,2) v=(1,1) 0:0',
  ].join('\n'));
});

test('the ball draws over a paddle cell in mid-action renders', () => {
  assert.equal(render(st([1, 3], [1, -1], 4, 6, [2, 1])), [
    '2 : 1',
    '....................',
    '....................',
    '....................',
    '.o..................',
    '.|..................',
    '.|................|.',
    '..................|.',
    '..................|.',
    '....................',
  ].join('\n'));
});

test('tickN composes single ticks', () => {
  let s = newMatch();
  for (let i = 0; i < 17; i++) s = tick(s);
  assert.deepEqual(tickN(newMatch(), 17), s);
});
