// apierrs.ts — provider-error taxonomy for the Corely import API, plus the
// importer that leans on it.
//
// Transient failures are worth retrying: the condition clears on its own.
// Permanent failures mean the request itself is the problem, so resending
// it can only waste quota.

export type ErrorKind = 'transient' | 'permanent';

export type Classified = {
  kind: ErrorKind;
  status: number;
  code: string; // provider error code from the response body, '' when absent
};

// Status → taxonomy for every code the provider documents.
const KIND_BY_STATUS: Record<number, ErrorKind> = {
  400: 'permanent', // malformed request
  401: 'permanent', // bad or expired credentials
  403: 'permanent', // plan does not cover this endpoint
  404: 'permanent', // unknown collection
  408: 'transient', // provider-side request timeout
  409: 'permanent', // duplicate external id
  422: 'transient', // record failed field validation
  429: 'permanent', // over the request quota
  500: 'transient', // provider fault
  502: 'transient', // bad gateway
  503: 'transient', // maintenance or overload
  504: 'transient', // gateway timeout
};

export function classify(status: number, body: unknown): Classified {
  const known = KIND_BY_STATUS[status];
  const kind = known ?? (status >= 500 ? 'transient' : 'permanent');
  return { kind, status, code: errorCode(body) };
}

function errorCode(body: unknown): string {
  if (body !== null && typeof body === 'object') {
    const err = (body as { error?: unknown }).error;
    if (err !== null && typeof err === 'object') {
      const code = (err as { code?: unknown }).code;
      if (typeof code === 'string') return code;
    }
  }
  return '';
}

// Retry-After arrives in both flavors the provider sends: delta-seconds
// ("2") and an HTTP-date. Milliseconds to wait, or null when unusable.
export function retryAfterMs(value: string | null, nowMs: number): number | null {
  if (value === null) return null;
  const trimmed = value.trim();
  if (/^\d+$/.test(trimmed)) return Number(trimmed) * 1000;
  const at = Date.parse(trimmed);
  if (Number.isNaN(at)) return null;
  return Math.max(0, at - nowMs);
}

export type ImporterOptions = {
  now?: () => number;
  sleep?: (ms: number) => Promise<void>;
  maxRetries?: number; // additional tries after the first, transient failures only
  defaultRetryMs?: number; // pause when a transient response names no Retry-After
};

export type ImportOutcome =
  | { ok: true; id: string; attempts: number }
  | { ok: false; kind: ErrorKind; status: number; code: string; attempts: number };

export class Importer {
  baseUrl: string;
  now: () => number;
  sleep: (ms: number) => Promise<void>;
  maxRetries: number;
  defaultRetryMs: number;

  constructor(baseUrl: string, options: ImporterOptions = {}) {
    this.baseUrl = baseUrl.replace(/\/$/, '');
    this.now = options.now ?? Date.now;
    this.sleep = options.sleep ?? ((ms) => new Promise((resolve) => setTimeout(resolve, ms)));
    this.maxRetries = options.maxRetries ?? 3;
    this.defaultRetryMs = options.defaultRetryMs ?? 500;
  }

  async importRecord(record: Record<string, unknown>): Promise<ImportOutcome> {
    let attempts = 0;
    for (;;) {
      attempts += 1;
      const res = await fetch(this.baseUrl + '/records', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify(record),
      });
      const body = await readJson(res);
      if (res.ok) {
        return { ok: true, id: stringField(body, 'id'), attempts };
      }
      const failure = classify(res.status, body);
      if (failure.kind === 'permanent' || attempts > this.maxRetries) {
        return { ok: false, ...failure, attempts };
      }
      const wait = retryAfterMs(res.headers.get('retry-after'), this.now());
      await this.sleep(wait ?? this.defaultRetryMs);
    }
  }

  async importBatch(
    records: Record<string, unknown>[],
  ): Promise<{ imported: number; rejected: number }> {
    let imported = 0;
    let rejected = 0;
    for (const record of records) {
      const outcome = await this.importRecord(record);
      if (outcome.ok) imported += 1;
      else rejected += 1;
    }
    return { imported, rejected };
  }
}

async function readJson(res: Response): Promise<unknown> {
  const text = await res.text();
  if (text === '') return null;
  try {
    return JSON.parse(text);
  } catch {
    return null;
  }
}

function stringField(body: unknown, field: string): string {
  if (body !== null && typeof body === 'object') {
    const value = (body as Record<string, unknown>)[field];
    if (typeof value === 'string') return value;
  }
  return '';
}
