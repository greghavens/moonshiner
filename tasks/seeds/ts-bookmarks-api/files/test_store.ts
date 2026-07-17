import { test } from 'node:test';
import assert from 'node:assert/strict';
import { BookmarkStore, ValidationError, DuplicateUrlError } from './store.ts';

test('create fills defaults, trims, and returns the stored record', () => {
  const store = new BookmarkStore();
  const b = store.create({ url: ' https://nodejs.org/docs ', title: ' Node docs ' });
  assert.equal(b.id, 'bm-1');
  assert.equal(b.url, 'https://nodejs.org/docs');
  assert.equal(b.title, 'Node docs');
  assert.deepEqual(b.tags, []);
  assert.deepEqual(Object.keys(b).sort(), ['id', 'tags', 'title', 'url']);
});

test('tags are trimmed, lowercased, and deduped keeping first appearance', () => {
  const store = new BookmarkStore();
  const b = store.create({ url: 'https://example.com/a', title: 'A', tags: [' Reading ', 'DOCS', 'reading'] });
  assert.deepEqual(b.tags, ['reading', 'docs']);
});

test('returned bookmarks are copies — mutating them cannot corrupt the store', () => {
  const store = new BookmarkStore();
  const created = store.create({ url: 'https://example.com/a', title: 'A', tags: ['docs'] });
  created.tags.push('hacked-in');
  (created as { title: string }).title = 'scribbled';
  const fresh = store.get('bm-1');
  assert.ok(fresh);
  assert.deepEqual(fresh.tags, ['docs']);
  assert.equal(fresh.title, 'A');
  fresh.tags.splice(0);
  assert.deepEqual(store.get('bm-1')?.tags, ['docs']);
});

test('create reports every invalid field at once', () => {
  const store = new BookmarkStore();
  try {
    store.create({ url: 'ftp://files.example.com', title: '   ', tags: ['ok', '  '], extra: 1 } as never);
    assert.fail('expected ValidationError');
  } catch (err) {
    assert.ok(err instanceof ValidationError);
    assert.deepEqual(Object.keys(err.fields).sort(), ['extra', 'tags', 'title', 'url']);
    for (const message of Object.values(err.fields)) {
      assert.equal(typeof message, 'string');
      assert.ok(message.length > 0);
    }
  }
  assert.equal(store.list().length, 0, 'nothing was stored');
});

test('url must be an absolute http(s) url; title and url are required', () => {
  const store = new BookmarkStore();
  assert.throws(() => store.create({ url: 'not a url', title: 'x' }), ValidationError);
  assert.throws(() => store.create({ url: 'ftp://files.example.com/a', title: 'x' }), ValidationError);
  assert.throws(() => store.create({} as never), (err: unknown) => {
    assert.ok(err instanceof ValidationError);
    assert.deepEqual(Object.keys(err.fields).sort(), ['title', 'url']);
    return true;
  });
  store.create({ url: 'http://plain.example/ok', title: 'plain http is fine' });
});

test('an exact duplicate url (after trimming) is refused with DuplicateUrlError', () => {
  const store = new BookmarkStore();
  store.create({ url: 'https://example.com/guide', title: 'first' });
  try {
    store.create({ url: '  https://example.com/guide  ', title: 'second' });
    assert.fail('expected DuplicateUrlError');
  } catch (err) {
    assert.ok(err instanceof DuplicateUrlError);
    assert.equal(err.url, 'https://example.com/guide');
  }
  // comparison is exact — a different case is a different url, allowed
  store.create({ url: 'https://Example.com/guide', title: 'different case' });
  assert.equal(store.list().length, 2);
});

test('update patches only the provided fields', () => {
  const store = new BookmarkStore();
  store.create({ url: 'https://example.com/a', title: 'A', tags: ['docs'] });
  const updated = store.update('bm-1', { title: 'A2' });
  assert.ok(updated);
  assert.equal(updated.title, 'A2');
  assert.equal(updated.url, 'https://example.com/a');
  assert.deepEqual(updated.tags, ['docs']);
  const retagged = store.update('bm-1', { tags: [' NEW ', 'new'] });
  assert.deepEqual(retagged?.tags, ['new']);
});

test('update on a missing id returns undefined before it validates anything', () => {
  const store = new BookmarkStore();
  assert.equal(store.update('bm-99', { title: '' }), undefined);
});

test('update enforces the same rules as create', () => {
  const store = new BookmarkStore();
  store.create({ url: 'https://example.com/a', title: 'A' });
  store.create({ url: 'https://example.com/b', title: 'B' });
  assert.throws(() => store.update('bm-2', { url: 'https://example.com/a' }), DuplicateUrlError);
  assert.throws(() => store.update('bm-2', { notes: 'nope' } as never), ValidationError);
  assert.throws(() => store.update('bm-2', { title: ' ' }), ValidationError);
  const same = store.update('bm-2', { url: 'https://example.com/b', title: 'B2' });
  assert.equal(same?.title, 'B2', 'rewriting your own url is not a duplicate');
});

test('remove reports whether it deleted, and ids are never reused', () => {
  const store = new BookmarkStore();
  store.create({ url: 'https://example.com/a', title: 'A' });
  store.create({ url: 'https://example.com/b', title: 'B' });
  assert.equal(store.remove('bm-2'), true);
  assert.equal(store.remove('bm-2'), false);
  assert.equal(store.get('bm-2'), undefined);
  const next = store.create({ url: 'https://example.com/c', title: 'C' });
  assert.equal(next.id, 'bm-3');
});

test('list keeps creation order even past ten entries, and filters by normalized tag', () => {
  const store = new BookmarkStore();
  for (let i = 1; i <= 11; i++) {
    store.create({
      url: `https://example.com/page-${i}`,
      title: `Page ${i}`,
      tags: i % 2 === 0 ? ['even'] : ['odd'],
    });
  }
  const ids = store.list().map((b) => b.id);
  assert.deepEqual(ids.slice(0, 3), ['bm-1', 'bm-2', 'bm-3']);
  assert.equal(ids[9], 'bm-10');
  assert.equal(ids[10], 'bm-11');
  const even = store.list({ tag: ' EVEN ' });
  assert.deepEqual(even.map((b) => b.id), ['bm-2', 'bm-4', 'bm-6', 'bm-8', 'bm-10']);
});
