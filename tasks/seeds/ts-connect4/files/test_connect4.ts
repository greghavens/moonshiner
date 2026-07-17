// Acceptance suite for the Connect Four engine + fixed-depth negamax bot.
// Run: node --test test_connect4.ts
//
// The bot is exactly reproducible: plain negamax at the requested depth,
// win scores weighted by distance (faster wins score higher), a
// center-column leaf heuristic, and ties broken to the lowest column.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  newGame,
  parseBoard,
  drop,
  legalMoves,
  render,
  bestMove,
  selfPlay,
} from './connect4.ts';

function played(cols: number[]) {
  let g = newGame();
  for (const c of cols) g = drop(g, c);
  return g;
}

test('a new game is empty with X to move', () => {
  const g = newGame();
  assert.equal(g.turn, 'X');
  assert.equal(g.winner, null);
  assert.equal(render(g), Array(6).fill('.......').join('\n'));
  assert.deepEqual(legalMoves(g), [0, 1, 2, 3, 4, 5, 6]);
});

test('pieces fall to the lowest free row and turns alternate', () => {
  const g = played([3, 3, 4]);
  assert.equal(render(g), [
    '.......',
    '.......',
    '.......',
    '.......',
    '...O...',
    '...XX..',
  ].join('\n'));
  assert.equal(g.turn, 'O');
});

test('drop rejects bad columns, full columns, and finished games', () => {
  const g = newGame();
  assert.throws(() => drop(g, -1), RangeError);
  assert.throws(() => drop(g, 7), RangeError);
  const full = played([3, 3, 3, 3, 3, 3]);
  assert.throws(() => drop(full, 3), /full/);
  assert.deepEqual(legalMoves(full), [0, 1, 2, 4, 5, 6]);
  const won = played([0, 1, 0, 1, 0, 2, 0]);
  assert.equal(won.winner, 'X');
  assert.throws(() => drop(won, 4), /over/);
  assert.deepEqual(legalMoves(won), []);
});

test('drop is pure: the source position is untouched', () => {
  const g = played([3, 3]);
  const before = render(g);
  drop(g, 0);
  assert.equal(render(g), before);
  assert.equal(g.turn, 'X');
});

test('vertical four in a column wins', () => {
  const g = played([0, 1, 0, 1, 0, 2, 0]);
  assert.equal(g.winner, 'X');
});

test('rising diagonal four wins', () => {
  const g = played([0, 1, 1, 2, 2, 3, 2, 3, 3, 6, 3]);
  assert.equal(g.winner, 'X');
  assert.equal(render(g), [
    '.......',
    '.......',
    '...X...',
    '..XX...',
    '.XXO...',
    'XOOO..O',
  ].join('\n'));
});

test('falling diagonal four wins', () => {
  const g = played([6, 5, 5, 4, 4, 3, 4, 3, 3, 0, 3]);
  assert.equal(g.winner, 'X');
  assert.equal(render(g), [
    '.......',
    '.......',
    '...X...',
    '...XX..',
    '...OXX.',
    'O..OOOX',
  ].join('\n'));
});

test('parseBoard loads a position and derives the side to move', () => {
  const g = parseBoard([
    '.......',
    '.......',
    '.......',
    '.......',
    'OOO....',
    'XXX....',
  ].join('\n'));
  assert.equal(g.turn, 'X');
  assert.equal(g.winner, null);
  const oToMove = parseBoard([
    '.......',
    '.......',
    '.......',
    '.......',
    '.......',
    'X......',
  ].join('\n'));
  assert.equal(oToMove.turn, 'O');
});

test('parseBoard detects an existing horizontal winner', () => {
  const g = parseBoard([
    '.......',
    '.......',
    '.......',
    '.......',
    'OOO....',
    'XXXX...',
  ].join('\n'));
  assert.equal(g.winner, 'X');
});

test('parseBoard rejects malformed fixtures', () => {
  assert.throws(() => parseBoard('...\n...'), /6 lines/);
  assert.throws(() => parseBoard([
    '.......',
    '.......',
    '.......',
    '.......',
    'X......',
    '.......',
  ].join('\n')), /floating/);
  assert.throws(() => parseBoard([
    '.......',
    '.......',
    '.......',
    '.......',
    '.......',
    'OO.X...',
  ].join('\n')), /counts/);
  assert.throws(() => parseBoard([
    '.......',
    '.......',
    '.......',
    '.......',
    '.......',
    'Z......',
  ].join('\n')), /bad cell/);
});

test('the bot takes an immediate win at any depth', () => {
  const g = parseBoard([
    '.......',
    '.......',
    '.......',
    '.......',
    'OOO....',
    'XXX....',
  ].join('\n'));
  assert.equal(bestMove(g, 1), 3);
  assert.equal(bestMove(g, 4), 3);
});

test('the bot blocks a one-move loss when it can see it', () => {
  const g = parseBoard([
    '.......',
    '.......',
    '.......',
    '.......',
    '..X....',
    'XOOO..X',
  ].join('\n'));
  assert.equal(g.turn, 'X');
  assert.equal(bestMove(g, 2), 4);
  assert.equal(bestMove(g, 4), 4);
});

test('the center heuristic steers the opening to column 3', () => {
  assert.equal(bestMove(newGame(), 1), 3);
  assert.equal(bestMove(newGame(), 2), 3);
  assert.equal(bestMove(newGame(), 4), 3);
});

test('with all values tied the bot picks the lowest column', () => {
  // Column 3 is full, so the center heuristic reads zero everywhere.
  const g = played([3, 3, 3, 3, 3, 3]);
  assert.equal(bestMove(g, 1), 0);
});

test('bestMove validates depth and refuses finished games', () => {
  assert.throws(() => bestMove(newGame(), 0), RangeError);
  const won = played([0, 1, 0, 1, 0, 2, 0]);
  assert.throws(() => bestMove(won, 3), /over/);
});

test('bot-vs-bot at depth 1 is a pinned 15-move X win', () => {
  const { moves, game } = selfPlay(1);
  assert.equal(moves.join(','), '3,3,3,3,3,3,0,0,0,0,0,0,1,1,2');
  assert.equal(game.winner, 'X');
  assert.equal(render(game), [
    'O..O...',
    'X..X...',
    'O..O...',
    'X..X...',
    'OO.O...',
    'XXXX...',
  ].join('\n'));
});

test('bot-vs-bot at depth 2 fills the board to a pinned draw', () => {
  const { moves, game } = selfPlay(2);
  assert.equal(moves.length, 42);
  assert.equal(game.winner, 'draw');
  assert.equal(
    moves.join(','),
    '3,3,3,3,3,3,0,0,0,0,0,0,1,2,1,1,1,1,1,2,2,2,2,2,4,4,4,4,4,4,6,5,5,5,5,5,5,6,6,6,6,6',
  );
  assert.equal(render(game), [
    'OXOOOXO',
    'XOXXXOX',
    'OXOOOXO',
    'XOXXXOX',
    'OXOOOXO',
    'XXOXXOX',
  ].join('\n'));
});

test('bot-vs-bot at depth 3 is a pinned 18-move O win', () => {
  const { moves, game } = selfPlay(3);
  assert.equal(moves.join(','), '3,3,3,3,0,0,1,2,0,1,2,0,1,2,0,1,0,2');
  assert.equal(game.winner, 'O');
  assert.equal(render(game), [
    'X......',
    'X......',
    'OOOO...',
    'XXOX...',
    'OOXO...',
    'XXOX...',
  ].join('\n'));
});
