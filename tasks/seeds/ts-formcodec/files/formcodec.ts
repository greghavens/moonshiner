// formcodec.ts — serializes the quote-request form model into an
// application/x-www-form-urlencoded string. Draft autosave has shipped on
// this for a quarter: collectPairs decides WHAT submits (document order,
// disabled/unchecked skipped), encodePairs handles the wire encoding.

export type Option = { value: string; selected: boolean };

export type Field =
  | { kind: 'text'; name: string; value: string; disabled?: boolean }
  | { kind: 'textarea'; name: string; value: string; disabled?: boolean }
  | { kind: 'checkbox'; name: string; value: string; checked: boolean; disabled?: boolean }
  | { kind: 'radio'; name: string; value: string; checked: boolean; disabled?: boolean }
  | {
      kind: 'select';
      name: string;
      multiple: boolean;
      options: Option[];
      disabled?: boolean;
    };

// Which fields contribute pairs, in document (array) order.
export function collectPairs(fields: Field[]): Array<[string, string]> {
  const pairs: Array<[string, string]> = [];
  for (const field of fields) {
    if (field.disabled || field.name === '') continue;
    switch (field.kind) {
      case 'text':
      case 'textarea':
        pairs.push([field.name, field.value]);
        break;
      case 'checkbox':
      case 'radio':
        if (field.checked) pairs.push([field.name, field.value]);
        break;
      case 'select': {
        const selected = field.options.filter((option) => option.selected);
        if (field.multiple) {
          for (const option of selected) pairs.push([field.name, option.value]);
        } else if (selected.length > 0) {
          pairs.push([field.name, selected[0].value]);
        }
        break;
      }
    }
  }
  return pairs;
}

// application/x-www-form-urlencoded alphabet: A-Za-z0-9 * - . _ stay literal,
// space becomes '+', every other byte of the utf-8 encoding becomes %XX
// (uppercase hex).
const SAFE_BYTE = /[A-Za-z0-9*\-._]/;

function encodeComponent(text: string): string {
  const bytes = new TextEncoder().encode(text);
  let out = '';
  for (const byte of bytes) {
    if (byte === 0x20) {
      out += '+';
    } else if (SAFE_BYTE.test(String.fromCharCode(byte))) {
      out += String.fromCharCode(byte);
    } else {
      out += '%' + byte.toString(16).toUpperCase().padStart(2, '0');
    }
  }
  return out;
}

// Encodes exactly what it is given — filtering happened in collectPairs.
export function encodePairs(pairs: Array<[string, string]>): string {
  return pairs
    .map(([name, value]) => `${encodeComponent(name)}=${encodeComponent(value)}`)
    .join('&');
}

export function serialize(fields: Field[]): string {
  return encodePairs(collectPairs(fields));
}
