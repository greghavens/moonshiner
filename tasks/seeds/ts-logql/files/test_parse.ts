import { test } from 'node:test';
import assert from 'node:assert/strict';
import { parse, ParseError } from './parse.ts';

function failsAt(query: string, position: number, pattern?: RegExp): void {
  try {
    parse(query);
  } catch (err) {
    assert.ok(err instanceof ParseError, `expected ParseError, got ${String(err)}`);
    assert.equal(err.position, position, `wrong position for: ${query}`);
    assert.match(err.message, new RegExp(`position ${position}\\b`));
    if (pattern) assert.match(err.message, pattern);
    return;
  }
  assert.fail(`expected a parse error for: ${query}`);
}

test('well-formed queries parse', () => {
  for (const q of [
    'level = "error"',
    'status >= 500 AND NOT service = "billing"',
    '(a = 1 OR b = 2) AND c != 3',
    'msg ~ "Timeout" OR retry = true',
    'delta >= -1.5',
    'level = "error" | stats count',
    'status >= 200 | stats avg(duration) by service',
  ]) {
    assert.ok(parse(q));
  }
});

test('a missing value is reported one past the end of input', () => {
  failsAt('level = ', 9);
});

test('a doubled operator points at the stray token', () => {
  failsAt('level == "x"', 8);
});

test('an unclosed group is reported at end of input', () => {
  failsAt('(a = 1 OR b = 2', 16);
});

test('trailing junk is an error at its own position', () => {
  failsAt('a = 1 )', 7);
});

test('keywords are uppercase; a lowercase and is just a stray identifier', () => {
  failsAt('a = 1 and b = 2', 7);
});

test('an unterminated string points at its opening quote', () => {
  failsAt('msg ~ "boom', 7, /string/);
});

test('unsupported characters are rejected where they appear', () => {
  failsAt('a = 1 && b = 2', 7);
});

test('a query cannot start at the pipe', () => {
  failsAt('| stats count', 1);
});

test('a comparison needs a field name first', () => {
  failsAt('= 5', 1);
});

test('only stats may follow the pipe', () => {
  failsAt('a = 1 | max(x)', 9, /stats/);
});

test('unknown aggregate functions are named', () => {
  failsAt('level = "x" | stats blah(y)', 21, /blah/);
});

test('aggregates other than count need a parenthesized field', () => {
  failsAt('a = 1 | stats sum', 18);
});

test('by needs a field name', () => {
  failsAt('a = 1 | stats count by', 23);
});
