import { test } from 'node:test';
import assert from 'node:assert/strict';
import { validateField } from './fields.ts';

test('a missing answer only matters when the field is required', () => {
  const required = { id: 'email', type: 'text', required: true };
  const optional = { id: 'nickname', type: 'text' };
  const errs = validateField(required, undefined);
  assert.equal(errs.length, 1);
  assert.match(errs[0], /required/);
  assert.deepEqual(validateField(optional, undefined), []);
});

test('text fields check type, length bounds and pattern', () => {
  assert.match(validateField({ id: 'a', type: 'text' }, 42)[0], /text/);
  assert.match(validateField({ id: 'a', type: 'text', minLength: 3 }, 'hi')[0], /3/);
  assert.match(validateField({ id: 'a', type: 'text', maxLength: 8 }, 'wayyyy too long')[0], /8/);
  assert.match(
    validateField({ id: 'a', type: 'text', pattern: '^\\d+$' }, 'abc')[0],
    /pattern|match/i,
  );
  assert.deepEqual(validateField({ id: 'a', type: 'text', minLength: 2, maxLength: 5 }, 'four'), []);
});

test('number fields check type and range', () => {
  assert.match(validateField({ id: 'n', type: 'number' }, 'seven')[0], /number/);
  assert.match(validateField({ id: 'n', type: 'number' }, Number.NaN)[0], /number/);
  assert.match(validateField({ id: 'n', type: 'number', min: 1 }, 0)[0], /1/);
  assert.match(validateField({ id: 'n', type: 'number', max: 50 }, 51)[0], /50/);
  assert.deepEqual(validateField({ id: 'n', type: 'number', min: 1, max: 50 }, 25), []);
});

test('boolean fields accept exactly true or false', () => {
  assert.match(validateField({ id: 'b', type: 'boolean' }, 'yes')[0], /boolean/);
  assert.deepEqual(validateField({ id: 'b', type: 'boolean' }, false), []);
});

test('choice fields name the rejected value', () => {
  const field = { id: 'plan', type: 'choice', options: ['free', 'pro'] };
  assert.match(validateField(field, 'gold')[0], /gold/);
  assert.deepEqual(validateField(field, 'pro'), []);
});

test('violations accumulate instead of short-circuiting', () => {
  const field = { id: 'code', type: 'text', minLength: 6, pattern: '^[A-Z]+$' };
  assert.equal(validateField(field, 'ab').length, 2);
});
