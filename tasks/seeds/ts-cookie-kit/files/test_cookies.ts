import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  parseCookieHeader,
  parseSetCookie,
  serializeSetCookie,
  cookieExpiresAt,
  isExpired,
} from './cookies.ts';

const JUN_9_2027 = Date.UTC(2027, 5, 9, 10, 18, 14); // Wed, 09 Jun 2027 10:18:14 GMT
const NOW = Date.UTC(2027, 0, 15, 12, 0, 0);

// ---------- parseCookieHeader (the request-side Cookie header) ----------

test('splits pairs on semicolons and trims surrounding whitespace', () => {
  assert.deepEqual(parseCookieHeader('sid=abc123; theme=dark'), { sid: 'abc123', theme: 'dark' });
  assert.deepEqual(parseCookieHeader('  sid = abc123 ;theme= dark '), { sid: 'abc123', theme: 'dark' });
});

test('only the first occurrence of a name counts', () => {
  assert.deepEqual(parseCookieHeader('lang=fr; region=ca; lang=en'), { lang: 'fr', region: 'ca' });
});

test('values keep everything after the first equals sign and may be quoted', () => {
  assert.deepEqual(parseCookieHeader('tok=abc=def'), { tok: 'abc=def' });
  assert.deepEqual(parseCookieHeader('pref="compact"'), { pref: 'compact' });
  assert.deepEqual(parseCookieHeader('flag='), { flag: '' });
});

test('segments without an equals sign, empty segments, and empty names are skipped', () => {
  assert.deepEqual(parseCookieHeader('a=1; ; junk; =zzz; b=2'), { a: '1', b: '2' });
});

test('no percent-decoding happens at this layer', () => {
  assert.deepEqual(parseCookieHeader('q=one%20two'), { q: 'one%20two' });
});

// ---------- parseSetCookie (the response-side Set-Cookie line) ----------

test('parses a fully loaded Set-Cookie line', () => {
  const c = parseSetCookie(
    'sid=A3fWa; Domain=.Example.COM; Path=/account; ' +
    'Expires=Wed, 09 Jun 2027 10:18:14 GMT; Max-Age=3600; Secure; HttpOnly; SameSite=lax');
  assert.ok(c);
  assert.equal(c.name, 'sid');
  assert.equal(c.value, 'A3fWa');
  assert.equal(c.domain, 'example.com', 'domain is lowercased with the leading dot stripped');
  assert.equal(c.path, '/account');
  assert.equal(c.expires, JUN_9_2027);
  assert.equal(c.maxAge, 3600);
  assert.equal(c.secure, true);
  assert.equal(c.httpOnly, true);
  assert.equal(c.sameSite, 'Lax');
});

test('attribute names are case-insensitive and quoted values are unwrapped', () => {
  const c = parseSetCookie('sid="A3fWa"; MAX-AGE=5; HTTPONLY');
  assert.ok(c);
  assert.equal(c.value, 'A3fWa');
  assert.equal(c.maxAge, 5);
  assert.equal(c.httpOnly, true);
  assert.equal(c.secure, false);
});

test('malformed Max-Age or Expires drops just that attribute; negatives are legal Max-Age', () => {
  const c1 = parseSetCookie('sid=x; Max-Age=12abc; Expires=whenever');
  assert.ok(c1);
  assert.equal(c1.maxAge, undefined);
  assert.equal(c1.expires, undefined);
  const c2 = parseSetCookie('sid=x; Max-Age=-1');
  assert.ok(c2);
  assert.equal(c2.maxAge, -1);
});

test('when an attribute repeats, the last occurrence wins', () => {
  const c = parseSetCookie('sid=x; Path=/a; Path=/b');
  assert.ok(c);
  assert.equal(c.path, '/b');
});

test('unknown attributes and unknown SameSite values are ignored', () => {
  const c = parseSetCookie('sid=x; X-Extra=1; SameSite=Whatever');
  assert.ok(c);
  assert.equal(c.sameSite, undefined);
});

test('lines without a name=value pair parse to null', () => {
  assert.equal(parseSetCookie(''), null);
  assert.equal(parseSetCookie('noequals'), null);
  assert.equal(parseSetCookie('=orphan; Path=/'), null);
});

test('SameSite=None requires Secure or the whole cookie is rejected', () => {
  assert.equal(parseSetCookie('sid=x; SameSite=None'), null);
  const ok = parseSetCookie('sid=x; SameSite=none; Secure');
  assert.ok(ok);
  assert.equal(ok.sameSite, 'None');
});

test('__Secure- prefix demands Secure', () => {
  assert.equal(parseSetCookie('__Secure-sid=x; Path=/'), null);
  const ok = parseSetCookie('__Secure-sid=x; Secure');
  assert.ok(ok);
  assert.equal(ok.name, '__Secure-sid');
});

test('__Host- prefix demands Secure, Path=/, and no Domain', () => {
  assert.equal(parseSetCookie('__Host-sid=x; Secure; Path=/; Domain=example.com'), null);
  assert.equal(parseSetCookie('__Host-sid=x; Secure; Path=/app'), null);
  assert.equal(parseSetCookie('__Host-sid=x; Secure'), null, 'Path=/ must be explicit');
  assert.equal(parseSetCookie('__Host-sid=x; Path=/'), null, 'Secure is required');
  const ok = parseSetCookie('__Host-sid=x; Secure; Path=/');
  assert.ok(ok);
  assert.equal(ok.name, '__Host-sid');
});

// ---------- serializeSetCookie ----------

test('serializes name=value with attributes in canonical order', () => {
  const line = serializeSetCookie({
    name: 'sid', value: 'abc', domain: 'Example.com', path: '/account',
    expires: JUN_9_2027, maxAge: 3600, secure: true, httpOnly: true, sameSite: 'lax',
  });
  assert.equal(line,
    'sid=abc; Domain=example.com; Path=/account; ' +
    'Expires=Wed, 09 Jun 2027 10:18:14 GMT; Max-Age=3600; Secure; HttpOnly; SameSite=Lax');
});

test('a bare cookie serializes to just name=value, empty value allowed', () => {
  assert.equal(serializeSetCookie({ name: 'sid', value: 'abc' }), 'sid=abc');
  assert.equal(serializeSetCookie({ name: 'sid', value: '' }), 'sid=');
});

test('invalid names are refused', () => {
  for (const name of ['', 'si d', 'sid;', 'na,me', 'sid=']) {
    assert.throws(() => serializeSetCookie({ name, value: 'v' }), TypeError, `name ${JSON.stringify(name)}`);
  }
});

test('values may not contain separators, quotes, backslashes, or whitespace', () => {
  for (const value of ['a b', 'a;b', 'a,b', 'a"b', 'a\\b']) {
    assert.throws(() => serializeSetCookie({ name: 'sid', value }), TypeError, `value ${JSON.stringify(value)}`);
  }
});

test('SameSite is normalized and None demands Secure', () => {
  assert.throws(() => serializeSetCookie({ name: 'sid', value: 'v', sameSite: 'None' }), TypeError);
  assert.equal(
    serializeSetCookie({ name: 'sid', value: 'v', sameSite: 'none', secure: true }),
    'sid=v; Secure; SameSite=None');
  assert.throws(() => serializeSetCookie({ name: 'sid', value: 'v', sameSite: 'never' }), TypeError);
});

test('prefix rules are enforced on the way out too', () => {
  assert.throws(() => serializeSetCookie({ name: '__Secure-sid', value: 'v' }), TypeError);
  assert.throws(
    () => serializeSetCookie({ name: '__Host-sid', value: 'v', secure: true, path: '/app' }), TypeError);
  assert.throws(
    () => serializeSetCookie({ name: '__Host-sid', value: 'v', secure: true, path: '/', domain: 'example.com' }),
    TypeError);
  assert.equal(
    serializeSetCookie({ name: '__Host-sid', value: 'v', secure: true, path: '/' }),
    '__Host-sid=v; Path=/; Secure');
});

test('paths must be absolute and semicolon-free; Max-Age must be an integer', () => {
  assert.throws(() => serializeSetCookie({ name: 's', value: 'v', path: 'account' }), TypeError);
  assert.throws(() => serializeSetCookie({ name: 's', value: 'v', path: '/a;b' }), TypeError);
  assert.throws(() => serializeSetCookie({ name: 's', value: 'v', maxAge: 1.5 }), TypeError);
  assert.equal(serializeSetCookie({ name: 's', value: 'v', maxAge: -1 }), 's=v; Max-Age=-1');
});

// ---------- expiry math ----------

test('a cookie with neither Expires nor Max-Age is a session cookie', () => {
  const c = parseSetCookie('sid=x');
  assert.ok(c);
  assert.equal(cookieExpiresAt(c, NOW), null);
  assert.equal(isExpired(c, NOW), false);
});

test('Max-Age counts from now', () => {
  const c = parseSetCookie('sid=x; Max-Age=3600');
  assert.ok(c);
  assert.equal(cookieExpiresAt(c, NOW), NOW + 3600 * 1000);
});

test('Max-Age wins over Expires when both are present', () => {
  const c = parseSetCookie(`sid=x; Expires=Wed, 09 Jun 2027 10:18:14 GMT; Max-Age=60`);
  assert.ok(c);
  assert.equal(cookieExpiresAt(c, NOW), NOW + 60 * 1000);
});

test('zero or negative Max-Age expires the cookie immediately', () => {
  const zero = parseSetCookie('sid=x; Max-Age=0');
  assert.ok(zero);
  assert.equal(isExpired(zero, NOW), true);
  const neg = parseSetCookie('sid=x; Max-Age=-5');
  assert.ok(neg);
  assert.equal(isExpired(neg, NOW), true);
});

test('Expires alone sets the deadline, inclusive at the boundary', () => {
  const c = parseSetCookie('sid=x; Expires=Wed, 09 Jun 2027 10:18:14 GMT');
  assert.ok(c);
  assert.equal(cookieExpiresAt(c, NOW), JUN_9_2027);
  assert.equal(isExpired(c, JUN_9_2027 - 1), false);
  assert.equal(isExpired(c, JUN_9_2027), true);
});
