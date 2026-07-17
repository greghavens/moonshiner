// Acceptance tests for the workflow expression engine (exprflow.ts).
//
// evaluate(template, context) resolves ${ } expressions against a data
// context. A template that is exactly one ${ } yields the typed value;
// any surrounding text makes the result a string.
//
// Run: node --test test_exprflow.ts

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { evaluate, ExprError } from './exprflow.ts';

function failsAt(template: string, context: unknown, pos: number, detail: string): void {
  try {
    evaluate(template, context);
  } catch (err) {
    assert.ok(err instanceof ExprError, `expected ExprError, got ${err}`);
    assert.equal(err.pos, pos, `pos (message: ${err.message})`);
    assert.ok(err.message.startsWith(`position ${pos}: `),
      `message must start with "position ${pos}: " — got: ${err.message}`);
    assert.ok(err.message.includes(detail),
      `message must mention "${detail}" — got: ${err.message}`);
    return;
  }
  throw new Error('expected an ExprError, nothing was thrown');
}

const ctx = {
  user: { name: 'Ada', tags: ['ops', 'admin'] },
  count: 3,
  items: [],
  flag: false,
};

// ------------------------------------------------------- plain text passes

test('templates without ${ come back unchanged', () => {
  assert.equal(evaluate('plain text', ctx), 'plain text');
  assert.equal(evaluate('costs $5 (really)', ctx), 'costs $5 (really)');
  assert.equal(evaluate('', ctx), '');
});

// ------------------------------------------------------- single-expression

test('literals keep their types in single-expression mode', () => {
  assert.equal(evaluate('${ 42 }', ctx), 42);
  assert.equal(evaluate('${ 3.5 }', ctx), 3.5);
  assert.equal(evaluate('${ "hi" }', ctx), 'hi');
  assert.equal(evaluate("${ 'hi' }", ctx), 'hi');
  assert.equal(evaluate('${ true }', ctx), true);
  assert.equal(evaluate('${ null }', ctx), null);
});

test('string literals decode escapes', () => {
  assert.equal(evaluate('${ "a\\nb\\tc" }', ctx), 'a\nb\tc');
  assert.equal(evaluate("${ 'it\\'s' }", ctx), "it's");
  assert.equal(evaluate('${ "q:\\"x\\"" }', ctx), 'q:"x"');
});

test('surrounding text forces string mode, even just a space', () => {
  assert.equal(evaluate('${ 1 }', ctx), 1);
  assert.equal(evaluate(' ${ 1 }', ctx), ' 1');
  assert.equal(evaluate('${ 1 } ', ctx), '1 ');
});

// ------------------------------------------------------------------- paths

test('dot paths resolve into the context', () => {
  assert.equal(evaluate('${ .user.name }', ctx), 'Ada');
  assert.equal(evaluate('${ .user.tags[1] }', ctx), 'admin');
  assert.equal(evaluate('${ .count }', ctx), 3);
});

test('a lone dot is the whole context, by reference', () => {
  assert.equal(evaluate('${ . }', ctx), ctx);
  assert.equal(evaluate('${ .user.tags }', ctx), ctx.user.tags);
});

test('a path can index the context root', () => {
  assert.equal(evaluate('${ .[1] }', ['a', 'b']), 'b');
});

test('missing or untraversable paths resolve to null', () => {
  assert.equal(evaluate('${ .missing }', ctx), null);
  assert.equal(evaluate('${ .user.dept.head }', ctx), null);
  assert.equal(evaluate('${ .user.tags[9] }', ctx), null);
  assert.equal(evaluate('${ .user.name.length }', ctx), null); // strings are not traversed
  assert.equal(evaluate('${ .count[0] }', ctx), null);
});

// -------------------------------------------------------------- arithmetic

test('arithmetic with standard precedence and parentheses', () => {
  assert.equal(evaluate('${ 1 + 2 * 3 }', ctx), 7);
  assert.equal(evaluate('${ (1 + 2) * 3 }', ctx), 9);
  assert.equal(evaluate('${ 10 % 3 }', ctx), 1);
  assert.equal(evaluate('${ 7 % 4 * 2 }', ctx), 6);
  assert.equal(evaluate('${ 10 / 4 }', ctx), 2.5);
  assert.equal(evaluate('${ 5 - 2 - 1 }', ctx), 2);
  assert.equal(evaluate('${ (.count + 1) * 2 }', ctx), 8);
});

test('unary minus and not', () => {
  assert.equal(evaluate('${ -.count }', ctx), -3);
  assert.equal(evaluate('${ 2 - -3 }', ctx), 5);
  assert.equal(evaluate('${ !.flag }', ctx), true);
});

test('+ adds numbers, otherwise concatenates the stringified operands', () => {
  assert.equal(evaluate('${ "id-" + 7 }', ctx), 'id-7');
  assert.equal(evaluate('${ .count + "!" }', ctx), '3!');
  assert.equal(evaluate('${ 1 + 2 + "x" }', ctx), '3x');
  assert.equal(evaluate('${ "v" + null }', ctx), 'v');
});

test('other arithmetic requires numbers on both sides', () => {
  failsAt('${ "a" * 2 }', ctx, 7, 'arithmetic requires numbers');
  failsAt('${ -"a" }', ctx, 3, 'arithmetic requires numbers');
});

// ------------------------------------------------------------- comparisons

test('numeric and string ordering comparisons', () => {
  assert.equal(evaluate('${ 2 < 10 }', ctx), true);
  assert.equal(evaluate('${ 2 <= 1 }', ctx), false);
  assert.equal(evaluate('${ .count >= 3 }', ctx), true);
  assert.equal(evaluate('${ "abc" < "b" }', ctx), true);
  assert.equal(evaluate('${ "b" > "abc" }', ctx), true);
});

test('ordering across types is an error at the operator', () => {
  failsAt('${ 1 < "2" }', ctx, 5, 'comparison requires two numbers or two strings');
});

test('comparisons bind tighter than equality', () => {
  assert.equal(evaluate('${ 1 < 2 == true }', ctx), true);
});

// ---------------------------------------------------------------- equality

test('equality is by value, never by coercion', () => {
  assert.equal(evaluate('${ .user.name == "Ada" }', ctx), true);
  assert.equal(evaluate('${ 1 != 2 }', ctx), true);
  assert.equal(evaluate('${ 1 == "1" }', ctx), false);
  assert.equal(evaluate('${ .missing == null }', ctx), true);
});

test('equality on arrays and objects is structural', () => {
  const c = { a: [1, { k: 2 }], b: [1, { k: 2 }], c: [1, { k: 3 }] };
  assert.equal(evaluate('${ .a == .b }', c), true);
  assert.equal(evaluate('${ .a == .c }', c), false);
  assert.equal(evaluate('${ .a != .c }', c), true);
});

// --------------------------------------------------- truthiness and logic

test('workflow truthiness: false, null, 0, "", and [] are falsy', () => {
  assert.equal(evaluate('${ !0 }', ctx), true);
  assert.equal(evaluate('${ !"" }', ctx), true);
  assert.equal(evaluate('${ !.items }', ctx), true); // [] is falsy here
  assert.equal(evaluate('${ !.missing }', ctx), true);
  assert.equal(evaluate('${ !"0" }', ctx), false);
  assert.equal(evaluate('${ !.user }', ctx), false); // objects are truthy
});

test('&& and || return the deciding operand', () => {
  assert.equal(evaluate('${ 0 || "fallback" }', ctx), 'fallback');
  assert.equal(evaluate('${ "" || .count }', ctx), 3);
  assert.equal(evaluate('${ .user && .user.name }', ctx), 'Ada');
  assert.equal(evaluate('${ null && .anything }', ctx), null);
});

test('&& and || short-circuit', () => {
  assert.equal(evaluate('${ false && ("x" * 2) }', ctx), false);
  assert.equal(evaluate('${ true || ("x" * 2) }', ctx), true);
});

test('|| binds looser than &&', () => {
  assert.equal(evaluate('${ true || false && false }', ctx), true);
});

// -------------------------------------------------------------- mixed mode

test('interpolation stringifies into the surrounding text', () => {
  assert.equal(evaluate('order ${ .count } ready', ctx), 'order 3 ready');
  assert.equal(evaluate('${ .user.name }: ${ .count + 1 } items', ctx), 'Ada: 4 items');
});

test('null and missing values interpolate as empty text', () => {
  assert.equal(evaluate('a=${ .a }, b=${ .b }', { a: 1, b: null }), 'a=1, b=');
  assert.equal(evaluate('[${ .missing }]', ctx), '[]');
});

test('booleans, arrays, and objects interpolate predictably', () => {
  assert.equal(evaluate('flag=${ .flag }', ctx), 'flag=false');
  assert.equal(evaluate('tags: ${ .tags }', { tags: [1, 'a'] }), 'tags: [1,"a"]');
  assert.equal(evaluate('cfg: ${ .cfg }', { cfg: { a: 1 } }), 'cfg: {"a":1}');
});

test('a closing brace inside a string literal does not end the expression', () => {
  assert.equal(evaluate('${ "a}b" }', ctx), 'a}b');
  assert.equal(evaluate('x ${ "}" } y', ctx), 'x } y');
});

// ------------------------------------------------------------ parse errors

test('unterminated expression', () => {
  failsAt('x ${ .a', ctx, 2, 'unterminated expression');
});

test('empty expression', () => {
  failsAt('${   }', ctx, 0, 'empty expression');
});

test('bare identifiers are not paths', () => {
  failsAt('${ status }', ctx, 3, 'unexpected token "status"');
});

test('leftover tokens after a complete expression', () => {
  failsAt('${ 1 2 }', ctx, 5, 'unexpected token "2"');
});

test('missing operand', () => {
  failsAt('${ 1 + }', ctx, 7, 'unexpected token "}"');
});

test('unterminated string literal', () => {
  failsAt('${ "abc }', ctx, 3, 'unterminated string');
});

test('malformed path segments', () => {
  failsAt('${ .a[x] }', ctx, 6, 'invalid path segment');
});

test('ExprError extends Error and exposes pos', () => {
  try {
    evaluate('${ nope }', ctx);
    throw new Error('should have thrown');
  } catch (err) {
    assert.ok(err instanceof ExprError);
    assert.ok(err instanceof Error);
    assert.equal(typeof (err as ExprError).pos, 'number');
  }
});
