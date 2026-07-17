/** Content scan: split the source map into pages (.md) and passthrough
 * assets (everything else), and lift front matter into typed Page fields. */
import { parseFrontmatter } from './frontmatter.ts';
import { renderMarkdown } from './markdown.ts';
import { outputPath, pageUrl, slugify } from './paths.ts';
import type { Page, SiteConfig } from './types.ts';

export interface ScanResult {
  pages: Page[];
  assets: Record<string, string>;
}

const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;

function pageFrom(sourcePath: string, source: string, config: SiteConfig): Page {
  const { meta, body } = parseFrontmatter(source);

  let title: string;
  if (typeof meta.title === 'string' && meta.title.trim() !== '') {
    title = meta.title.trim();
  } else {
    const stem = sourcePath.replace(/\.md$/, '').split('/').pop() ?? sourcePath;
    title = slugify(stem).replace(/-/g, ' ');
  }

  let date: string | null = null;
  if (meta.date !== undefined) {
    if (typeof meta.date !== 'string' || !DATE_RE.test(meta.date)) {
      throw new Error(`${sourcePath}: date must be YYYY-MM-DD`);
    }
    date = meta.date;
  }

  const tags = Array.isArray(meta.tags) ? meta.tags : [];
  const outPath = outputPath(sourcePath, config);

  return {
    sourcePath,
    outPath,
    url: pageUrl(outPath, config),
    title,
    date,
    tags,
    meta,
    html: renderMarkdown(body),
    excerpt: '',
  };
}

export function scanContent(sources: Record<string, string>, config: SiteConfig): ScanResult {
  const pages: Page[] = [];
  const assets: Record<string, string> = {};
  for (const sourcePath of Object.keys(sources).sort()) {
    if (sourcePath.endsWith('.md')) {
      pages.push(pageFrom(sourcePath, sources[sourcePath], config));
    } else {
      assets[sourcePath] = sources[sourcePath];
    }
  }
  return { pages, assets };
}
