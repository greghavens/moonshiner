import { test } from 'node:test';
import assert from 'node:assert/strict';
import { DEFAULTS, INHERITED, computeStyles, specificity } from './cascade.ts';
import type { Rule, StyleNode } from './cascade.ts';

function el(tag: string, attrs: Record<string, string>, ...children: StyleNode[]): StyleNode {
  return { tag, attrs, children };
}

function rule(selector: string, ...declarations: Array<[string, string]>): Rule {
  return { selector, declarations: declarations.map(([prop, value]) => ({ prop, value })) };
}

// The newsletter fixture the inliner runs over.
const h1 = el('h1', { class: 'title' });
const headerEl = el('header', { class: 'site-header' }, h1);
const para1 = el('p', { class: 'lead' });
const para2 = el('p', {});
const finePrint = el('p', { class: 'fine-print' });
const callout = el('div', { class: 'callout', 'data-tone': 'info' }, finePrint);
const mainEl = el('main', { class: 'content' }, para1, para2, callout);
const legalP = el('p', { class: 'legal' });
const footerEl = el('footer', {}, legalP);
const page = el('div', { id: 'page', class: 'page' }, headerEl, mainEl, footerEl);
const everyNode = [page, headerEl, h1, mainEl, para1, para2, callout, finePrint, footerEl, legalP];

// ---------- specificity tuples ----------

test('specificity counts (ids, classes+attributes, types)', () => {
  assert.deepEqual(specificity('*'), [0, 0, 0]);
  assert.deepEqual(specificity('div'), [0, 0, 1]);
  assert.deepEqual(specificity('.item'), [0, 1, 0]);
  assert.deepEqual(specificity('#nav'), [1, 0, 0]);
  assert.deepEqual(specificity('div.item.active'), [0, 2, 1]);
  assert.deepEqual(specificity('input[type=text].wide'), [0, 2, 1]);
  assert.deepEqual(specificity('#main.card[data-x]'), [1, 2, 0]);
  assert.deepEqual(specificity('.a.a'), [0, 2, 0]); // repeats count each time
});

// ---------- exported cascade tables ----------

test('inheritable property list and root defaults are pinned', () => {
  assert.deepEqual(new Set(INHERITED), new Set(['color', 'font-size', 'font-family', 'text-align']));
  assert.deepEqual(DEFAULTS, { color: 'black', 'font-size': '16px' });
});

// ---------- matching + defaults ----------

test('a type rule styles matching elements; everything else keeps defaults', () => {
  const styles = computeStyles(page, [rule('p', ['color', 'navy'])]);
  assert.deepEqual(styles.get(para1), { color: 'navy', 'font-size': '16px' });
  assert.deepEqual(styles.get(h1), { color: 'black', 'font-size': '16px' });
});

test('with no rules at all, every element carries exactly the defaults', () => {
  const styles = computeStyles(page, []);
  assert.equal(styles.size, everyNode.length);
  for (const node of everyNode) {
    assert.deepEqual(styles.get(node), { color: 'black', 'font-size': '16px' });
  }
});

test('attribute selectors match presence and whole values', () => {
  const styles = computeStyles(page, [
    rule('[data-tone=info]', ['background', 'lightyellow']),
    rule('[data-tone]', ['border-left', '3px solid']),
  ]);
  assert.equal(styles.get(callout)!['background'], 'lightyellow');
  assert.equal(styles.get(callout)!['border-left'], '3px solid');
  assert.ok(!('background' in styles.get(para1)!));
  assert.ok(!('background' in styles.get(finePrint)!)); // background does not inherit
});

test('different matching rules contribute different properties', () => {
  const styles = computeStyles(page, [
    rule('.lead', ['font-style', 'italic']),
    rule('p', ['border', '1px solid gray']),
  ]);
  assert.equal(styles.get(para1)!['font-style'], 'italic');
  assert.equal(styles.get(para1)!['border'], '1px solid gray');
  assert.ok(!('border' in styles.get(h1)!));
});

// ---------- the cascade ----------

test('higher specificity wins even against a later rule', () => {
  const styles = computeStyles(page, [
    rule('.lead', ['color', 'teal']),
    rule('p', ['color', 'gray']),
  ]);
  assert.equal(styles.get(para1)!['color'], 'teal');
  assert.equal(styles.get(para2)!['color'], 'gray');
});

test('a type selector beats the universal selector regardless of order', () => {
  const styles = computeStyles(page, [
    rule('p', ['color', 'blue']),
    rule('*', ['color', 'red']),
  ]);
  assert.equal(styles.get(para2)!['color'], 'blue');
  assert.equal(styles.get(headerEl)!['color'], 'red');
});

test('equal specificity: the later rule wins', () => {
  let styles = computeStyles(page, [
    rule('p', ['color', 'gray']),
    rule('p', ['color', 'olive']),
  ]);
  assert.equal(styles.get(para2)!['color'], 'olive');
  styles = computeStyles(page, [
    rule('.lead', ['color', 'teal']),
    rule('.lead', ['color', 'maroon']),
  ]);
  assert.equal(styles.get(para1)!['color'], 'maroon');
});

test('within one rule, the later declaration of a property wins', () => {
  const styles = computeStyles(page, [
    rule('p', ['color', 'red'], ['color', 'blue']),
  ]);
  assert.equal(styles.get(para2)!['color'], 'blue');
});

// ---------- !important ----------

test('an important declaration beats higher specificity', () => {
  const styles = computeStyles(page, [
    rule('.lead', ['color', 'teal']),
    rule('p', ['color', 'gray !important']),
  ]);
  assert.equal(styles.get(para1)!['color'], 'gray');
});

test('between two important declarations, specificity then order decide', () => {
  let styles = computeStyles(page, [
    rule('p', ['color', 'gray !important']),
    rule('.lead', ['color', 'teal !important']),
  ]);
  assert.equal(styles.get(para1)!['color'], 'teal');
  styles = computeStyles(page, [
    rule('p', ['color', 'gray !important']),
    rule('p', ['color', 'olive !important']),
  ]);
  assert.equal(styles.get(para2)!['color'], 'olive');
});

test('an earlier important declaration survives a later normal one in the same rule', () => {
  const styles = computeStyles(page, [
    rule('p', ['color', 'red !important'], ['color', 'blue']),
  ]);
  assert.equal(styles.get(para2)!['color'], 'red');
});

test('the important marker is case-insensitive, whitespace-tolerant, and stripped from the value', () => {
  const styles = computeStyles(page, [
    rule('.lead', ['color', 'teal']),
    rule('p', ['color', 'red !IMPORTANT'], ['margin', '  12px   !important  ']),
  ]);
  assert.equal(styles.get(para1)!['color'], 'red');
  assert.equal(styles.get(para1)!['margin'], '12px');
});

// ---------- inheritance ----------

test('inheritable properties flow down and can be re-set mid-tree', () => {
  const styles = computeStyles(page, [
    rule('#page', ['color', 'dimgray']),
    rule('.content', ['color', 'darkslategray']),
  ]);
  assert.equal(styles.get(h1)!['color'], 'dimgray');
  assert.equal(styles.get(footerEl)!['color'], 'dimgray');
  assert.equal(styles.get(legalP)!['color'], 'dimgray');
  assert.equal(styles.get(para2)!['color'], 'darkslategray');
  assert.equal(styles.get(finePrint)!['color'], 'darkslategray');
});

test('font-size inherits verbatim; values are opaque strings', () => {
  const styles = computeStyles(page, [rule('.content', ['font-size', '15px'])]);
  assert.equal(styles.get(finePrint)!['font-size'], '15px');
  assert.equal(styles.get(h1)!['font-size'], '16px');
});

test('inheritable properties without a default appear only where set or inherited', () => {
  const styles = computeStyles(page, [rule('.callout', ['text-align', 'center'])]);
  assert.equal(styles.get(callout)!['text-align'], 'center');
  assert.equal(styles.get(finePrint)!['text-align'], 'center');
  assert.ok(!('text-align' in styles.get(para1)!));
  assert.ok(!('text-align' in styles.get(page)!));
});

test('non-inherited properties stay on the element they were declared for', () => {
  const styles = computeStyles(page, [
    rule('.content', ['margin', '24px'], ['display', 'flex']),
  ]);
  assert.equal(styles.get(mainEl)!['margin'], '24px');
  assert.equal(styles.get(mainEl)!['display'], 'flex');
  assert.ok(!('margin' in styles.get(para1)!));
  assert.ok(!('display' in styles.get(para1)!));
  assert.ok(!('margin' in styles.get(legalP)!));
});

test('an element declaration beats an inherited value', () => {
  const styles = computeStyles(page, [
    rule('.content', ['color', 'darkslategray']),
    rule('.fine-print', ['color', 'firebrick']),
  ]);
  assert.equal(styles.get(finePrint)!['color'], 'firebrick');
  assert.equal(styles.get(para1)!['color'], 'darkslategray');
});

test('a universal rule reaches every element directly', () => {
  const styles = computeStyles(page, [rule('*', ['font-family', 'Georgia'])]);
  for (const node of everyNode) {
    assert.equal(styles.get(node)!['font-family'], 'Georgia');
  }
});

// ---------- rejected selector syntax ----------

test('selectors outside the compound subset are rejected with an Error', () => {
  const rejected = [
    '', '   ',
    'p .x',          // descendant combinator: out of scope here
    'p > .x',        // child combinator: out of scope here
    'p:first-child', // pseudo-classes: out of scope here
    '::after',
    'a,b',
    'p + .x', '.x ~ y',
    'p*',
  ];
  for (const src of rejected) {
    assert.throws(() => specificity(src), Error, `specificity should reject ${JSON.stringify(src)}`);
    assert.throws(
      () => computeStyles(page, [rule(src, ['color', 'red'])]),
      Error,
      `computeStyles should reject ${JSON.stringify(src)}`,
    );
  }
});
