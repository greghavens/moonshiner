import { test } from 'node:test';
import assert from 'node:assert/strict';
import { ReceiptRenderer } from './renderer.ts';
import type { Invoice, SymbolLookup } from './renderer.ts';

const symbols: SymbolLookup = async (code) => {
  const table: Record<string, string> = { USD: '$', EUR: '€' };
  const symbol = table[code];
  if (!symbol) throw new Error(`unknown currency ${code}`);
  return symbol;
};

const stationeryOrder: Invoice = {
  id: 'INV-1001',
  currency: 'USD',
  items: [
    { name: 'Keyboard', cents: 4900 },
    { name: 'Mousepad', cents: 950 },
  ],
};

const deskOrder: Invoice = {
  id: 'INV-2002',
  currency: 'EUR',
  items: [{ name: 'Standing desk', cents: 39900 }],
};

const stationeryReceipt =
  'Receipt INV-1001\nKeyboard: $49.00\nMousepad: $9.50\nTotal: $58.50';
const deskReceipt = 'Receipt INV-2002\nStanding desk: €399.00\nTotal: €399.00';

test('renders a receipt on its own', async () => {
  const renderer = new ReceiptRenderer(symbols);
  assert.equal(await renderer.render(stationeryOrder), stationeryReceipt);
});

test('overlapping renders keep their receipts separate', async () => {
  const renderer = new ReceiptRenderer(symbols);
  const [a, b] = await Promise.all([
    renderer.render(stationeryOrder),
    renderer.render(deskOrder),
  ]);
  assert.equal(a, stationeryReceipt);
  assert.equal(b, deskReceipt);
});

test('a receipt never contains another invoice\'s lines', async () => {
  const renderer = new ReceiptRenderer(symbols);
  const [desk, stationery] = await Promise.all([
    renderer.render(deskOrder),
    renderer.render(stationeryOrder),
  ]);
  assert.ok(!desk.includes('Keyboard'), 'desk receipt picked up stationery lines');
  assert.ok(!stationery.includes('Standing desk'), 'stationery receipt picked up desk lines');
  assert.ok(desk.startsWith('Receipt INV-2002'), 'desk receipt lost its own header');
});

test('rendering twice in sequence is stable', async () => {
  const renderer = new ReceiptRenderer(symbols);
  const first = await renderer.render(deskOrder);
  const second = await renderer.render(deskOrder);
  assert.equal(first, deskReceipt);
  assert.equal(second, deskReceipt);
});
