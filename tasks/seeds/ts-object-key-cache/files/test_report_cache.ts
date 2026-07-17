import { test } from 'node:test';
import assert from 'node:assert/strict';
import { ReportCache } from './report_cache.ts';
import type { ReportParams } from './report_cache.ts';

class FakeFetcher {
  calls: ReportParams[] = [];

  async run(params: ReportParams): Promise<number[]> {
    this.calls.push(params);
    return [this.calls.length * 100, this.calls.length];
  }
}

function salesWeek(): ReportParams {
  return {
    metric: 'sales',
    range: { from: '2026-06-01', to: '2026-06-07' },
    filters: { region: 'eu', tier: 'gold' },
    limit: 50,
  };
}

test('the same query written as a fresh literal is a cache hit', async () => {
  const fetcher = new FakeFetcher();
  const cache = new ReportCache(fetcher);
  const first = await cache.run(salesWeek());
  const second = await cache.run(salesWeek());
  assert.deepEqual(first, [100, 1]);
  assert.deepEqual(second, [100, 1]);
  assert.equal(fetcher.calls.length, 1, 'an equal query went back to the warehouse');
});

test('property order does not change the key', async () => {
  const fetcher = new FakeFetcher();
  const cache = new ReportCache(fetcher);
  await cache.run({ metric: 'sales', range: { from: '2026-06-01', to: '2026-06-07' }, limit: 5 });
  await cache.run({ limit: 5, range: { to: '2026-06-07', from: '2026-06-01' }, metric: 'sales' });
  assert.equal(fetcher.calls.length, 1);
});

test('nested filter order does not change the key', async () => {
  const fetcher = new FakeFetcher();
  const cache = new ReportCache(fetcher);
  const base = { metric: 'churn', range: { from: '2026-05-01', to: '2026-05-31' } };
  await cache.run({ ...base, filters: { region: 'eu', tier: 'gold' } });
  await cache.run({ ...base, filters: { tier: 'gold', region: 'eu' } });
  assert.equal(fetcher.calls.length, 1);
});

test('different filter values are different entries', async () => {
  const fetcher = new FakeFetcher();
  const cache = new ReportCache(fetcher);
  const base = { metric: 'churn', range: { from: '2026-05-01', to: '2026-05-31' } };
  const eu = await cache.run({ ...base, filters: { region: 'eu' } });
  const us = await cache.run({ ...base, filters: { region: 'us' } });
  assert.notDeepEqual(eu, us);
  assert.equal(fetcher.calls.length, 2);
  assert.equal(cache.stats().size, 2);
});

test('string and number filter values never collide', async () => {
  const fetcher = new FakeFetcher();
  const cache = new ReportCache(fetcher);
  const base = { metric: 'pages', range: { from: '2026-05-01', to: '2026-05-02' } };
  await cache.run({ ...base, filters: { page: 25 } });
  await cache.run({ ...base, filters: { page: '25' } });
  assert.equal(fetcher.calls.length, 2);
});

test('values containing separator characters never collide', async () => {
  const fetcher = new FakeFetcher();
  const cache = new ReportCache(fetcher);
  const base = { metric: 'events', range: { from: '2026-05-01', to: '2026-05-02' } };
  await cache.run({ ...base, filters: { source: 'eu', tier: 'gold' } });
  await cache.run({ ...base, filters: { source: 'eu:tier=gold' } });
  assert.equal(fetcher.calls.length, 2);
  assert.equal(cache.stats().size, 2);
});

test('invalidate accepts an equal literal and evicts the entry', async () => {
  const fetcher = new FakeFetcher();
  const cache = new ReportCache(fetcher);
  await cache.run(salesWeek());
  assert.equal(cache.invalidate(salesWeek()), true, 'equal params must find the entry');
  assert.equal(cache.invalidate(salesWeek()), false, 'already evicted');
  await cache.run(salesWeek());
  assert.equal(fetcher.calls.length, 2, 'after eviction the query must be re-fetched');
});

test('mutating the params object after a run does not corrupt the journal', async () => {
  const fetcher = new FakeFetcher();
  const cache = new ReportCache(fetcher);
  const params = salesWeek();
  await cache.run(params);
  params.range.to = '2026-09-30';
  params.metric = 'refunds';
  const logged = cache.stats().journal[0].params;
  assert.equal(logged.metric, 'sales');
  assert.equal(logged.range.to, '2026-06-07');
  await cache.run(salesWeek());
  assert.equal(fetcher.calls.length, 1, 'the original query must still hit after the caller mutated its object');
});

test('mutating returned rows does not corrupt the cached entry', async () => {
  const fetcher = new FakeFetcher();
  const cache = new ReportCache(fetcher);
  const first = await cache.run(salesWeek());
  first.push(999999);
  first[0] = -1;
  const second = await cache.run(salesWeek());
  assert.deepEqual(second, [100, 1]);
  assert.equal(fetcher.calls.length, 1);
});

test('the journal records hits and misses in order', async () => {
  const fetcher = new FakeFetcher();
  const cache = new ReportCache(fetcher);
  const other: ReportParams = { metric: 'signups', range: { from: '2026-06-01', to: '2026-06-07' } };
  await cache.run(salesWeek());
  await cache.run(salesWeek());
  await cache.run(other);
  const { size, journal } = cache.stats();
  assert.equal(size, 2);
  assert.deepEqual(journal.map((e) => e.hit), [false, true, false]);
  assert.deepEqual(journal.map((e) => e.params.metric), ['sales', 'sales', 'signups']);
});

test('stats snapshots are isolated from later tampering', async () => {
  const fetcher = new FakeFetcher();
  const cache = new ReportCache(fetcher);
  await cache.run(salesWeek());
  const snapshot = cache.stats();
  snapshot.journal[0].params.metric = 'tampered';
  snapshot.journal[0].hit = true;
  assert.equal(cache.stats().journal[0].params.metric, 'sales');
  assert.equal(cache.stats().journal[0].hit, false);
});
