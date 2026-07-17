import { test } from 'node:test';
import assert from 'node:assert/strict';
import { encode, decode } from './qs.ts';

test('encodes flat string pairs joined by &', () => {
  assert.equal(encode({ a: '1', b: 'two' }), 'a=1&b=two');
});

test('decodes flat pairs into an object of strings', () => {
  assert.deepEqual(decode('a=1&b=two'), { a: '1', b: 'two' });
});

test('empty object encodes to the empty string and back', () => {
  assert.equal(encode({}), '');
  assert.deepEqual(decode(''), {});
});

test('decode strips a single leading question mark', () => {
  assert.deepEqual(decode('?page=3'), { page: '3' });
});

test('a bare key with no equals sign decodes to the empty string', () => {
  assert.deepEqual(decode('archived&q=cats'), { archived: '', q: 'cats' });
});

test('values are percent-encoded on the way out', () => {
  assert.equal(encode({ q: 'a&b=c d' }), 'q=a%26b%3Dc%20d');
});

test('values are percent-decoded on the way in', () => {
  assert.deepEqual(decode('q=a%26b%3Dc%20d'), { q: 'a&b=c d' });
});

test('plus signs in values decode as spaces', () => {
  assert.deepEqual(decode('q=hello+world+again'), { q: 'hello world again' });
});

test('an unencoded equals sign inside a value belongs to the value', () => {
  assert.deepEqual(decode('expr=a=b'), { expr: 'a=b' });
});

test('nested objects encode with bracket keys', () => {
  assert.equal(
    encode({ user: { name: 'ada', role: 'admin' } }),
    'user[name]=ada&user[role]=admin',
  );
});

test('bracket keys decode into nested objects', () => {
  assert.deepEqual(decode('user[name]=ada&user[role]=admin'), {
    user: { name: 'ada', role: 'admin' },
  });
});

test('nesting works to arbitrary depth', () => {
  assert.equal(encode({ a: { b: { c: 'deep' } } }), 'a[b][c]=deep');
  assert.deepEqual(decode('a[b][c]=deep'), { a: { b: { c: 'deep' } } });
});

test('arrays encode as repeated empty-bracket keys', () => {
  assert.equal(encode({ tags: ['x', 'y'] }), 'tags[]=x&tags[]=y');
});

test('empty-bracket keys decode into arrays in order', () => {
  assert.deepEqual(decode('tags[]=x&tags[]=y&tags[]=z'), { tags: ['x', 'y', 'z'] });
});

test('a single empty-bracket pair still decodes to a one-element array', () => {
  assert.deepEqual(decode('tags[]=only'), { tags: ['only'] });
});

test('repeated plain keys collect into an array', () => {
  assert.deepEqual(decode('id=1&id=2&id=3'), { id: ['1', '2', '3'] });
});

test('arrays nested inside objects round-trip', () => {
  const wire = 'user[pets][]=cat&user[pets][]=dog';
  assert.equal(encode({ user: { pets: ['cat', 'dog'] } }), wire);
  assert.deepEqual(decode(wire), { user: { pets: ['cat', 'dog'] } });
});

test('top-level key names are percent-encoded', () => {
  assert.equal(encode({ 'full name': 'ada' }), 'full%20name=ada');
  assert.deepEqual(decode('full%20name=ada'), { 'full name': 'ada' });
});

test('nested segment names are encoded individually, brackets stay literal', () => {
  assert.equal(encode({ user: { 'first name': 'ada' } }), 'user[first%20name]=ada');
  assert.deepEqual(decode('user[first%20name]=ada'), { user: { 'first name': 'ada' } });
});

test('number and boolean leaves are stringified by encode', () => {
  assert.equal(encode({ n: 3, ok: true, off: false }), 'n=3&ok=true&off=false');
});

test('decode(encode(x)) round-trips a realistic filter object', () => {
  const filters = {
    q: 'wireless mouse',
    page: 2,
    sort: { field: 'price', dir: 'asc' },
    brands: ['logi tech', 'razer'],
    facets: { color: { include: ['black', 'white'] } },
  };
  assert.deepEqual(decode(encode(filters)), {
    q: 'wireless mouse',
    page: '2',
    sort: { field: 'price', dir: 'asc' },
    brands: ['logi tech', 'razer'],
    facets: { color: { include: ['black', 'white'] } },
  });
});
