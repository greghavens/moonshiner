import { test } from 'node:test';
import assert from 'node:assert/strict';
import { buildSite } from './site.ts';

function post(fm: Record<string, string>, body = 'Body text.'): string {
  const lines = ['---'];
  for (const [k, v] of Object.entries(fm)) lines.push(`${k}: ${v}`);
  lines.push('---', '', body);
  return lines.join('\n');
}

test('buildSite returns a Map with every expected page', () => {
  const pages = buildSite([
    post({ title: 'First', date: '2024-01-02', tags: 'tools' }),
    post({ title: 'Second', date: '2024-01-03' }),
  ]);
  assert.ok(pages instanceof Map);
  assert.deepEqual(
    [...pages.keys()].sort(),
    ['index.html', 'posts/first.html', 'posts/second.html', 'tags/index.html', 'tags/tools.html'],
  );
});

test('post pages render the article template exactly', () => {
  const pages = buildSite([
    post({ title: 'First', date: '2024-01-02', tags: 'ts, tools' }, '# Hi\n\nHello **world**'),
  ]);
  assert.equal(
    pages.get('posts/first.html'),
    '<article>\n' +
      '<h1>First</h1>\n' +
      '<p class="meta">2024-01-02 · tags: <a href="../tags/tools.html">tools</a>, <a href="../tags/ts.html">ts</a></p>\n' +
      '<h1>Hi</h1>\n' +
      '<p>Hello <strong>world</strong></p>\n' +
      '</article>',
  );
});

test('a post without tags gets a bare date meta line', () => {
  const pages = buildSite([post({ title: 'Solo', date: '2024-05-05' }, 'x')]);
  const page = pages.get('posts/solo.html')!;
  assert.ok(page.includes('<p class="meta">2024-05-05</p>'));
  assert.ok(!page.includes('tags:'));
});

test('titles are HTML-escaped on post pages and in the archive', () => {
  const pages = buildSite([post({ title: 'A & B <3', date: '2024-01-01' }, 'x')]);
  assert.ok(pages.get('posts/a-b-3.html')!.includes('<h1>A &amp; B &lt;3</h1>'));
  assert.ok(pages.get('index.html')!.includes('A &amp; B &lt;3'));
});

test('archive lists newest first, slug ascending on date ties', () => {
  const pages = buildSite([
    post({ title: 'Bee', date: '2024-03-01', slug: 'bb' }, 'x'),
    post({ title: 'Old', date: '2024-01-15', slug: 'cc' }, 'x'),
    post({ title: 'Ay', date: '2024-03-01', slug: 'aa' }, 'x'),
  ]);
  const index = pages.get('index.html')!;
  assert.ok(index.includes('<h1>Archive</h1>'));
  const liAa = '<li><a href="posts/aa.html">Ay</a> — 2024-03-01</li>';
  const liBb = '<li><a href="posts/bb.html">Bee</a> — 2024-03-01</li>';
  const liCc = '<li><a href="posts/cc.html">Old</a> — 2024-01-15</li>';
  for (const li of [liAa, liBb, liCc]) assert.ok(index.includes(li), `missing ${li}`);
  assert.ok(index.indexOf(liAa) < index.indexOf(liBb));
  assert.ok(index.indexOf(liBb) < index.indexOf(liCc));
});

test('drafts get no page and vanish from archive and tags', () => {
  const pages = buildSite([
    post({ title: 'Live', date: '2024-01-01', tags: 'shared' }, 'x'),
    post({ title: 'Hidden', date: '2024-06-01', draft: 'true', tags: 'shared, secret' }, 'x'),
  ]);
  assert.equal(pages.get('posts/hidden.html'), undefined);
  assert.ok(!pages.get('index.html')!.includes('Hidden'));
  assert.equal(pages.get('tags/secret.html'), undefined);
  assert.ok(!pages.get('tags/index.html')!.includes('secret'));
  assert.ok(!pages.get('tags/shared.html')!.includes('Hidden'));
});

test('tag pages list only their posts, newest first, with relative links', () => {
  const pages = buildSite([
    post({ title: 'One', date: '2024-01-01', tags: 'ts' }, 'x'),
    post({ title: 'Two', date: '2024-02-01', tags: 'ts, go' }, 'x'),
    post({ title: 'Three', date: '2024-03-01', tags: 'go' }, 'x'),
  ]);
  const ts = pages.get('tags/ts.html')!;
  assert.ok(ts.includes('<h1>Tag: ts</h1>'));
  const liTwo = '<li><a href="../posts/two.html">Two</a> — 2024-02-01</li>';
  const liOne = '<li><a href="../posts/one.html">One</a> — 2024-01-01</li>';
  assert.ok(ts.includes(liTwo));
  assert.ok(ts.includes(liOne));
  assert.ok(ts.indexOf(liTwo) < ts.indexOf(liOne));
  assert.ok(!ts.includes('Three'));
});

test('the tag index counts posts per tag, alphabetically', () => {
  const pages = buildSite([
    post({ title: 'One', date: '2024-01-01', tags: 'ts' }, 'x'),
    post({ title: 'Two', date: '2024-02-01', tags: 'ts, go' }, 'x'),
  ]);
  const idx = pages.get('tags/index.html')!;
  assert.ok(idx.includes('<h1>Tags</h1>'));
  const liGo = '<li><a href="go.html">go</a> (1)</li>';
  const liTs = '<li><a href="ts.html">ts</a> (2)</li>';
  assert.ok(idx.includes(liGo));
  assert.ok(idx.includes(liTs));
  assert.ok(idx.indexOf(liGo) < idx.indexOf(liTs));
});

test('duplicate slugs are refused by name', () => {
  assert.throws(
    () => buildSite([
      post({ title: 'Same Title', date: '2024-01-01' }, 'x'),
      post({ title: 'Same Title', date: '2024-02-01' }, 'y'),
    ]),
    /same-title/,
  );
});

test('duplicate slug validation includes drafts', () => {
  assert.throws(
    () => buildSite([
      post({ title: 'Hidden', date: '2024-01-01', slug: 'collision', draft: 'true' }, 'x'),
      post({ title: 'Live', date: '2024-02-01', slug: 'collision' }, 'y'),
    ]),
    /collision/,
    'a draft/live collision must be rejected before drafts are filtered',
  );
  assert.throws(
    () => buildSite([
      post({ title: 'Hidden One', date: '2024-01-01', slug: 'hidden-dup', draft: 'true' }, 'x'),
      post({ title: 'Hidden Two', date: '2024-02-01', slug: 'hidden-dup', draft: 'true' }, 'y'),
    ]),
    /hidden-dup/,
    'a draft/draft collision must also be rejected',
  );
});

test('an empty site still gets an archive and tag index', () => {
  const pages = buildSite([]);
  const index = pages.get('index.html')!;
  assert.ok(index.includes('<ul class="archive">'));
  assert.ok(!index.includes('<li>'));
  const tags = pages.get('tags/index.html')!;
  assert.ok(tags.includes('<ul class="tags">'));
  assert.ok(!tags.includes('<li>'));
});

test('a bad source aborts the whole build', () => {
  assert.throws(() => buildSite([post({ title: 'T', date: '2024-02-30' }, 'x')]), /2024-02-30/);
});
