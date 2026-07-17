import { test } from 'node:test';
import assert from 'node:assert/strict';
import { parsePost, slugify } from './posts.ts';

const src = (lines: string[]) => lines.join('\n');

test('parses title, date and body with defaults', () => {
  const p = parsePost(src(['---', 'title: Hello', 'date: 2024-03-05', '---', '', 'First paragraph.', '']));
  assert.equal(p.title, 'Hello');
  assert.equal(p.date, '2024-03-05');
  assert.equal(p.body, 'First paragraph.');
  assert.equal(p.draft, false);
  assert.deepEqual(p.tags, []);
  assert.equal(p.slug, 'hello');
});

test('slug defaults to slugified title', () => {
  const p = parsePost(src(['---', 'title: Hello, World! 2.0', 'date: 2024-01-01', '---', 'x']));
  assert.equal(p.slug, 'hello-world-2-0');
});

test('explicit slug wins over the title', () => {
  const p = parsePost(src(['---', 'title: Hello', 'slug: custom-path', 'date: 2024-01-01', '---', 'x']));
  assert.equal(p.slug, 'custom-path');
});

test('slugify collapses punctuation runs and trims dashes', () => {
  assert.equal(slugify('Hello, World! 2.0'), 'hello-world-2-0');
  assert.equal(slugify('  --Weird--  '), 'weird');
  assert.equal(slugify('Café au lait'), 'caf-au-lait');
  assert.equal(slugify('plain'), 'plain');
});

test('tags are comma-split, slugified, deduped in first-seen order', () => {
  const p = parsePost(src(['---', 'title: T', 'date: 2024-01-01',
    'tags: TypeScript,  Web Dev, typescript, ', '---', 'x']));
  assert.deepEqual(p.tags, ['typescript', 'web-dev']);
});

test('draft flag parses true/false and rejects anything else', () => {
  const mk = (v: string) => parsePost(src(['---', 'title: T', 'date: 2024-01-01', `draft: ${v}`, '---', 'x']));
  assert.equal(mk('true').draft, true);
  assert.equal(mk('false').draft, false);
  assert.throws(() => mk('yes'), /draft/);
});

test('values may contain colons after the first one', () => {
  const p = parsePost(src(['---', 'title: Re: the launch', 'date: 2024-01-01', '---', 'x']));
  assert.equal(p.title, 'Re: the launch');
});

test('blank lines inside front matter are ignored', () => {
  const p = parsePost(src(['---', 'title: T', '', 'date: 2024-01-01', '---', 'x']));
  assert.equal(p.title, 'T');
});

test('body keeps internal blank lines but sheds outer ones', () => {
  const p = parsePost(src(['---', 'title: T', 'date: 2024-01-01', '---', '', '', 'a', '', 'b', '', '']));
  assert.equal(p.body, 'a\n\nb');
});

test('body may itself contain --- lines', () => {
  const p = parsePost(src(['---', 'title: T', 'date: 2024-01-01', '---', 'a', '---', 'b']));
  assert.equal(p.body, 'a\n---\nb');
});

test('missing front matter fence is an error', () => {
  assert.throws(() => parsePost('title: x\ndate: 2024-01-01\n\nbody'), /front matter/);
  assert.throws(() => parsePost(src(['---', 'title: x', 'date: 2024-01-01', 'no closing fence'])), /front matter/);
});

test('title and date are required and non-empty', () => {
  assert.throws(() => parsePost(src(['---', 'date: 2024-01-01', '---', 'x'])), /title/);
  assert.throws(() => parsePost(src(['---', 'title:', 'date: 2024-01-01', '---', 'x'])), /title/);
  assert.throws(() => parsePost(src(['---', 'title: T', '---', 'x'])), /date/);
});

test('dates must be real YYYY-MM-DD calendar dates', () => {
  for (const bad of ['2024-13-01', '2024-02-30', '05/03/2024', '2024-1-1']) {
    assert.throws(
      () => parsePost(src(['---', 'title: T', `date: ${bad}`, '---', 'x'])),
      (e: unknown) => (e as Error).message.includes(bad),
      `expected error naming ${bad}`,
    );
  }
  // leap day on a leap year is fine
  const p = parsePost(src(['---', 'title: T', 'date: 2024-02-29', '---', 'x']));
  assert.equal(p.date, '2024-02-29');
});

test('unknown front matter keys are rejected by name', () => {
  assert.throws(
    () => parsePost(src(['---', 'title: T', 'date: 2024-01-01', 'author: bob', '---', 'x'])),
    /author/,
  );
});
