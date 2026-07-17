/** feed.xml emitter: the newest dated pages, one item per page. Undated
 * pages (about, index, ...) are structural and never syndicated. */
import { escapeXml } from '../html.ts';
import type { BuildContext, Page, Plugin } from '../types.ts';

function newestFirst(a: Page, b: Page): number {
  if (a.date !== b.date) return a.date! < b.date! ? 1 : -1;
  return a.sourcePath < b.sourcePath ? -1 : 1;
}

export const feedPlugin: Plugin = {
  name: 'feed',
  emit(ctx: BuildContext): Record<string, string> {
    const dated = ctx.pages.filter((p) => p.date !== null);
    const items = dated
      .sort(newestFirst)
      .slice(0, ctx.config.feedLimit)
      .map((page) =>
        [
          '  <item>',
          `    <title>${escapeXml(page.title)}</title>`,
          `    <link>${escapeXml(page.url)}</link>`,
          `    <pubDate>${page.date}</pubDate>`,
          `    <description>${escapeXml(page.excerpt)}</description>`,
          '  </item>',
        ].join('\n'),
      );
    const xml = [
      '<?xml version="1.0" encoding="UTF-8"?>',
      '<rss version="2.0">',
      '<channel>',
      `  <title>${escapeXml(ctx.config.title)}</title>`,
      `  <link>${escapeXml(ctx.config.baseUrl)}/</link>`,
      ...items,
      '</channel>',
      '</rss>',
      '',
    ].join('\n');
    return { 'feed.xml': xml };
  },
};
