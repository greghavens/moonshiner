// Acceptance suite for the pure-function minesweeper engine.
// Run: node --test test_minesweeper.ts
//
// Boards load from fixture layout strings ('*' mine, '.' safe). Every
// mutator is pure: it returns a new Game and never modifies its argument;
// actions that change nothing return the exact same object.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  newGame,
  parseLayout,
  adjacency,
  reveal,
  mark,
  chord,
  flagsRemaining,
  render,
} from './minesweeper.ts';

// Two mines in opposite corners; every safe cell is one connected region.
const CORNERS = ['*....', '.....', '.....', '....*'].join('\n');
// Two separated mines for chord scenarios.
const CHORDY = ['.*...', '.....', '..*..'].join('\n');
// Tiny board where a win takes exactly two reveals.
const TINY = ['**', '..'].join('\n');

test('parseLayout reads a rectangular grid of . and *', () => {
  const mines = parseLayout('.*\n..\n');
  assert.deepEqual(mines, [[false, true], [false, false]]);
});

test('parseLayout rejects malformed layouts', () => {
  assert.throws(() => parseLayout(''), Error);
  assert.throws(() => parseLayout('\n\n'), Error);
  assert.throws(() => parseLayout('..\n.'), Error);
  assert.throws(() => parseLayout('.x\n..'), Error);
});

test('a fresh board renders fully hidden and is in play', () => {
  const g = newGame(CORNERS);
  assert.equal(g.status, 'playing');
  assert.equal(render(g), ['#####', '#####', '#####', '#####'].join('\n'));
  assert.equal(flagsRemaining(g), 2);
});

test('adjacency counts mines in the 8-neighborhood', () => {
  const g = newGame(CORNERS);
  assert.equal(adjacency(g, 0, 1), 1);
  assert.equal(adjacency(g, 1, 1), 1);
  assert.equal(adjacency(g, 3, 3), 1);
  assert.equal(adjacency(g, 1, 3), 0);
  const t = newGame(TINY);
  assert.equal(adjacency(t, 1, 0), 2);
  assert.equal(adjacency(t, 1, 1), 2);
});

test('coordinates outside the board throw RangeError', () => {
  const g = newGame(TINY);
  assert.throws(() => reveal(g, -1, 0), RangeError);
  assert.throws(() => reveal(g, 0, 2), RangeError);
  assert.throws(() => mark(g, 2, 0), RangeError);
  assert.throws(() => chord(g, 0, 5), RangeError);
  assert.throws(() => adjacency(g, 5, 0), RangeError);
});

test('revealing a numbered cell exposes only that cell', () => {
  const g = reveal(newGame(CORNERS), 0, 1);
  assert.equal(render(g), ['#1###', '#####', '#####', '#####'].join('\n'));
  assert.equal(g.status, 'playing');
});

test('revealing a zero cell flood-fills through the region', () => {
  const g = reveal(newGame(CORNERS), 1, 3);
  assert.equal(render(g), ['#1...', '11...', '...11', '...1#'].join('\n'));
});

test('reveal is pure: the original game is untouched', () => {
  const g0 = newGame(CORNERS);
  const before = render(g0);
  const g1 = reveal(g0, 1, 3);
  assert.notEqual(g1, g0);
  assert.equal(render(g0), before);
  assert.equal(g0.status, 'playing');
});

test('flood-fill never reveals a flagged cell', () => {
  let g = mark(newGame(CORNERS), 2, 2);
  g = reveal(g, 1, 3);
  assert.equal(render(g), ['#1...', '11...', '..F11', '...1#'].join('\n'));
  assert.equal(g.status, 'playing');
  assert.equal(flagsRemaining(g), 1);
});

test('flood-fill reveals question-marked cells and clears the mark', () => {
  let g = mark(mark(newGame(CORNERS), 2, 2), 2, 2);
  assert.equal(render(g).split('\n')[2], '##?##');
  g = reveal(g, 1, 3);
  assert.equal(render(g), ['#1...', '11...', '...11', '...1#'].join('\n'));
});

test('revealing a flagged cell is a no-op returning the same object', () => {
  const g = mark(newGame(CORNERS), 0, 0);
  assert.equal(reveal(g, 0, 0), g);
});

test('mark cycles none -> flag -> question -> none on hidden cells', () => {
  const g0 = newGame(CORNERS);
  const g1 = mark(g0, 1, 1);
  const g2 = mark(g1, 1, 1);
  const g3 = mark(g2, 1, 1);
  assert.equal(render(g1).split('\n')[1], '#F###');
  assert.equal(render(g2).split('\n')[1], '#?###');
  assert.equal(render(g3).split('\n')[1], '#####');
  assert.equal(flagsRemaining(g1), 1);
  assert.equal(flagsRemaining(g2), 2);
});

test('marking a revealed cell is a no-op returning the same object', () => {
  const g = reveal(newGame(CORNERS), 0, 1);
  assert.equal(mark(g, 0, 1), g);
});

test('a question-marked cell can still be revealed', () => {
  let g = mark(mark(newGame(CORNERS), 0, 1), 0, 1);
  g = reveal(g, 0, 1);
  assert.equal(render(g).split('\n')[0], '#1###');
});

test('flagsRemaining goes negative when overflagged', () => {
  let g = newGame(TINY);
  g = mark(g, 0, 0);
  g = mark(g, 0, 1);
  g = mark(g, 1, 0);
  assert.equal(flagsRemaining(g), -1);
});

test('revealing a mine loses; boom cell is !, other mines *', () => {
  const g = reveal(newGame(CORNERS), 3, 4);
  assert.equal(g.status, 'lost');
  assert.deepEqual(g.boom, [3, 4]);
  assert.equal(render(g), ['*####', '#####', '#####', '####!'].join('\n'));
});

test('lost render marks wrong flags X and keeps question marks hidden info', () => {
  let g = mark(newGame(CORNERS), 1, 1); // flag on a safe cell
  g = mark(mark(g, 0, 0), 0, 0); // question the (0,0) mine
  g = reveal(g, 3, 4);
  assert.equal(g.status, 'lost');
  assert.equal(render(g), ['*####', '#X###', '#####', '####!'].join('\n'));
});

test('after the game is over every action is a same-object no-op', () => {
  const lost = reveal(newGame(CORNERS), 3, 4);
  assert.equal(reveal(lost, 0, 1), lost);
  assert.equal(mark(lost, 1, 1), lost);
  assert.equal(chord(lost, 1, 1), lost);
});

test('revealing every safe cell wins regardless of flags', () => {
  let g = mark(newGame(TINY), 0, 0); // one correct flag, one mine unflagged
  g = reveal(g, 1, 0);
  assert.equal(g.status, 'playing');
  assert.equal(render(g), ['F#', '2#'].join('\n'));
  g = reveal(g, 1, 1);
  assert.equal(g.status, 'won');
  assert.equal(render(g), ['F#', '22'].join('\n'));
});

test('a single zero-region reveal can win outright', () => {
  const g = reveal(newGame(CORNERS), 1, 3);
  assert.equal(g.status, 'won');
});

test('chord on a satisfied number reveals the unflagged neighbors', () => {
  let g = reveal(newGame(CHORDY), 1, 0);
  assert.equal(render(g), ['#####', '1####', '#####'].join('\n'));
  g = mark(g, 0, 1);
  g = chord(g, 1, 0);
  assert.equal(g.status, 'playing');
  assert.equal(render(g), ['1F###', '12###', '.1###'].join('\n'));
});

test('chord with the wrong number of flags is a same-object no-op', () => {
  const g = reveal(newGame(CHORDY), 1, 0);
  assert.equal(chord(g, 1, 0), g); // zero flags placed
  const two = mark(mark(g, 0, 0), 0, 1); // two flags around a 1
  assert.equal(chord(two, 1, 0), two);
});

test('question marks do not count toward chord satisfaction', () => {
  let g = reveal(newGame(CHORDY), 1, 0);
  g = mark(mark(g, 0, 1), 0, 1); // question on the actual mine
  assert.equal(chord(g, 1, 0), g);
});

test('chord on a hidden cell or a zero cell is a same-object no-op', () => {
  const g0 = newGame(CHORDY);
  assert.equal(chord(g0, 1, 0), g0); // hidden
  const g1 = reveal(g0, 0, 3); // opens the zero region on the right
  assert.equal(g1.status, 'playing');
  assert.equal(chord(g1, 0, 4), g1); // revealed but adjacency 0
});

test('chording over a wrong flag hits the mine in reading order', () => {
  let g = reveal(newGame(CHORDY), 1, 0);
  g = mark(g, 0, 0); // flag the safe corner instead of the mine
  g = chord(g, 1, 0);
  assert.equal(g.status, 'lost');
  assert.deepEqual(g.boom, [0, 1]);
  assert.equal(render(g), ['X!###', '1####', '##*##'].join('\n'));
});

test('chord is pure: source position survives a losing chord', () => {
  let g = reveal(newGame(CHORDY), 1, 0);
  g = mark(g, 0, 0);
  const boom = chord(g, 1, 0);
  assert.equal(boom.status, 'lost');
  assert.equal(g.status, 'playing');
  assert.equal(render(g), ['F####', '1####', '#####'].join('\n'));
});

test('a chord can finish the board and win', () => {
  // Flag both mines on CHORDY, open the left block with a chord, then
  // finish the right side; the final board keeps its flags.
  let g = newGame(CHORDY);
  g = mark(g, 0, 1);
  g = mark(g, 2, 2);
  g = reveal(g, 1, 0);
  g = chord(g, 1, 0); // opens (0,0), (1,1) and floods (2,0)-(2,1)
  g = reveal(g, 0, 2);
  g = reveal(g, 1, 2);
  g = reveal(g, 0, 3); // zero: floods the whole right side
  assert.equal(g.status, 'won');
  assert.equal(render(g), ['1F1..', '1221.', '.1F1.'].join('\n'));
});
