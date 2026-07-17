import { test } from 'node:test';
import assert from 'node:assert/strict';
import { LineStream } from './line_stream.ts';
import type { ConsoleLine } from './line_stream.ts';

const enc = new TextEncoder();

function run(chunks: Uint8Array[]): ConsoleLine[] {
  const stream = new LineStream();
  const out: ConsoleLine[] = [];
  for (const chunk of chunks) out.push(...stream.push(chunk));
  out.push(...stream.flush());
  return out;
}

test('plain ascii lines split across pushes keep their numbering', () => {
  const stream = new LineStream();
  assert.deepEqual(stream.push(enc.encode('boot ok\nlink u')), [{ line: 1, text: 'boot ok' }]);
  assert.deepEqual(stream.push(enc.encode('p\n')), [{ line: 2, text: 'link up' }]);
  assert.deepEqual(stream.flush(), []);
});

test('every byte-level split point yields identical lines', () => {
  const first = 'temp: 22°C ✓ — café';
  const second = 'route: 🚚 dépôt 9';
  const bytes = enc.encode(`${first}\n${second}\n`);
  const expected = [
    { line: 1, text: first },
    { line: 2, text: second },
  ];
  for (let i = 0; i <= bytes.length; i++) {
    const got = run([bytes.slice(0, i), bytes.slice(i)]);
    assert.deepEqual(got, expected, `split after byte ${i} corrupted the output`);
  }
});

test('one byte per chunk still decodes multibyte text intact', () => {
  const text = '温度: 22°C ✓';
  const bytes = enc.encode(`${text}\n`);
  const chunks: Uint8Array[] = [];
  for (let i = 0; i < bytes.length; i++) chunks.push(bytes.slice(i, i + 1));
  assert.deepEqual(run(chunks), [{ line: 1, text }]);
});

test('CRLF terminators are not part of the line text', () => {
  const got = run([enc.encode('ready\r'), enc.encode('\nrun\r\n')]);
  assert.deepEqual(got, [
    { line: 1, text: 'ready' },
    { line: 2, text: 'run' },
  ]);
});

test('the final line arrives from flush even without a terminator', () => {
  const stream = new LineStream();
  assert.deepEqual(stream.push(enc.encode('one\ntwo\nthr')), [
    { line: 1, text: 'one' },
    { line: 2, text: 'two' },
  ]);
  assert.deepEqual(stream.push(enc.encode('ee')), []);
  assert.deepEqual(stream.flush(), [{ line: 3, text: 'three' }]);
});

test('a final CRLF-less line after CRLF records keeps exact numbering', () => {
  const got = run([enc.encode('alpha\r\nbeta\r\ngamma')]);
  assert.deepEqual(got, [
    { line: 1, text: 'alpha' },
    { line: 2, text: 'beta' },
    { line: 3, text: 'gamma' },
  ]);
});

test('a newline-terminated stream owes nothing at flush', () => {
  const stream = new LineStream();
  stream.push(enc.encode('done\n'));
  assert.deepEqual(stream.flush(), []);
});

test('an invalid byte becomes a single replacement character', () => {
  const got = run([Uint8Array.from([0x41, 0xff, 0x42, 0x0a])]);
  assert.deepEqual(got, [{ line: 1, text: 'A�B' }]);
});

test('a character truncated by end of stream is flushed as a replacement', () => {
  const got = run([Uint8Array.from([0xe2, 0x80])]);
  assert.deepEqual(got, [{ line: 1, text: '�' }]);
});

test('empty chunks are harmless no-ops', () => {
  const stream = new LineStream();
  assert.deepEqual(stream.push(new Uint8Array(0)), []);
  assert.deepEqual(stream.push(enc.encode('mid')), []);
  assert.deepEqual(stream.push(new Uint8Array(0)), []);
  assert.deepEqual(stream.push(enc.encode('dle\n')), [{ line: 1, text: 'middle' }]);
  assert.deepEqual(stream.flush(), []);
});
