import { test } from 'node:test';
import assert from 'node:assert/strict';
import { buildSite } from './site.ts';
import { loadConfig } from './config.ts';
import { parseFrontmatter } from './frontmatter.ts';
import { renderMarkdown } from './markdown.ts';
import type { Plugin } from './types.ts';

// ---------------------------------------------------------------- fixtures

const baseConfig = { title: 'Ops Notes', baseUrl: 'https://ops.example.com' };

const baseSources: Record<string, string> = {
  'index.md': [
    '---',
    'title: Home',
    '---',
    '',
    'Welcome to the **ops** handbook.',
    '',
    '- [About](https://ops.example.com/about/)',
    '- runbooks',
  ].join('\n'),
  'about.md': ['---', 'title: About', '---', '', '## Team', '', 'We keep the lights on.'].join('\n'),
  'posts/first-post.md': [
    '---',
    'title: First Post',
    'date: 2024-01-15',
    'tags: ops, intro',
    'excerpt: Hello from the ops team.',
    '---',
    '',
    'Our first update.',
  ].join('\n'),
  'posts/second-post.md': [
    '---',
    'title: Second Post',
    'date: 2024-02-20',
    '---',
    '',
    'A *second* update with `code`.',
  ].join('\n'),
  'style.css': 'body { color: #222; }\n',
};

// ================================================================ existing behavior
// This block passes against the shipped code and must stay green.

test('end-to-end build produces exactly the expected file map keys', () => {
  const { files } = buildSite(baseConfig, baseSources);
  assert.deepEqual(Object.keys(files), [
    'about/index.html',
    'feed.xml',
    'index.html',
    'posts/first-post/index.html',
    'posts/second-post/index.html',
    'sitemap.xml',
    'style.css',
  ]);
});

test('markdown renders inside the layout with title and nav', () => {
  const { files } = buildSite(baseConfig, baseSources);
  const index = files['index.html'];
  assert.ok(index.startsWith('<!doctype html>\n'), index.slice(0, 40));
  assert.ok(index.includes('<title>Home — Ops Notes</title>'));
  assert.ok(index.includes('<strong>ops</strong>'));
  assert.ok(index.includes('<a href="https://ops.example.com/about/">About</a>'));
  assert.ok(index.includes('<ul>'));

  const about = files['about/index.html'];
  assert.ok(about.includes('<h2>Team</h2>'));
  assert.equal(
    about.includes(
      '<nav><a href="https://ops.example.com/about/" aria-current="page">About</a> ' +
        '<a href="https://ops.example.com/">Home</a></nav>',
    ),
    true,
    about,
  );
});

test('sitemap.xml is stable, sorted, and carries lastmod for dated pages', () => {
  const { files } = buildSite(baseConfig, baseSources);
  assert.equal(
    files['sitemap.xml'],
    [
      '<?xml version="1.0" encoding="UTF-8"?>',
      '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
      '  <url><loc>https://ops.example.com/</loc></url>',
      '  <url><loc>https://ops.example.com/about/</loc></url>',
      '  <url><loc>https://ops.example.com/posts/first-post/</loc><lastmod>2024-01-15</lastmod></url>',
      '  <url><loc>https://ops.example.com/posts/second-post/</loc><lastmod>2024-02-20</lastmod></url>',
      '</urlset>',
      '',
    ].join('\n'),
  );
});

test('feed.xml lists dated pages newest first with excerpts as descriptions', () => {
  const { files } = buildSite(baseConfig, baseSources);
  const feed = files['feed.xml'];
  const second = feed.indexOf('<title>Second Post</title>');
  const first = feed.indexOf('<title>First Post</title>');
  assert.ok(second !== -1 && first !== -1 && second < first, feed);
  assert.ok(feed.includes('<description>Hello from the ops team.</description>'));
  assert.ok(feed.includes('<description>A second update with code.</description>'));
  assert.ok(feed.includes('<pubDate>2024-02-20</pubDate>'));
  assert.ok(!feed.includes('<title>Home</title>'), 'undated pages are never feed items');

  const limited = buildSite({ ...baseConfig, feedLimit: 1 }, baseSources);
  assert.ok(limited.files['feed.xml'].includes('<title>Second Post</title>'));
  assert.ok(!limited.files['feed.xml'].includes('<title>First Post</title>'));
});

test('assets pass through byte-identical and unsafe text is escaped', () => {
  const { files } = buildSite(baseConfig, baseSources);
  assert.equal(files['style.css'], 'body { color: #222; }\n');

  const escaped = buildSite(baseConfig, {
    'notes.md': ['---', 'title: Notes', '---', '', 'Alerts & <script> tags stay text.'].join('\n'),
  });
  assert.ok(escaped.files['index.html'] === undefined);
  assert.ok(escaped.files['notes/index.html'].includes('Alerts &amp; &lt;script&gt; tags stay text.'));
});

test('prettyUrls: false switches to flat .html paths and urls', () => {
  const { files } = buildSite({ ...baseConfig, prettyUrls: false }, baseSources);
  assert.ok(files['about.html'] !== undefined, Object.keys(files).join(','));
  assert.ok(files['about/index.html'] === undefined);
  assert.ok(files['sitemap.xml'].includes('<loc>https://ops.example.com/about.html</loc>'));
});

test('config is validated strictly', () => {
  assert.throws(() => buildSite({}, baseSources), /title must be a non-empty string/);
  assert.throws(() => buildSite({ ...baseConfig, basUrl: 'x' }, baseSources), /unknown config key: basUrl/);
  assert.throws(() => buildSite({ ...baseConfig, feedLimit: 0 }, baseSources), /feedLimit must be a positive integer/);
  assert.throws(() => buildSite({ ...baseConfig, prettyUrls: 'yes' }, baseSources), /prettyUrls must be a boolean/);
  const cfg = loadConfig(baseConfig);
  assert.equal(cfg.prettyUrls, true);
  assert.equal(cfg.feedLimit, 10);
  assert.equal(cfg.baseUrl, 'https://ops.example.com');
});

test('front matter coerces types and reports malformed lines', () => {
  const parsed = parseFrontmatter(
    ['---', 'title: Hello', 'weight: 3', 'pinned: true', 'tags: a, b , c', '---', 'Body.'].join('\n'),
  );
  assert.deepEqual(parsed.meta, { title: 'Hello', weight: 3, pinned: true, tags: ['a', 'b', 'c'] });
  assert.equal(parsed.body, 'Body.');

  assert.deepEqual(parseFrontmatter('No block here.'), { meta: {}, body: 'No block here.' });
  assert.throws(() => parseFrontmatter('---\njust words\n---\n'), /line 2 is not "key: value"/);
  assert.throws(() => parseFrontmatter('---\ntitle: x\n'), /without a closing ---/);
});

test('markdown subset: headings, lists, inline styles, links', () => {
  const html = renderMarkdown(
    ['# Big', '', '- one', '- **two**', '', 'See [docs](https://d.example/) and `x`.'].join('\n'),
  );
  assert.ok(html.includes('<h1>Big</h1>'));
  assert.ok(html.includes('<li>one</li>'));
  assert.ok(html.includes('<li><strong>two</strong></li>'));
  assert.ok(html.includes('<a href="https://d.example/">docs</a>'));
  assert.ok(html.includes('<code>x</code>'));
});

test('custom plugins: filtering, emitting, duplicate names rejected', () => {
  const gate: Plugin = {
    name: 'tag-gate',
    includePage: (page) => !page.tags.includes('internal'),
  };
  const robots: Plugin = {
    name: 'robots',
    emit: () => ({ 'robots.txt': 'User-agent: *\nAllow: /\n' }),
  };
  const sources = {
    ...baseSources,
    'secret.md': ['---', 'title: Secret', 'tags: internal', '---', '', 'Internal only.'].join('\n'),
  };
  const { files } = buildSite(baseConfig, sources, [gate, robots]);
  assert.ok(files['secret/index.html'] === undefined, 'filtered page must not be rendered');
  assert.ok(!files['sitemap.xml'].includes('secret'), 'filtered page must not reach the sitemap');
  assert.equal(files['robots.txt'], 'User-agent: *\nAllow: /\n');

  const dupe: Plugin = { name: 'layout', render: () => null };
  assert.throws(() => buildSite(baseConfig, baseSources, [dupe]), /duplicate plugin name: layout/);
});

test('two sources mapping to one output path is a hard error', () => {
  const sources = {
    'about.md': '---\ntitle: A\n---\nx',
    'about/index.md': '---\ntitle: B\n---\ny',
  };
  assert.throws(() => buildSite(baseConfig, sources), /output collision at about\/index.html/);
});

// ================================================================ draft pages feature
// New acceptance tests: they fail until the drafts feature exists.

const draftSources: Record<string, string> = {
  'index.md': ['---', 'title: Home', '---', '', 'Welcome.'].join('\n'),
  'about.md': ['---', 'title: About', '---', '', 'Who we are.'].join('\n'),
  'roadmap.md': ['---', 'title: Roadmap', 'draft: true', '---', '', 'Next quarter plans.'].join('\n'),
  'posts/launch.md': ['---', 'title: Launch', 'date: 2024-03-01', '---', '', 'We shipped.'].join('\n'),
  'posts/teaser.md': ['---', 'title: Teaser', 'date: 2024-04-01', 'draft: true', '---', '', 'Coming soon.'].join(
    '\n',
  ),
};

const BANNER = '<p class="draft-banner">Draft — not published</p>';

test('drafts: config accepts includeDrafts as a strict boolean defaulting to false', () => {
  const cfg = loadConfig({ ...baseConfig, includeDrafts: true }) as { includeDrafts?: boolean };
  assert.equal(cfg.includeDrafts, true);
  const off = loadConfig(baseConfig) as { includeDrafts?: boolean };
  assert.equal(off.includeDrafts, false);
  assert.throws(() => loadConfig({ ...baseConfig, includeDrafts: 'yes' }), /includeDrafts must be a boolean/);
});

test('drafts: default build drops draft pages from output, nav, sitemap and feed', () => {
  const { files } = buildSite(baseConfig, draftSources);
  assert.deepEqual(Object.keys(files), [
    'about/index.html',
    'feed.xml',
    'index.html',
    'posts/launch/index.html',
    'sitemap.xml',
  ]);
  assert.ok(!files['index.html'].includes('Roadmap'), 'nav must not link a draft in a default build');
  assert.ok(!files['sitemap.xml'].includes('roadmap'));
  assert.ok(!files['sitemap.xml'].includes('teaser'));
  assert.ok(!files['feed.xml'].includes('Teaser'));
  assert.ok(files['feed.xml'].includes('<title>Launch</title>'));
});

test('drafts: includeDrafts builds drafts at their normal paths with the banner', () => {
  const { files } = buildSite({ ...baseConfig, includeDrafts: true }, draftSources);
  assert.deepEqual(Object.keys(files), [
    'about/index.html',
    'feed.xml',
    'index.html',
    'posts/launch/index.html',
    'posts/teaser/index.html',
    'roadmap/index.html',
    'sitemap.xml',
  ]);

  const roadmap = files['roadmap/index.html'];
  assert.ok(roadmap.includes('<title>Roadmap — Ops Notes</title>'));
  assert.ok(
    roadmap.includes(`<main>\n${BANNER}`),
    'the banner must be the first thing inside <main>; got:\n' + roadmap,
  );
  assert.ok(roadmap.includes('<p>Next quarter plans.</p>'), 'draft body must render normally');
  assert.ok(files['posts/teaser/index.html'].includes(BANNER));

  assert.ok(files['index.html'].includes('>Roadmap</a>'), 'included drafts appear in the nav');
  assert.ok(!files['about/index.html'].includes(BANNER), 'non-draft pages never get the banner');
  assert.ok(!files['index.html'].includes(BANNER));
});

test('drafts: sitemap and feed exclude drafts even when drafts are included', () => {
  const preview = buildSite({ ...baseConfig, includeDrafts: true }, draftSources);
  const published = buildSite(baseConfig, draftSources);
  assert.equal(
    preview.files['sitemap.xml'],
    published.files['sitemap.xml'],
    'sitemap must be byte-identical with and without drafts included',
  );
  assert.equal(
    preview.files['feed.xml'],
    published.files['feed.xml'],
    'feed must be byte-identical with and without drafts included',
  );
  assert.ok(!preview.files['sitemap.xml'].includes('roadmap'));
  assert.ok(!preview.files['feed.xml'].includes('Teaser'));
});

test('drafts: draft: false and undated draft edge cases behave like normal pages', () => {
  const sources = {
    ...draftSources,
    'guide.md': ['---', 'title: Guide', 'draft: false', '---', '', 'Published on purpose.'].join('\n'),
  };
  const off = buildSite(baseConfig, sources);
  assert.ok(off.files['guide/index.html'] !== undefined, 'draft: false is not a draft');
  assert.ok(off.files['sitemap.xml'].includes('https://ops.example.com/guide/'));

  const on = buildSite({ ...baseConfig, includeDrafts: true }, sources);
  assert.ok(!on.files['guide/index.html'].includes(BANNER));
});
