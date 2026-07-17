import { test } from 'node:test';
import assert from 'node:assert/strict';
import { MessageCatalog } from './catalog.ts';

// --- plural forms ---

test('selects one/other with the default rule and interpolates {count}', () => {
  const c = new MessageCatalog();
  c.addPlural('en', 'inbox.unread', {
    one: 'You have {count} unread message',
    other: 'You have {count} unread messages',
  });
  assert.equal(c.t('en', 'inbox.unread', { count: 1 }), 'You have 1 unread message');
  assert.equal(c.t('en', 'inbox.unread', { count: 5 }), 'You have 5 unread messages');
  assert.equal(c.t('en', 'inbox.unread', { count: 0 }), 'You have 0 unread messages');
});

test('addPlural requires an "other" form', () => {
  const c = new MessageCatalog();
  assert.throws(() => c.addPlural('en', 'k', { one: 'one thing' }));
});

test('addPlural rejects categories outside the CLDR six', () => {
  const c = new MessageCatalog();
  assert.throws(() =>
    c.addPlural('en', 'k', { other: 'x', several: 'y' } as Record<string, string>),
  );
});

test('translating a plural key without a numeric count throws TypeError', () => {
  const c = new MessageCatalog();
  c.addPlural('en', 'inbox.unread', { one: 'a', other: 'b' });
  assert.throws(() => c.t('en', 'inbox.unread'), TypeError);
  assert.throws(() => c.t('en', 'inbox.unread', { count: 'many' }), TypeError);
});

test('setPluralRule installs a per-language rule keyed by language part', () => {
  const c = new MessageCatalog();
  // simplified Polish: 1 -> one, 2..4 -> few, else -> many
  c.setPluralRule('pl', (n: number) => {
    if (n === 1) return 'one';
    if (n >= 2 && n <= 4) return 'few';
    return 'many';
  });
  c.addPlural('pl', 'files', {
    one: '{count} plik',
    few: '{count} pliki',
    many: '{count} plików',
    other: '{count} pliku',
  });
  assert.equal(c.t('pl', 'files', { count: 1 }), '1 plik');
  assert.equal(c.t('pl-PL', 'files', { count: 3 }), '3 pliki');
  assert.equal(c.t('pl', 'files', { count: 5 }), '5 plików');
});

test('a category the entry lacks falls back to its "other" form', () => {
  const c = new MessageCatalog();
  c.setPluralRule('en', (n: number) => (n === 0 ? 'zero' : n === 1 ? 'one' : 'other'));
  c.addPlural('en', 'results', { one: 'one result', other: '{count} results' });
  assert.equal(c.t('en', 'results', { count: 0 }), '0 results');
});

test('plural entries follow the locale fallback chain', () => {
  const c = new MessageCatalog();
  c.addPlural('de', 'cart.items', { one: '{count} Artikel', other: '{count} Artikel' });
  assert.equal(c.t('de-CH', 'cart.items', { count: 2 }), '2 Artikel');
});

test('has() sees plural entries', () => {
  const c = new MessageCatalog();
  c.addPlural('en', 'inbox.unread', { other: 'x' });
  assert.equal(c.has('en', 'inbox.unread'), true);
});

// --- namespace fallbacks ---

test('a key missing from its namespace resolves via the registered fallback', () => {
  const c = new MessageCatalog();
  c.addNamespaceFallback('admin', 'common');
  c.add('en', 'common.save', 'Save');
  assert.equal(c.t('en', 'admin.save'), 'Save');
});

test('an entry anywhere in the locale chain beats a namespace fallback hit', () => {
  const c = new MessageCatalog({ defaultLocale: 'en' });
  c.addNamespaceFallback('admin', 'common');
  c.add('de', 'common.save', 'Speichern');
  c.add('en', 'admin.save', 'Save changes');
  // 'admin.save' exists in the default locale, so the exact key wins even
  // though German could satisfy it through the namespace fallback.
  assert.equal(c.t('de', 'admin.save'), 'Save changes');
});

test('namespace fallbacks chain transitively', () => {
  const c = new MessageCatalog();
  c.addNamespaceFallback('wizard', 'forms');
  c.addNamespaceFallback('forms', 'common');
  c.add('en', 'common.next', 'Next');
  assert.equal(c.t('en', 'wizard.next'), 'Next');
});

test('a fallback cycle terminates and yields the key itself', () => {
  const c = new MessageCatalog();
  c.addNamespaceFallback('a', 'b');
  c.addNamespaceFallback('b', 'a');
  assert.equal(c.t('en', 'a.title'), 'a.title');
});

test('plural keys resolve through namespace fallbacks too', () => {
  const c = new MessageCatalog();
  c.addNamespaceFallback('admin', 'common');
  c.addPlural('en', 'common.rows', { one: '{count} row', other: '{count} rows' });
  assert.equal(c.t('en', 'admin.rows', { count: 2 }), '2 rows');
});
