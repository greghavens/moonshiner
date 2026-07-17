// Request-body parsing for the intake gateway: one entry point that turns a
// raw body plus its Content-Type header into structured data. Handlers call
// parseBody() and switch on the returned type; anything unparseable throws a
// BodyParseError with a stable machine-readable code.

export type ParsedBody = { type: 'json'; value: unknown };

export type ErrorCode =
  | 'unsupported-type'
  | 'too-large'
  | 'bad-json'
  | 'bad-charset'
  | 'bad-encoding';

export class BodyParseError extends Error {
  code: ErrorCode;
  constructor(code: ErrorCode, message: string) {
    super(message);
    this.name = 'BodyParseError';
    this.code = code;
  }
}

export interface ParseOptions {
  /** Overall cap on the raw body size, in bytes. */
  maxBytes?: number;
}

const DEFAULT_MAX_BYTES = 1024 * 1024;

interface MediaType {
  type: string;
  params: Record<string, string>;
}

function parseMediaType(header: string): MediaType {
  const [first, ...rest] = header.split(';');
  const params: Record<string, string> = {};
  for (const part of rest) {
    const eq = part.indexOf('=');
    if (eq === -1) continue;
    const name = part.slice(0, eq).trim().toLowerCase();
    let value = part.slice(eq + 1).trim();
    if (value.startsWith('"') && value.endsWith('"') && value.length >= 2) {
      value = value.slice(1, -1);
    }
    if (name) params[name] = value;
  }
  return { type: first.trim().toLowerCase(), params };
}

function decodeUtf8(raw: Uint8Array, what: string): string {
  try {
    return new TextDecoder('utf-8', { fatal: true }).decode(raw);
  } catch {
    throw new BodyParseError('bad-encoding', `${what} is not valid UTF-8`);
  }
}

export function parseBody(
  raw: Uint8Array,
  contentType: string | undefined,
  opts: ParseOptions = {},
): ParsedBody {
  if (!contentType || !contentType.trim()) {
    throw new BodyParseError('unsupported-type', 'missing content type');
  }
  const media = parseMediaType(contentType);

  if (media.type === 'application/json') {
    const cap = opts.maxBytes ?? DEFAULT_MAX_BYTES;
    if (raw.length > cap) {
      throw new BodyParseError('too-large', `json body of ${raw.length} bytes exceeds cap of ${cap}`);
    }
    const charset = (media.params.charset ?? 'utf-8').toLowerCase();
    if (charset !== 'utf-8' && charset !== 'utf8') {
      throw new BodyParseError('bad-charset', `unsupported charset ${charset} for json`);
    }
    const text = decodeUtf8(raw, 'json body');
    try {
      return { type: 'json', value: JSON.parse(text) };
    } catch {
      throw new BodyParseError('bad-json', 'body is not valid JSON');
    }
  }

  throw new BodyParseError('unsupported-type', `no parser for ${media.type}`);
}
