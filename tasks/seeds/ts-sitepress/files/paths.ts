/** Slugs, output paths and canonical urls. */
import type { SiteConfig } from './types.ts';

export function slugify(text: string): string {
  return text
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '');
}

/** "posts/First Post.md" -> pretty "posts/first-post/index.html",
 *  plain "posts/first-post.html". "index.md" is always "index.html". */
export function outputPath(sourcePath: string, config: SiteConfig): string {
  const noExt = sourcePath.replace(/\.md$/, '');
  const segments = noExt.split('/').map(slugify);
  const last = segments[segments.length - 1];
  if (last === 'index') {
    return [...segments.slice(0, -1), 'index.html'].join('/');
  }
  if (config.prettyUrls) {
    return [...segments, 'index.html'].join('/');
  }
  return `${segments.join('/')}.html`;
}

/** Canonical url for an output path: directory indexes end in "/". */
export function pageUrl(outPath: string, config: SiteConfig): string {
  if (outPath === 'index.html') return `${config.baseUrl}/`;
  if (outPath.endsWith('/index.html')) {
    return `${config.baseUrl}/${outPath.slice(0, -'index.html'.length)}`;
  }
  return `${config.baseUrl}/${outPath}`;
}
