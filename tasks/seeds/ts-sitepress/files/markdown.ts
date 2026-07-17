/** The markdown subset the handbook actually uses: #/##/### headings,
 * paragraphs, dash lists, and inline bold/em/code/links. Source order in,
 * html out, no state. */
import { escapeHtml } from './html.ts';

function inline(text: string): string {
  let out = escapeHtml(text);
  out = out.replace(/`([^`]+)`/g, (_m, code: string) => `<code>${code}</code>`);
  out = out.replace(/\*\*([^*]+)\*\*/g, (_m, bold: string) => `<strong>${bold}</strong>`);
  out = out.replace(/\*([^*]+)\*/g, (_m, em: string) => `<em>${em}</em>`);
  out = out.replace(
    /\[([^\]]+)\]\(([^)\s]+)\)/g,
    (_m, label: string, href: string) => `<a href="${href}">${label}</a>`,
  );
  return out;
}

export function renderMarkdown(source: string): string {
  const blocks = source
    .split(/\n\s*\n/)
    .map((b) => b.replace(/\s+$/, ''))
    .filter((b) => b.trim() !== '');

  const out: string[] = [];
  for (const block of blocks) {
    const heading = /^(#{1,3})\s+(.+)$/.exec(block.trim());
    if (heading) {
      const level = heading[1].length;
      out.push(`<h${level}>${inline(heading[2].trim())}</h${level}>`);
      continue;
    }
    const lines = block
      .split('\n')
      .map((l) => l.trim())
      .filter((l) => l !== '');
    if (lines.every((l) => l.startsWith('- '))) {
      const items = lines.map((l) => `<li>${inline(l.slice(2).trim())}</li>`);
      out.push(`<ul>\n${items.join('\n')}\n</ul>`);
      continue;
    }
    out.push(`<p>${inline(lines.join(' '))}</p>`);
  }
  return out.join('\n');
}
