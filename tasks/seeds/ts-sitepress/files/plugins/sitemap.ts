/** sitemap.xml emitter. Search engines read this, so entries are sorted
 * by url for stable diffs between deploys. */
import { escapeXml } from '../html.ts';
import type { BuildContext, Plugin } from '../types.ts';

export const sitemapPlugin: Plugin = {
  name: 'sitemap',
  emit(ctx: BuildContext): Record<string, string> {
    const entries = [...ctx.pages]
      .sort((a, b) => (a.url < b.url ? -1 : a.url > b.url ? 1 : 0))
      .map((page) => {
        const lastmod = page.date === null ? '' : `<lastmod>${page.date}</lastmod>`;
        return `  <url><loc>${escapeXml(page.url)}</loc>${lastmod}</url>`;
      });
    const xml = [
      '<?xml version="1.0" encoding="UTF-8"?>',
      '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
      ...entries,
      '</urlset>',
      '',
    ].join('\n');
    return { 'sitemap.xml': xml };
  },
};
