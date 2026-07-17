// Minimal Okta Group Rules client used by the IT automation scripts.
// Speaks the Core Okta management API: SSWS auth, JSON both ways, and the
// standard Okta error envelope on non-2xx responses.

export interface GroupRuleExpression {
  type?: string;
  value: string;
}

export interface GroupRuleConditions {
  expression: GroupRuleExpression;
  people?: {
    users?: { exclude?: string[] };
    groups?: { exclude?: string[] };
  };
}

export interface GroupRuleActions {
  assignUserToGroups: { groupIds: string[] };
}

export interface GroupRule {
  id?: string;
  type: string;
  name: string;
  status?: "ACTIVE" | "INACTIVE" | "INVALID";
  conditions: GroupRuleConditions;
  actions: GroupRuleActions;
  created?: string;
  lastUpdated?: string;
}

export class OktaHttpError extends Error {
  readonly status: number;
  readonly errorCode: string;
  readonly errorSummary: string;
  readonly errorId: string;

  constructor(status: number, errorCode: string, errorSummary: string, errorId: string) {
    super(`Okta API error ${errorCode} (HTTP ${status}): ${errorSummary}`);
    this.status = status;
    this.errorCode = errorCode;
    this.errorSummary = errorSummary;
    this.errorId = errorId;
  }
}

export interface OktaResponse {
  status: number;
  json: any;
  headers: Headers;
}

export class GroupRulesClient {
  private readonly baseUrl: string;
  private readonly apiToken: string;

  constructor(baseUrl: string, apiToken: string) {
    this.baseUrl = baseUrl.replace(/\/$/, "");
    this.apiToken = apiToken;
  }

  async request(method: string, pathOrUrl: string, body?: unknown): Promise<OktaResponse> {
    const url = pathOrUrl.startsWith("http") ? pathOrUrl : this.baseUrl + pathOrUrl;
    const headers: Record<string, string> = {
      Authorization: `SSWS ${this.apiToken}`,
      Accept: "application/json",
    };
    if (body !== undefined) headers["Content-Type"] = "application/json";
    const res = await fetch(url, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    const text = await res.text();
    const json = text.length > 0 ? JSON.parse(text) : null;
    if (!res.ok) {
      throw new OktaHttpError(
        res.status,
        json?.errorCode ?? "unknown",
        json?.errorSummary ?? res.statusText,
        json?.errorId ?? "",
      );
    }
    return { status: res.status, json, headers: res.headers };
  }

  async getRule(ruleId: string): Promise<GroupRule> {
    const { json } = await this.request("GET", `/api/v1/groups/rules/${encodeURIComponent(ruleId)}`);
    return json as GroupRule;
  }

  async listRulesPage(limit = 50, after?: string): Promise<{ rules: GroupRule[]; linkHeader: string | null }> {
    let path = `/api/v1/groups/rules?limit=${limit}`;
    if (after !== undefined) path += `&after=${encodeURIComponent(after)}`;
    const { json, headers } = await this.request("GET", path);
    return { rules: json as GroupRule[], linkHeader: headers.get("link") };
  }
}
