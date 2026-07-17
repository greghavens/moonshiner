import { test } from 'node:test';
import assert from 'node:assert/strict';
import { buildAccessors, renderReport } from './report.ts';

const columns = [
  { header: 'Name', key: 'name' },
  { header: 'Email', key: 'email' },
  { header: 'Seats', key: 'seats', format: (v: unknown) => `x${v}` },
];

const rows = [
  { name: 'Acme Corp', email: 'ops@acme.test', seats: 12 },
  { name: 'Globex', email: 'it@globex.test', seats: 3 },
];

test('each column accessor renders its own field', () => {
  const [name, email, seats] = buildAccessors(columns);
  assert.equal(name(rows[0]), 'Acme Corp');
  assert.equal(email(rows[0]), 'ops@acme.test');
  assert.equal(seats(rows[0]), 'x12');
});

test('report body lines up with the header row', () => {
  assert.equal(
    renderReport(columns, rows),
    'Name,Email,Seats\n' +
      'Acme Corp,ops@acme.test,x12\n' +
      'Globex,it@globex.test,x3',
  );
});

test('cells containing commas or quotes are escaped', () => {
  const out = renderReport(
    [{ header: 'Company', key: 'name' }],
    [{ name: 'Wayne, Bruce "Bats"' }],
  );
  assert.equal(out, 'Company\n"Wayne, Bruce ""Bats"""');
});

test('accessor lists built from different specs stay independent', () => {
  const [first] = buildAccessors([
    { header: 'SKU', key: 'sku' },
    { header: 'Qty', key: 'qty' },
  ]);
  assert.equal(first({ sku: 'A-1', qty: 9 }), 'A-1');
});
