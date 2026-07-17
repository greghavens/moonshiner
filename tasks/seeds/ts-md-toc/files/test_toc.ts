import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { extractHeadings, slugify, generateToc, insertToc } from './toc.ts';

const guide = readFileSync(fileURLToPath(new URL('./fixtures/guide.md', import.meta.url)), 'utf8');

const guideToc = [
  '- [Field Guide](#field-guide)',
  '  - [Getting Started](#getting-started)',
  '    - [Install & Run](#install--run)',
  '  - [Usage](#usage)',
  '    - [Basics](#basics)',
  '  - [Usage](#usage-1)',
].join('\n');

test('slugify lowercases, drops punctuation, and maps each space to a hyphen', () => {
  assert.equal(slugify('Hello World'), 'hello-world');
  assert.equal(slugify('Install & Run'), 'install--run');
  assert.equal(slugify('C++ FAQ!'), 'c-faq');
  assert.equal(slugify('already-hyphen_ok'), 'already-hyphen_ok');
  assert.equal(slugify('Version 2.0'), 'version-20');
});

test('extractHeadings finds ATX headings with levels, text, and slugs', () => {
  const md = '# Top\n\ncontent\n\n## Sub One\n\n### Grand Child\n';
  assert.deepEqual(extractHeadings(md), [
    { level: 1, text: 'Top', slug: 'top' },
    { level: 2, text: 'Sub One', slug: 'sub-one' },
    { level: 3, text: 'Grand Child', slug: 'grand-child' },
  ]);
});

test('a hash without a following space or with seven hashes is not a heading', () => {
  const md = '#nospace\n\n####### seven\n\n## Real\n';
  assert.deepEqual(extractHeadings(md), [{ level: 2, text: 'Real', slug: 'real' }]);
});

test('closing hashes are stripped and inline markdown is flattened to text', () => {
  const md = '## Read the [docs](https://example.com) ##\n\n### Using `npm` **safely**\n';
  assert.deepEqual(extractHeadings(md), [
    { level: 2, text: 'Read the docs', slug: 'read-the-docs' },
    { level: 3, text: 'Using npm safely', slug: 'using-npm-safely' },
  ]);
});

test('headings inside fenced code blocks are ignored (``` and ~~~)', () => {
  const md = [
    '# Real',
    '',
    '```sh',
    '# fake one',
    '```',
    '',
    '~~~',
    '## fake two',
    '~~~',
    '',
    '## Real Two',
    '',
  ].join('\n');
  assert.deepEqual(extractHeadings(md), [
    { level: 1, text: 'Real', slug: 'real' },
    { level: 2, text: 'Real Two', slug: 'real-two' },
  ]);
});

test('duplicate heading titles get -1, -2 anchor suffixes in document order', () => {
  const md = '## Setup\n\n## Setup\n\n## Setup\n';
  assert.deepEqual(extractHeadings(md).map((h: { slug: string }) => h.slug), [
    'setup',
    'setup-1',
    'setup-2',
  ]);
});

test('duplicate counting spans ALL heading levels, even ones the TOC omits', () => {
  const md = '# T\n\n#### Notes\n\n## Notes\n';
  assert.deepEqual(extractHeadings(md).map((h: { slug: string }) => h.slug), ['t', 'notes', 'notes-1']);
  // the level-4 heading is out of the (default level<=3) TOC, but it already claimed #notes
  assert.equal(generateToc(md), '- [T](#t)\n  - [Notes](#notes-1)');
});

test('TOC indentation is relative to the shallowest included heading', () => {
  const md = '## A\n\n### B\n\n## C\n';
  assert.equal(generateToc(md), '- [A](#a)\n  - [B](#b)\n- [C](#c)');
});

test('generateToc honors the maxLevel option', () => {
  const md = '## A\n\n### B\n\n## C\n';
  assert.equal(generateToc(md, { maxLevel: 2 }), '- [A](#a)\n- [C](#c)');
});

test('generateToc renders the sample guide exactly', () => {
  assert.equal(generateToc(guide), guideToc);
});

test('generateToc of a heading-less document is empty', () => {
  assert.equal(generateToc(''), '');
  assert.equal(generateToc('just prose\n\nmore prose\n'), '');
  assert.deepEqual(extractHeadings(''), []);
});

test('insertToc fills the marker block and is idempotent', () => {
  const once = insertToc(guide);
  assert.ok(once.includes('<!-- toc -->\n\n' + guideToc + '\n\n<!-- tocstop -->'));
  assert.ok(once.includes('## Getting Started'));
  const twice = insertToc(once);
  assert.equal(twice, once);
});

test('insertToc replaces stale content between the markers', () => {
  const md = '<!-- toc -->\nOLD JUNK\nmore junk\n<!-- tocstop -->\n\n# Only\n';
  const out = insertToc(md);
  assert.ok(out.includes('<!-- toc -->\n\n- [Only](#only)\n\n<!-- tocstop -->'));
  assert.ok(!out.includes('OLD JUNK'));
});

test('insertToc leaves documents without markers untouched', () => {
  const md = '# Just a doc\n\nno markers here\n';
  assert.equal(insertToc(md), md);
  const onlyStart = '<!-- toc -->\n\n# Half\n';
  assert.equal(insertToc(onlyStart), onlyStart);
});
