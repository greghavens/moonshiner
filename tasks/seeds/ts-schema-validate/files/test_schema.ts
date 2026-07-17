import { test } from 'node:test';
import assert from 'node:assert/strict';
import * as s from './schema.ts';

test('primitive schemas accept matching values', () => {
  assert.deepEqual(s.string().validate('hi'), { ok: true, value: 'hi' });
  assert.deepEqual(s.number().validate(3.5), { ok: true, value: 3.5 });
  assert.deepEqual(s.boolean().validate(false), { ok: true, value: false });
});

test('type mismatches name the expected and actual types', () => {
  assert.deepEqual(s.string().validate(42), {
    ok: false,
    errors: [{ path: '', message: 'expected string, got number' }],
  });
  assert.deepEqual(s.number().validate(null), {
    ok: false,
    errors: [{ path: '', message: 'expected number, got null' }],
  });
  assert.deepEqual(s.boolean().validate([true]), {
    ok: false,
    errors: [{ path: '', message: 'expected boolean, got array' }],
  });
});

test('string constraints stack and report every failure', () => {
  const schema = s.string().min(5).pattern(/^[a-z]+$/);
  assert.deepEqual(schema.validate('A1'), {
    ok: false,
    errors: [
      { path: '', message: 'must have at least 5 characters' },
      { path: '', message: 'does not match pattern' },
    ],
  });
  assert.equal(schema.validate('abcdef').ok, true);
});

test('string max length is enforced', () => {
  assert.deepEqual(s.string().max(3).validate('abcd'), {
    ok: false,
    errors: [{ path: '', message: 'must have at most 3 characters' }],
  });
});

test('a failed type check suppresses constraint checks', () => {
  assert.deepEqual(s.string().min(5).validate(9), {
    ok: false,
    errors: [{ path: '', message: 'expected string, got number' }],
  });
});

test('number bounds and integrality', () => {
  assert.deepEqual(s.number().min(10).validate(3), {
    ok: false,
    errors: [{ path: '', message: 'must be >= 10' }],
  });
  assert.deepEqual(s.number().max(100).validate(250), {
    ok: false,
    errors: [{ path: '', message: 'must be <= 100' }],
  });
  assert.deepEqual(s.number().int().validate(2.5), {
    ok: false,
    errors: [{ path: '', message: 'must be an integer' }],
  });
  assert.equal(s.number().min(0).max(10).int().validate(7).ok, true);
});

test('objects validate each declared field', () => {
  const user = s.object({ name: s.string(), age: s.number() });
  assert.deepEqual(user.validate({ name: 'ada', age: 36 }), {
    ok: true,
    value: { name: 'ada', age: 36 },
  });
});

test('missing required keys are reported at their path', () => {
  const user = s.object({ name: s.string(), age: s.number() });
  assert.deepEqual(user.validate({ name: 'ada' }), {
    ok: false,
    errors: [{ path: 'age', message: 'is required' }],
  });
});

test('a non-object at the root reports at the empty path', () => {
  const user = s.object({ name: s.string() });
  assert.deepEqual(user.validate(7), {
    ok: false,
    errors: [{ path: '', message: 'expected object, got number' }],
  });
  assert.deepEqual(user.validate([1]), {
    ok: false,
    errors: [{ path: '', message: 'expected object, got array' }],
  });
});

test('all field errors are collected in shape-declaration order', () => {
  const form = s.object({
    title: s.string().min(3),
    count: s.number(),
    live: s.boolean(),
  });
  assert.deepEqual(form.validate({ title: 'ab', count: 'many', live: 1 }), {
    ok: false,
    errors: [
      { path: 'title', message: 'must have at least 3 characters' },
      { path: 'count', message: 'expected number, got string' },
      { path: 'live', message: 'expected boolean, got number' },
    ],
  });
});

test('unexpected keys are errors, after field errors, in input order', () => {
  const cfg = s.object({ host: s.string() });
  assert.deepEqual(cfg.validate({ host: 9, extra: 1, more: 2 }), {
    ok: false,
    errors: [
      { path: 'host', message: 'expected string, got number' },
      { path: 'extra', message: 'unexpected key' },
      { path: 'more', message: 'unexpected key' },
    ],
  });
});

test('passthrough() admits unknown keys', () => {
  const cfg = s.object({ host: s.string() }).passthrough();
  assert.deepEqual(cfg.validate({ host: 'db1', region: 'eu' }), {
    ok: true,
    value: { host: 'db1', region: 'eu' },
  });
});

test('optional fields may be absent or undefined but not wrong', () => {
  const user = s.object({ name: s.string(), nick: s.optional(s.string()) });
  assert.equal(user.validate({ name: 'ada' }).ok, true);
  assert.equal(user.validate({ name: 'ada', nick: undefined }).ok, true);
  assert.equal(user.validate({ name: 'ada', nick: 'al' }).ok, true);
  assert.deepEqual(user.validate({ name: 'ada', nick: 7 }), {
    ok: false,
    errors: [{ path: 'nick', message: 'expected string, got number' }],
  });
});

test('nested object errors carry dotted paths', () => {
  const schema = s.object({
    user: s.object({ email: s.string(), prefs: s.object({ dark: s.boolean() }) }),
  });
  assert.deepEqual(
    schema.validate({ user: { email: 5, prefs: { dark: 'yes' } } }),
    {
      ok: false,
      errors: [
        { path: 'user.email', message: 'expected string, got number' },
        { path: 'user.prefs.dark', message: 'expected boolean, got string' },
      ],
    },
  );
});

test('arrays validate every element with indexed paths', () => {
  const tags = s.array(s.string().min(2));
  assert.equal(tags.validate(['ab', 'cd']).ok, true);
  assert.deepEqual(tags.validate(['ok', 5, 'x']), {
    ok: false,
    errors: [
      { path: '[1]', message: 'expected string, got number' },
      { path: '[2]', message: 'must have at least 2 characters' },
    ],
  });
});

test('a non-array is a type error, not a per-element mess', () => {
  assert.deepEqual(s.array(s.string()).validate('nope'), {
    ok: false,
    errors: [{ path: '', message: 'expected array, got string' }],
  });
});

test('arrays of objects produce combined index and field paths', () => {
  const users = s.object({ users: s.array(s.object({ email: s.string() })) });
  assert.deepEqual(
    users.validate({ users: [{ email: 'a@b.c' }, { email: 42 }] }),
    {
      ok: false,
      errors: [{ path: 'users[1].email', message: 'expected string, got number' }],
    },
  );
});

test('a deeply broken payload reports the full inventory of problems', () => {
  const schema = s.object({
    id: s.number().int(),
    profile: s.object({
      handle: s.string().min(3).pattern(/^[a-z0-9_]+$/),
      links: s.array(s.string()),
    }),
    admin: s.optional(s.boolean()),
  });
  const result = schema.validate({
    id: 1.5,
    profile: { handle: 'A!', links: ['ok', 7] },
    admin: 'yes',
    rogue: true,
  });
  assert.deepEqual(result, {
    ok: false,
    errors: [
      { path: 'id', message: 'must be an integer' },
      { path: 'profile.handle', message: 'must have at least 3 characters' },
      { path: 'profile.handle', message: 'does not match pattern' },
      { path: 'profile.links[1]', message: 'expected string, got number' },
      { path: 'admin', message: 'expected boolean, got string' },
      { path: 'rogue', message: 'unexpected key' },
    ],
  });
});
