import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  parseCacheControl,
  freshnessLifetimeMs,
  currentAgeMs,
  evaluateCached,
  evaluateConditional,
} from './freshness.ts';
import type { StoredResponse } from './freshness.ts';

// Fixed clock for every scenario: stored at noon, April 1 2025 UTC.
const T0 = Date.UTC(2025, 3, 1, 12, 0, 0); // 'Tue, 01 Apr 2025 12:00:00 GMT'
const HTTP_T0 = 'Tue, 01 Apr 2025 12:00:00 GMT';
const SEC = 1000;
const HOUR = 3600 * SEC;

function resp(headers: Record<string, string>, status = 200, storedAtMs = T0): StoredResponse {
  return { status, headers, storedAtMs };
}

// ---------- parseCacheControl ----------

test('missing header parses to the empty policy', () => {
  const cc = parseCacheControl(undefined);
  assert.equal(cc.maxAge, null);
  assert.equal(cc.sMaxage, null);
  assert.equal(cc.noCache, false);
  assert.equal(cc.noStore, false);
  assert.equal(cc.mustRevalidate, false);
  assert.deepEqual(cc.extensions, {});
});

test('directive names are case-insensitive and quoted values are unwrapped', () => {
  const cc = parseCacheControl('Max-Age="120", No-Cache, PRIVATE');
  assert.equal(cc.maxAge, 120);
  assert.equal(cc.noCache, true);
  assert.equal(cc.private, true);
});

test('non-integer or negative seconds drop the directive', () => {
  assert.equal(parseCacheControl('max-age=abc').maxAge, null);
  assert.equal(parseCacheControl('max-age=-5').maxAge, null);
  assert.equal(parseCacheControl('max-age=60.5').maxAge, null);
  assert.equal(parseCacheControl('s-maxage=9x').sMaxage, null);
});

test('when a directive repeats, the first occurrence wins', () => {
  const cc = parseCacheControl('max-age=30, public, max-age=90');
  assert.equal(cc.maxAge, 30);
});

test('unknown directives land in extensions, bare tokens as true', () => {
  const cc = parseCacheControl('stale-while-revalidate=30, x-cdn=edge7, x-fast');
  assert.deepEqual(cc.extensions, { 'stale-while-revalidate': '30', 'x-cdn': 'edge7', 'x-fast': true });
});

test('whitespace around commas and equals signs is tolerated', () => {
  const cc = parseCacheControl('  max-age = 60 ,  public , s-maxage= 600');
  assert.equal(cc.maxAge, 60);
  assert.equal(cc.public, true);
  assert.equal(cc.sMaxage, 600);
});

// ---------- freshnessLifetimeMs ----------

test('s-maxage beats max-age for a shared cache; private caches ignore it', () => {
  const r = resp({ 'cache-control': 'max-age=60, s-maxage=600' });
  assert.equal(freshnessLifetimeMs(r, { shared: true }), 600 * SEC);
  assert.equal(freshnessLifetimeMs(r), 60 * SEC);
});

test('max-age beats Expires', () => {
  const r = resp({
    'cache-control': 'max-age=60',
    date: HTTP_T0,
    expires: 'Tue, 01 Apr 2025 13:00:00 GMT',
  });
  assert.equal(freshnessLifetimeMs(r), 60 * SEC);
});

test('Expires minus Date gives the lifetime when no max-age is present', () => {
  const r = resp({ date: HTTP_T0, expires: 'Tue, 01 Apr 2025 12:05:00 GMT' });
  assert.equal(freshnessLifetimeMs(r), 300 * SEC);
});

test('Expires with a missing Date header measures from the stored-at time', () => {
  const r = resp({ expires: 'Tue, 01 Apr 2025 12:01:00 GMT' });
  assert.equal(freshnessLifetimeMs(r), 60 * SEC);
});

test('an Expires in the past or unparseable means already expired', () => {
  assert.equal(freshnessLifetimeMs(resp({ date: HTTP_T0, expires: 'Tue, 01 Apr 2025 11:00:00 GMT' })), 0);
  assert.equal(freshnessLifetimeMs(resp({ date: HTTP_T0, expires: '0' })), 0);
});

test('heuristic freshness is a fraction of Date minus Last-Modified', () => {
  const r = resp({ date: HTTP_T0, 'last-modified': 'Tue, 01 Apr 2025 02:00:00 GMT' });
  assert.equal(freshnessLifetimeMs(r), HOUR, '10% of ten hours');
  assert.equal(freshnessLifetimeMs(r, { heuristicFraction: 0.2 }), 2 * HOUR);
});

test('heuristic freshness is capped at 24 hours', () => {
  const r = resp({ date: HTTP_T0, 'last-modified': 'Wed, 12 Mar 2025 12:00:00 GMT' });
  assert.equal(freshnessLifetimeMs(r), 24 * HOUR);
});

test('heuristics apply only to heuristically cacheable statuses', () => {
  const headers = { date: HTTP_T0, 'last-modified': 'Tue, 01 Apr 2025 02:00:00 GMT' };
  assert.equal(freshnessLifetimeMs(resp(headers, 302)), 0);
  assert.equal(freshnessLifetimeMs(resp(headers, 404)), HOUR);
});

test('no freshness information at all means a lifetime of zero', () => {
  assert.equal(freshnessLifetimeMs(resp({})), 0);
});

// ---------- currentAgeMs ----------

test('age without Date or Age headers is just resident time', () => {
  assert.equal(currentAgeMs(resp({}), T0 + 10 * SEC), 10 * SEC);
});

test('a Date older than the stored-at time adds apparent age', () => {
  const r = resp({ date: 'Tue, 01 Apr 2025 11:59:30 GMT' });
  assert.equal(currentAgeMs(r, T0 + 10 * SEC), 40 * SEC);
});

test('the Age header wins when larger than apparent age', () => {
  const r = resp({ date: HTTP_T0, age: '50' });
  assert.equal(currentAgeMs(r, T0 + 10 * SEC), 60 * SEC);
});

test('a garbage Age header is ignored and clock skew never goes negative', () => {
  const r = resp({ date: 'Tue, 01 Apr 2025 12:00:40 GMT', age: 'abc' });
  assert.equal(currentAgeMs(r, T0 + 10 * SEC), 10 * SEC);
});

// ---------- evaluateCached decision table ----------

test('a fresh response is served from cache', () => {
  const r = resp({ 'cache-control': 'max-age=300' });
  const d = evaluateCached(r, {}, T0 + 100 * SEC);
  assert.equal(d.action, 'serve');
  assert.equal(d.conditionalHeaders, undefined);
});

test('age equal to lifetime is already stale', () => {
  const r = resp({ 'cache-control': 'max-age=100', etag: '"v1"' });
  const d = evaluateCached(r, {}, T0 + 100 * SEC);
  assert.equal(d.action, 'revalidate');
});

test('stale with an ETag revalidates with If-None-Match', () => {
  const r = resp({ 'cache-control': 'max-age=10', etag: 'W/"v42"' });
  const d = evaluateCached(r, {}, T0 + HOUR);
  assert.equal(d.action, 'revalidate');
  assert.deepEqual(d.conditionalHeaders, { 'if-none-match': 'W/"v42"' });
});

test('stale with only Last-Modified echoes it as If-Modified-Since, and both validators travel together', () => {
  const lm = 'Mon, 31 Mar 2025 08:00:00 GMT';
  const only = evaluateCached(resp({ 'cache-control': 'max-age=10', 'last-modified': lm }), {}, T0 + HOUR);
  assert.deepEqual(only.conditionalHeaders, { 'if-modified-since': lm });
  const both = evaluateCached(
    resp({ 'cache-control': 'max-age=10', etag: '"v7"', 'last-modified': lm }), {}, T0 + HOUR);
  assert.deepEqual(both.conditionalHeaders, { 'if-none-match': '"v7"', 'if-modified-since': lm });
});

test('stale with no validators must fetch', () => {
  const d = evaluateCached(resp({ 'cache-control': 'max-age=10' }), {}, T0 + HOUR);
  assert.equal(d.action, 'fetch');
  assert.equal(d.conditionalHeaders, undefined);
});

test('no-store on either side forces a fetch, never a conditional', () => {
  const stored = resp({ 'cache-control': 'no-store, max-age=300', etag: '"v1"' });
  assert.equal(evaluateCached(stored, {}, T0 + SEC).action, 'fetch');
  const fresh = resp({ 'cache-control': 'max-age=300', etag: '"v1"' });
  const d = evaluateCached(fresh, { 'cache-control': 'no-store' }, T0 + SEC);
  assert.equal(d.action, 'fetch');
  assert.equal(d.conditionalHeaders, undefined);
});

test('no-cache means revalidate even while fresh, fetch when unverifiable', () => {
  const withValidator = resp({ 'cache-control': 'no-cache, max-age=300', etag: '"v1"' });
  const d1 = evaluateCached(withValidator, {}, T0 + SEC);
  assert.equal(d1.action, 'revalidate');
  assert.deepEqual(d1.conditionalHeaders, { 'if-none-match': '"v1"' });
  const bare = resp({ 'cache-control': 'no-cache, max-age=300' });
  assert.equal(evaluateCached(bare, {}, T0 + SEC).action, 'fetch');
});

test('a client sending no-cache forces revalidation of a fresh entry', () => {
  const r = resp({ 'cache-control': 'max-age=300', etag: '"v1"' });
  const d = evaluateCached(r, { 'cache-control': 'no-cache' }, T0 + SEC);
  assert.equal(d.action, 'revalidate');
});

test('a private response is useless to a shared cache', () => {
  const r = resp({ 'cache-control': 'private, max-age=300', etag: '"v1"' });
  const d = evaluateCached(r, {}, T0 + SEC, { shared: true });
  assert.equal(d.action, 'fetch');
  assert.equal(d.conditionalHeaders, undefined);
  assert.equal(evaluateCached(r, {}, T0 + SEC).action, 'serve', 'private cache may still serve it');
});

// ---------- evaluateConditional (origin-server 304/412 table) ----------

test('no conditional headers means a plain 200', () => {
  assert.deepEqual(evaluateConditional({}, { etag: '"v1"' }), { status: 200 });
});

test('If-None-Match matching the current ETag turns GET/HEAD into 304', () => {
  assert.equal(evaluateConditional({ 'if-none-match': '"v1"' }, { etag: '"v1"' }).status, 304);
  assert.equal(evaluateConditional({ 'if-none-match': '"v1"' }, { etag: '"v1"' }, 'HEAD').status, 304);
});

test('If-None-Match uses weak comparison in both directions', () => {
  assert.equal(evaluateConditional({ 'if-none-match': 'W/"v1"' }, { etag: '"v1"' }).status, 304);
  assert.equal(evaluateConditional({ 'if-none-match': '"v1"' }, { etag: 'W/"v1"' }).status, 304);
});

test('If-None-Match handles lists, including ETags with commas inside the quotes', () => {
  const current = { etag: '"v2,final"' };
  assert.equal(evaluateConditional({ 'if-none-match': '"other", "v2,final"' }, current).status, 304);
  assert.equal(evaluateConditional({ 'if-none-match': '"other", "v2"' }, current).status, 200);
});

test('If-None-Match: * matches whenever a representation exists', () => {
  assert.equal(evaluateConditional({ 'if-none-match': '*' }, { etag: '"anything"' }).status, 304);
  assert.equal(evaluateConditional({ 'if-none-match': '*' }, {}).status, 200);
});

test('a matching If-None-Match on a write method is 412, not 304', () => {
  assert.equal(evaluateConditional({ 'if-none-match': '"v1"' }, { etag: '"v1"' }, 'PUT').status, 412);
  assert.equal(evaluateConditional({ 'if-none-match': '"v1"' }, { etag: '"v1"' }, 'DELETE').status, 412);
});

test('when If-None-Match is present but misses, If-Modified-Since is ignored', () => {
  const current = { etag: '"v2"', lastModifiedMs: Date.UTC(2025, 2, 30, 0, 0, 0) };
  const headers = {
    'if-none-match': '"v1"',
    'if-modified-since': 'Tue, 01 Apr 2025 00:00:00 GMT', // alone this would say not-modified
  };
  assert.equal(evaluateConditional(headers, current).status, 200);
});

test('If-Modified-Since compares against Last-Modified, equal timestamps included', () => {
  const lmMs = Date.UTC(2025, 2, 30, 8, 0, 0);
  const at = 'Sun, 30 Mar 2025 08:00:00 GMT';
  const before = 'Sun, 30 Mar 2025 07:59:59 GMT';
  assert.equal(evaluateConditional({ 'if-modified-since': at }, { lastModifiedMs: lmMs }).status, 304);
  assert.equal(evaluateConditional({ 'if-modified-since': before }, { lastModifiedMs: lmMs }).status, 200);
});

test('If-Modified-Since is ignored for non-GET/HEAD methods, bad dates, or unknown modification times', () => {
  const at = 'Sun, 30 Mar 2025 08:00:00 GMT';
  const lmMs = Date.UTC(2025, 2, 30, 8, 0, 0);
  assert.equal(evaluateConditional({ 'if-modified-since': at }, { lastModifiedMs: lmMs }, 'POST').status, 200);
  assert.equal(evaluateConditional({ 'if-modified-since': 'yesterday' }, { lastModifiedMs: lmMs }).status, 200);
  assert.equal(evaluateConditional({ 'if-modified-since': at }, {}).status, 200);
});
