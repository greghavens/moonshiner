import { test } from 'node:test';
import assert from 'node:assert/strict';
import { applyMask, unmask } from './mask.ts';

const PHONE = '(###) ###-####';
const PLATE = 'AA-99';

// ---------- applying a mask ----------

test('a complete raw value fills the whole mask', () => {
  const r = applyMask('4155551234', PHONE);
  assert.equal(r.masked, '(415) 555-1234');
  assert.equal(r.complete, true);
});

test('empty input produces empty output', () => {
  const r = applyMask('', PHONE);
  assert.equal(r.masked, '');
  assert.equal(r.complete, false);
  assert.equal(r.cursor, 0);
});

test('literals are inserted as typing reaches them', () => {
  assert.equal(applyMask('4', PHONE).masked, '(4');
  assert.equal(applyMask('415', PHONE).masked, '(415');
  assert.equal(applyMask('4155', PHONE).masked, '(415) 5');
  assert.equal(applyMask('4155551', PHONE).masked, '(415) 555-1');
});

test('partially filled masks report complete: false', () => {
  assert.equal(applyMask('415555', PHONE).complete, false);
  assert.equal(applyMask('415555123', PHONE).complete, false);
});

test('pasting an already-masked value is idempotent', () => {
  assert.equal(applyMask('(415) 555-1234', PHONE).masked, '(415) 555-1234');
  assert.equal(applyMask('(415) 5', PHONE).masked, '(415) 5');
});

test('characters that fit no slot are rejected, the rest still land', () => {
  assert.equal(applyMask('41x5', PHONE).masked, '(415');
  assert.equal(applyMask('abc', PHONE).masked, '');
  assert.equal(applyMask('4-1-5', PHONE).masked, '(415');
});

test('input beyond the last slot is ignored', () => {
  const r = applyMask('41555512349999', PHONE);
  assert.equal(r.masked, '(415) 555-1234');
  assert.equal(r.complete, true);
});

test('letter slots accept only letters and uppercase them', () => {
  assert.equal(applyMask('ab12', PLATE).masked, 'AB-12');
  assert.equal(applyMask('1ab2', PLATE).masked, 'AB-2');
  assert.equal(applyMask('ab', PLATE).masked, 'AB');
});

test('9 works as a digit slot like #', () => {
  assert.equal(applyMask('0714', '99/99').masked, '07/14');
  assert.equal(applyMask('07', '99/99').masked, '07');
});

test('star slots take letters or digits and preserve case', () => {
  assert.equal(applyMask('ab1x', 'AA-**').masked, 'AB-1x');
  assert.equal(applyMask('ab!x', 'AA-**').masked, 'AB-x');
});

test('a backslash escapes a mask token into a literal', () => {
  assert.equal(applyMask('42', 'v\\9-99').masked, 'v9-42');
  assert.equal(applyMask('b', '\\AA').masked, 'AB');
  assert.equal(applyMask('Ab', '\\AA').masked, 'AB');
});

// ---------- cursor mapping ----------

test('cursor maps through auto-inserted literals', () => {
  assert.equal(applyMask('4155551234', PHONE, 3).cursor, 4);
  assert.equal(applyMask('4155551234', PHONE, 4).cursor, 7);
  assert.equal(applyMask('4155551234', PHONE, 0).cursor, 0);
  assert.equal(applyMask('4155551234', PHONE, 10).cursor, 14);
});

test('rejected characters do not advance the cursor', () => {
  assert.equal(applyMask('41x55', PHONE, 3).cursor, 3);
  assert.equal(applyMask('41x55', PHONE, 5).cursor, 7);
});

test('cursor defaults to the end of the masked text', () => {
  assert.equal(applyMask('4155', PHONE).cursor, 7);
  assert.equal(applyMask('4155551234', PHONE).cursor, 14);
});

// ---------- unmasking ----------

test('unmask strips literals and returns slot characters', () => {
  assert.equal(unmask('(415) 555-1234', PHONE), '4155551234');
  assert.equal(unmask('AB-12', PLATE), 'AB12');
});

test('unmask handles a partially filled value', () => {
  assert.equal(unmask('(415) 5', PHONE), '4155');
  assert.equal(unmask('', PHONE), '');
});

test('unmask(applyMask(x).masked) round-trips the accepted input', () => {
  for (const raw of ['4155551234', '415', '4155']) {
    assert.equal(unmask(applyMask(raw, PHONE).masked, PHONE), raw);
  }
  assert.equal(unmask(applyMask('ab12', PLATE).masked, PLATE), 'AB12');
});

test('unmask sees escaped tokens as literals', () => {
  assert.equal(unmask('v9-42', 'v\\9-99'), '42');
});

test('unmask rejects text whose literals do not line up', () => {
  assert.throws(() => unmask('415-555', PHONE), /literal|expected/i);
});

test('unmask rejects slot characters of the wrong class', () => {
  assert.throws(() => unmask('(4x5) 555-1234', PHONE), /'x'/);
  assert.throws(() => unmask('12-34', PLATE), Error);
});

test('unmask rejects text longer than the mask', () => {
  assert.throws(() => unmask('(415) 555-12345', PHONE), Error);
});
