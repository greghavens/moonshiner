import { test } from 'node:test';
import assert from 'node:assert/strict';
import { buildBreadcrumbs } from './breadcrumbs.ts';

// --- locale labels ---

const localeLabels = {
  de: { '/': 'Startseite', '/docs': 'Doku', '/docs/guides': 'Anleitungen' },
  'de-CH': { '/docs': 'Dokumentation' },
  fr: { '/docs': 'Documentation' },
};

test('labels come from the requested locale', () => {
  const crumbs = buildBreadcrumbs('/docs/guides', { locale: 'de', localeLabels });
  assert.deepEqual(crumbs.map((c) => c.label), ['Startseite', 'Doku', 'Anleitungen']);
});

test('a regional locale falls back to its language for missing hrefs', () => {
  const crumbs = buildBreadcrumbs('/docs/guides', { locale: 'de-CH', localeLabels });
  // '/docs' has a de-CH override, the rest fall back to 'de'
  assert.deepEqual(crumbs.map((c) => c.label), ['Startseite', 'Dokumentation', 'Anleitungen']);
});

test('locale misses fall back to the default labels map, then humanization', () => {
  const crumbs = buildBreadcrumbs('/docs/new-stuff', {
    locale: 'fr',
    localeLabels,
    labels: { '/docs/new-stuff': 'Fresh' },
    homeLabel: 'Start',
  });
  assert.deepEqual(crumbs.map((c) => c.label), ['Start', 'Documentation', 'Fresh']);
  const noDefault = buildBreadcrumbs('/docs/new-stuff', { locale: 'fr', localeLabels });
  assert.equal(noDefault[2].label, 'New Stuff');
});

test('a "/" entry in the locale labels renames the home crumb', () => {
  const crumbs = buildBreadcrumbs('/', { locale: 'de-AT', localeLabels });
  assert.deepEqual(crumbs, [{ label: 'Startseite', href: '/', current: true }]);
});

// --- middle-collapse ellipsis ---

test('long trails collapse the middle into one ellipsis crumb', () => {
  const crumbs = buildBreadcrumbs('/a/b/c/d/e', { maxItems: 4 });
  assert.equal(crumbs.length, 4);
  assert.deepEqual(crumbs[0], { label: 'Home', href: '/', current: false });
  const ellipsis = crumbs[1];
  assert.equal(ellipsis.label, '…');
  assert.equal(ellipsis.href, '');
  assert.equal(ellipsis.current, false);
  assert.equal(ellipsis.ellipsis, true);
  assert.deepEqual(
    ellipsis.collapsed,
    [
      { label: 'A', href: '/a', current: false },
      { label: 'B', href: '/a/b', current: false },
      { label: 'C', href: '/a/b/c', current: false },
    ],
  );
  assert.deepEqual(crumbs[2], { label: 'D', href: '/a/b/c/d', current: false });
  assert.deepEqual(crumbs[3], { label: 'E', href: '/a/b/c/d/e', current: true });
});

test('a trail exactly at maxItems is left alone, byte for byte', () => {
  const plain = buildBreadcrumbs('/a/b/c');
  assert.deepEqual(buildBreadcrumbs('/a/b/c', { maxItems: 4 }), plain);
  assert.deepEqual(buildBreadcrumbs('/a/b/c', { maxItems: 10 }), plain);
});

test('the ellipsis itself takes a slot, so going one over hides two crumbs', () => {
  const crumbs = buildBreadcrumbs('/a/b/c/d', { maxItems: 4 });
  assert.equal(crumbs.length, 4);
  assert.equal(crumbs[1].ellipsis, true);
  assert.deepEqual(crumbs[1].collapsed, [
    { label: 'A', href: '/a', current: false },
    { label: 'B', href: '/a/b', current: false },
  ]);
  assert.deepEqual(crumbs.map((c) => c.href), ['/', '', '/a/b/c', '/a/b/c/d']);
});

test('maxItems below 3 is a RangeError', () => {
  assert.throws(() => buildBreadcrumbs('/a/b/c/d', { maxItems: 2 }), RangeError);
  assert.throws(() => buildBreadcrumbs('/a', { maxItems: 0 }), RangeError);
});

test('collapsed crumbs keep their locale labels and current stays on the tail', () => {
  const crumbs = buildBreadcrumbs('/docs/guides/routing/nested', {
    locale: 'de',
    localeLabels,
    maxItems: 3,
  });
  assert.equal(crumbs.length, 3);
  assert.equal(crumbs[0].label, 'Startseite');
  assert.deepEqual(
    crumbs[1].collapsed!.map((c) => c.label),
    ['Doku', 'Anleitungen', 'Routing'],
  );
  assert.deepEqual(crumbs.map((c) => c.current), [false, false, true]);
});
