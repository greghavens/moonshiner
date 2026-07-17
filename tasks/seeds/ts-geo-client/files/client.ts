export type RequestOptions = {
  timeoutMs: number;
  retries: number;
  cacheTtlS: number;
  headers: Record<string, string>;
};

export type RequestPlan = {
  url: string;
  timeoutMs: number;
  retries: number;
  cacheTtlS: number;
  headers: Record<string, string>;
};

const DEFAULT_OPTIONS: RequestOptions = {
  timeoutMs: 5_000,
  retries: 2,
  cacheTtlS: 300,
  headers: { accept: 'application/json' },
};

/**
 * Builds fully-resolved request plans for the geocoding service. The
 * transport layer executes a plan verbatim, so a plan must reflect exactly
 * the defaults, the client's own overrides, and the single call's options —
 * in that order of precedence — and nothing else.
 */
export class GeoClient {
  private baseUrl: string;
  private options: RequestOptions;

  constructor(baseUrl: string, overrides: Partial<RequestOptions> = {}) {
    this.baseUrl = baseUrl.replace(/\/+$/, '');
    this.options = Object.assign(DEFAULT_OPTIONS, overrides);
  }

  /** Persistent header for this client, e.g. an API key. */
  setHeader(name: string, value: string): void {
    this.options.headers[name] = value;
  }

  /** Resolve one lookup into a plan; callOpts apply to this call only. */
  plan(path: string, callOpts: Partial<RequestOptions> = {}): RequestPlan {
    const merged = Object.assign(this.options, callOpts);
    const headers = Object.assign(merged.headers, callOpts.headers ?? {});
    return {
      url: `${this.baseUrl}/${path.replace(/^\/+/, '')}`,
      timeoutMs: merged.timeoutMs,
      retries: merged.retries,
      cacheTtlS: merged.cacheTtlS,
      headers,
    };
  }

  currentOptions(): RequestOptions {
    return this.options;
  }
}
