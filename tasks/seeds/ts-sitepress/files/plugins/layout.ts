/** The default renderer: wraps page html in the site chrome. Nav lists
 * top-level pages (no slash in the source path) sorted by title. */
import { escapeAttr, escapeHtml } from '../html.ts';
import type { BuildContext, Page, Plugin } from '../types.ts';

function navFor(page: Page, ctx: BuildContext): string {
  const topLevel = ctx.pages
    .filter((p) => !p.sourcePath.includes('/'))
    .sort((a, b) => (a.title < b.title ? -1 : a.title > b.title ? 1 : 0));
  const links = topLevel.map((p) => {
    const marker = p.outPath === page.outPath ? ' aria-current="page"' : '';
    return `<a href="${escapeAttr(p.url)}"${marker}>${escapeHtml(p.title)}</a>`;
  });
  return `<nav>${links.join(' ')}</nav>`;
}

export const layoutPlugin: Plugin = {
  name: 'layout',
  render(page: Page, ctx: BuildContext): string {
    const lines = [
      '<!doctype html>',
      '<html>',
      '<head>',
      `<title>${escapeHtml(page.title)} — ${escapeHtml(ctx.config.title)}</title>`,
      '</head>',
      '<body>',
      navFor(page, ctx),
      '<main>',
      page.html,
      '</main>',
      '</body>',
      '</html>',
      '',
    ];
    return lines.join('\n');
  },
};
