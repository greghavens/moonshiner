// Minimal Cisco Meraki Dashboard API v1 transport used by our network
// tooling. Bearer auth per the v1 docs; errors arrive as {"errors": [...]}.

export interface MerakiClientOptions {
  /** Versioned base URL, e.g. https://api.meraki.com/api/v1 */
  baseUrl: string;
  apiKey: string;
  fetchImpl?: typeof fetch;
}

export class MerakiApiError extends Error {
  readonly status: number;
  readonly errors: string[];

  constructor(status: number, errors: string[]) {
    super(`Meraki API error ${status}: ${errors.join("; ")}`);
    this.name = "MerakiApiError";
    this.status = status;
    this.errors = errors;
  }
}

export class MerakiClient {
  readonly baseUrl: string;
  private readonly apiKey: string;
  private readonly fetchImpl: typeof fetch;

  constructor(opts: MerakiClientOptions) {
    this.baseUrl = opts.baseUrl.replace(/\/+$/, "");
    this.apiKey = opts.apiKey;
    this.fetchImpl = opts.fetchImpl ?? fetch;
  }

  async get(path: string): Promise<unknown> {
    return this.request("GET", path);
  }

  async put(path: string, body: unknown): Promise<unknown> {
    return this.request("PUT", path, body);
  }

  private async request(
    method: string,
    path: string,
    body?: unknown,
  ): Promise<unknown> {
    const headers: Record<string, string> = {
      Authorization: `Bearer ${this.apiKey}`,
      Accept: "application/json",
    };
    let payload: string | undefined;
    if (body !== undefined) {
      headers["Content-Type"] = "application/json";
      payload = JSON.stringify(body);
    }
    const res = await this.fetchImpl(`${this.baseUrl}${path}`, {
      method,
      headers,
      body: payload,
    });
    const text = await res.text();
    if (!res.ok) {
      let errors: string[] = [];
      try {
        const parsed = JSON.parse(text) as { errors?: unknown };
        if (parsed && Array.isArray(parsed.errors)) {
          errors = parsed.errors.map(String);
        }
      } catch {
        // non-JSON error body; fall through to the generic message
      }
      if (errors.length === 0) {
        errors = [`HTTP ${res.status}`];
      }
      throw new MerakiApiError(res.status, errors);
    }
    return text.length > 0 ? JSON.parse(text) : null;
  }
}
