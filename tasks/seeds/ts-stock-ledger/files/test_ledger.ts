import { test } from 'node:test';
import assert from 'node:assert/strict';
import { StockLedger } from './ledger.ts';

// Generous settle window so even stray late work has finished before we assert.
const settle = () => new Promise<void>((resolve) => setTimeout(resolve, 20));

test('a single adjustment persists', async () => {
  const ledger = new StockLedger();
  assert.equal(await ledger.adjust('WIDGET-9', 5), 5);
  assert.equal(await ledger.quantity('WIDGET-9'), 5);
});

test('a batch with two lines for the same sku applies both', async () => {
  const ledger = new StockLedger();
  await ledger.applyBatch([
    { sku: 'BOLT-M4', delta: 40 },
    { sku: 'BOLT-M4', delta: 25 },
  ]);
  await settle();
  assert.equal(await ledger.quantity('BOLT-M4'), 65);
});

test('the batch is fully applied when applyBatch resolves', async () => {
  const ledger = new StockLedger();
  await ledger.applyBatch([
    { sku: 'NUT-M4', delta: 10 },
    { sku: 'WASHER-M4', delta: 10 },
  ]);
  assert.equal(ledger.history().length, 2);
  assert.equal(await ledger.quantity('NUT-M4'), 10);
  assert.equal(await ledger.quantity('WASHER-M4'), 10);
});

test('movement history records each batch line, in order', async () => {
  const ledger = new StockLedger();
  await ledger.applyBatch([
    { sku: 'A-1', delta: 3 },
    { sku: 'A-2', delta: 4 },
    { sku: 'A-1', delta: 2 },
  ]);
  await settle();
  assert.deepEqual(ledger.history(), [
    { sku: 'A-1', delta: 3 },
    { sku: 'A-2', delta: 4 },
    { sku: 'A-1', delta: 2 },
  ]);
  assert.equal(await ledger.quantity('A-1'), 5);
  assert.equal(await ledger.quantity('A-2'), 4);
});
