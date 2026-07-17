import { test } from 'node:test';
import assert from 'node:assert/strict';
import { Catalog } from './catalog.ts';
import { importFeed } from './importer.ts';
import type { FeedItem } from './importer.ts';

function item(guid: string, title: string, durationS = 1800): FeedItem {
  return { guid, title, durationS };
}

test('importing a fresh feed stores every episode and reports it', async () => {
  const catalog = new Catalog();
  const report = await importFeed(catalog, [
    item('ep-101', 'The Cold Open'),
    item('ep-102', 'Mailbag Special'),
    item('ep-103', 'Season Finale'),
  ]);

  assert.equal(report.imported, 3);
  assert.equal(report.skipped, 0);
  assert.deepEqual(report.failures, []);
  assert.equal(await catalog.count(), 3);
  assert.deepEqual(await catalog.allGuids(), ['ep-101', 'ep-102', 'ep-103']);
});

test('episodes already in the catalog are skipped, not overwritten', async () => {
  const catalog = new Catalog();
  await catalog.put({ guid: 'ep-201', title: 'Original Cut', durationS: 2400 });

  const report = await importFeed(catalog, [
    item('ep-201', 'Remastered Cut'),
    item('ep-202', 'Brand New'),
  ]);

  assert.equal(report.skipped, 1);
  assert.equal(report.imported, 1);
  assert.equal((await catalog.get('ep-201'))?.title, 'Original Cut');
  assert.equal(await catalog.count(), 2);
});

test('malformed items are reported without derailing the rest', async () => {
  const catalog = new Catalog();
  const report = await importFeed(catalog, [
    item('ep-301', 'Good One'),
    { title: 'Lost Its Guid', durationS: 900 },
    { guid: 'ep-303', title: '   ', durationS: 900 },
    { guid: 'ep-304', title: 'Rewound', durationS: -30 },
    item('ep-305', 'Also Good'),
  ]);

  assert.equal(report.imported, 2);
  assert.deepEqual(report.failures, [
    { guid: '<missing>', reason: 'missing guid' },
    { guid: 'ep-303', reason: 'missing title' },
    { guid: 'ep-304', reason: 'negative duration' },
  ]);
  assert.deepEqual(await catalog.allGuids(), ['ep-301', 'ep-305']);
});

test('a guid repeated within one feed lands once and counts as a skip', async () => {
  const catalog = new Catalog();
  const report = await importFeed(catalog, [
    item('ep-401', 'First Posting'),
    item('ep-401', 'Accidental Repost'),
  ]);

  assert.equal(report.imported, 1);
  assert.equal(report.skipped, 1);
  assert.equal(await catalog.count(), 1);
  assert.equal((await catalog.get('ep-401'))?.title, 'First Posting');
});

test('an empty feed reports zero work', async () => {
  const catalog = new Catalog();
  const report = await importFeed(catalog, []);
  assert.deepEqual(report, { imported: 0, skipped: 0, failures: [] });
  assert.equal(await catalog.count(), 0);
});
