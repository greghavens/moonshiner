import { test } from 'node:test';
import assert from 'node:assert/strict';
import { parseBody, BodyParseError } from './bodyparser.ts';

function bytes(s: string): Uint8Array {
  return new TextEncoder().encode(s);
}

function code(fn: () => unknown): string {
  try {
    fn();
  } catch (err) {
    if (err instanceof BodyParseError) return err.code;
    throw err;
  }
  return '(no error)';
}

interface Part {
  headers: string[];
  body: string | Uint8Array;
}

function multipart(boundary: string, parts: Part[], extra: { preamble?: string; epilogue?: string; noClose?: boolean } = {}): Uint8Array {
  const chunks: Buffer[] = [];
  if (extra.preamble) chunks.push(Buffer.from(extra.preamble + '\r\n'));
  for (const part of parts) {
    chunks.push(Buffer.from(`--${boundary}\r\n`));
    for (const h of part.headers) chunks.push(Buffer.from(h + '\r\n'));
    chunks.push(Buffer.from('\r\n'));
    chunks.push(Buffer.from(part.body as Uint8Array));
    chunks.push(Buffer.from('\r\n'));
  }
  if (!extra.noClose) chunks.push(Buffer.from(`--${boundary}--\r\n`));
  if (extra.epilogue) chunks.push(Buffer.from(extra.epilogue));
  return Buffer.concat(chunks);
}

// ===================== EXISTING BEHAVIOR (green today, must stay green) =====================

test('parses a JSON object body', () => {
  const b = parseBody(bytes('{"name":"Renée","tags":["a","b"]}'), 'application/json');
  assert.equal(b.type, 'json');
  assert.deepEqual(b.value, { name: 'Renée', tags: ['a', 'b'] });
});

test('json media type and charset are case-insensitive, parameters tolerated', () => {
  const b = parseBody(bytes('[1,2]'), 'APPLICATION/JSON; CharSet=UTF-8');
  assert.deepEqual(b.value, [1, 2]);
});

test('json refuses non-utf-8 charsets', () => {
  assert.equal(code(() => parseBody(bytes('{}'), 'application/json; charset=iso-8859-1')), 'bad-charset');
});

test('json over the byte cap is refused before parsing', () => {
  const body = bytes(JSON.stringify({ note: 'x'.repeat(100) }));
  assert.equal(code(() => parseBody(body, 'application/json', { maxBytes: 64 })), 'too-large');
});

test('malformed JSON reports bad-json', () => {
  assert.equal(code(() => parseBody(bytes('{"a":'), 'application/json')), 'bad-json');
});

test('json that is not valid utf-8 reports bad-encoding', () => {
  assert.equal(code(() => parseBody(Uint8Array.from([0xff, 0xfe, 0x7b]), 'application/json')), 'bad-encoding');
});

test('missing or unhandled content types report unsupported-type', () => {
  assert.equal(code(() => parseBody(bytes('x'), undefined)), 'unsupported-type');
  assert.equal(code(() => parseBody(bytes('x'), '')), 'unsupported-type');
  assert.equal(code(() => parseBody(bytes('hello'), 'text/plain')), 'unsupported-type');
});

// ===================== NEW: application/x-www-form-urlencoded =====================

test('urlencoded decodes plus signs and utf-8 percent escapes', () => {
  const b = parseBody(bytes('name=Ren%C3%A9e+Fournier&city=Z%C3%BCrich'), 'application/x-www-form-urlencoded');
  assert.equal(b.type, 'form');
  if (b.type !== 'form') throw new Error('unreachable');
  assert.deepEqual(b.fields, { name: ['Renée Fournier'], city: ['Zürich'] });
});

test('urlencoded keeps repeated keys in order and preserves first-appearance key order', () => {
  const b = parseBody(bytes('tag=work&note=&tag=travel'), 'application/x-www-form-urlencoded');
  if (b.type !== 'form') throw new Error('expected form');
  assert.deepEqual(b.fields, { tag: ['work', 'travel'], note: [''] });
  assert.deepEqual(Object.keys(b.fields), ['tag', 'note']);
});

test('urlencoded handles bare keys, empty segments, plus in keys, and the empty body', () => {
  const b = parseBody(bytes('agree&full+name=Ada&&x=1'), 'application/x-www-form-urlencoded');
  if (b.type !== 'form') throw new Error('expected form');
  assert.deepEqual(b.fields, { agree: [''], 'full name': ['Ada'], x: ['1'] });
  const empty = parseBody(bytes(''), 'application/x-www-form-urlencoded');
  if (empty.type !== 'form') throw new Error('expected form');
  assert.deepEqual(empty.fields, {});
});

test('urlencoded honors an iso-8859-1 charset parameter (latin1 alias too)', () => {
  const b = parseBody(bytes('name=Ren%E9e'), 'application/x-www-form-urlencoded; charset=iso-8859-1');
  if (b.type !== 'form') throw new Error('expected form');
  assert.deepEqual(b.fields, { name: ['Renée'] });
  const alias = parseBody(bytes('city=Z%FCrich'), 'application/x-www-form-urlencoded; charset=latin1');
  if (alias.type !== 'form') throw new Error('expected form');
  assert.deepEqual(alias.fields, { city: ['Zürich'] });
});

test('urlencoded refuses charsets it does not speak', () => {
  assert.equal(
    code(() => parseBody(bytes('a=1'), 'application/x-www-form-urlencoded; charset=shift_jis')),
    'bad-charset');
});

test('urlencoded reports malformed percent escapes as bad-encoding', () => {
  const ct = 'application/x-www-form-urlencoded';
  assert.equal(code(() => parseBody(bytes('a=%G1'), ct)), 'bad-encoding');
  assert.equal(code(() => parseBody(bytes('a=%2'), ct)), 'bad-encoding');
  assert.equal(code(() => parseBody(bytes('a=%FF'), ct)), 'bad-encoding', 'lone 0xFF is not utf-8');
});

// ===================== NEW: multipart/form-data =====================

const CT = (b: string) => `multipart/form-data; boundary=${b}`;

test('multipart separates text fields from file parts', () => {
  const body = multipart('----vitrine7381', [
    { headers: ['Content-Disposition: form-data; name="name"'], body: 'Renée Fournier' },
    { headers: ['Content-Disposition: form-data; name="message"'], body: 'expense report for Zürich trip' },
    {
      headers: [
        'Content-Disposition: form-data; name="attachment"; filename="reçu-café.pdf"',
        'Content-Type: application/pdf',
      ],
      body: '%PDF-1.4 test receipt',
    },
  ]);
  const b = parseBody(body, CT('----vitrine7381'));
  assert.equal(b.type, 'multipart');
  if (b.type !== 'multipart') throw new Error('unreachable');
  assert.deepEqual(b.fields, { name: ['Renée Fournier'], message: ['expense report for Zürich trip'] });
  assert.equal(b.files.length, 1);
  const f = b.files[0];
  assert.equal(f.field, 'attachment');
  assert.equal(f.filename, 'reçu-café.pdf');
  assert.equal(f.contentType, 'application/pdf');
  assert.equal(Buffer.from(f.data).toString('utf8'), '%PDF-1.4 test receipt');
});

test('multipart accepts a quoted boundary and defaults file content-type to octet-stream', () => {
  const body = multipart('b42', [
    { headers: ['Content-Disposition: form-data; name="avatar"; filename="photo.bin"'], body: 'xyz' },
  ]);
  const b = parseBody(body, 'multipart/form-data; boundary="b42"');
  if (b.type !== 'multipart') throw new Error('expected multipart');
  assert.equal(b.files[0].contentType, 'application/octet-stream');
});

test('multipart file bytes pass through untouched, even when not valid text', () => {
  const blob = Uint8Array.from([0, 1, 2, 255, 254, 10, 13]);
  const body = multipart('b7', [
    { headers: ['Content-Disposition: form-data; name="dump"; filename="state.bin"'], body: blob },
  ]);
  const b = parseBody(body, CT('b7'));
  if (b.type !== 'multipart') throw new Error('expected multipart');
  assert.equal(Buffer.compare(Buffer.from(b.files[0].data), Buffer.from(blob)), 0);
});

test('multipart part header names are case-insensitive and bare tokens are accepted', () => {
  const body = multipart('b9', [
    { headers: ['CONTENT-DISPOSITION: form-data; name=city'], body: 'Zürich' },
  ]);
  const b = parseBody(body, CT('b9'));
  if (b.type !== 'multipart') throw new Error('expected multipart');
  assert.deepEqual(b.fields, { city: ['Zürich'] });
});

test('multipart ignores preamble and epilogue, and keeps empty fields', () => {
  const body = multipart('b11', [
    { headers: ['Content-Disposition: form-data; name="comment"'], body: '' },
  ], { preamble: 'This is a message in MIME format.', epilogue: 'trailing junk' });
  const b = parseBody(body, CT('b11'));
  if (b.type !== 'multipart') throw new Error('expected multipart');
  assert.deepEqual(b.fields, { comment: [''] });
});

test('multipart without a boundary parameter is bad-multipart', () => {
  assert.equal(code(() => parseBody(bytes('irrelevant'), 'multipart/form-data')), 'bad-multipart');
});

test('multipart missing its closing delimiter is bad-multipart', () => {
  const body = multipart('b13', [
    { headers: ['Content-Disposition: form-data; name="a"'], body: '1' },
  ], { noClose: true });
  assert.equal(code(() => parseBody(body, CT('b13'))), 'bad-multipart');
});

test('a part without a usable Content-Disposition name is bad-multipart', () => {
  const noName = multipart('b15', [
    { headers: ['Content-Disposition: form-data'], body: '1' },
  ]);
  assert.equal(code(() => parseBody(noName, CT('b15'))), 'bad-multipart');
  const noHeader = multipart('b16', [
    { headers: ['Content-Type: text/plain'], body: '1' },
  ]);
  assert.equal(code(() => parseBody(noHeader, CT('b16'))), 'bad-multipart');
});

test('a text field that is not valid utf-8 is bad-encoding', () => {
  const body = multipart('b17', [
    { headers: ['Content-Disposition: form-data; name="note"'], body: Uint8Array.from([0xff, 0xfe]) },
  ]);
  assert.equal(code(() => parseBody(body, CT('b17'))), 'bad-encoding');
});

// ===================== NEW: per-type size caps =====================

test('limits.json caps only json bodies', () => {
  const json = bytes('{"note":"0123456789012345678901234567890123456789"}');
  assert.equal(code(() => parseBody(json, 'application/json', { limits: { json: 16 } })), 'too-large');
  const form = bytes('note=0123456789012345678901234567890123456789');
  const parsed = parseBody(form, 'application/x-www-form-urlencoded', { limits: { json: 16 } });
  assert.equal(parsed.type, 'form');
});

test('limits.urlencoded and limits.multipart cap their own types', () => {
  const form = bytes('a=' + 'x'.repeat(64));
  assert.equal(
    code(() => parseBody(form, 'application/x-www-form-urlencoded', { limits: { urlencoded: 32 } })),
    'too-large');
  const body = multipart('b19', [
    { headers: ['Content-Disposition: form-data; name="a"'], body: 'x'.repeat(64) },
  ]);
  assert.equal(code(() => parseBody(body, CT('b19'), { limits: { multipart: 48 } })), 'too-large');
});

test('a per-type limit overrides maxBytes for that type; maxBytes still backstops the rest', () => {
  const json = bytes('{"ok":true,"padding":"0123456789"}');
  const b = parseBody(json, 'application/json', { maxBytes: 8, limits: { json: 128 } });
  assert.equal(b.type, 'json');
  const form = bytes('a=' + 'y'.repeat(64));
  assert.equal(
    code(() => parseBody(form, 'application/x-www-form-urlencoded', { maxBytes: 32 })),
    'too-large');
});
