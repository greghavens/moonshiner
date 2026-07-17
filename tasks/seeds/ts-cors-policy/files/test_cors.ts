import { test } from 'node:test';
import assert from 'node:assert/strict';
import { compilePolicy, evaluate, needsPreflight } from './cors.ts';
import type { CorsRequest } from './cors.ts';

function req(method: string, headers: Record<string, string> = {}): CorsRequest {
  return { method, headers };
}

// ---------- policy compilation ----------

test('compilePolicy rejects credentials combined with the wildcard origin', () => {
  assert.throws(() => compilePolicy({ origins: ['*'], credentials: true }), TypeError);
});

test('compilePolicy rejects "*" mixed with other origin entries', () => {
  assert.throws(() => compilePolicy({ origins: ['*', 'https://app.example.com'] }), TypeError);
});

test('compilePolicy rejects malformed origin patterns', () => {
  assert.throws(() => compilePolicy({ origins: ['*.example.com'] }), TypeError, 'missing scheme');
  assert.throws(() => compilePolicy({ origins: ['https://ex*.com'] }), TypeError, 'star inside a label');
  assert.throws(() => compilePolicy({ origins: ['https://*'] }), TypeError, 'star with no suffix');
  assert.throws(() => compilePolicy({ origins: ['https://*.*.example.com'] }), TypeError, 'two stars');
});

test('compilePolicy accepts exact origins, one leading-label wildcard, and localhost with port', () => {
  const policy = compilePolicy({
    origins: ['https://app.example.com', 'https://*.example.com', 'http://localhost:5173'],
  });
  assert.equal(policy.originAllowed('https://app.example.com'), true);
  assert.equal(policy.originAllowed('http://localhost:5173'), true);
});

// ---------- origin matching ----------

test('exact origins compare case-insensitively', () => {
  const policy = compilePolicy({ origins: ['https://api.example.com'] });
  assert.equal(policy.originAllowed('HTTPS://API.Example.COM'), true);
  assert.equal(policy.originAllowed('https://api.example.com:8443'), false, 'explicit port is a different origin');
  assert.equal(policy.originAllowed('http://api.example.com'), false, 'scheme matters');
  assert.equal(policy.originAllowed('https://api.example.com/'), false, 'trailing slash is not an origin');
});

test('a wildcard entry matches subdomains at any depth but never the apex', () => {
  const policy = compilePolicy({ origins: ['https://*.example.com'] });
  assert.equal(policy.originAllowed('https://app.example.com'), true);
  assert.equal(policy.originAllowed('https://a.b.example.com'), true);
  assert.equal(policy.originAllowed('https://example.com'), false, 'apex must not match');
  assert.equal(policy.originAllowed('http://app.example.com'), false, 'scheme still matters');
});

test('the dot in a wildcard entry is literal, not an any-character match', () => {
  const policy = compilePolicy({ origins: ['https://*.intra.example'] });
  assert.equal(policy.originAllowed('https://box7.intra.example'), true);
  assert.equal(policy.originAllowed('https://box7.intraxexample'), false);
  assert.equal(policy.originAllowed('https://box7xintra.example'), false);
  assert.equal(policy.originAllowed('https://intra.example'), false);
});

test('the "null" origin matches only when listed literally', () => {
  const wild = compilePolicy({ origins: ['https://*.example.com'] });
  assert.equal(wild.originAllowed('null'), false);
  const listed = compilePolicy({ origins: ['null'] });
  assert.equal(listed.originAllowed('null'), true);
  const star = compilePolicy({ origins: ['*'] });
  assert.equal(star.originAllowed('null'), true, 'the full wildcard policy allows every origin');
});

// ---------- preflight requirement detection ----------

test('simple GET/HEAD/POST with safelisted headers need no preflight', () => {
  assert.equal(needsPreflight(req('GET')), false);
  assert.equal(needsPreflight(req('head')), false);
  assert.equal(needsPreflight(req('POST', { 'content-type': 'text/plain' })), false);
  assert.equal(needsPreflight(req('POST', { 'Content-Type': 'TEXT/PLAIN; charset=UTF-8' })), false);
  assert.equal(needsPreflight(req('GET', { 'Accept-Language': 'fr-CA' })), false);
});

test('non-simple methods need a preflight', () => {
  assert.equal(needsPreflight(req('DELETE')), true);
  assert.equal(needsPreflight(req('put')), true);
  assert.equal(needsPreflight(req('PATCH')), true);
});

test('non-safelisted headers and non-form content types need a preflight', () => {
  assert.equal(needsPreflight(req('GET', { 'x-request-id': 'r-100' })), true);
  assert.equal(needsPreflight(req('POST', { 'content-type': 'application/json' })), true);
  assert.equal(needsPreflight(req('POST', { 'content-type': 'multipart/form-data; boundary=b' })), false);
  assert.equal(needsPreflight(req('POST', { 'content-type': 'application/x-www-form-urlencoded' })), false);
});

// ---------- evaluate: classification ----------

test('a request without an Origin header passes through untouched', () => {
  const policy = compilePolicy({ origins: ['https://app.example.com'] });
  const d = evaluate(policy, req('GET'));
  assert.equal(d.type, 'passthrough');
  assert.equal(d.allowed, true);
  assert.deepEqual(d.headers, { vary: 'Origin' });
});

test('passthrough under a pure-wildcard policy adds no headers at all', () => {
  const policy = compilePolicy({ origins: ['*'] });
  const d = evaluate(policy, req('GET'));
  assert.equal(d.type, 'passthrough');
  assert.deepEqual(d.headers, {});
});

test('OPTIONS without Access-Control-Request-Method is an actual request, not a preflight', () => {
  const policy = compilePolicy({ origins: ['https://app.example.com'] });
  const d = evaluate(policy, req('OPTIONS', { origin: 'https://app.example.com' }));
  assert.equal(d.type, 'actual');
  assert.equal(d.allowed, true);
});

// ---------- evaluate: preflight ----------

test('allowed preflight echoes the origin and lists the configured methods', () => {
  const policy = compilePolicy({
    origins: ['https://app.example.com'],
    methods: ['get', 'delete'],
    maxAge: 600,
  });
  const d = evaluate(policy, req('OPTIONS', {
    origin: 'https://app.example.com',
    'access-control-request-method': 'DELETE',
  }));
  assert.equal(d.type, 'preflight');
  assert.equal(d.allowed, true);
  assert.equal(d.headers['access-control-allow-origin'], 'https://app.example.com');
  assert.equal(d.headers['access-control-allow-methods'], 'GET, DELETE');
  assert.equal(d.headers['access-control-max-age'], '600');
  assert.equal(d.headers.vary, 'Origin');
  assert.equal('access-control-allow-headers' in d.headers, false, 'nothing was requested, nothing to allow');
});

test('the allow-origin header echoes the origin exactly as the client sent it', () => {
  const policy = compilePolicy({ origins: ['https://app.example.com'] });
  const d = evaluate(policy, req('OPTIONS', {
    Origin: 'https://APP.Example.com',
    'Access-Control-Request-Method': 'GET',
  }));
  assert.equal(d.allowed, true);
  assert.equal(d.headers['access-control-allow-origin'], 'https://APP.Example.com');
});

test('preflight for a method outside the configured list is refused with no CORS headers', () => {
  const policy = compilePolicy({ origins: ['https://app.example.com'], methods: ['GET', 'POST'] });
  const d = evaluate(policy, req('OPTIONS', {
    origin: 'https://app.example.com',
    'access-control-request-method': 'PUT',
  }));
  assert.equal(d.type, 'preflight');
  assert.equal(d.allowed, false);
  assert.deepEqual(d.headers, { vary: 'Origin' });
});

test('preflight from a disallowed origin is refused with no CORS headers', () => {
  const policy = compilePolicy({ origins: ['https://app.example.com'] });
  const d = evaluate(policy, req('OPTIONS', {
    origin: 'https://other.example.net',
    'access-control-request-method': 'GET',
  }));
  assert.equal(d.allowed, false);
  assert.deepEqual(d.headers, { vary: 'Origin' });
});

test('allowlist mode: every requested header must be covered, response lists the configured names', () => {
  const policy = compilePolicy({
    origins: ['https://app.example.com'],
    methods: ['GET', 'POST'],
    allowHeaders: ['X-Request-Id', 'Content-Type'],
  });
  const ok = evaluate(policy, req('OPTIONS', {
    origin: 'https://app.example.com',
    'access-control-request-method': 'POST',
    'access-control-request-headers': 'content-type, x-request-id',
  }));
  assert.equal(ok.allowed, true);
  assert.equal(ok.headers['access-control-allow-headers'], 'X-Request-Id, Content-Type');

  const bad = evaluate(policy, req('OPTIONS', {
    origin: 'https://app.example.com',
    'access-control-request-method': 'POST',
    'access-control-request-headers': 'x-api-key',
  }));
  assert.equal(bad.allowed, false);
  assert.deepEqual(bad.headers, { vary: 'Origin' });
});

test('reflect mode echoes the requested header list verbatim', () => {
  const policy = compilePolicy({
    origins: ['https://app.example.com'],
    allowHeaders: 'reflect',
  });
  const d = evaluate(policy, req('OPTIONS', {
    origin: 'https://app.example.com',
    'access-control-request-method': 'GET',
    'access-control-request-headers': 'X-Trace-Id, X-Widget-Rev',
  }));
  assert.equal(d.allowed, true);
  assert.equal(d.headers['access-control-allow-headers'], 'X-Trace-Id, X-Widget-Rev');
});

test('credentialed preflight sets allow-credentials and echoes a wildcard-matched origin', () => {
  const policy = compilePolicy({
    origins: ['https://*.example.com'],
    credentials: true,
  });
  const d = evaluate(policy, req('OPTIONS', {
    origin: 'https://dash.example.com',
    'access-control-request-method': 'GET',
  }));
  assert.equal(d.allowed, true);
  assert.equal(d.headers['access-control-allow-origin'], 'https://dash.example.com');
  assert.equal(d.headers['access-control-allow-credentials'], 'true');
  assert.equal(d.headers.vary, 'Origin');
});

// ---------- evaluate: actual requests ----------

test('allowed actual request gets allow-origin plus configured expose-headers', () => {
  const policy = compilePolicy({
    origins: ['https://app.example.com'],
    exposeHeaders: ['X-Total-Count', 'X-Page'],
  });
  const d = evaluate(policy, req('GET', { origin: 'https://app.example.com' }));
  assert.equal(d.type, 'actual');
  assert.equal(d.allowed, true);
  assert.equal(d.headers['access-control-allow-origin'], 'https://app.example.com');
  assert.equal(d.headers['access-control-expose-headers'], 'X-Total-Count, X-Page');
  assert.equal('access-control-allow-methods' in d.headers, false, 'method list is preflight-only');
  assert.equal('access-control-max-age' in d.headers, false, 'max-age is preflight-only');
});

test('actual request under the pure-wildcard policy answers a literal *', () => {
  const policy = compilePolicy({ origins: ['*'] });
  const d = evaluate(policy, req('GET', { origin: 'https://anywhere.example.net' }));
  assert.equal(d.allowed, true);
  assert.equal(d.headers['access-control-allow-origin'], '*');
  assert.equal('vary' in d.headers, false);
});

test('credentialed actual request echoes the origin and never answers *', () => {
  const policy = compilePolicy({ origins: ['https://app.example.com'], credentials: true });
  const d = evaluate(policy, req('GET', { origin: 'https://app.example.com' }));
  assert.equal(d.headers['access-control-allow-origin'], 'https://app.example.com');
  assert.equal(d.headers['access-control-allow-credentials'], 'true');
});

test('actual request from a disallowed origin is refused with no CORS headers', () => {
  const policy = compilePolicy({ origins: ['https://app.example.com'] });
  const d = evaluate(policy, req('GET', { origin: 'https://app.example.com.zz' }));
  assert.equal(d.type, 'actual');
  assert.equal(d.allowed, false);
  assert.deepEqual(d.headers, { vary: 'Origin' });
});

test('expose-headers is omitted when not configured and on preflights', () => {
  const policy = compilePolicy({ origins: ['https://app.example.com'], exposeHeaders: ['X-Total-Count'] });
  const plain = evaluate(compilePolicy({ origins: ['https://app.example.com'] }),
    req('GET', { origin: 'https://app.example.com' }));
  assert.equal('access-control-expose-headers' in plain.headers, false);
  const pre = evaluate(policy, req('OPTIONS', {
    origin: 'https://app.example.com',
    'access-control-request-method': 'GET',
  }));
  assert.equal('access-control-expose-headers' in pre.headers, false);
});
