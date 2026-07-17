// Acceptance suite for the two-player concentration engine.
// Run: node --test test_memory.ts
//
// Layouts are fixtures (each uppercase letter exactly twice). flip() is a
// pure transition; a match keeps the same player on the clock, a miss
// passes the turn. Renders and full flip-script transcripts are pinned.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { newGame, flip, isOver, winner, render, peek, transcript } from './memory.ts';

const TINY = 'AB\nBA';
const QUAD = 'ABCD\nBADC';

test('newGame validates its fixture layout', () => {
  assert.throws(() => newGame(''), /empty/);
  assert.throws(() => newGame('AB\nB'), /ragged/);
  assert.throws(() => newGame('Ab\nBA'), /bad card symbol/);
  assert.throws(() => newGame('AB\nBB'), /appears/); // A once, B three times
  assert.throws(() => newGame('AA\nAA'), /appears/);
  const g = newGame('AB\nBA\n'); // trailing newline is fine
  assert.equal(g.rows, 2);
  assert.equal(g.cols, 2);
});

test('a fresh game is all face-down with P1 on the clock', () => {
  const g = newGame(QUAD);
  assert.equal(render(g), '####\n####');
  assert.equal(g.current, 1);
  assert.deepEqual(g.scores, [0, 0]);
  assert.equal(g.pairsDone, 0);
  assert.equal(g.up, null);
  assert.equal(isOver(g), false);
  assert.equal(winner(g), null);
});

test('the first flip of a turn shows the card face up', () => {
  const g = flip(newGame(QUAD), 0, 2);
  assert.equal(render(g), '##C#\n####');
  assert.deepEqual(g.up, [0, 2]);
  assert.equal(g.current, 1);
});

test('a match banks the pair and the same player goes again', () => {
  let g = flip(newGame(QUAD), 0, 0);
  g = flip(g, 1, 1);
  assert.equal(g.current, 1); // match = go again
  assert.deepEqual(g.scores, [1, 0]);
  assert.equal(g.pairsDone, 1);
  assert.equal(g.up, null);
  assert.equal(render(g), 'a###\n#a##');
});

test('a miss hides both cards and passes the turn', () => {
  let g = flip(newGame(QUAD), 0, 0);
  g = flip(g, 0, 1);
  assert.equal(g.current, 2);
  assert.deepEqual(g.scores, [0, 0]);
  assert.equal(g.pairsDone, 1);
  assert.equal(g.up, null);
  assert.equal(render(g), '####\n####');
});

test('flip is pure: the source state never changes', () => {
  const g0 = newGame(QUAD);
  const g1 = flip(g0, 0, 0);
  assert.equal(render(g0), '####\n####');
  assert.equal(g0.up, null);
  const g2 = flip(g1, 1, 1);
  assert.deepEqual(g1.up, [0, 0]);
  assert.deepEqual(g1.scores, [0, 0]);
  assert.deepEqual(g2.scores, [1, 0]);
});

test('flip validation: bounds, matched cards, and the same card twice', () => {
  const g = newGame(QUAD);
  assert.throws(() => flip(g, -1, 0), RangeError);
  assert.throws(() => flip(g, 0, 4), RangeError);
  const up = flip(g, 0, 0);
  assert.throws(() => flip(up, 0, 0), /face up/);
  const banked = flip(up, 1, 1);
  assert.throws(() => flip(banked, 1, 1), /matched/);
});

test('the game ends when the last pair is banked', () => {
  let g = newGame(TINY);
  g = flip(g, 0, 0);
  g = flip(g, 1, 1); // A pair, P1
  g = flip(g, 0, 1);
  g = flip(g, 1, 0); // B pair, P1 again
  assert.equal(isOver(g), true);
  assert.equal(winner(g), 1);
  assert.deepEqual(g.scores, [2, 0]);
  assert.equal(render(g), 'ab\nba');
  assert.throws(() => flip(g, 0, 0), /over/);
});

test('peek shows every face for the harness, matched pairs lowercase', () => {
  let g = newGame(QUAD);
  assert.equal(peek(g), 'ABCD\nBADC');
  g = flip(flip(g, 0, 0), 1, 1);
  assert.equal(peek(g), 'aBCD\nBaDC');
});

test('turn and score bookkeeping across alternating misses', () => {
  let g = newGame(QUAD);
  g = flip(flip(g, 0, 0), 0, 1); // P1 miss
  g = flip(flip(g, 0, 2), 0, 3); // P2 miss
  g = flip(flip(g, 1, 0), 1, 1); // P1 miss (B vs A)
  assert.equal(g.current, 2);
  assert.equal(g.pairsDone, 3);
  assert.deepEqual(g.scores, [0, 0]);
});

test('a full scripted game produces the pinned transcript', () => {
  const t = transcript(QUAD, [
    [0, 0], [1, 1], // P1 banks A
    [0, 1], [0, 2], // P1 misses B/C
    [1, 0], [0, 1], // P2 banks B
    [0, 2], [1, 3], // P2 banks C
    [0, 3], [1, 2], // P2 banks D
  ]);
  assert.equal(t, [
    'P1 flips (0,0)=A',
    'P1 flips (1,1)=A',
    'P1 match A -> 1',
    'P1 flips (0,1)=B',
    'P1 flips (0,2)=C',
    'no match -> P2',
    'P2 flips (1,0)=B',
    'P2 flips (0,1)=B',
    'P2 match B -> 1',
    'P2 flips (0,2)=C',
    'P2 flips (1,3)=C',
    'P2 match C -> 2',
    'P2 flips (0,3)=D',
    'P2 flips (1,2)=D',
    'P2 match D -> 3',
    'final P1=1 P2=3',
    'winner: P2',
  ].join('\n'));
});

test('a drawn game closes with winner: tie', () => {
  const t = transcript(QUAD, [
    [0, 0], [1, 1], // P1 banks A
    [0, 2], [1, 3], // P1 banks C
    [0, 1], [0, 3], // P1 misses B/D
    [1, 0], [0, 1], // P2 banks B
    [0, 3], [1, 2], // P2 banks D
  ]);
  const lines = t.split('\n');
  assert.equal(lines[lines.length - 2], 'final P1=2 P2=2');
  assert.equal(lines[lines.length - 1], 'winner: tie');
});

test('a transcript can stop mid-game without final lines', () => {
  const t = transcript(TINY, [[0, 0], [0, 1]]);
  assert.equal(t, [
    'P1 flips (0,0)=A',
    'P1 flips (0,1)=B',
    'no match -> P2',
  ].join('\n'));
});

test('transcript surfaces illegal flips as errors', () => {
  assert.throws(() => transcript(TINY, [[0, 0], [0, 0]]), /face up/);
  assert.throws(() => transcript(TINY, [[5, 0]]), RangeError);
});
