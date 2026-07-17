import { test } from 'node:test';
import assert from 'node:assert/strict';
import { parseSelector, select } from './cssmatch.ts';
import type { DomNode } from './cssmatch.ts';

function el(tag: string, attrs: Record<string, string>, ...children: DomNode[]): DomNode {
  return { tag, attrs, children };
}

// The lint fixture: every element carries a unique data-n label so tests can
// assert exactly which nodes matched, and in what order.
const doc = el('html', { 'data-n': 'root' },
  el('nav', { 'data-n': 'nav', id: 'topnav', class: 'site-nav' },
    el('ul', { 'data-n': 'menu', class: 'menu' },
      el('li', { 'data-n': 'li1', class: 'item first' },
        el('a', { 'data-n': 'a1', href: '/' })),
      el('li', { 'data-n': 'li2', class: 'item' },
        el('a', { 'data-n': 'a2', href: '/docs', 'data-kind': 'guide' })),
      el('li', { 'data-n': 'li3', class: 'item last' },
        el('span', { 'data-n': 'badge', class: 'badge new' }),
        el('a', { 'data-n': 'a3', href: '/blog', 'data-kind': 'guides' })))),
  el('main', { 'data-n': 'main', id: 'content' },
    el('article', { 'data-n': 'art', class: 'post featured', 'data-lang': 'en US' },
      el('h2', { 'data-n': 'h2', class: 'title' }),
      el('p', { 'data-n': 'sum', class: 'summary' }),
      el('section', { 'data-n': 'body', class: 'body' },
        el('section', { 'data-n': 'inner', class: 'inner' },
          el('p', { 'data-n': 'p1' },
            el('a', { 'data-n': 'deep', target: '_blank', class: 'ref' })),
          el('p', { 'data-n': 'p2', class: 'note' })))),
    el('aside', { 'data-n': 'aside', class: 'sidebar' },
      el('section', { 'data-n': 'widget', class: 'widget' },
        el('h3', { 'data-n': 'h3' }),
        el('ul', { 'data-n': 'links', class: 'links items-list' },
          el('li', { 'data-n': 'li4' },
            el('a', { 'data-n': 'a4', href: '#top' })),
          el('li', { 'data-n': 'li5', class: 'ext' },
            el('a', { 'data-n': 'a5', rel: 'external', href: '' })))))));

function found(selector: string): string {
  return select(doc, selector).map((n) => n.attrs['data-n']).join(' ');
}

// ---------- simple selectors ----------

test('type selector matches by tag in document order', () => {
  assert.equal(found('li'), 'li1 li2 li3 li4 li5');
  assert.equal(found('h3'), 'h3');
  assert.equal(found('table'), '');
});

test('the root element is selectable like any other', () => {
  assert.equal(found('html'), 'root');
});

test('class selector matches whole space-separated tokens only', () => {
  assert.equal(found('.item'), 'li1 li2 li3'); // "items-list" must not match
  assert.equal(found('.items-list'), 'links');
  assert.equal(found('.first'), 'li1');
  assert.equal(found('.new'), 'badge');
  assert.equal(found('.Item'), ''); // token comparison is case-sensitive
  assert.equal(found('.absent'), '');
});

test('id selector matches attrs.id exactly', () => {
  assert.equal(found('#content'), 'main');
  assert.equal(found('#nope'), '');
});

test('attribute presence matches even when the value is empty', () => {
  assert.equal(found('[href]'), 'a1 a2 a3 a4 a5');
  assert.equal(found('[target]'), 'deep');
});

test('attribute value selectors compare the whole value', () => {
  assert.equal(found('[data-kind=guide]'), 'a2'); // "guides" is not "guide"
  assert.equal(found('[rel=external]'), 'a5');
  assert.equal(found('[data-lang="en US"]'), 'art'); // quoted values may hold spaces
});

// ---------- compound selectors ----------

test('compound selectors require every part to hold', () => {
  assert.equal(found('li.item.first'), 'li1');
  assert.equal(found('a[data-kind=guide]'), 'a2');
  assert.equal(found('p.note'), 'p2');
  assert.equal(found('section.widget'), 'widget');
});

// ---------- combinators ----------

test('descendant combinator matches at any depth', () => {
  assert.equal(found('nav a'), 'a1 a2 a3');
  assert.equal(found('main a'), 'deep a4 a5');
  assert.equal(found('article a'), 'deep');
  assert.equal(found('.post .note'), 'p2');
});

test('child combinator matches direct children only', () => {
  assert.equal(found('ul > li'), 'li1 li2 li3 li4 li5');
  assert.equal(found('article > p'), 'sum'); // p1/p2 sit two levels down
  assert.equal(found('article > section'), 'body');
  assert.equal(found('main > p'), '');
});

test('nested same-type ancestors yield each match once', () => {
  assert.equal(found('section section'), 'inner');
  // p1/p2 have TWO section ancestors; they must still appear exactly once
  assert.equal(found('section p'), 'p1 p2');
});

test('combinators chain across the whole selector', () => {
  assert.equal(found('main section > p'), 'p1 p2');
  assert.equal(found('aside > section h3'), 'h3');
});

test('whitespace around the child combinator is optional', () => {
  assert.equal(found('ul.menu>li'), 'li1 li2 li3');
  assert.equal(found('ul.menu > li'), 'li1 li2 li3');
  assert.equal(found('  nav a '), 'a1 a2 a3');
});

// ---------- positional pseudo-classes ----------

test('first-child means first element of its parent', () => {
  assert.equal(found('li:first-child'), 'li1 li4');
  assert.equal(found('a:first-child'), 'a1 a2 deep a4 a5'); // a3 follows the badge
});

test('the root has no parent, so positional pseudo-classes never match it', () => {
  assert.equal(found('html:first-child'), '');
  assert.equal(found('html:nth-child(1)'), '');
});

test('nth-child uses a 1-based index among the parent children', () => {
  assert.equal(found('li:nth-child(2)'), 'li2 li5');
  assert.equal(found('p:nth-child(2)'), 'sum p2');
  assert.equal(found('span:nth-child(1)'), 'badge');
  assert.equal(found('ul.menu > li:nth-child(3)'), 'li3');
});

test('pseudo-classes participate in chains', () => {
  assert.equal(found('ul > li:first-child a'), 'a1 a4');
});

// ---------- results and small trees ----------

test('results come back in pre-order document order', () => {
  assert.equal(found('a'), 'a1 a2 a3 deep a4 a5');
  assert.equal(found('section'), 'body inner widget');
});

test('a single-node tree with empty attrs works', () => {
  const lone: DomNode = { tag: 'p', attrs: {}, children: [] };
  assert.deepEqual(select(lone, 'p'), [lone]);
  assert.deepEqual(select(lone, 'p:first-child'), []);
  assert.deepEqual(select(lone, '.anything'), []);
});

// ---------- parsing ----------

test('parseSelector accepts the supported subset', () => {
  assert.ok(parseSelector('ul.menu > li:nth-child(2) a[href="/x"]'));
  assert.ok(parseSelector('[data-kind]'));
  assert.ok(parseSelector('#content .note'));
});

test('syntax outside the subset is rejected with an Error', () => {
  const rejected = [
    '', '   ',                    // empty
    '*',                          // universal selector: out of scope
    'li,a', 'li , a',             // selector lists: out of scope
    'li + a', 'li ~ a',           // sibling combinators: out of scope
    '> li', 'li >',               // dangling combinator
    ':nth-child(2n)', ':nth-child(odd)', ':nth-child(even)', // an+b forms: out of scope
    ':nth-child(0)', ':nth-child(-1)', ':nth-child()',       // index must be >= 1
    ':last-child', ':hover',      // unsupported pseudo-classes
    '::before',                   // pseudo-elements: out of scope
    '[href', "[x='v']", '[data-kind = guide]',               // strict attribute syntax
    'li..x', '#', '.',
  ];
  for (const src of rejected) {
    assert.throws(() => parseSelector(src), Error, `should reject: ${JSON.stringify(src)}`);
    assert.throws(() => select(doc, src), Error, `select should reject: ${JSON.stringify(src)}`);
  }
});
