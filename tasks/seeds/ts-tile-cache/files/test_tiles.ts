import { test } from 'node:test';
import assert from 'node:assert/strict';
import { TileCache, dedupeKeys, prefetch } from './tiles.ts';
import type { FetchTile } from './tiles.ts';

function countingFetcher() {
  const calls: string[] = [];
  const fetchTile: FetchTile = async (key) => {
    calls.push(`${key.z}/${key.x}/${key.y}`);
    return `tile:${key.z}/${key.x}/${key.y}`;
  };
  return { calls, fetchTile };
}

test('revisiting a tile serves it from cache', async () => {
  const { calls, fetchTile } = countingFetcher();
  const cache = new TileCache(fetchTile);
  assert.equal(await cache.get({ z: 3, x: 4, y: 5 }), 'tile:3/4/5');
  assert.equal(await cache.get({ z: 3, x: 4, y: 5 }), 'tile:3/4/5');
  assert.deepEqual(calls, ['3/4/5'], 'the same tile went to the network twice');
  assert.equal(cache.size(), 1);
});

test('distinct tiles are cached side by side', async () => {
  const { calls, fetchTile } = countingFetcher();
  const cache = new TileCache(fetchTile);
  assert.equal(await cache.get({ z: 3, x: 4, y: 5 }), 'tile:3/4/5');
  assert.equal(await cache.get({ z: 3, x: 4, y: 6 }), 'tile:3/4/6');
  assert.equal(calls.length, 2);
  assert.equal(cache.size(), 2);
});

test('deduping a pan burst keeps one of each tile', () => {
  const burst = [
    { z: 2, x: 1, y: 1 },
    { z: 2, x: 1, y: 2 },
    { z: 2, x: 1, y: 1 },
    { z: 2, x: 1, y: 1 },
  ];
  assert.deepEqual(dedupeKeys(burst), [
    { z: 2, x: 1, y: 1 },
    { z: 2, x: 1, y: 2 },
  ]);
});

test('prefetching a viewport hits the network once per distinct tile', async () => {
  const { calls, fetchTile } = countingFetcher();
  const cache = new TileCache(fetchTile);
  const fetched = await prefetch(cache, [
    { z: 1, x: 0, y: 0 },
    { z: 1, x: 0, y: 0 },
    { z: 1, x: 1, y: 0 },
  ]);
  assert.equal(fetched, 2);
  assert.deepEqual([...calls].sort(), ['1/0/0', '1/1/0']);
});
