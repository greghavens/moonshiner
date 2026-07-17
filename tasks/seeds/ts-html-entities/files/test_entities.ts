import { test } from 'node:test';
import assert from 'node:assert/strict';
import { encodeEntities, decodeEntities } from './entities.ts';

// ---------- encoding ----------

test('minimal mode escapes exactly the five critical characters', () => {
  assert.equal(
    encodeEntities('<a href="x">it\'s</a> & more'),
    '&lt;a href=&quot;x&quot;&gt;it&apos;s&lt;/a&gt; &amp; more',
  );
});

test('plain text passes through untouched', () => {
  assert.equal(encodeEntities('hello world 123'), 'hello world 123');
  assert.equal(encodeEntities(''), '');
});

test('minimal mode leaves non-ASCII characters alone', () => {
  assert.equal(encodeEntities('café — 100°'), 'café — 100°');
});

test('an ampersand that already starts a valid entity is not re-encoded', () => {
  assert.equal(encodeEntities('5 &lt; 6 & 7'), '5 &lt; 6 &amp; 7');
  assert.equal(encodeEntities('caf&#xE9; & caf&#233;'), 'caf&#xE9; &amp; caf&#233;');
});

test('an ampersand starting an unknown entity name is still encoded', () => {
  assert.equal(encodeEntities('&bogus; and &amp;'), '&amp;bogus; and &amp;');
});

test('doubleEncode: true turns off the protection', () => {
  assert.equal(encodeEntities('&lt;', { doubleEncode: true }), '&amp;lt;');
  assert.equal(encodeEntities('&#233;', { doubleEncode: true }), '&amp;#233;');
});

test('full mode uses names from the common table', () => {
  assert.equal(
    encodeEntities('© 2026 — see p. 4 § 2 …', { mode: 'full' }),
    '&copy; 2026 &mdash; see p. 4 &sect; 2 &hellip;',
  );
  assert.equal(encodeEntities('a\u00A0b', { mode: 'full' }), 'a&nbsp;b');
});

test('full mode falls back to uppercase hex references for unnamed characters', () => {
  assert.equal(encodeEntities('café', { mode: 'full' }), 'caf&#xE9;');
  assert.equal(encodeEntities('Ж', { mode: 'full' }), '&#x416;');
});

test('full mode still escapes the critical five', () => {
  assert.equal(encodeEntities('a < b & c', { mode: 'full' }), 'a &lt; b &amp; c');
});

test('full mode emits one reference per astral code point, not per surrogate', () => {
  assert.equal(encodeEntities('😀', { mode: 'full' }), '&#x1F600;');
});

// ---------- decoding ----------

test('decodes the critical five by name', () => {
  assert.equal(decodeEntities('&lt;b&gt; &quot;hi&quot; &apos;yo&apos; &amp; done'), '<b> "hi" \'yo\' & done');
});

test('decodes names from the common table', () => {
  assert.equal(decodeEntities('&copy;&nbsp;&hellip;&mdash;&euro;&deg;'), '\u00A9\u00A0\u2026\u2014\u20AC\u00B0');
});

test('decodes decimal and hex numeric references, hex case-insensitively', () => {
  assert.equal(decodeEntities('caf&#233;'), 'café');
  assert.equal(decodeEntities('caf&#xE9;'), 'café');
  assert.equal(decodeEntities('caf&#Xe9;'), 'café');
});

test('decodes astral-plane references to a single character', () => {
  assert.equal(decodeEntities('&#128512;'), '😀');
  assert.equal(decodeEntities('&#x1F600;'), '😀');
});

test('decoding is single-pass, never recursive', () => {
  assert.equal(decodeEntities('&amp;amp;'), '&amp;');
  assert.equal(decodeEntities('&amp;lt;b&amp;gt;'), '&lt;b&gt;');
});

test('lenient mode keeps unknown or malformed entities verbatim', () => {
  assert.equal(decodeEntities('&bogus; stays'), '&bogus; stays');
  assert.equal(decodeEntities('AT&T'), 'AT&T');
  assert.equal(decodeEntities('&amp'), '&amp');
  assert.equal(decodeEntities('&#;'), '&#;');
  assert.equal(decodeEntities('&#xZZ;'), '&#xZZ;');
});

test('entity names are case-sensitive', () => {
  assert.equal(decodeEntities('&AMP;'), '&AMP;');
});

test('strict mode throws on an unknown entity and names it', () => {
  assert.throws(() => decodeEntities('&bogus;', { mode: 'strict' }), /&bogus;/);
});

test('strict mode throws on a bare ampersand and reports where it is', () => {
  assert.throws(() => decodeEntities('Fish & Chips', { mode: 'strict' }), /index 5/);
});

test('strict mode accepts text whose entities are all valid', () => {
  assert.equal(decodeEntities('Fish &amp; Chips &#33;', { mode: 'strict' }), 'Fish & Chips !');
});

test('out-of-range and surrogate references become U+FFFD when lenient', () => {
  assert.equal(decodeEntities('&#x110000;'), '�');
  assert.equal(decodeEntities('&#xD800;'), '�');
  assert.equal(decodeEntities('&#0;'), '�');
});

test('out-of-range and surrogate references throw when strict', () => {
  assert.throws(() => decodeEntities('&#x110000;', { mode: 'strict' }), Error);
  assert.throws(() => decodeEntities('&#xD800;', { mode: 'strict' }), Error);
});

// ---------- round trips ----------

test('decode(encode(x)) is the identity in minimal mode', () => {
  const nasty = '<script>if (a && b) alert("x < y")</script> & \'quotes\'';
  assert.equal(decodeEntities(encodeEntities(nasty, { doubleEncode: true })), nasty);
});

test('decode(encode(x)) is the identity in full mode', () => {
  const nasty = 'café © “quotes” — 😀 & <b>ok</b> end';
  assert.equal(decodeEntities(encodeEntities(nasty, { mode: 'full', doubleEncode: true })), nasty);
});
