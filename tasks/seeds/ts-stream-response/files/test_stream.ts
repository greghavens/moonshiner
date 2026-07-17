import { test } from 'node:test';
import assert from 'node:assert/strict';
import { ChunkedResponse, streamLines, FRAME_BYTES } from './chunkstream.ts';
import type { Sink } from './chunkstream.ts';

// Captures every byte the writer hands to the connection, in order.
class FakeSocket implements Sink {
  chunks: Buffer[] = [];
  closes = 0;
  write(data: Uint8Array): void {
    if (this.closes > 0) throw new Error('write after close');
    this.chunks.push(Buffer.from(data));
  }
  close(): void {
    this.closes += 1;
  }
  bytes(): Buffer {
    return Buffer.concat(this.chunks);
  }
}

async function* reader(records: string[], failWith?: string): AsyncGenerator<string> {
  for (const record of records) {
    await Promise.resolve(); // hop the microtask queue like a real reader
    yield record;
  }
  if (failWith !== undefined) {
    await Promise.resolve();
    throw new Error(failWith);
  }
}

function countStatusLines(capture: Buffer): number {
  return capture.toString('latin1').split('HTTP/1.1 ').length - 1;
}

interface Response {
  status: number;
  headers: Record<string, string>;
  rest: Buffer;
}

function parseResponse(capture: Buffer): Response {
  const sep = capture.indexOf('\r\n\r\n');
  assert.notEqual(sep, -1, 'capture contains a complete header block');
  const headLines = capture.subarray(0, sep).toString('latin1').split('\r\n');
  const m = /^HTTP\/1\.1 (\d{3}) /.exec(headLines[0]);
  assert.ok(m, `first bytes are a status line, got ${JSON.stringify(headLines[0])}`);
  const headers: Record<string, string> = {};
  for (const line of headLines.slice(1)) {
    const colon = line.indexOf(':');
    assert.notEqual(colon, -1, `well-formed header line: ${JSON.stringify(line)}`);
    headers[line.slice(0, colon).trim().toLowerCase()] = line.slice(colon + 1).trim();
  }
  return { status: Number(m![1]), headers, rest: Buffer.from(capture.subarray(sep + 4)) };
}

/** Strict chunked-transfer decoder: any framing slip is an assertion failure. */
function decodeChunked(rest: Buffer): { body: Buffer; sizes: number[] } {
  const parts: Buffer[] = [];
  const sizes: number[] = [];
  let off = 0;
  for (;;) {
    const lineEnd = rest.indexOf('\r\n', off);
    assert.notEqual(lineEnd, -1, 'chunk size line is CRLF-terminated');
    const sizeHex = rest.subarray(off, lineEnd).toString('latin1');
    assert.match(sizeHex, /^[0-9a-fA-F]+$/, `chunk size is plain hex, got ${JSON.stringify(sizeHex)}`);
    const size = parseInt(sizeHex, 16);
    off = lineEnd + 2;
    if (size === 0) {
      assert.equal(rest.subarray(off, off + 2).toString('latin1'), '\r\n', 'terminal chunk ends with CRLF');
      assert.equal(rest.length, off + 2, 'nothing may follow the terminal chunk');
      return { body: Buffer.concat(parts), sizes };
    }
    sizes.push(size);
    assert.ok(off + size + 2 <= rest.length, 'declared chunk data fits the capture');
    parts.push(Buffer.from(rest.subarray(off, off + size)));
    off += size;
    assert.equal(rest.subarray(off, off + 2).toString('latin1'), '\r\n', 'chunk data is CRLF-terminated');
    off += 2;
  }
}

test('a short export streams intact and terminates cleanly', async () => {
  const sock = new FakeSocket();
  const records = [
    '{"seq":1,"event":"login","user":"amara"}',
    '{"seq":2,"event":"view","user":"amara","page":"billing"}',
    '{"seq":3,"event":"logout","user":"amara"}',
  ];
  await streamLines(new ChunkedResponse(sock), reader(records));
  assert.equal(countStatusLines(sock.bytes()), 1);
  const res = parseResponse(sock.bytes());
  assert.equal(res.status, 200);
  assert.equal(res.headers['transfer-encoding'], 'chunked');
  const { body } = decodeChunked(res.rest);
  assert.equal(body.toString('utf8'), records.join('\n') + '\n');
  assert.equal(sock.closes, 1);
});

test('a record landing exactly on the frame boundary must not end the stream early', async () => {
  const sock = new FakeSocket();
  const boundaryRecord = 'x'.repeat(FRAME_BYTES - 1); // plus the newline = exactly one frame
  const tail = '{"seq":9,"last":true}';
  await streamLines(new ChunkedResponse(sock), reader([boundaryRecord, tail]));
  assert.equal(countStatusLines(sock.bytes()), 1);
  const res = parseResponse(sock.bytes());
  const { body } = decodeChunked(res.rest);
  assert.equal(body.toString('utf8'), boundaryRecord + '\n' + tail + '\n');
  assert.equal(sock.closes, 1);
});

test('frame accounting is in bytes, not characters', async () => {
  const sock = new FakeSocket();
  let record = 'inspection note: café ☕ Zürich — ';
  while (Buffer.byteLength(record + '\n') < 2 * FRAME_BYTES) record += 'x';
  assert.equal(Buffer.byteLength(record + '\n'), 2 * FRAME_BYTES, 'fixture pads to exactly two frames');
  await streamLines(new ChunkedResponse(sock), reader([record]));
  const res = parseResponse(sock.bytes());
  const { body, sizes } = decodeChunked(res.rest);
  assert.equal(body.toString('utf8'), record + '\n');
  assert.ok(Math.max(...sizes) <= FRAME_BYTES, `no frame exceeds FRAME_BYTES, got ${sizes}`);
  assert.equal(sock.closes, 1);
});

test('a source that fails before producing anything gets one complete 500', async () => {
  const sock = new FakeSocket();
  await streamLines(new ChunkedResponse(sock), reader([], 'catalog offline'));
  assert.equal(countStatusLines(sock.bytes()), 1);
  const res = parseResponse(sock.bytes());
  assert.equal(res.status, 500);
  assert.equal(res.headers['content-length'], String(res.rest.length), 'content-length matches the body');
  assert.deepEqual(JSON.parse(res.rest.toString('utf8')), { error: 'catalog offline' });
  assert.equal(sock.closes, 1);
});

test('a source that fails mid-export appends an error record and ends the body properly', async () => {
  const sock = new FakeSocket();
  const records = ['{"seq":1,"event":"login"}', '{"seq":2,"event":"view"}'];
  await streamLines(new ChunkedResponse(sock), reader(records, 'upstream read failed'));
  assert.equal(countStatusLines(sock.bytes()), 1, 'exactly one status line in the whole capture');
  const res = parseResponse(sock.bytes());
  assert.equal(res.status, 200, 'the status was already committed when the failure happened');
  const { body } = decodeChunked(res.rest);
  const lines = body.toString('utf8').split('\n').filter((l) => l.length > 0);
  assert.deepEqual(lines.slice(0, 2), records, 'records sent before the failure are intact');
  assert.deepEqual(JSON.parse(lines[2]), { error: 'upstream read failed' });
  assert.equal(lines.length, 3);
  assert.equal(sock.closes, 1);
});

test('end is idempotent and writing after the end is refused', async () => {
  const sock = new FakeSocket();
  const res = new ChunkedResponse(sock);
  res.writeLine('{"seq":1}');
  res.end();
  res.end();
  assert.equal(sock.closes, 1, 'the connection is released exactly once');
  assert.throws(() => res.writeLine('{"seq":2}'), /ended/);
  const parsed = parseResponse(sock.bytes());
  const { body } = decodeChunked(parsed.rest);
  assert.equal(body.toString('utf8'), '{"seq":1}\n');
});

test('an empty export is still a well-formed empty chunked body', async () => {
  const sock = new FakeSocket();
  await streamLines(new ChunkedResponse(sock), reader([]));
  assert.equal(countStatusLines(sock.bytes()), 1);
  const res = parseResponse(sock.bytes());
  assert.equal(res.status, 200);
  const { body } = decodeChunked(res.rest);
  assert.equal(body.length, 0);
  assert.equal(sock.closes, 1);
});
