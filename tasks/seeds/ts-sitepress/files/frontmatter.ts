/** Front-matter parser.
 *
 * A source may open with a `---` block of `key: value` lines. Values are
 * coerced: true/false -> boolean, numeric -> number, and the `tags` key
 * splits on commas into a trimmed list. Everything else stays a string.
 */
import type { MetaValue } from './types.ts';

export interface ParsedSource {
  meta: Record<string, MetaValue>;
  body: string;
}

function coerce(key: string, raw: string): MetaValue {
  const value = raw.trim();
  if (key === 'tags') {
    return value === '' ? [] : value.split(',').map((t) => t.trim()).filter((t) => t !== '');
  }
  if (value === 'true') return true;
  if (value === 'false') return false;
  if (/^-?\d+(\.\d+)?$/.test(value)) return Number(value);
  return value;
}

export function parseFrontmatter(source: string): ParsedSource {
  const lines = source.split('\n');
  if (lines[0]?.trim() !== '---') {
    return { meta: {}, body: source };
  }
  const meta: Record<string, MetaValue> = {};
  let end = -1;
  for (let i = 1; i < lines.length; i++) {
    const line = lines[i];
    if (line.trim() === '---') {
      end = i;
      break;
    }
    if (line.trim() === '') continue;
    const sep = line.indexOf(':');
    if (sep === -1) {
      throw new Error(`front matter: line ${i + 1} is not "key: value": ${line.trim()}`);
    }
    const key = line.slice(0, sep).trim();
    if (key === '') {
      throw new Error(`front matter: line ${i + 1} has an empty key`);
    }
    meta[key] = coerce(key, line.slice(sep + 1));
  }
  if (end === -1) {
    throw new Error('front matter: opening --- without a closing ---');
  }
  return { meta, body: lines.slice(end + 1).join('\n') };
}
