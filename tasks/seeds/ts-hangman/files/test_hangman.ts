// Acceptance suite for the hangman engine.
// Run: node --test test_hangman.ts
//
// Answers come from the fixture list words.txt. The gallows ladder, the
// no-penalty repeated-guess rule, phrase masking (spaces pre-revealed) and
// full win/loss transcripts are all pinned.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import {
  MAX_MISSES,
  loadWords,
  newGame,
  guess,
  mask,
  gallows,
  transcript,
} from './hangman.ts';

const WORDS = loadWords(readFileSync(new URL('./words.txt', import.meta.url), 'utf8'));

function missTimes(g: ReturnType<typeof newGame>, letters: string) {
  for (const ch of letters) g = guess(g, ch);
  return g;
}

test('the fixture word list loads in file order', () => {
  assert.deepEqual(WORDS, [
    'alpaca',
    'bandsaw',
    'copper kettle',
    'drizzle',
    'open door',
    'quartz',
  ]);
});

test('loadWords validates entries and skips blank lines', () => {
  assert.deepEqual(loadWords('one\n\ntwo words\n'), ['one', 'two words']);
  assert.throws(() => loadWords(''), /empty/);
  assert.throws(() => loadWords('Fine\n'), /bad word list entry/);
  assert.throws(() => loadWords('two  spaces'), /bad word list entry/);
  assert.throws(() => loadWords(' padded'), /bad word list entry/);
  assert.throws(() => loadWords('nope!'), /bad word list entry/);
});

test('newGame accepts words and phrases, rejects anything else', () => {
  assert.equal(newGame('quartz').status, 'playing');
  assert.equal(newGame('open door').status, 'playing');
  assert.throws(() => newGame('Quartz'), /bad answer/);
  assert.throws(() => newGame('bad  gap'), /bad answer/);
  assert.throws(() => newGame(''), /bad answer/);
});

test('a fresh word masks to underscores; phrases pre-reveal their spaces', () => {
  assert.equal(mask(newGame('quartz')), '______');
  assert.equal(mask(newGame('open door')), '____ ____');
  assert.equal(mask(newGame('copper kettle')), '______ ______');
});

test('a hit reveals every occurrence of the letter', () => {
  const g = guess(newGame('copper kettle'), 'e');
  assert.equal(mask(g), '____e_ _e___e');
  assert.equal(g.misses, 0);
  const g2 = guess(g, 'p');
  assert.equal(mask(g2), '__ppe_ _e___e');
});

test('a miss climbs the gallows ladder without touching the mask', () => {
  const g = guess(newGame('quartz'), 'e');
  assert.equal(g.misses, 1);
  assert.equal(mask(g), '______');
  assert.deepEqual(g.guessed, ['e']);
});

test('guess is pure and validates its input', () => {
  const g0 = newGame('quartz');
  const g1 = guess(g0, 'q');
  assert.equal(mask(g0), '______');
  assert.deepEqual(g0.guessed, []);
  assert.equal(mask(g1), 'q_____');
  assert.throws(() => guess(g0, 'ab'), /single lowercase/);
  assert.throws(() => guess(g0, 'Q'), /single lowercase/);
  assert.throws(() => guess(g0, '3'), /single lowercase/);
  assert.throws(() => guess(g0, ''), /single lowercase/);
});

test('a repeated guess is free: same object back, hit or miss alike', () => {
  let g = guess(newGame('quartz'), 'q'); // hit
  g = guess(g, 'x'); // miss
  assert.equal(guess(g, 'q'), g);
  assert.equal(guess(g, 'x'), g);
  assert.equal(g.misses, 1);
  assert.deepEqual(g.guessed, ['q', 'x']);
});

test('the seven gallows stages render exactly', () => {
  const stages = [
    '+---+\n|   |\n|\n|\n|\n=====',
    '+---+\n|   |\n|   o\n|\n|\n=====',
    '+---+\n|   |\n|   o\n|   |\n|\n=====',
    '+---+\n|   |\n|   o\n|  /|\n|\n=====',
    '+---+\n|   |\n|   o\n|  /|\\\n|\n=====',
    '+---+\n|   |\n|   o\n|  /|\\\n|  /\n=====',
    '+---+\n|   |\n|   o\n|  /|\\\n|  / \\\n=====',
  ];
  let g = newGame('drizzle');
  const wrong = 'abcstu';
  assert.equal(gallows(g), stages[0]);
  for (let i = 0; i < wrong.length; i++) {
    g = guess(g, wrong[i]);
    assert.equal(g.misses, i + 1);
    assert.equal(gallows(g), stages[i + 1]);
  }
  assert.equal(g.status, 'lost');
});

test('revealing the last letter wins; spaces never need guessing', () => {
  let g = newGame('open door');
  for (const ch of 'oenprd') g = guess(g, ch);
  assert.equal(g.status, 'won');
  assert.equal(mask(g), 'open door');
});

test('the sixth miss loses and further guesses throw', () => {
  const g = missTimes(newGame('quartz'), 'bcdefg');
  assert.equal(g.misses, MAX_MISSES);
  assert.equal(g.status, 'lost');
  assert.equal(mask(g), '______');
  assert.throws(() => guess(g, 'q'), /over/);
});

test('a winning transcript is pinned line for line', () => {
  assert.equal(transcript('quartz', 'qxuayrbtcz'), [
    'guess q: hit q_____',
    'guess x: miss 1/6 q_____',
    'guess u: hit qu____',
    'guess a: hit qua___',
    'guess y: miss 2/6 qua___',
    'guess r: hit quar__',
    'guess b: miss 3/6 quar__',
    'guess t: hit quart_',
    'guess c: miss 4/6 quart_',
    'guess z: hit quartz',
    'won quartz',
  ].join('\n'));
});

test('a phrase transcript keeps its spaces visible throughout', () => {
  assert.equal(transcript('open door', 'oexnprd'), [
    'guess o: hit o___ _oo_',
    'guess e: hit o_e_ _oo_',
    'guess x: miss 1/6 o_e_ _oo_',
    'guess n: hit o_en _oo_',
    'guess p: hit open _oo_',
    'guess r: hit open _oor',
    'guess d: hit open door',
    'won open door',
  ].join('\n'));
});

test('a losing transcript ends at the sixth miss and ignores the rest', () => {
  assert.equal(transcript('drizzle', 'abcstuvw'), [
    'guess a: miss 1/6 _______',
    'guess b: miss 2/6 _______',
    'guess c: miss 3/6 _______',
    'guess s: miss 4/6 _______',
    'guess t: miss 5/6 _______',
    'guess u: miss 6/6 _______',
    'lost drizzle',
  ].join('\n'));
});

test('repeats show in transcripts without advancing anything', () => {
  assert.equal(transcript('alpaca', 'axxlpc'), [
    'guess a: hit a__a_a',
    'guess x: miss 1/6 a__a_a',
    'guess x: repeat a__a_a',
    'guess l: hit al_a_a',
    'guess p: hit alpa_a',
    'guess c: hit alpaca',
    'won alpaca',
  ].join('\n'));
});
