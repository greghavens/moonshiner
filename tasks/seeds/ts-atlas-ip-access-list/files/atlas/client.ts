// Thin MongoDB Atlas Administration API v2 client used by our network
// tooling. Speaks the versioned dated media type and decodes the documented
// application/json ApiError envelope.
//
// Contract references (see docs/official_sources.json):
//   https://www.mongodb.com/docs/atlas/api/versioned-api-overview/
//   https://www.mongodb.com/docs/atlas/api/atlas-admin-api-ref/

export const ATLAS_MEDIA_TYPE = "application/vnd.atlas.2023-01-01+json";

export interface AtlasClientOptions {
  baseUrl: string;
  token: string;
  fetchImpl?: typeof fetch;
}

export interface RequestOptions {
  query?: Record<string, string>;
  body?: unknown;
}

export class AtlasApiError extends Error {
  readonly status: number;
  readonly errorCode: string;
  readonly detail: string;
  readonly reason: string;
  readonly parameters: unknown[];
  /** Seconds from a Retry-After header, when the response carried one. */
  readonly retryAfterSeconds: number | null;

  constructor(args: {
    status: number;
    errorCode: string;
    detail: string;
    reason: string;
    parameters: unknown[];
    retryAfterSeconds: number | null;
  }) {
    super(`atlas: ${args.status} ${args.reason} (${args.errorCode}): ${args.detail}`);
    this.name = "AtlasApiError";
    this.status = args.status;
    this.errorCode = args.errorCode;
    this.detail = args.detail;
    this.reason = args.reason;
    this.parameters = args.parameters;
    this.retryAfterSeconds = args.retryAfterSeconds;
  }
}

export class AtlasClient {
  private readonly baseUrl: string;
  private readonly token: string;
  private readonly fetchImpl: typeof fetch;

  constructor(opts: AtlasClientOptions) {
    if (!opts.baseUrl) throw new Error("baseUrl is required");
    if (!opts.token) throw new Error("token is required");
    this.baseUrl = opts.baseUrl.replace(/\/+$/, "");
    this.token = opts.token;
    this.fetchImpl = opts.fetchImpl ?? fetch;
  }

  /**
   * Perform one API call. `path` is used verbatim (callers are responsible
   * for percent-encoding path segments); `query` values are encoded.
   */
  async request<T>(method: string, path: string, opts: RequestOptions = {}): Promise<T> {
    let url = this.baseUrl + path;
    if (opts.query && Object.keys(opts.query).length > 0) {
      const qs = new URLSearchParams(opts.query).toString();
      url += (path.includes("?") ? "&" : "?") + qs;
    }
    const headers: Record<string, string> = {
      authorization: `Bearer ${this.token}`,
      accept: ATLAS_MEDIA_TYPE,
    };
    let body: string | undefined;
    if (opts.body !== undefined) {
      body = JSON.stringify(opts.body);
      headers["content-type"] = ATLAS_MEDIA_TYPE;
    }
    const res = await this.fetchImpl(url, { method, headers, body });
    const text = await res.text();
    if (!res.ok) {
      throw this.decodeError(res, text);
    }
    return (text ? JSON.parse(text) : undefined) as T;
  }

  private decodeError(res: Response, text: string): AtlasApiError {
    let doc: Record<string, unknown> = {};
    try {
      doc = JSON.parse(text) as Record<string, unknown>;
    } catch {
      // non-JSON error body; fall through to the envelope defaults
    }
    const retryAfter = res.headers.get("retry-after");
    return new AtlasApiError({
      status: res.status,
      errorCode: typeof doc.errorCode === "string" ? doc.errorCode : "",
      detail: typeof doc.detail === "string" ? doc.detail : "",
      reason: typeof doc.reason === "string" ? doc.reason : "",
      parameters: Array.isArray(doc.parameters) ? doc.parameters : [],
      retryAfterSeconds: retryAfter !== null && /^\d+$/.test(retryAfter) ? Number(retryAfter) : null,
    });
  }
}
