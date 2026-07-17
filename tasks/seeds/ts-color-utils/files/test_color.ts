import { test } from 'node:test';
import assert from 'node:assert/strict';
import { parseHex, toHex, rgbToHsl, hslToRgb, mix } from './color.ts';

function near(actual: number, expected: number, eps = 0.05, label = '') {
  assert.ok(
    Math.abs(actual - expected) <= eps,
    `${label} expected ${expected} +/- ${eps}, got ${actual}`,
  );
}

test('parseHex reads 6-digit colors with or without the hash', () => {
  assert.deepEqual(parseHex('#4080ff'), { r: 64, g: 128, b: 255, a: 1 });
  assert.deepEqual(parseHex('4080ff'), { r: 64, g: 128, b: 255, a: 1 });
});

test('parseHex is case-insensitive', () => {
  assert.deepEqual(parseHex('#ABCDEF'), { r: 171, g: 205, b: 239, a: 1 });
});

test('parseHex expands 3-digit shorthand per CSS', () => {
  assert.deepEqual(parseHex('#abc'), { r: 170, g: 187, b: 204, a: 1 });
  assert.deepEqual(parseHex('#f00'), { r: 255, g: 0, b: 0, a: 1 });
});

test('parseHex reads alpha from 8-digit colors', () => {
  const c = parseHex('#8000ff80');
  assert.equal(c.r, 128);
  assert.equal(c.g, 0);
  assert.equal(c.b, 255);
  near(c.a, 128 / 255, 0.001, 'alpha');
  assert.equal(parseHex('#000000ff').a, 1);
  assert.equal(parseHex('#00000000').a, 0);
});

test('parseHex throws TypeError on malformed input', () => {
  for (const bad of ['', '#12', '#12345', '#12345g', 'nope', '#1234567', '#ggg']) {
    assert.throws(() => parseHex(bad), TypeError, `expected throw for ${JSON.stringify(bad)}`);
  }
});

test('toHex renders lowercase rrggbb and omits alpha when opaque', () => {
  assert.equal(toHex({ r: 64, g: 128, b: 255 }), '#4080ff');
  assert.equal(toHex({ r: 64, g: 128, b: 255, a: 1 }), '#4080ff');
});

test('toHex appends the alpha byte when translucent', () => {
  assert.equal(toHex({ r: 0, g: 0, b: 0, a: 128 / 255 }), '#00000080');
  assert.equal(toHex({ r: 255, g: 255, b: 255, a: 0 }), '#ffffff00');
});

test('toHex clamps out-of-range channels instead of throwing', () => {
  assert.equal(toHex({ r: 300, g: -20, b: 12 }), '#ff000c');
  assert.equal(toHex({ r: 0, g: 0, b: 0, a: 2 }), '#000000');
  assert.equal(toHex({ r: 10, g: 10, b: 10, a: -1 }), '#0a0a0a00');
});

test('toHex rounds fractional channels and zero-pads small ones', () => {
  assert.equal(toHex({ r: 12.4, g: 12.6, b: 5 }), '#0c0d05');
});

test('rgbToHsl handles the primary colors', () => {
  const red = rgbToHsl({ r: 255, g: 0, b: 0 });
  near(red.h, 0, 0.05, 'red h'); near(red.s, 100, 0.05, 'red s'); near(red.l, 50, 0.05, 'red l');
  const lime = rgbToHsl({ r: 0, g: 255, b: 0 });
  near(lime.h, 120, 0.05, 'lime h');
  const blue = rgbToHsl({ r: 0, g: 0, b: 255 });
  near(blue.h, 240, 0.05, 'blue h');
});

test('rgbToHsl reports achromatic colors with zero saturation and hue', () => {
  const white = rgbToHsl({ r: 255, g: 255, b: 255 });
  assert.equal(white.h, 0); assert.equal(white.s, 0); near(white.l, 100, 0.05, 'white l');
  const black = rgbToHsl({ r: 0, g: 0, b: 0 });
  assert.equal(black.h, 0); assert.equal(black.s, 0); near(black.l, 0, 0.05, 'black l');
  const gray = rgbToHsl({ r: 128, g: 128, b: 128 });
  assert.equal(gray.s, 0); near(gray.l, 50.2, 0.05, 'gray l');
});

test('rgbToHsl handles mixed colors', () => {
  const teal = rgbToHsl({ r: 0, g: 128, b: 128 });
  near(teal.h, 180, 0.05, 'teal h'); near(teal.s, 100, 0.05, 'teal s'); near(teal.l, 25.1, 0.05, 'teal l');
  const olive = rgbToHsl({ r: 128, g: 128, b: 0 });
  near(olive.h, 60, 0.05, 'olive h'); near(olive.s, 100, 0.05, 'olive s');
});

test('hslToRgb produces integer channels for known colors', () => {
  assert.deepEqual(hslToRgb({ h: 0, s: 100, l: 50 }), { r: 255, g: 0, b: 0 });
  assert.deepEqual(hslToRgb({ h: 120, s: 100, l: 50 }), { r: 0, g: 255, b: 0 });
  assert.deepEqual(hslToRgb({ h: 210, s: 100, l: 50 }), { r: 0, g: 128, b: 255 });
  assert.deepEqual(hslToRgb({ h: 0, s: 0, l: 100 }), { r: 255, g: 255, b: 255 });
  assert.deepEqual(hslToRgb({ h: 0, s: 0, l: 0 }), { r: 0, g: 0, b: 0 });
});

test('hslToRgb wraps hue modulo 360, including negatives', () => {
  assert.deepEqual(hslToRgb({ h: 480, s: 100, l: 50 }), hslToRgb({ h: 120, s: 100, l: 50 }));
  assert.deepEqual(hslToRgb({ h: -30, s: 100, l: 50 }), hslToRgb({ h: 330, s: 100, l: 50 }));
});

test('hslToRgb clamps saturation and lightness to 0-100', () => {
  assert.deepEqual(hslToRgb({ h: 0, s: 150, l: 50 }), { r: 255, g: 0, b: 0 });
  assert.deepEqual(hslToRgb({ h: 200, s: 50, l: 120 }), { r: 255, g: 255, b: 255 });
  assert.deepEqual(hslToRgb({ h: 200, s: -10, l: 50 }), hslToRgb({ h: 200, s: 0, l: 50 }));
});

test('hex -> hsl -> hex survives the round trip within rounding', () => {
  for (const hex of ['#336699', '#e91e63', '#00bcd4', '#795548', '#c0ffee']) {
    const rgb = parseHex(hex);
    const back = hslToRgb(rgbToHsl({ r: rgb.r, g: rgb.g, b: rgb.b }));
    near(back.r, rgb.r, 1, `${hex} r`);
    near(back.g, rgb.g, 1, `${hex} g`);
    near(back.b, rgb.b, 1, `${hex} b`);
  }
});

test('mix interpolates channels linearly and rounds', () => {
  const red = { r: 255, g: 0, b: 0 };
  const blue = { r: 0, g: 0, b: 255 };
  assert.deepEqual(mix(red, blue, 0.5), { r: 128, g: 0, b: 128, a: 1 });
  assert.deepEqual(mix(red, blue, 0), { r: 255, g: 0, b: 0, a: 1 });
  assert.deepEqual(mix(red, blue, 1), { r: 0, g: 0, b: 255, a: 1 });
});

test('mix clamps t to the unit interval', () => {
  const a = { r: 10, g: 20, b: 30 };
  const b = { r: 200, g: 100, b: 0 };
  assert.deepEqual(mix(a, b, -0.5), mix(a, b, 0));
  assert.deepEqual(mix(a, b, 1.5), mix(a, b, 1));
});

test('mix interpolates alpha, defaulting missing alpha to opaque', () => {
  const glass = { r: 0, g: 0, b: 0, a: 0 };
  const solid = { r: 0, g: 0, b: 0 };
  const half = mix(glass, solid, 0.5);
  near(half.a, 0.5, 0.001, 'alpha');
});
