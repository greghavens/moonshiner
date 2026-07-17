import { test } from 'node:test';
import assert from 'node:assert/strict';
import { Ledger } from './ledger.ts';
import { settle } from './settle.ts';

type Row = { name: string; netCents: number };
type Transfer = { from: string; to: string; amountCents: number };

function applyTransfers(rows: Row[], transfers: Transfer[]) {
  const net = new Map(rows.map((r) => [r.name, r.netCents]));
  for (const t of transfers) {
    assert.ok(Number.isInteger(t.amountCents) && t.amountCents > 0, `bad transfer amount ${t.amountCents}`);
    net.set(t.from, (net.get(t.from) ?? 0) + t.amountCents);
    net.set(t.to, (net.get(t.to) ?? 0) - t.amountCents);
  }
  return net;
}

test('no balances means no transfers', () => {
  assert.deepEqual(settle([]), []);
});

test('an already settled group needs no transfers', () => {
  assert.deepEqual(settle([{ name: 'alex', netCents: 0 }, { name: 'blair', netCents: 0 }]), []);
});

test('single debtor pays the single creditor', () => {
  assert.deepEqual(settle([{ name: 'alex', netCents: -500 }, { name: 'blair', netCents: 500 }]), [
    { from: 'alex', to: 'blair', amountCents: 500 },
  ]);
});

test('exact opposites pair up before the greedy pass shortens the plan', () => {
  const rows: Row[] = [
    { name: 'alex', netCents: 700 },
    { name: 'blair', netCents: 500 },
    { name: 'cody', netCents: 300 },
    { name: 'dana', netCents: -700 },
    { name: 'eve', netCents: -800 },
  ];
  // dana's 700 exactly matches alex; pure largest-vs-largest would need four transfers
  const plan = settle(rows);
  assert.deepEqual(plan, [
    { from: 'dana', to: 'alex', amountCents: 700 },
    { from: 'eve', to: 'blair', amountCents: 500 },
    { from: 'eve', to: 'cody', amountCents: 300 },
  ]);
  for (const [, v] of applyTransfers(rows, plan)) assert.equal(v, 0);
});

test('exact matching walks debtors alphabetically and consumes creditors', () => {
  const plan = settle([
    { name: 'cody', netCents: -300 },
    { name: 'dana', netCents: -300 },
    { name: 'alex', netCents: 300 },
    { name: 'blair', netCents: 250 },
    { name: 'ed', netCents: 50 },
  ]);
  // cody claims the only exact creditor; dana falls through to the greedy pass
  assert.deepEqual(plan, [
    { from: 'cody', to: 'alex', amountCents: 300 },
    { from: 'dana', to: 'blair', amountCents: 250 },
    { from: 'dana', to: 'ed', amountCents: 50 },
  ]);
});

test('two equal debts pair with two equal credits by name order', () => {
  const plan = settle([
    { name: 'dana', netCents: -300 },
    { name: 'blair', netCents: 300 },
    { name: 'cody', netCents: -300 },
    { name: 'alex', netCents: 300 },
  ]);
  assert.deepEqual(plan, [
    { from: 'cody', to: 'alex', amountCents: 300 },
    { from: 'dana', to: 'blair', amountCents: 300 },
  ]);
});

test('largest debtor pays the largest creditor, names break size ties', () => {
  const plan = settle([
    { name: 'blair', netCents: -500 },
    { name: 'alex', netCents: -500 },
    { name: 'cody', netCents: 1001 },
    { name: 'drew', netCents: -1 },
  ]);
  assert.deepEqual(plan, [
    { from: 'alex', to: 'cody', amountCents: 500 },
    { from: 'blair', to: 'cody', amountCents: 500 },
    { from: 'drew', to: 'cody', amountCents: 1 },
  ]);
});

test('zero balances never show up in the plan', () => {
  const plan = settle([
    { name: 'idle', netCents: 0 },
    { name: 'alex', netCents: -20 },
    { name: 'blair', netCents: 20 },
  ]);
  assert.deepEqual(plan, [{ from: 'alex', to: 'blair', amountCents: 20 }]);
});

test('the plan zeroes out every balance', () => {
  const rows: Row[] = [
    { name: 'ana', netCents: 120 },
    { name: 'bo', netCents: -45 },
    { name: 'cy', netCents: 35 },
    { name: 'di', netCents: -80 },
    { name: 'ed', netCents: -30 },
  ];
  const net = applyTransfers(rows, settle(rows));
  for (const [name, v] of net) assert.equal(v, 0, `${name} left with ${v}`);
});

test('input balances are not mutated or reordered', () => {
  const rows: Row[] = [
    { name: 'zed', netCents: -10 },
    { name: 'amy', netCents: 10 },
  ];
  const snapshot = JSON.parse(JSON.stringify(rows));
  settle(rows);
  assert.deepEqual(rows, snapshot);
});

test('input order does not change the plan', () => {
  const rows: Row[] = [
    { name: 'eve', netCents: -800 },
    { name: 'cody', netCents: 300 },
    { name: 'alex', netCents: 700 },
    { name: 'dana', netCents: -700 },
    { name: 'blair', netCents: 500 },
  ];
  assert.deepEqual(settle(rows), [
    { from: 'dana', to: 'alex', amountCents: 700 },
    { from: 'eve', to: 'blair', amountCents: 500 },
    { from: 'eve', to: 'cody', amountCents: 300 },
  ]);
});

test('unbalanced input is rejected', () => {
  assert.throws(() => settle([{ name: 'alex', netCents: 1 }]), /sum to zero/);
});

test('end to end: ledger balances settle cleanly', () => {
  const l = new Ledger();
  for (const n of ['alex', 'blair', 'cody']) l.addMember(n);
  l.addExpense({ payer: 'alex', amountCents: 3000, split: { kind: 'equal' } });
  l.addExpense({ payer: 'blair', amountCents: 600, split: { kind: 'exact', shares: { alex: 200, cody: 400 } } });
  const plan = settle(l.balances());
  assert.deepEqual(plan, [
    { from: 'cody', to: 'alex', amountCents: 1400 },
    { from: 'blair', to: 'alex', amountCents: 400 },
  ]);
  for (const [, v] of applyTransfers(l.balances(), plan)) assert.equal(v, 0);
});
