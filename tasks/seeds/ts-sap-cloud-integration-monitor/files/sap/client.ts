// Minimal SAP Cloud Integration OData API (v2) client used by the ops
// tooling. Service root is <tenant url>/api/v1; the API speaks OData 2.0
// (d-envelope JSON) and is protected by basic authentication / OAuth.

export interface CpiResponse {
  status: number;
  json: any;
  headers: Record<string, string>;
  text: string;
}

/** OData 2.0 error document decoded from a non-2xx response. */
export class CpiError extends Error {
  readonly status: number;
  readonly code: string;
  /** The full error object exactly as the tenant returned it. */
  readonly errorBody: any;

  constructor(status: number, code: string, messageValue: string, errorBody: any) {
    super(`SAP Cloud Integration API error ${code} (HTTP ${status}): ${messageValue}`);
    this.status = status;
    this.code = code;
    this.errorBody = errorBody;
  }
}

export class CpiClient {
  readonly serviceRoot: string;
  private readonly authHeader: string;

  constructor(tenantUrl: string, user: string, password: string) {
    this.serviceRoot = tenantUrl.replace(/\/$/, "") + "/api/v1";
    this.authHeader = "Basic " + Buffer.from(`${user}:${password}`).toString("base64");
  }

  /** GET a resource path (relative to /api/v1) or an absolute URL. */
  async request(pathOrUrl: string): Promise<CpiResponse> {
    const url = /^https?:\/\//.test(pathOrUrl)
      ? pathOrUrl
      : this.serviceRoot + (pathOrUrl.startsWith("/") ? "" : "/") + pathOrUrl;
    const res = await fetch(url, {
      headers: { Authorization: this.authHeader, Accept: "application/json" },
    });
    const text = await res.text();
    let json: any = null;
    try {
      json = text === "" ? null : JSON.parse(text);
    } catch {
      json = null;
    }
    const headers: Record<string, string> = {};
    res.headers.forEach((v, k) => (headers[k.toLowerCase()] = v));
    if (res.status >= 400 && json && typeof json === "object" && json.error) {
      const code = json.error.code ?? "UNKNOWN";
      const value = json.error.message?.value ?? "";
      throw new CpiError(res.status, code, value, json.error);
    }
    return { status: res.status, json, headers, text };
  }

  /**
   * One page of MessageProcessingLogs. `query` is the pre-encoded query
   * string (without the leading "?"), or "" for no options.
   */
  async getLogsPage(query: string): Promise<{ logs: any[]; nextUrl: string | null; count: string | null }> {
    const path = "/MessageProcessingLogs" + (query ? "?" + query : "");
    const { json } = await this.request(path);
    const d = json?.d ?? {};
    return {
      logs: Array.isArray(d.results) ? d.results : [],
      nextUrl: typeof d.__next === "string" ? d.__next : null,
      count: typeof d.__count === "string" ? d.__count : null,
    };
  }
}
