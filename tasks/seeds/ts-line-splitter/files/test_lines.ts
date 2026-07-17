import { test } from 'node:test';
import assert from 'node:assert/strict';
import { splitLines } from './lines.ts';

async function* chunked(...chunks: string[]): AsyncGenerator<string> {
  for (const c of chunks) yield c;
}

async function collect(source: AsyncIterable<string>): Promise<string[]> {
  const out: string[] = [];
  for await (const line of source) out.push(line);
  return out;
}

test('splits LF-terminated lines in a single chunk', async () => {
  assert.deepEqual(await collect(splitLines(chunked('alpha\nbeta\ngamma\n'))), [
    'alpha',
    'beta',
    'gamma',
  ]);
});

test('strips CRLF terminators', async () => {
  assert.deepEqual(await collect(splitLines(chunked('one\r\ntwo\r\n'))), ['one', 'two']);
});

test('handles mixed LF and CRLF in the same stream', async () => {
  assert.deepEqual(await collect(splitLines(chunked('a\r\nb\nc\r\n'))), ['a', 'b', 'c']);
});

test('reassembles a line that arrives across several chunks', async () => {
  assert.deepEqual(await collect(splitLines(chunked('hel', 'lo\nwor', 'ld\n'))), [
    'hello',
    'world',
  ]);
});

test('a CRLF pair split across a chunk boundary is one terminator', async () => {
  assert.deepEqual(await collect(splitLines(chunked('line one\r', '\nline two\n'))), [
    'line one',
    'line two',
  ]);
});

test('a lone carriage return is line content, not a terminator', async () => {
  assert.deepEqual(await collect(splitLines(chunked('col1\rcol2\n'))), ['col1\rcol2']);
});

test('a chunk-final carriage return followed by more content stays in the line', async () => {
  assert.deepEqual(await collect(splitLines(chunked('a\r', 'b\n'))), ['a\rb']);
});

test('a trailing line without a terminator is still yielded', async () => {
  assert.deepEqual(await collect(splitLines(chunked('done\nalmost'))), ['done', 'almost']);
});

test('input ending exactly at a newline yields no phantom empty line', async () => {
  assert.deepEqual(await collect(splitLines(chunked('a\nb\n'))), ['a', 'b']);
});

test('blank lines between terminators are preserved as empty strings', async () => {
  assert.deepEqual(await collect(splitLines(chunked('a\n\nb\n'))), ['a', '', 'b']);
  assert.deepEqual(await collect(splitLines(chunked('\r\n\r\n'))), ['', '']);
});

test('a bare newline is one empty line', async () => {
  assert.deepEqual(await collect(splitLines(chunked('\n'))), ['']);
});

test('empty chunks are tolerated mid-stream', async () => {
  assert.deepEqual(await collect(splitLines(chunked('a', '', '\n', '', 'b\n'))), ['a', 'b']);
});

test('empty input produces no lines', async () => {
  assert.deepEqual(await collect(splitLines(chunked())), []);
  assert.deepEqual(await collect(splitLines(chunked(''))), []);
});

test('accepts a plain synchronous iterable of chunks', async () => {
  assert.deepEqual(await collect(splitLines(['x\ny', '\nz\n'])), ['x', 'y', 'z']);
});

test('yields lines as their chunks arrive instead of draining the source first', async () => {
  let pulls = 0;
  async function* source(): AsyncGenerator<string> {
    pulls++;
    yield 'first\nsecond\n';
    pulls++;
    yield 'third\n';
  }
  const it = splitLines(source());
  assert.deepEqual(await it.next(), { value: 'first', done: false });
  assert.deepEqual(await it.next(), { value: 'second', done: false });
  assert.equal(pulls, 1, 'second chunk must not be pulled until its lines are needed');
  assert.deepEqual(await it.next(), { value: 'third', done: false });
  assert.equal(pulls, 2);
  assert.equal((await it.next()).done, true);
});

test('handles a realistic NDJSON tail with awkward chunk boundaries', async () => {
  const lines = await collect(
    splitLines(
      chunked('{"level":"info"}\r', '\n{"level":"warn"', '}\n{"lev', 'el":"error"}'),
    ),
  );
  assert.deepEqual(lines, ['{"level":"info"}', '{"level":"warn"}', '{"level":"error"}']);
  for (const line of lines) assert.doesNotThrow(() => JSON.parse(line));
});
