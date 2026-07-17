import { test } from 'node:test';
import assert from 'node:assert/strict';
import { FormState } from './form_state.ts';

function signupForm(): FormState {
  const form = new FormState();
  const required = { test: (v: string) => v.length > 0, message: 'is required' };
  form.addField('email', [
    required,
    { test: (v: string) => v.includes('@'), message: 'must contain @' },
  ]);
  form.addField('password', [
    required,
    { test: (v: string) => v.length >= 8, message: 'must be at least 8 characters' },
  ]);
  form.addField('nickname');
  return form;
}

test('validating one field reports its failures', () => {
  const form = signupForm();
  form.setValue('email', 'nope');
  assert.deepEqual(form.validateField('email'), ['email: must contain @']);
});

test('validating the whole form aggregates failures across fields', () => {
  const form = signupForm();
  form.setValue('email', 'dev@example.com');
  assert.deepEqual(form.validateAll(), [
    'password: is required',
    'password: must be at least 8 characters',
  ]);
});

test('a fully valid form validates clean', () => {
  const form = signupForm();
  form.setValue('email', 'dev@example.com');
  form.setValue('password', 'hunter2hunter2');
  assert.deepEqual(form.validateAll(), []);
});

test('handlers wired into a view still drive the form', () => {
  const form = signupForm();
  const { onChange, onSubmit } = form.handlers();
  onChange('email', 'dev@example.com');
  onChange('password', 'hunter2hunter2');
  assert.equal(form.value('email'), 'dev@example.com');
  assert.deepEqual(onSubmit(), []);
  assert.equal(form.isDirty(), true);
});
