import { test } from 'node:test';
import assert from 'node:assert/strict';
import { buildBreadcrumbs } from './breadcrumbs.ts';

test('builds home plus one crumb per segment with cumulative hrefs', () => {
  assert.deepEqual(buildBreadcrumbs('/docs/guides'), [
    { label: 'Home', href: '/', current: false },
    { label: 'Docs', href: '/docs', current: false },
    { label: 'Guides', href: '/docs/guides', current: true },
  ]);
});

test('humanizes kebab and snake segments', () => {
  const crumbs = buildBreadcrumbs('/docs/getting-started/api_reference');
  assert.equal(crumbs[2].label, 'Getting Started');
  assert.equal(crumbs[3].label, 'Api Reference');
});

test('href-keyed labels override humanization', () => {
  const crumbs = buildBreadcrumbs('/docs/api', {
    labels: { '/docs': 'Documentation', '/docs/api': 'API Reference' },
  });
  assert.equal(crumbs[1].label, 'Documentation');
  assert.equal(crumbs[2].label, 'API Reference');
});

test('the root path is a single current home crumb', () => {
  assert.deepEqual(buildBreadcrumbs('/'), [{ label: 'Home', href: '/', current: true }]);
});

test('trailing slash and query string are ignored', () => {
  assert.deepEqual(buildBreadcrumbs('/docs/'), buildBreadcrumbs('/docs'));
  assert.deepEqual(buildBreadcrumbs('/docs?page=2'), buildBreadcrumbs('/docs'));
});

test('only the last crumb is current', () => {
  const crumbs = buildBreadcrumbs('/a/b/c');
  assert.deepEqual(crumbs.map((c) => c.current), [false, false, false, true]);
});

test('a custom home label is used', () => {
  assert.equal(buildBreadcrumbs('/docs', { homeLabel: 'Start' })[0].label, 'Start');
});

test('paths not starting with / are rejected', () => {
  assert.throws(() => buildBreadcrumbs('docs/guides'));
});
