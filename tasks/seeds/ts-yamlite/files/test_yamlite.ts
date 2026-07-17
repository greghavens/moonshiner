// Acceptance tests for the YAML-subset loader (yamlite.ts).
//
// The loader is a from-scratch, dependency-free parser for the block-style
// YAML subset our workflow tooling emits. Indentation drives structure;
// every parse error must carry a 1-based line and column.
//
// Run: node --test test_yamlite.ts

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { parse, YamlParseError } from './yamlite.ts';

function fails(text: string): YamlParseError {
  try {
    parse(text);
  } catch (err) {
    assert.ok(err instanceof YamlParseError, `expected YamlParseError, got ${err}`);
    return err;
  }
  throw new Error('expected a YamlParseError, nothing was thrown');
}

function failsAt(text: string, line: number, col: number, detail: string): void {
  const err = fails(text);
  assert.equal(err.line, line, `line (message: ${err.message})`);
  assert.equal(err.col, col, `col (message: ${err.message})`);
  assert.ok(err.message.startsWith(`line ${line}, col ${col}: `),
    `message must start with "line ${line}, col ${col}: " — got: ${err.message}`);
  assert.ok(err.message.includes(detail),
    `message must mention "${detail}" — got: ${err.message}`);
}

// ---------------------------------------------------------------- documents

test('empty and comment-only documents parse to null', () => {
  assert.equal(parse(''), null);
  assert.equal(parse('   \n\n'), null);
  assert.equal(parse('# just a note\n  # another\n'), null);
});

test('single-scalar documents are typed', () => {
  assert.equal(parse('hello world'), 'hello world');
  assert.equal(parse('42'), 42);
  assert.equal(parse('-3.5'), -3.5);
  assert.equal(parse('1e3'), 1000);
  assert.equal(parse('true'), true);
  assert.equal(parse('~'), null);
  assert.equal(parse('null'), null);
});

test('only lowercase true/false/null are special', () => {
  assert.equal(parse('True'), 'True');
  assert.equal(parse('FALSE'), 'FALSE');
  assert.equal(parse('Null'), 'Null');
  assert.equal(parse('no'), 'no');
  assert.equal(parse('on'), 'on');
});

test('quoted document scalars stay strings', () => {
  assert.equal(parse('"true"'), 'true');
  assert.equal(parse("'42'"), '42');
});

test('a second content line after a scalar document is an error', () => {
  failsAt('hello\nworld\n', 2, 1, 'unexpected content after scalar');
});

test('crlf line endings are accepted', () => {
  assert.deepEqual(parse('a: 1\r\nb: two\r\n'), { a: 1, b: 'two' });
});

// ----------------------------------------------------------------- mappings

test('flat mapping with typed values', () => {
  const doc = parse('name: loader\nretries: 3\nratio: 0.25\nenabled: true\nnote: ~\n');
  assert.deepEqual(doc, { name: 'loader', retries: 3, ratio: 0.25, enabled: true, note: null });
});

test('nested mappings by indentation', () => {
  const doc = parse('server:\n  host: local\n  limits:\n    cpu: 2\n  port: 8080\ntop: yes\n');
  assert.deepEqual(doc, {
    server: { host: 'local', limits: { cpu: 2 }, port: 8080 },
    top: 'yes',
  });
});

test('a colon without a following space is plain text', () => {
  assert.deepEqual(parse('url: http://hub:8080/path\nwhen: 12:30\n'),
    { url: 'http://hub:8080/path', when: '12:30' });
});

test("a second ': ' inside a plain value is an error", () => {
  failsAt('summary: see: the notes\n', 1, 13, "unexpected ':' in plain scalar");
});

test('key with empty value is null; explicit ~ too', () => {
  assert.deepEqual(parse('a:\nb: ~\nc:   # trailing note\n'), { a: null, b: null, c: null });
});

test('comments: full-line and trailing are stripped, # glued to text is not', () => {
  const doc = parse('# header\npath: a#b\nlabel: front # tail comment\n  # indented comment\nlast: 1\n');
  assert.deepEqual(doc, { path: 'a#b', label: 'front', last: 1 });
});

test('quoted keys, including keys containing colons', () => {
  assert.deepEqual(parse('"a: b": 1\n\'plain\': 2\n'), { 'a: b': 1, plain: 2 });
});

test('duplicate mapping keys are rejected where the duplicate appears', () => {
  failsAt('a: 1\nb: 2\na: 3\n', 3, 1, 'duplicate mapping key "a"');
  // a quoted spelling of the same key is still a duplicate
  failsAt('host: x\n"host": y\n', 2, 1, 'duplicate mapping key "host"');
});

test('keys are always strings', () => {
  assert.deepEqual(parse('1: one\ntrue: yes\n'), { '1': 'one', 'true': 'yes' });
});

// ---------------------------------------------------------------- sequences

test('flat sequence with typed items', () => {
  assert.deepEqual(parse('- alpha\n- 2\n- true\n- ~\n'), ['alpha', 2, true, null]);
});

test('dash with no value is a null item', () => {
  assert.deepEqual(parse('- a\n-\n- \n'), ['a', null, null]);
});

test('sequence under a key: indented form', () => {
  assert.deepEqual(parse('steps:\n  - one\n  - two\n'), { steps: ['one', 'two'] });
});

test('sequence under a key: same-indent form', () => {
  assert.deepEqual(parse('steps:\n- one\n- two\nafter: 1\n'),
    { steps: ['one', 'two'], after: 1 });
});

test('compact mapping opens on the dash line and continues below it', () => {
  const doc = parse('- name: alpha\n  role: build\n- name: beta\n');
  assert.deepEqual(doc, [{ name: 'alpha', role: 'build' }, { name: 'beta' }]);
});

test('dash alone with an indented child block', () => {
  assert.deepEqual(parse('-\n  a: 1\n  b: 2\n- x\n'), [{ a: 1, b: 2 }, 'x']);
});

test('nested sequence opens on the dash line', () => {
  assert.deepEqual(parse('- - a\n  - b\n- c\n'), [['a', 'b'], 'c']);
});

test('negative numbers are items, not indicators', () => {
  assert.deepEqual(parse('- -5\n- -0.5\n'), [-5, -0.5]);
});

test('a non-dash line at sequence indent is an error', () => {
  failsAt('- a\noops: 1\n', 2, 1, "expected '-' sequence indicator");
});

test('a workflow-style document round trip', () => {
  const doc = parse([
    'document:',
    '  dsl: "1.0"',
    '  name: nightly-sync',
    'do:',
    '  - fetch:',
    '      call: http',
    '      with:',
    '        endpoint: /orders',
    '  - route:',
    '      switch:',
    '        - when: ${ .fetch.total > 100 }',
    '          then: bulk',
    '        - then: continue',
    '',
  ].join('\n'));
  assert.deepEqual(doc, {
    document: { dsl: '1.0', name: 'nightly-sync' },
    do: [
      { fetch: { call: 'http', with: { endpoint: '/orders' } } },
      { route: { switch: [{ when: '${ .fetch.total > 100 }', then: 'bulk' }, { then: 'continue' }] } },
    ],
  });
});

// -------------------------------------------------------------- indentation

test('a stray deeper line after a finished entry is an error', () => {
  failsAt('a: 1\n    b: 2\n', 2, 5, 'unexpected indentation');
});

test('a dedent that lands between block levels is an error', () => {
  failsAt('a:\n    b: 1\n  c: 2\n', 3, 3, 'unexpected indentation');
});

test('tabs in indentation are rejected at the tab', () => {
  failsAt('a:\n\tb: 1\n', 2, 1, 'tab character in indentation');
  failsAt('a:\n  \tb: 1\n', 2, 3, 'tab character in indentation');
});

// ------------------------------------------------------------ quoted values

test('double-quoted strings decode escapes', () => {
  assert.deepEqual(parse('msg: "line1\\nline2\\tend \\"q\\" \\\\ \\u0041"\n'),
    { msg: 'line1\nline2\tend "q" \\ A' });
});

test('double-quoted strings never get typed', () => {
  assert.deepEqual(parse('a: "true"\nb: "3"\nc: ""\n'), { a: 'true', b: '3', c: '' });
});

test('single-quoted strings: doubled quote is the only escape', () => {
  assert.deepEqual(parse("say: 'it''s a \\n literal'\n"), { say: "it's a \\n literal" });
});

test('trailing comments after quoted scalars are fine', () => {
  assert.deepEqual(parse('a: "x # not a comment" # real comment\n'), { a: 'x # not a comment' });
});

test('invalid escape sequences are rejected at the backslash', () => {
  failsAt('a: "bad \\q here"\n', 1, 9, 'invalid escape sequence \\q');
  failsAt('a: "\\u12"\n', 1, 5, 'invalid unicode escape');
});

test('unterminated quoted strings are rejected at the opening quote', () => {
  failsAt('a: "never closed\n', 1, 4, 'unterminated double-quoted string');
  failsAt("a: 'nope\n", 1, 4, 'unterminated single-quoted string');
});

test('junk after a closing quote is an error', () => {
  failsAt('a: "done" oops\n', 1, 11, 'unexpected content after quoted scalar');
});

// ------------------------------------------------------------ block scalars

test('literal block keeps line breaks and clips to one trailing newline', () => {
  const doc = parse('script: |\n  echo one\n  echo two\n\n\nafter: 1\n');
  assert.deepEqual(doc, { script: 'echo one\necho two\n', after: 1 });
});

test('literal block with strip chomping drops the trailing newline', () => {
  assert.deepEqual(parse('cmd: |-\n  run me\n'), { cmd: 'run me' });
});

test('literal block preserves interior blank lines and deeper indentation', () => {
  const doc = parse('text: |\n  first\n\n    indented\n  last\n');
  assert.deepEqual(doc, { text: 'first\n\n  indented\nlast\n' });
});

test('folded block joins lines with spaces; blank lines become newlines', () => {
  const doc = parse('note: >\n  wraps onto\n  one line\n\n  second para\n');
  assert.deepEqual(doc, { note: 'wraps onto one line\nsecond para\n' });
});

test('folded block with strip chomping', () => {
  assert.deepEqual(parse('note: >-\n  a\n  b\n'), { note: 'a b' });
});

test('block scalar with no content lines is an empty string', () => {
  assert.deepEqual(parse('a: |\nb: 1\n'), { a: '', b: 1 });
});

test('block scalars work as sequence items and inside compact mappings', () => {
  const doc = parse('- |\n  free text\n- run: |\n    echo hi\n  next: 2\n');
  assert.deepEqual(doc, ['free text\n', { run: 'echo hi\n', next: 2 }]);
});

test('content after a block scalar indicator is an error', () => {
  failsAt('a: | junk\n  x\n', 1, 6, 'unexpected content after block scalar indicator');
  failsAt('a: |2\n  x\n', 1, 5, 'unexpected content after block scalar indicator');
  failsAt('a: |+\n  x\n', 1, 5, 'unexpected content after block scalar indicator');
});

test('under-indented interior line in a block scalar is an error', () => {
  failsAt('outer:\n  text: |\n      a\n    b\n  next: 1\n', 4, 5,
    'bad indentation in block scalar');
});

// --------------------------------------------------- unsupported yaml forms

test('flow collections are rejected', () => {
  failsAt('a: [1, 2]\n', 1, 4, 'flow collections are not supported');
  failsAt('a: {b: 1}\n', 1, 4, 'flow collections are not supported');
  failsAt('[1, 2]\n', 1, 1, 'flow collections are not supported');
});

test('anchors, aliases, and tags are rejected', () => {
  failsAt('a: &anchor 1\n', 1, 4, 'anchors, aliases, and tags are not supported');
  failsAt('a: *ref\n', 1, 4, 'anchors, aliases, and tags are not supported');
  failsAt('a: !!str 5\n', 1, 4, 'anchors, aliases, and tags are not supported');
});

test('ampersands and asterisks inside plain text are fine', () => {
  assert.deepEqual(parse('a: this & that\nb: 2 * 3\n'), { a: 'this & that', b: '2 * 3' });
});

// -------------------------------------------------------------- error shape

test('YamlParseError extends Error and exposes line/col', () => {
  const err = fails('a: [1]\n');
  assert.ok(err instanceof Error);
  assert.equal(typeof err.line, 'number');
  assert.equal(typeof err.col, 'number');
});
