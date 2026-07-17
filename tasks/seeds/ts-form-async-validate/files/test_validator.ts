import { test } from 'node:test';
import assert from 'node:assert/strict';
import { FormValidator, required, minLength, pattern } from './validator.ts';

test('required flags empty-ish values only', () => {
  const v = required();
  assert.equal(v(''), 'is required');
  assert.equal(v(null), 'is required');
  assert.equal(v(undefined), 'is required');
  assert.equal(v('x'), null);
  assert.equal(v(0), null);
});

test('minLength and pattern check strings and honor custom messages', () => {
  assert.equal(minLength(3)('ab'), 'must be at least 3 characters');
  assert.equal(minLength(3, 'too short')('ab'), 'too short');
  assert.equal(minLength(3)('abc'), null);
  assert.equal(pattern(/^\d+$/)('12a'), 'has an invalid format');
  assert.equal(pattern(/^\d+$/)('123'), null);
});

test('validateField returns messages in validator order', () => {
  const form = new FormValidator();
  form.addField('username', [required(), minLength(4, 'short')]);
  form.setValue('username', '');
  assert.deepEqual(form.validateField('username'), ['is required', 'short']);
  form.setValue('username', 'abcd');
  assert.deepEqual(form.validateField('username'), []);
});

test('validateAll maps every field and isValid summarizes', () => {
  const form = new FormValidator();
  form.addField('email', [required()]);
  form.addField('nickname', []);
  assert.deepEqual(form.validateAll(), { email: ['is required'], nickname: [] });
  assert.equal(form.isValid(), false);
  form.setValue('email', 'a@b.c');
  assert.equal(form.isValid(), true);
});

test('values round-trip through set/get', () => {
  const form = new FormValidator();
  form.addField('age', []);
  form.setValue('age', 42);
  assert.equal(form.getValue('age'), 42);
});

test('duplicate fields and unknown fields are errors', () => {
  const form = new FormValidator();
  form.addField('a', []);
  assert.throws(() => form.addField('a', []));
  assert.throws(() => form.setValue('missing', 1));
  assert.throws(() => form.validateField('missing'));
});
