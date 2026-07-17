// Metrics push client for the fleet agents. Credentials come from a config
// source backed by the rotation sidecar: it rewrites the bearer token on its
// own schedule, and current() always reflects the latest material on disk.
// The client retries auth and availability errors, and callers may pass
// extra headers (trace ids and the like) that ride along on every attempt.

export interface ClientConfig {
  endpoint: string;
  token: string;
  orgId: string;
}

export interface ConfigSource {
  current(): ClientConfig;
}

export interface PushRequest {
  headers: Record<string, string>;
  body: string;
}

export interface PushResponse {
  status: number;
  body: string;
}

export type Transport = (url: string, req: PushRequest) => Promise<PushResponse>;

export interface PushResult {
  status: number;
  attempts: number;
  body: string;
}

const RETRYABLE = new Set([401, 503]);

export class PushClient {
  private transport: Transport;
  private maxAttempts: number;
  private endpoint: string;
  private baseHeaders: Record<string, string>;

  constructor(source: ConfigSource, transport: Transport, options: { maxAttempts?: number } = {}) {
    this.transport = transport;
    this.maxAttempts = options.maxAttempts ?? 3;
    const config = source.current();
    this.endpoint = config.endpoint;
    this.baseHeaders = {
      authorization: `Bearer ${config.token}`,
      'x-org-id': config.orgId,
      'content-type': 'application/json',
    };
  }

  async push(
    series: string,
    value: number,
    extraHeaders: Record<string, string> = {},
  ): Promise<PushResult> {
    const headers = Object.assign(extraHeaders, this.baseHeaders);
    const body = JSON.stringify({ series, value });
    let lastStatus = 0;
    for (let attempt = 1; attempt <= this.maxAttempts; attempt++) {
      const res = await this.transport(`${this.endpoint}/v1/ingest`, { headers, body });
      if (!RETRYABLE.has(res.status)) {
        return { status: res.status, attempts: attempt, body: res.body };
      }
      lastStatus = res.status;
    }
    throw new Error(`push gave up after ${this.maxAttempts} attempts: last status ${lastStatus}`);
  }
}
