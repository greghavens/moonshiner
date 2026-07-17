import { test } from 'node:test';
import assert from 'node:assert/strict';
import { renderMarkdown, escapeHtml } from './render.ts';

test('escapeHtml covers the four specials', () => {
  assert.equal(escapeHtml('a & b < c > d "e"'), 'a &amp; b &lt; c &gt; d &quot;e&quot;');
  assert.equal(escapeHtml('plain'), 'plain');
});

test('a lone block becomes a paragraph', () => {
  assert.equal(renderMarkdown('Just text.'), '<p>Just text.</p>');
});

test('blank lines separate paragraphs, however many there are', () => {
  assert.equal(renderMarkdown('a\n\nb'), '<p>a</p>\n<p>b</p>');
  assert.equal(renderMarkdown('a\n\n\n\nb'), '<p>a</p>\n<p>b</p>');
});

test('single newlines inside a paragraph join with a space', () => {
  assert.equal(renderMarkdown('line one\nline two'), '<p>line one line two</p>');
});

test('headings h1 through h3', () => {
  assert.equal(renderMarkdown('# Title'), '<h1>Title</h1>');
  assert.equal(renderMarkdown('## Sub'), '<h2>Sub</h2>');
  assert.equal(renderMarkdown('### Deep'), '<h3>Deep</h3>');
});

test('four hashes or a missing space is not a heading', () => {
  assert.equal(renderMarkdown('#### nope'), '<p>#### nope</p>');
  assert.equal(renderMarkdown('#nope'), '<p>#nope</p>');
});

test('dash blocks become bullet lists', () => {
  assert.equal(renderMarkdown('- alpha\n- beta'), '<ul>\n<li>alpha</li>\n<li>beta</li>\n</ul>');
});

test('list items get inline formatting too', () => {
  assert.equal(renderMarkdown('- **bold** item'), '<ul>\n<li><strong>bold</strong> item</li>\n</ul>');
});

test('bold, italic and code spans', () => {
  assert.equal(renderMarkdown('a **b** c'), '<p>a <strong>b</strong> c</p>');
  assert.equal(renderMarkdown('an *emphatic* word'), '<p>an <em>emphatic</em> word</p>');
  assert.equal(renderMarkdown('run `npm ci` first'), '<p>run <code>npm ci</code> first</p>');
});

test('double asterisks never leak an <em>', () => {
  const html = renderMarkdown('**strong**');
  assert.equal(html, '<p><strong>strong</strong></p>');
  assert.ok(!html.includes('<em>'));
});

test('code spans keep their contents literal', () => {
  assert.equal(renderMarkdown('use `**argv**` here'), '<p>use <code>**argv**</code> here</p>');
  assert.equal(renderMarkdown('`[not](a-link)`'), '<p><code>[not](a-link)</code></p>');
});

test('code span contents are still HTML-escaped', () => {
  assert.equal(renderMarkdown('check `x < 1`'), '<p>check <code>x &lt; 1</code></p>');
});

test('links render with href and text', () => {
  assert.equal(renderMarkdown('[docs](https://x.dev)'), '<p><a href="https://x.dev">docs</a></p>');
});

test('ampersands in link targets are escaped in the attribute', () => {
  assert.equal(
    renderMarkdown('[q](http://x?a=1&b=2)'),
    '<p><a href="http://x?a=1&amp;b=2">q</a></p>',
  );
});

test('plain text is HTML-escaped', () => {
  assert.equal(renderMarkdown('a < b & c > d'), '<p>a &lt; b &amp; c &gt; d</p>');
});

test('empty or whitespace-only input renders to nothing', () => {
  assert.equal(renderMarkdown(''), '');
  assert.equal(renderMarkdown('  \n \n'), '');
});

test('a small document holds together', () => {
  const doc = '# Release notes\n\nWe shipped **v2** — see [changes](/log).\n\n- faster\n- smaller';
  assert.equal(
    renderMarkdown(doc),
    '<h1>Release notes</h1>\n' +
      '<p>We shipped <strong>v2</strong> — see <a href="/log">changes</a>.</p>\n' +
      '<ul>\n<li>faster</li>\n<li>smaller</li>\n</ul>',
  );
});
