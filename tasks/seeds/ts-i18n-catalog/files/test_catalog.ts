import { test } from 'node:test';
import assert from 'node:assert/strict';
import { MessageCatalog } from './catalog.ts';

test('returns the message registered for an exact locale', () => {
  const c = new MessageCatalog();
  c.add('en', 'nav.home', 'Home');
  assert.equal(c.t('en', 'nav.home'), 'Home');
});

test('falls back from a regional tag to its language', () => {
  const c = new MessageCatalog();
  c.add('de', 'nav.home', 'Startseite');
  assert.equal(c.t('de-AT', 'nav.home'), 'Startseite');
});

test('falls back to the default locale when the language has no entry', () => {
  const c = new MessageCatalog({ defaultLocale: 'en' });
  c.add('en', 'nav.settings', 'Settings');
  assert.equal(c.t('fr-CA', 'nav.settings'), 'Settings');
});

test('a regional entry wins over the plain language entry', () => {
  const c = new MessageCatalog();
  c.add('en', 'billing.currency', 'Dollar');
  c.add('en-GB', 'billing.currency', 'Pound');
  assert.equal(c.t('en-GB', 'billing.currency'), 'Pound');
  assert.equal(c.t('en-US', 'billing.currency'), 'Dollar');
});

test('an unknown key comes back as the key itself', () => {
  const c = new MessageCatalog();
  assert.equal(c.t('en', 'missing.key'), 'missing.key');
});

test('interpolates {name} placeholders from params', () => {
  const c = new MessageCatalog();
  c.add('en', 'greeting', 'Hello, {name}! You have {n} messages.');
  assert.equal(c.t('en', 'greeting', { name: 'Ada', n: 3 }), 'Hello, Ada! You have 3 messages.');
});

test('leaves a placeholder verbatim when its param is missing', () => {
  const c = new MessageCatalog();
  c.add('en', 'greeting', 'Hello, {name}!');
  assert.equal(c.t('en', 'greeting'), 'Hello, {name}!');
});

test('addAll registers a batch of entries and has() sees the fallback chain', () => {
  const c = new MessageCatalog();
  c.addAll('en', { 'a.one': '1', 'a.two': '2' });
  assert.equal(c.t('en', 'a.two'), '2');
  assert.equal(c.has('pt-BR', 'a.one'), true);
  assert.equal(c.has('en', 'a.three'), false);
});

test('add rejects empty locale or key', () => {
  const c = new MessageCatalog();
  assert.throws(() => c.add('', 'k', 'v'));
  assert.throws(() => c.add('en', '', 'v'));
});
