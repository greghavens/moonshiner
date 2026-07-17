/** Excerpt transform: an explicit `excerpt:` front-matter key wins;
 * otherwise the first paragraph of the rendered body, stripped of tags,
 * clipped to 160 chars. The feed uses these as item descriptions. */
import type { Page, Plugin } from '../types.ts';

const LIMIT = 160;

function firstParagraphText(html: string): string {
  const match = /<p>([\s\S]*?)<\/p>/.exec(html);
  if (!match) return '';
  return match[1].replace(/<[^>]+>/g, '').trim();
}

export const excerptPlugin: Plugin = {
  name: 'excerpt',
  transform(page: Page): Page {
    let text: string;
    if (typeof page.meta.excerpt === 'string' && page.meta.excerpt.trim() !== '') {
      text = page.meta.excerpt.trim();
    } else {
      text = firstParagraphText(page.html);
    }
    if (text.length > LIMIT) {
      text = `${text.slice(0, LIMIT - 1).trimEnd()}…`;
    }
    return { ...page, excerpt: text };
  },
};
