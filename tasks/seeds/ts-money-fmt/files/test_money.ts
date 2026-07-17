import { test } from 'node:test';
import assert from 'node:assert/strict';
import { formatMoney, parseMoney, toMinorUnits } from './money.ts';
import type { CurrencyLocale } from './money.ts';

const US: CurrencyLocale = {
  symbol: '$',
  position: 'prefix',
  symbolSpace: false,
  groupSep: ',',
  decimalSep: '.',
  decimals: 2,
};

const DE: CurrencyLocale = {
  symbol: '€',
  position: 'suffix',
  symbolSpace: true,
  groupSep: '.',
  decimalSep: ',',
  decimals: 2,
};

const JP: CurrencyLocale = {
  symbol: '¥',
  position: 'prefix',
  symbolSpace: false,
  groupSep: ',',
  decimalSep: '.',
  decimals: 0,
};

const CH: CurrencyLocale = {
  symbol: 'CHF',
  position: 'prefix',
  symbolSpace: true,
  groupSep: "'",
  decimalSep: '.',
  decimals: 2,
};

// ---------- formatting ----------

test('formats minor units with grouping and two decimals (US)', () => {
  assert.equal(formatMoney(123456, US), '$1,234.56');
});

test('zero and sub-unit amounts pad the integer part', () => {
  assert.equal(formatMoney(0, US), '$0.00');
  assert.equal(formatMoney(5, US), '$0.05');
  assert.equal(formatMoney(50, US), '$0.50');
});

test('grouping repeats every three digits', () => {
  assert.equal(formatMoney(123456789, US), '$1,234,567.89');
  assert.equal(formatMoney(100000000000, US), '$1,000,000,000.00');
});

test('negative amounts put the minus sign first', () => {
  assert.equal(formatMoney(-123456, US), '-$1,234.56');
  assert.equal(formatMoney(-5, US), '-$0.05');
});

test('suffix locales place a spaced symbol after the number (DE)', () => {
  assert.equal(formatMoney(123456, DE), '1.234,56 €');
  assert.equal(formatMoney(-123456, DE), '-1.234,56 €');
});

test('zero-decimal currencies render no decimal separator (JP)', () => {
  assert.equal(formatMoney(123456, JP), '¥123,456');
  assert.equal(formatMoney(0, JP), '¥0');
});

test('multi-character symbol with a space and apostrophe grouping (CH)', () => {
  assert.equal(formatMoney(987654321, CH), "CHF 9'876'543.21");
});

test('formatMoney rejects non-integer and unsafe amounts', () => {
  assert.throws(() => formatMoney(10.5, US), TypeError);
  assert.throws(() => formatMoney(Number.NaN, US), TypeError);
  assert.throws(() => formatMoney(2 ** 53, US), TypeError);
});

// ---------- parsing ----------

test('parses a fully formatted string back to minor units', () => {
  assert.equal(parseMoney('$1,234.56', US), 123456);
  assert.equal(parseMoney('1.234,56 €', DE), 123456);
  assert.equal(parseMoney('¥1,000', JP), 1000);
  assert.equal(parseMoney("CHF 9'876'543.21", CH), 987654321);
});

test('the symbol and grouping are optional on input', () => {
  assert.equal(parseMoney('1234.56', US), 123456);
  assert.equal(parseMoney('1,234.56', US), 123456);
  assert.equal(parseMoney('  1234.56  ', US), 123456);
});

test('missing or short decimal parts are padded, not rejected', () => {
  assert.equal(parseMoney('12', US), 1200);
  assert.equal(parseMoney('$1,234', US), 123400);
  assert.equal(parseMoney('1.5', US), 150);
});

test('negative amounts parse with the sign before or after the symbol', () => {
  assert.equal(parseMoney('-$0.05', US), -5);
  assert.equal(parseMoney('$-0.05', US), -5);
  assert.equal(parseMoney('-1.234,56 €', DE), -123456);
});

test('parse(format(x)) is the identity across locales', () => {
  const amounts = [0, 1, 99, 100, 123456, -123456, 999999999, -1];
  for (const locale of [US, DE, JP, CH]) {
    for (const minor of amounts) {
      assert.equal(parseMoney(formatMoney(minor, locale), locale), minor);
    }
  }
});

test('rejects empty input and stray characters', () => {
  assert.throws(() => parseMoney('', US), /empty/i);
  assert.throws(() => parseMoney('   ', US), /empty/i);
  assert.throws(() => parseMoney('12abc', US), Error);
  assert.throws(() => parseMoney('€5.00', US), Error);
});

test('rejects more decimal digits than the currency allows', () => {
  assert.throws(() => parseMoney('12.345', US), Error);
  assert.throws(() => parseMoney('¥10.5', JP), Error);
});

test('rejects a second decimal separator', () => {
  assert.throws(() => parseMoney('1.2.3', US), Error);
});

// ---------- decimal-string conversion and rounding ----------

test('converts exact decimal strings without float drift', () => {
  // Anything going through binary floats collapses these.
  assert.equal(toMinorUnits('0.29', 2, 'floor'), 29);
  assert.equal(toMinorUnits('123456789012.34', 2, 'floor'), 12345678901234);
  assert.equal(toMinorUnits('0.07', 2, 'floor'), 7);
});

test('default rounding mode is half-up', () => {
  assert.equal(toMinorUnits('7', 2), 700);
  assert.equal(toMinorUnits('1.005', 2), 101);
});

test('half-up rounds a half away from zero', () => {
  assert.equal(toMinorUnits('1.005', 2, 'half-up'), 101);
  assert.equal(toMinorUnits('-1.005', 2, 'half-up'), -101);
  assert.equal(toMinorUnits('1.004', 2, 'half-up'), 100);
  assert.equal(toMinorUnits('2.5', 0, 'half-up'), 3);
});

test('half-even sends an exact half to the even neighbor', () => {
  assert.equal(toMinorUnits('1.005', 2, 'half-even'), 100);
  assert.equal(toMinorUnits('1.015', 2, 'half-even'), 102);
  assert.equal(toMinorUnits('1.025', 2, 'half-even'), 102);
  assert.equal(toMinorUnits('2.5', 0, 'half-even'), 2);
  assert.equal(toMinorUnits('3.5', 0, 'half-even'), 4);
});

test('half-even only applies to an exact half', () => {
  assert.equal(toMinorUnits('1.0051', 2, 'half-even'), 101);
  assert.equal(toMinorUnits('1.0049', 2, 'half-even'), 100);
});

test('floor rounds toward negative infinity', () => {
  assert.equal(toMinorUnits('1.999', 2, 'floor'), 199);
  assert.equal(toMinorUnits('-1.001', 2, 'floor'), -101);
  assert.equal(toMinorUnits('-1.000', 2, 'floor'), -100);
});

test('results are never negative zero', () => {
  assert.ok(Object.is(toMinorUnits('-0.004', 2, 'half-up'), 0));
  assert.ok(Object.is(toMinorUnits('-0', 2, 'half-even'), 0));
});

test('toMinorUnits rejects text that is not a plain decimal number', () => {
  assert.throws(() => toMinorUnits('abc', 2), Error);
  assert.throws(() => toMinorUnits('1..2', 2), Error);
  assert.throws(() => toMinorUnits('1,000.50', 2), Error);
  assert.throws(() => toMinorUnits('', 2), Error);
});
