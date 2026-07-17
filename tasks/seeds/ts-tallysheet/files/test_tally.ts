// Acceptance suite for tally.ts -- fixed-width store-audit tally sheets.
//
// Pinned behavior:
//   section: "## " + title, truncated then padded to 44 chars, plus "\n"
//   row:     sku (trunc/pad 14) + desc (trunc/pad 24) + String(qty)
//            right-aligned to 6 (wider numerals kept whole), plus "\n"
//   render() returns the whole sheet and is idempotent.
//
// Copy accounting contract: onCopy(result.length) fires for every string
// the builder materializes while assembling sheet text (padding, +, +=,
// template literals, joins, or equivalent). The scale test budgets the
// cumulative total at 6x the final sheet length.
//
// Run: node --test test_tally.ts

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { SheetBuilder } from './tally.ts';

// ---- test-local formatting oracle (independent of the module) ------------

function expSection(title: string): string {
  return ('## ' + title).slice(0, 44).padEnd(44) + '\n';
}

function expRow(sku: string, desc: string, qty: number): string {
  return (
    sku.slice(0, 14).padEnd(14) +
    desc.slice(0, 24).padEnd(24) +
    String(qty).padStart(6) +
    '\n'
  );
}

// Deterministic PRNG so the big sheet is byte-identical on every run.
function lcg(seed: number): () => number {
  let s = seed >>> 0;
  return () => {
    s = (Math.imul(s, 1664525) + 1013904223) >>> 0;
    return s;
  };
}

test('empty sheet renders as the empty string', () => {
  const b = new SheetBuilder();
  assert.equal(b.render(), '');
  assert.equal(b.render(), '');
});

test('section and row layout is exact', () => {
  const b = new SheetBuilder();
  b.section('Aisle 3 - Dry Goods');
  b.row('SKU-100200', 'Peach Halves 400g', 12);
  b.row('A-VERY-LONG-SKU-CODE-42', 'An Extremely Long Description That Overflows', 7);
  b.row('SKU-7', 'Bulk Rice', 1234567);
  b.row('SKU-9', 'Sea Salt', -3);
  b.row('SKU-0', 'Oats', 0);

  const text = b.render();
  const lines = text.split('\n');
  assert.equal(lines[0], '## Aisle 3 - Dry Goods' + ' '.repeat(22));
  assert.equal(lines[1], 'SKU-100200' + ' '.repeat(4) + 'Peach Halves 400g' + ' '.repeat(7) + '    12');
  assert.equal(lines[2], 'A-VERY-LONG-SK' + 'An Extremely Long Descri' + '     7');
  // a 7-digit count is kept whole: that line is one char wider, not cut
  assert.equal(lines[3], 'SKU-7' + ' '.repeat(9) + 'Bulk Rice' + ' '.repeat(15) + '1234567');
  assert.equal(lines[4], 'SKU-9' + ' '.repeat(9) + 'Sea Salt' + ' '.repeat(16) + '    -3');
  assert.equal(lines[5], 'SKU-0' + ' '.repeat(9) + 'Oats' + ' '.repeat(20) + '     0');
  assert.equal(lines[6], '');

  const expected =
    expSection('Aisle 3 - Dry Goods') +
    expRow('SKU-100200', 'Peach Halves 400g', 12) +
    expRow('A-VERY-LONG-SKU-CODE-42', 'An Extremely Long Description That Overflows', 7) +
    expRow('SKU-7', 'Bulk Rice', 1234567) +
    expRow('SKU-9', 'Sea Salt', -3) +
    expRow('SKU-0', 'Oats', 0);
  assert.equal(text, expected);
});

test('render is idempotent and reflects rows added between calls', () => {
  const b = new SheetBuilder();
  b.row('SKU-1', 'Rice', 3);
  const first = b.render();
  assert.equal(first, expRow('SKU-1', 'Rice', 3));
  b.row('SKU-2', 'Salt', 4);
  const second = b.render();
  assert.equal(second, expRow('SKU-1', 'Rice', 3) + expRow('SKU-2', 'Salt', 4));
  assert.equal(b.render(), second);
});

test('onCopy hook is optional, harmless, and reports at least the sheet itself', () => {
  const plain = new SheetBuilder();
  const reports: number[] = [];
  let total = 0;
  const hooked = new SheetBuilder({
    onCopy: (n) => {
      reports.push(n);
      total += n;
    },
  });
  for (const b of [plain, hooked]) {
    b.section('Backroom');
    b.row('SKU-88', 'Iron Lentils 1kg', 41);
    b.row('SKU-89', 'Golden Honey 250g', 5);
  }
  const text = hooked.render();
  assert.equal(text, plain.render(), 'hook presence changed the output');
  assert.ok(reports.every((n) => Number.isInteger(n) && n >= 0));
  assert.ok(total >= text.length, 'the final sheet itself must be accounted for');
});

test('a full store export stays within the copy budget', () => {
  // Scale gate arithmetic (perf-seed policy: document the margin):
  //   150_000 rows + a section every 500 rows = 150_300 lines x 45 chars
  //   = 6_763_500 chars in the final sheet.
  //   Budget: 6x the sheet = 40_581_000 reported chars. Buffering the
  //   lines and joining once reports ~4x and passes with headroom; the
  //   grow-a-string-per-line shape re-reports the whole prefix on every
  //   append, ~45 * n^2 / 2 ≈ 5.1e14 chars (~12_000_000x the budget), so
  //   the hook trips it after ~1_300 rows -- milliseconds, not minutes.
  //   A within-budget build of this sheet runs in a couple of seconds.
  const ROWS = 150_000;
  const SHEET_LEN = 45 * (ROWS + ROWS / 500);
  const BUDGET = 6 * SHEET_LEN;

  const ADJ = ['Amber', 'Bulk', 'Chilled', 'Dried', 'Farm', 'Golden',
    'Iron', 'Jumbo', 'Long', 'Mild', 'Oak', 'Prime'];
  const NOUN = ['Beans', 'Cereal', 'Flour', 'Honey', 'Juice', 'Lentils',
    'Noodles', 'Oats', 'Pasta', 'Rice', 'Salt', 'Sugar'];
  const SIZE = ['100g', '250g', '400g', '1kg', '2kg', '5kg'];

  const rng = lcg(20260713);
  let copied = 0;
  const sheet = new SheetBuilder({
    onCopy: (n) => {
      copied += n;
      if (copied > BUDGET) {
        throw new Error(
          `copy budget exceeded: more than ${BUDGET} chars reported for a ` +
          `${ROWS}-row sheet -- the builder is re-copying its buffer as it grows`);
      }
    },
  });

  const parts: string[] = [];
  let aisle = 0;
  for (let i = 0; i < ROWS; i++) {
    if (i % 500 === 0) {
      aisle++;
      const title = 'Aisle ' + aisle;
      sheet.section(title);
      parts.push(expSection(title));
    }
    const sku = 'SKU-' + (100000 + (rng() % 900000));
    const desc = ADJ[rng() % 12] + ' ' + NOUN[rng() % 12] + ' ' + SIZE[rng() % 6];
    const qty = rng() % 100000;
    sheet.row(sku, desc, qty);
    parts.push(expRow(sku, desc, qty));
  }

  const got = sheet.render();
  const expected = parts.join('');
  assert.equal(got.length, SHEET_LEN);
  assert.ok(got === expected, 'sheet bytes diverged from the oracle at scale');
  assert.ok(copied <= BUDGET);
  assert.ok(copied >= got.length, 'the final sheet itself must be accounted for');
});
