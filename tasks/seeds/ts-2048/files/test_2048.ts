// Acceptance suite for the deterministic 2048 engine.
// Run: node --test test_2048.ts
//
// Everything is pinned: the merge law, the LCG, the spawn rule (position
// from the free-cell index, 2-vs-4 threshold) and the render format. Game
// objects are plain data, so fixtures below construct them directly.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { lcg, slide, newGame, move, play, render, gameOver } from './game2048.ts';

import type { Game } from './game2048.ts';

function g(board: number[][], score = 0, state = 3, won = false): Game {
  return { board, score, state, won } as Game;
}

test('lcg follows state = (state * 1103515245 + 12345) mod 2^31 exactly', () => {
  assert.equal(lcg(1), 1103527590);
  assert.equal(lcg(42), 1250496027);
  assert.equal(lcg(1250496027), 1116302264);
  assert.equal(lcg(2147483647), 1043980748); // the multiply must not lose precision
});

test('slide compacts toward index 0 and merges adjacent equals once', () => {
  assert.deepEqual(slide([2, 0, 0, 2]), { line: [4, 0, 0, 0], gained: 4 });
  assert.deepEqual(slide([0, 0, 0, 2]), { line: [2, 0, 0, 0], gained: 0 });
  assert.deepEqual(slide([2, 2, 2, 2]), { line: [4, 4, 0, 0], gained: 8 });
  assert.deepEqual(slide([4, 4, 2, 2]), { line: [8, 4, 0, 0], gained: 12 });
  assert.deepEqual(slide([4, 2, 2, 0]), { line: [4, 4, 0, 0], gained: 4 });
  assert.deepEqual(slide([2, 2, 4, 0]), { line: [4, 4, 0, 0], gained: 4 }); // merged 4 never re-merges
  assert.deepEqual(slide([2, 4, 2, 2]), { line: [2, 4, 4, 0], gained: 4 });
  assert.deepEqual(slide([0, 0, 0, 0]), { line: [0, 0, 0, 0], gained: 0 });
});

test('a new game spawns exactly two tiles at pinned LCG positions', () => {
  assert.equal(render(newGame(1)), [
    '   .|   .|   .|   .',
    '   2|   .|   2|   .',
    '   .|   .|   .|   .',
    '   .|   .|   .|   .',
  ].join('\n'));
  assert.equal(render(newGame(42)), [
    '   .|   .|   .|   .',
    '   .|   .|   .|   .',
    '   2|   .|   .|   2',
    '   .|   .|   .|   .',
  ].join('\n'));
  const fresh = newGame(42);
  assert.equal(fresh.score, 0);
  assert.equal(fresh.won, false);
  assert.equal(fresh.state, 1668674806); // four draws consumed
});

test('a value draw divisible by ten spawns a 4', () => {
  assert.equal(render(newGame(8)), [
    '   .|   4|   .|   .',
    '   .|   .|   .|   .',
    '   .|   .|   .|   .',
    '   .|   2|   .|   .',
  ].join('\n'));
});

test('move left slides every row toward column 0 and then spawns', () => {
  const after = move(g([[0, 2, 0, 2], [4, 4, 8, 0], [0, 0, 0, 0], [16, 0, 0, 16]], 10, 100), 'left');
  assert.equal(render(after), [
    '   4|   .|   .|   .',
    '   8|   8|   .|   .',
    '   .|   .|   .|   .',
    '  32|   4|   .|   .',
  ].join('\n'));
  assert.equal(after.score, 54); // 10 + 4 + 8 + 32
});

test('move down traverses from the bottom edge; the spawn can be a 4', () => {
  const after = move(g([[0, 2, 0, 2], [4, 4, 8, 0], [0, 0, 0, 0], [16, 0, 0, 16]], 10, 100), 'down');
  assert.equal(render(after), [
    '   .|   .|   .|   4',
    '   .|   .|   .|   .',
    '   4|   2|   .|   2',
    '  16|   4|   8|  16',
  ].join('\n'));
  assert.equal(after.score, 10); // nothing merged
});

test('move right pairs from the right edge', () => {
  const after = move(g([[2, 2, 2, 2], [0, 0, 0, 0], [0, 0, 0, 0], [0, 0, 0, 0]]), 'right');
  assert.equal(render(after), [
    '   .|   .|   4|   4',
    '   2|   .|   .|   .',
    '   .|   .|   .|   .',
    '   .|   .|   .|   .',
  ].join('\n'));
  assert.equal(after.score, 8);
});

test('move up and move down pair a full column from opposite edges', () => {
  const col = () => g([[2, 0, 0, 0], [2, 0, 0, 0], [2, 0, 0, 0], [2, 0, 0, 0]]);
  assert.equal(render(move(col(), 'up')), [
    '   4|   .|   .|   2',
    '   4|   .|   .|   .',
    '   .|   .|   .|   .',
    '   .|   .|   .|   .',
  ].join('\n'));
  assert.equal(render(move(col(), 'down')), [
    '   .|   .|   2|   .',
    '   .|   .|   .|   .',
    '   4|   .|   .|   .',
    '   4|   .|   .|   .',
  ].join('\n'));
});

test('a move that changes nothing is a same-object no-op with no spawn', () => {
  const stuck = g([[2, 4, 8, 16], [0, 0, 0, 0], [0, 0, 0, 0], [0, 0, 0, 0]], 5, 999);
  const after = move(stuck, 'left');
  assert.equal(after, stuck);
  assert.equal(after.state, 999); // rng untouched
});

test('move never mutates its argument', () => {
  const before = g([[0, 2, 0, 2], [4, 4, 8, 0], [0, 0, 0, 0], [16, 0, 0, 16]], 10, 100);
  const snapshot = JSON.stringify(before.board);
  move(before, 'left');
  assert.equal(JSON.stringify(before.board), snapshot);
  assert.equal(before.score, 10);
});

test('creating a 2048 tile sets the sticky won flag', () => {
  const after = move(g([[1024, 1024, 0, 0], [0, 0, 0, 0], [0, 0, 0, 0], [2, 4, 2, 4]], 0, 5), 'left');
  assert.equal(after.won, true);
  assert.equal(after.score, 2048);
  assert.equal(render(after).split('\n')[0], '2048|   .|   .|   .');
  // won stays true on later moves
  const later = move(after, 'right');
  assert.equal(later.won, true);
});

test('gameOver is true only when full with no adjacent equal pair', () => {
  assert.equal(gameOver(newGame(42)), false);
  const dead = g([[2, 4, 8, 16], [16, 8, 4, 2], [2, 4, 8, 16], [16, 8, 4, 2]]);
  assert.equal(gameOver(dead), true);
  const mergeable = g([[2, 4, 8, 16], [16, 8, 4, 2], [2, 4, 8, 16], [16, 8, 8, 2]]);
  assert.equal(gameOver(mergeable), false);
});

test('every move on a dead board is a same-object no-op', () => {
  const dead = g([[2, 4, 8, 16], [16, 8, 4, 2], [2, 4, 8, 16], [16, 8, 4, 2]], 77, 13);
  for (const dir of ['left', 'right', 'up', 'down'] as const) {
    assert.equal(move(dead, dir), dead);
  }
});

test('play replays a move script deterministically', () => {
  const a = play(42, 'LULDRU');
  assert.equal(a.score, 16);
  assert.equal(a.state, 238077914);
  assert.equal(render(a), [
    '   .|   2|   2|   2',
    '   .|   .|   .|   8',
    '   .|   .|   .|   .',
    '   .|   2|   .|   .',
  ].join('\n'));
  const b = play(7, 'DLDLDRUL');
  assert.equal(b.score, 24);
  assert.equal(render(b), [
    '   4|   4|   .|   .',
    '   8|   2|   .|   .',
    '   .|   .|   .|   .',
    '   .|   .|   2|   .',
  ].join('\n'));
});

test('play rejects unknown move letters', () => {
  assert.throws(() => play(1, 'LX'), Error);
});

test('two runs from the same seed are identical', () => {
  const a = play(1234, 'ULDR'.repeat(10));
  const b = play(1234, 'ULDR'.repeat(10));
  assert.equal(render(a), render(b));
  assert.equal(a.score, b.score);
  assert.equal(a.state, b.state);
});
