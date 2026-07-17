import { test } from 'node:test';
import assert from 'node:assert/strict';
import { Router } from './router.ts';

test('static routes match exactly', () => {
  const r = new Router();
  r.add('/health', 'health');
  r.add('/api/status', 'status');
  assert.deepEqual(r.match('/health'), { value: 'health', params: {} });
  assert.deepEqual(r.match('/api/status'), { value: 'status', params: {} });
  assert.equal(r.match('/api'), null);
  assert.equal(r.match('/api/status/extra'), null);
});

test('the root path is routable', () => {
  const r = new Router();
  r.add('/', 'home');
  assert.deepEqual(r.match('/'), { value: 'home', params: {} });
});

test('no matching route returns null, not an exception', () => {
  const r = new Router();
  r.add('/a', 1);
  assert.equal(r.match('/b'), null);
  assert.equal(r.match('/'), null);
});

test('params capture single segments by name', () => {
  const r = new Router();
  r.add('/users/:id', 'user');
  assert.deepEqual(r.match('/users/42'), { value: 'user', params: { id: '42' } });
  assert.equal(r.match('/users'), null);
  assert.equal(r.match('/users/42/posts'), null);
});

test('multiple params in one pattern all capture', () => {
  const r = new Router();
  r.add('/orgs/:org/repos/:repo', 'repo');
  assert.deepEqual(r.match('/orgs/acme/repos/gateway'), {
    value: 'repo',
    params: { org: 'acme', repo: 'gateway' },
  });
});

test('param values come back percent-decoded', () => {
  const r = new Router();
  r.add('/users/:id', 'user');
  assert.deepEqual(r.match('/users/ada%20l'), { value: 'user', params: { id: 'ada l' } });
});

test('a static segment beats a param regardless of registration order', () => {
  const first = new Router();
  first.add('/users/me', 'me');
  first.add('/users/:id', 'user');
  assert.deepEqual(first.match('/users/me'), { value: 'me', params: {} });
  assert.deepEqual(first.match('/users/7'), { value: 'user', params: { id: '7' } });

  const second = new Router();
  second.add('/users/:id', 'user');
  second.add('/users/me', 'me');
  assert.deepEqual(second.match('/users/me'), { value: 'me', params: {} });
  assert.deepEqual(second.match('/users/7'), { value: 'user', params: { id: '7' } });
});

test('precedence is decided segment by segment, earliest difference wins', () => {
  const r = new Router();
  r.add('/a/:x/c', 'param-first');
  r.add('/a/b/:y', 'static-first');
  assert.deepEqual(r.match('/a/b/c'), { value: 'static-first', params: { y: 'c' } });
  assert.deepEqual(r.match('/a/q/c'), { value: 'param-first', params: { x: 'q' } });
});

test('a param beats a wildcard', () => {
  const r = new Router();
  r.add('/files/*', 'splat');
  r.add('/files/:name', 'named');
  assert.deepEqual(r.match('/files/readme'), { value: 'named', params: { name: 'readme' } });
  assert.deepEqual(r.match('/files/docs/readme'), {
    value: 'splat',
    params: { '*': 'docs/readme' },
  });
});

test('a trailing wildcard needs at least one segment and captures the rest', () => {
  const r = new Router();
  r.add('/assets/*', 'assets');
  assert.deepEqual(r.match('/assets/logo.svg'), {
    value: 'assets',
    params: { '*': 'logo.svg' },
  });
  assert.deepEqual(r.match('/assets/img/icons/x.png'), {
    value: 'assets',
    params: { '*': 'img/icons/x.png' },
  });
  assert.equal(r.match('/assets'), null);
});

test('the matcher backtracks out of a static dead end', () => {
  const r = new Router();
  r.add('/static/:p/x', 'deep-static');
  r.add('/:param/b/y', 'shallow-param');
  // the 'static' branch matches two segments then dies on 'y' != 'x';
  // the router must fall back to the param branch at the root.
  assert.deepEqual(r.match('/static/b/y'), {
    value: 'shallow-param',
    params: { param: 'static' },
  });
  assert.deepEqual(r.match('/static/b/x'), {
    value: 'deep-static',
    params: { p: 'b' },
  });
});

test('query strings are ignored when matching', () => {
  const r = new Router();
  r.add('/users/:id', 'user');
  assert.deepEqual(r.match('/users/42?tab=posts&page=2'), {
    value: 'user',
    params: { id: '42' },
  });
});

test('a trailing slash is equivalent to none, except for the root', () => {
  const r = new Router();
  r.add('/users', 'users');
  r.add('/', 'home');
  assert.deepEqual(r.match('/users/'), { value: 'users', params: {} });
  assert.deepEqual(r.match('/'), { value: 'home', params: {} });
});

test('adding the same pattern shape twice throws, even with different param names', () => {
  const r = new Router();
  r.add('/users/:id', 'a');
  assert.throws(() => r.add('/users/:id', 'b'));
  assert.throws(() => r.add('/users/:uid', 'c'));
  const r2 = new Router();
  r2.add('/x', 1);
  assert.throws(() => r2.add('/x', 2));
});

test('patterns must start with a slash', () => {
  const r = new Router();
  assert.throws(() => r.add('users/:id', 'x'));
});

test('a wildcard anywhere but the final segment is rejected at add time', () => {
  const r = new Router();
  assert.throws(() => r.add('/files/*/meta', 'x'));
});

test('a realistic route table dispatches correctly', () => {
  const r = new Router();
  r.add('/', 'home');
  r.add('/users', 'users.index');
  r.add('/users/new', 'users.new');
  r.add('/users/:id', 'users.show');
  r.add('/users/:id/posts', 'users.posts');
  r.add('/docs/*', 'docs.catchall');

  assert.equal(r.match('/')!.value, 'home');
  assert.equal(r.match('/users')!.value, 'users.index');
  assert.equal(r.match('/users/new')!.value, 'users.new');
  assert.equal(r.match('/users/15')!.value, 'users.show');
  assert.deepEqual(r.match('/users/15/posts'), {
    value: 'users.posts',
    params: { id: '15' },
  });
  assert.equal(r.match('/docs/guide/intro')!.value, 'docs.catchall');
  assert.equal(r.match('/admin'), null);
});
