import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import { produceStockAdjusted } from './apps/producer/stock_adjusted.ts';
import { InventoryProjection } from './apps/consumer/inventory_projection.ts';
import {
  isStockAdjustedV2,
  type StockAdjustedV2,
} from './packages/contracts/stock_event.ts';
import { normalizeStockAdjusted } from './packages/compat/normalize_stock_event.ts';

const recorded = JSON.parse(readFileSync(
  new URL('./fixtures/recorded_stock_events_v1.json', import.meta.url),
  'utf8',
));
const legacyContract = JSON.parse(readFileSync(
  new URL('./packages/contracts/legacy_stock_event_v1.json', import.meta.url),
  'utf8',
));

test('current producer keeps the generated v2 event shape', () => {
  const event = produceStockAdjusted({
    eventId: 'evt-current-1',
    occurredAt: '2026-07-17T14:00:00Z',
    warehouseId: 'wh-south',
    sku: 'SKU-BLUE',
    delta: -4,
    reason: 'sale',
    actorId: 'picker-9',
  });
  assert.deepEqual(event, {
    schemaVersion: 2,
    type: 'inventory.stock_adjusted',
    eventId: 'evt-current-1',
    occurredAt: '2026-07-17T14:00:00Z',
    payload: {
      warehouseId: 'wh-south', sku: 'SKU-BLUE', delta: -4,
      reason: 'sale', actorId: 'picker-9',
    },
  });
  assert.equal(isStockAdjustedV2(event), true);
});

test('current events normalize as independent values and project unchanged', () => {
  const event = produceStockAdjusted({
    eventId: 'evt-current-2', occurredAt: '2026-07-17T15:00:00Z',
    warehouseId: 'wh-south', sku: 'SKU-GREEN', delta: 6,
    reason: 'return', actorId: null,
  });
  const normalized = normalizeStockAdjusted(event);
  assert.deepEqual(normalized, event);
  assert.notEqual(normalized, event);
  assert.notEqual(normalized.payload, event.payload);
  const projection = new InventoryProjection();
  projection.seed('wh-south', 'SKU-GREEN', 2);
  projection.apply(event);
  assert.deepEqual(projection.get('wh-south', 'SKU-GREEN'), {
    quantity: 8, sold: 0, returned: 6, recounts: 0,
    lastEventId: 'evt-current-2',
  });
});

test('protected v1 recordings normalize through every documented mapping', () => {
  assert.deepEqual(legacyContract.reasonMappings, {
    shipment: 'sale', receiving: 'return', cycle_count: 'recount',
  });
  const before = JSON.stringify(recorded);
  const normalized = recorded.map((event: unknown) => normalizeStockAdjusted(event));
  assert.deepEqual(
    normalized.map((event: StockAdjustedV2) => ({
      id: event.eventId,
      delta: event.payload.delta,
      reason: event.payload.reason,
      actorId: event.payload.actorId,
    })),
    [
      { id: 'rec-order-17', delta: -3, reason: 'sale', actorId: 'picker-4' },
      { id: 'rec-return-8', delta: 5, reason: 'return', actorId: null },
      { id: 'rec-count-3', delta: -2, reason: 'recount', actorId: 'auditor-2' },
    ],
  );
  assert.ok(normalized.every((event: unknown) => isStockAdjustedV2(event)));
  assert.equal(JSON.stringify(recorded), before, 'normalization must not rewrite recordings');
});

test('legacy and current events feed one projection contract', () => {
  const projection = new InventoryProjection();
  projection.seed('wh-north', 'SKU-RED', 10);
  for (const event of recorded) projection.apply(event);
  assert.deepEqual(projection.get('wh-north', 'SKU-RED'), {
    quantity: 10,
    sold: 3,
    returned: 5,
    recounts: 1,
    lastEventId: 'rec-count-3',
  });
});

test('normalization is contract-based rather than tied to recorded IDs', () => {
  const event = normalizeStockAdjusted({
    version: 1,
    event: 'stock.adjusted',
    id: 'synthetic-return-92',
    timestamp: '2024-01-02T03:04:05Z',
    warehouse: 'wh-east',
    sku: 'SKU-ODD',
    change: '+12',
    cause: 'receiving',
    operator: 'receiver-2',
  });
  assert.equal(event.payload.delta, 12);
  assert.equal(event.payload.reason, 'return');
  assert.equal(event.payload.warehouseId, 'wh-east');
});

test('bad legacy history is rejected before consumer state changes', () => {
  const badEvents = [
    { ...recorded[0], id: 'bad-reason', cause: 'manual_override' },
    { ...recorded[0], id: 'bad-decimal', change: '1.5' },
    { ...recorded[0], id: 'bad-junk', change: '-3 crates' },
    { ...recorded[0], id: 'bad-zero', change: '0' },
  ];
  for (const event of badEvents) {
    const projection = new InventoryProjection();
    projection.seed('wh-north', 'SKU-RED', 10);
    const before = projection.get('wh-north', 'SKU-RED');
    assert.throws(() => projection.apply(event), TypeError);
    assert.deepEqual(projection.get('wh-north', 'SKU-RED'), before);
  }
});

test('malformed current events are not mistaken for legacy history', () => {
  const malformed = {
    schemaVersion: 2,
    type: 'inventory.stock_adjusted',
    eventId: 'evt-bad',
    occurredAt: '2026-07-17T15:00:00Z',
    payload: {
      warehouseId: 'wh-south', sku: 'SKU-GREEN', delta: '-4',
      reason: 'sale', actorId: null,
    },
  };
  assert.equal(isStockAdjustedV2(malformed), false);
  assert.throws(() => normalizeStockAdjusted(malformed), TypeError);
});
