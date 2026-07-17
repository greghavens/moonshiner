import { test } from 'node:test';
import assert from 'node:assert/strict';
import { MessageFormat, MessageFormatError, pluralCategory } from './msgfmt.ts';

function fmt(src: string, values?: Record<string, unknown>, locale = 'en'): string {
  return new MessageFormat(src, locale).format(values);
}

function parseError(src: string, locale = 'en'): MessageFormatError {
  try {
    new MessageFormat(src, locale);
  } catch (err) {
    assert.ok(err instanceof MessageFormatError, `expected MessageFormatError, got ${err}`);
    return err;
  }
  throw new Error(`expected a parse error for: ${src}`);
}

function expectParseError(src: string, line: number, col: number, detail: string) {
  const err = parseError(src);
  assert.equal(err.message, `parse error at ${line}:${col}: ${detail}`);
  assert.equal(err.line, line);
  assert.equal(err.col, col);
}

// ---------- plain text and simple arguments ----------

test('plain text passes through untouched', () => {
  assert.equal(fmt('Deploy finished.'), 'Deploy finished.');
  assert.equal(fmt(''), '');
});

test('simple argument substitution with String() coercion', () => {
  assert.equal(fmt('Hello {name}!', { name: 'Ana' }), 'Hello Ana!');
  assert.equal(fmt('{a} and {b}', { a: 1, b: true }), '1 and true');
  assert.equal(fmt('{ padded }', { padded: 'x' }), 'x');
});

test('# is ordinary text outside any plural', () => {
  assert.equal(fmt('#1 fan of {name}', { name: 'Ana' }), '#1 fan of Ana');
});

// ---------- quoting ----------

test('doubled apostrophe is a literal apostrophe', () => {
  assert.equal(fmt("It''s ready"), "It's ready");
});

test('lone apostrophes in ordinary text stay literal', () => {
  assert.equal(fmt("don't stop"), "don't stop");
});

test('quoted braces are literal', () => {
  assert.equal(fmt("'{name}'", { name: 'nope' }), '{name}');
  assert.equal(fmt("'{'{a}'}'", { a: 'X' }), '{X}');
});

test('quoted # inside a plural is literal while bare # is live', () => {
  assert.equal(
    fmt("{n, plural, other {rank '#'#}}", { n: 3 }),
    'rank #3',
  );
});

// ---------- plural: en ----------

test('en plural picks one/other', () => {
  const src = '{n, plural, one {# file} other {# files}}';
  assert.equal(fmt(src, { n: 1 }), '1 file');
  assert.equal(fmt(src, { n: 0 }), '0 files');
  assert.equal(fmt(src, { n: 7 }), '7 files');
});

test('exact =N selectors beat categories', () => {
  const src = '{n, plural, =0 {no files} =1 {just one} one {ONE} other {# files}}';
  assert.equal(fmt(src, { n: 0 }), 'no files');
  assert.equal(fmt(src, { n: 1 }), 'just one');
  assert.equal(fmt(src, { n: 2 }), '2 files');
});

test('a missing category branch falls back to other', () => {
  // pl classifies 2 as "few", but the message only ships one/other
  assert.equal(
    fmt('{n, plural, one {plik} other {plikow}}', { n: 2 }, 'pl'),
    'plikow',
  );
});

// ---------- plural rules table: pl ----------

test('pl plural walk through a full message', () => {
  const src = 'Masz {n, plural, one {# plik} few {# pliki} many {# plikow} other {# pliku}}';
  const cases: Array<[number, string]> = [
    [1, 'Masz 1 plik'],
    [2, 'Masz 2 pliki'],
    [4, 'Masz 4 pliki'],
    [22, 'Masz 22 pliki'],
    [34, 'Masz 34 pliki'],
    [0, 'Masz 0 plikow'],
    [5, 'Masz 5 plikow'],
    [12, 'Masz 12 plikow'],
    [13, 'Masz 13 plikow'],
    [14, 'Masz 14 plikow'],
    [112, 'Masz 112 plikow'],
    [1.5, 'Masz 1.5 pliku'],
  ];
  for (const [n, want] of cases) {
    assert.equal(fmt(src, { n }, 'pl'), want, `n=${n}`);
  }
});

// ---------- plural rules table: ar (via pluralCategory) ----------

test('ar categories follow the CLDR-lite table', () => {
  const cases: Array<[number, string]> = [
    [0, 'zero'], [1, 'one'], [2, 'two'],
    [3, 'few'], [10, 'few'], [103, 'few'],
    [11, 'many'], [26, 'many'], [99, 'many'],
    [100, 'other'], [101, 'other'], [102, 'other'], [200, 'other'],
  ];
  for (const [n, want] of cases) {
    assert.equal(pluralCategory('ar', n), want, `n=${n}`);
  }
});

test('an ar message renders through all six branches', () => {
  const src = '{n, plural, zero {empty} one {single} two {pair} few {# few} many {# many} other {# other}}';
  assert.equal(fmt(src, { n: 0 }, 'ar'), 'empty');
  assert.equal(fmt(src, { n: 1 }, 'ar'), 'single');
  assert.equal(fmt(src, { n: 2 }, 'ar'), 'pair');
  assert.equal(fmt(src, { n: 7 }, 'ar'), '7 few');
  assert.equal(fmt(src, { n: 45 }, 'ar'), '45 many');
  assert.equal(fmt(src, { n: 100 }, 'ar'), '100 other');
});

test('pluralCategory handles subtags, case, negatives and non-integers', () => {
  assert.equal(pluralCategory('en', 1), 'one');
  assert.equal(pluralCategory('EN', 1), 'one');
  assert.equal(pluralCategory('pl-PL', 22), 'few');
  assert.equal(pluralCategory('pl', -3), 'few');   // classified by absolute value
  assert.equal(pluralCategory('pl', 1.5), 'other');
  assert.equal(pluralCategory('en', 1.5), 'other');
  assert.equal(pluralCategory('ar', 2.5), 'other');
});

test('unsupported locales throw, quoting the locale as passed', () => {
  assert.throws(() => pluralCategory('de', 1), { message: 'unsupported locale "de"' });
  assert.throws(() => new MessageFormat('hi', 'fr-FR'), {
    message: 'unsupported locale "fr-FR"',
  });
});

// ---------- select ----------

test('select matches String(value) and falls back to other', () => {
  const src = '{gender, select, male {his} female {her} other {their}} desk';
  assert.equal(fmt(src, { gender: 'female' }), 'her desk');
  assert.equal(fmt(src, { gender: 'male' }), 'his desk');
  assert.equal(fmt(src, { gender: 'nonbinary' }), 'their desk');
});

test('select coerces non-string values with String()', () => {
  const src = '{code, select, 404 {missing} other {code {code}}}';
  assert.equal(fmt(src, { code: 404 }), 'missing');
  assert.equal(fmt(src, { code: 500 }), 'code 500');
});

// ---------- nesting ----------

test('plural nests inside select', () => {
  const src =
    '{gender, select, female {She has {n, plural, one {# item} other {# items}}} ' +
    'other {They have {n, plural, one {# item} other {# items}}}}';
  assert.equal(fmt(src, { gender: 'female', n: 1 }), 'She has 1 item');
  assert.equal(fmt(src, { gender: 'x', n: 5 }), 'They have 5 items');
});

test('# stays live inside a select nested in a plural', () => {
  const src =
    '{count, plural, one {{gender, select, male {his # file} other {their # file}}} ' +
    'other {{gender, select, male {his # files} other {their # files}}}}';
  assert.equal(fmt(src, { count: 1, gender: 'male' }), 'his 1 file');
  assert.equal(fmt(src, { count: 3, gender: 'they' }), 'their 3 files');
});

test('a nested plural rebinds # to its own value', () => {
  const src = '{outer, plural, other {{inner, plural, other {#}}/#}}';
  assert.equal(fmt(src, { outer: 7, inner: 2 }), '2/7');
});

test('# is plain text inside a select with no plural ancestor', () => {
  assert.equal(fmt('{k, select, a {#1} other {#2}}', { k: 'a' }), '#1');
});

// ---------- whitespace tolerance ----------

test('whitespace around names, commas and selectors is free', () => {
  assert.equal(
    fmt('{ n , plural , one {x} other {y} }', { n: 1 }),
    'x',
  );
  assert.equal(fmt('{n, plural, one{x}other{y}}', { n: 2 }), 'y');
});

// ---------- parse errors: exact details and positions ----------

test('unclosed argument reports expected } at end of input', () => {
  expectParseError('{name', 1, 6, "expected '}'");
});

test('junk after a simple argument name', () => {
  expectParseError('{name!}', 1, 6, "expected '}'");
});

test('empty argument name', () => {
  expectParseError('{, plural, one {a} other {b}}', 1, 2, 'empty argument name');
});

test('unknown argument type', () => {
  expectParseError('{x, chart, a {b}}', 1, 5, 'unknown argument type "chart"');
});

test('plural with no branch list', () => {
  expectParseError('{n, plural}', 1, 11, "expected ','");
});

test('invalid plural selector', () => {
  expectParseError('{n, plural, bogus {a} other {b}}', 1, 13, 'invalid selector "bogus"');
});

test('exact selectors must be =digits', () => {
  expectParseError('{n, plural, =1.5 {a} other {b}}', 1, 13, 'invalid selector "=1.5"');
});

test('duplicate selector', () => {
  expectParseError('{n, plural, one {a} one {b} other {c}}', 1, 21, 'duplicate selector "one"');
});

test('selector without a branch body', () => {
  expectParseError('{n, plural, one other {b}}', 1, 17, "expected '{' after selector");
});

test('plural without other', () => {
  expectParseError('{n, plural, one {a}}', 1, 1, 'plural requires an "other" branch');
});

test('select without other', () => {
  expectParseError('{g, select, male {a}}', 1, 1, 'select requires an "other" branch');
});

test('stray closing brace in text', () => {
  expectParseError('Bye }', 1, 5, "unexpected '}'");
});

test('unclosed quoted literal points at the opening apostrophe', () => {
  expectParseError("it is '{ broken", 1, 7, 'unclosed quoted literal');
});

test('positions track line breaks (1-based line and column)', () => {
  expectParseError(
    'You have\n  {n, plural, one {# item} more {x} other {# items}}',
    2, 28, 'invalid selector "more"',
  );
});

test('unclosed branch body reports at end of input', () => {
  expectParseError('{n, plural, other {dangling', 1, 28, "expected '}'");
});

test('MessageFormatError is an Error with line/col fields', () => {
  const err = parseError('{oops');
  assert.ok(err instanceof Error);
  assert.equal(typeof err.line, 'number');
  assert.equal(typeof err.col, 'number');
});

// ---------- format-time errors ----------

test('missing variables throw unknown variable', () => {
  assert.throws(() => fmt('Hello {who}'), { message: 'unknown variable "who"' });
  assert.throws(() => fmt('{n, plural, one {x} other {y}}', {}), {
    message: 'unknown variable "n"',
  });
  assert.throws(() => fmt('{g, select, a {x} other {y}}', {}), {
    message: 'unknown variable "g"',
  });
});

test('plural arguments must be finite numbers', () => {
  assert.throws(() => fmt('{n, plural, one {x} other {y}}', { n: 'many' }), {
    message: 'plural argument "n" must be a number',
  });
  assert.throws(() => fmt('{n, plural, one {x} other {y}}', { n: Infinity }), {
    message: 'plural argument "n" must be a number',
  });
});

test('format defaults to an empty values object', () => {
  assert.equal(fmt('static text'), 'static text');
});
