import { test } from 'node:test';
import assert from 'node:assert/strict';
import { Ledger } from './ledger.ts';

function trip(...names: string[]) {
  const ledger = new Ledger();
  for (const n of names) ledger.addMember(n);
  return ledger;
}

test('members are registered and listed sorted', () => {
  const l = trip('cody', 'alex', 'blair');
  assert.deepEqual(l.members(), ['alex', 'blair', 'cody']);
});

test('duplicate member names are rejected', () => {
  const l = trip('alex');
  assert.throws(() => l.addMember('alex'), /already a member/);
});

test('blank member names are rejected', () => {
  const l = new Ledger();
  assert.throws(() => l.addMember(''), /member name/);
  assert.throws(() => l.addMember('   '), /member name/);
});

test('equal split with no remainder, payer participates', () => {
  const l = trip('alex', 'blair', 'cody');
  l.addExpense({ payer: 'alex', amountCents: 6000, description: 'cabin', split: { kind: 'equal' } });
  assert.deepEqual(l.balances(), [
    { name: 'alex', netCents: 4000 },
    { name: 'blair', netCents: -2000 },
    { name: 'cody', netCents: -2000 },
  ]);
});

test('equal split hands remainder cents to the alphabetically first participants', () => {
  const a = trip('alex', 'blair', 'cody');
  a.addExpense({ payer: 'alex', amountCents: 100, split: { kind: 'equal' } });
  // shares 34/33/33 — alex absorbs the extra cent
  assert.deepEqual(a.balances(), [
    { name: 'alex', netCents: 66 },
    { name: 'blair', netCents: -33 },
    { name: 'cody', netCents: -33 },
  ]);

  const b = trip('alex', 'blair', 'cody');
  b.addExpense({ payer: 'cody', amountCents: 200, split: { kind: 'equal' } });
  // shares 67/67/66 — two leftover cents, alex and blair take one each
  assert.deepEqual(b.balances(), [
    { name: 'alex', netCents: -67 },
    { name: 'blair', netCents: -67 },
    { name: 'cody', netCents: 134 },
  ]);
});

test('equal split can cover a subset that excludes the payer', () => {
  const l = trip('alex', 'blair', 'cody', 'drew');
  l.addExpense({ payer: 'drew', amountCents: 900, split: { kind: 'equal', among: ['alex', 'blair', 'cody'] } });
  assert.deepEqual(l.balances(), [
    { name: 'alex', netCents: -300 },
    { name: 'blair', netCents: -300 },
    { name: 'cody', netCents: -300 },
    { name: 'drew', netCents: 900 },
  ]);
});

test('exact split books the given shares, zero shares allowed', () => {
  const l = trip('alex', 'blair', 'cody');
  l.addExpense({ payer: 'alex', amountCents: 1000, split: { kind: 'exact', shares: { blair: 1000, cody: 0 } } });
  assert.deepEqual(l.balances(), [
    { name: 'alex', netCents: 1000 },
    { name: 'blair', netCents: -1000 },
    { name: 'cody', netCents: 0 },
  ]);
});

test('exact shares must sum to the amount', () => {
  const l = trip('alex', 'blair');
  assert.throws(
    () => l.addExpense({ payer: 'alex', amountCents: 1000, split: { kind: 'exact', shares: { alex: 700, blair: 200 } } }),
    /sum/,
  );
});

test('negative exact shares are rejected', () => {
  const l = trip('alex', 'blair');
  assert.throws(
    () => l.addExpense({ payer: 'alex', amountCents: 100, split: { kind: 'exact', shares: { alex: 200, blair: -100 } } }),
    RangeError,
  );
});

test('percent split gives leftover cents to the largest fractional remainder', () => {
  const l = trip('alex', 'blair', 'cody');
  l.addExpense({ payer: 'alex', amountCents: 101, split: { kind: 'percent', shares: { alex: 25, blair: 25, cody: 50 } } });
  // raw shares 25.25 / 25.25 / 50.5 — the one leftover cent goes to cody, not alphabetically
  assert.deepEqual(l.balances(), [
    { name: 'alex', netCents: 76 },
    { name: 'blair', netCents: -25 },
    { name: 'cody', netCents: -51 },
  ]);
});

test('percent shares must total 100', () => {
  const l = trip('alex', 'blair');
  assert.throws(
    () => l.addExpense({ payer: 'alex', amountCents: 100, split: { kind: 'percent', shares: { alex: 60, blair: 39 } } }),
    /100/,
  );
});

test('weight split rounds by largest remainder', () => {
  const l = trip('alex', 'blair');
  l.addExpense({ payer: 'blair', amountCents: 100, split: { kind: 'weights', shares: { alex: 2, blair: 1 } } });
  // raw 66.67 / 33.33 — alex has the bigger remainder, takes the extra cent
  assert.deepEqual(l.balances(), [
    { name: 'alex', netCents: -67 },
    { name: 'blair', netCents: 67 },
  ]);
});

test('weights must be positive finite numbers', () => {
  const l = trip('alex', 'blair');
  for (const bad of [0, -1, Infinity, NaN]) {
    assert.throws(
      () => l.addExpense({ payer: 'alex', amountCents: 100, split: { kind: 'weights', shares: { alex: 1, blair: bad } } }),
      RangeError,
    );
  }
});

test('amounts must be positive integer cents', () => {
  const l = trip('alex', 'blair');
  for (const bad of [0, -5, 10.5, NaN]) {
    assert.throws(
      () => l.addExpense({ payer: 'alex', amountCents: bad, split: { kind: 'equal' } }),
      RangeError,
    );
  }
  assert.deepEqual(l.balances(), [
    { name: 'alex', netCents: 0 },
    { name: 'blair', netCents: 0 },
  ]);
});

test('payer and participants must be registered members', () => {
  const l = trip('alex', 'blair');
  assert.throws(
    () => l.addExpense({ payer: 'mallory', amountCents: 100, split: { kind: 'equal' } }),
    /unknown member "mallory"/,
  );
  assert.throws(
    () => l.addExpense({ payer: 'alex', amountCents: 100, split: { kind: 'equal', among: ['alex', 'mallory'] } }),
    /unknown member "mallory"/,
  );
  assert.throws(
    () => l.addExpense({ payer: 'alex', amountCents: 100, split: { kind: 'exact', shares: { mallory: 100 } } }),
    /unknown member "mallory"/,
  );
});

test('empty participant sets are rejected', () => {
  const l = trip('alex');
  assert.throws(
    () => l.addExpense({ payer: 'alex', amountCents: 100, split: { kind: 'equal', among: [] } }),
    /no participants/,
  );
  const empty = new Ledger();
  empty.addMember('solo');
  assert.throws(
    () => empty.addExpense({ payer: 'solo', amountCents: 100, split: { kind: 'exact', shares: {} } }),
    /no participants/,
  );
});

test('duplicate names in among are rejected', () => {
  const l = trip('alex', 'blair');
  assert.throws(
    () => l.addExpense({ payer: 'alex', amountCents: 100, split: { kind: 'equal', among: ['blair', 'blair'] } }),
    /duplicate/,
  );
});

test('expenses accumulate across the ledger and always net to zero', () => {
  const l = trip('alex', 'blair', 'cody');
  l.addExpense({ payer: 'alex', amountCents: 3000, description: 'groceries', split: { kind: 'equal' } });
  l.addExpense({ payer: 'blair', amountCents: 600, description: 'tolls', split: { kind: 'exact', shares: { alex: 200, cody: 400 } } });
  const rows = l.balances();
  assert.deepEqual(rows, [
    { name: 'alex', netCents: 1800 },
    { name: 'blair', netCents: -400 },
    { name: 'cody', netCents: -1400 },
  ]);
  assert.equal(rows.reduce((s: number, r: { netCents: number }) => s + r.netCents, 0), 0);
});

test('members with no expenses still appear with a zero balance', () => {
  const l = trip('alex', 'blair', 'zoe');
  l.addExpense({ payer: 'alex', amountCents: 500, split: { kind: 'equal', among: ['alex', 'blair'] } });
  assert.deepEqual(l.balances(), [
    { name: 'alex', netCents: 250 },
    { name: 'blair', netCents: -250 },
    { name: 'zoe', netCents: 0 },
  ]);
});
