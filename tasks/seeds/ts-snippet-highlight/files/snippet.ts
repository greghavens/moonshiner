// Search-result snippet builder. Extracts a window of text around the
// first case-insensitive occurrence of the query term and wraps the match
// in <mark> tags, preserving the document's original casing. Cut edges are
// marked with an ellipsis.

export interface SnippetOptions {
  /** Characters of context on each side of the match. */
  radius?: number;
  ellipsis?: string;
}

export function buildSnippet(
  text: string,
  term: string,
  options: SnippetOptions = {},
): string {
  const radius = options.radius ?? 30;
  const ellipsis = options.ellipsis ?? '…';

  const index =
    term === '' ? -1 : text.toLowerCase().indexOf(term.toLowerCase());

  if (index === -1) {
    const head = text.slice(0, 2 * radius);
    return head.length < text.length ? head + ellipsis : head;
  }

  const start = Math.max(0, index - radius);
  const end = Math.min(text.length, index + term.length + radius);
  const prefix = start > 0 ? ellipsis : '';
  const suffix = end < text.length ? ellipsis : '';

  return (
    prefix +
    text.slice(start, index) +
    '<mark>' +
    text.slice(index, index + term.length) +
    '</mark>' +
    text.slice(index + term.length, end) +
    suffix
  );
}
