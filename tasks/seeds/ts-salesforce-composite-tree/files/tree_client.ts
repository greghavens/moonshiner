// Salesforce sObject Tree writer.
//
// Wraps POST /services/data/v67.0/composite/tree/{sObjectName} per the
// contract pinned in docs/contract.json: record hierarchies with
// attributes {type, referenceId}, children nested under relationship
// collection names, 200-record / 5-level limits, and all-or-nothing
// rollback decoded from hasErrors.

export interface TreeRecord {
  type: string;
  referenceId: string;
  fields: Record<string, unknown>;
  children?: Record<string, TreeRecord[]>;
}

export interface TreeError {
  referenceId: string;
  statusCode: string;
  message: string;
  fields: string[];
}

export class SalesforceApiError extends Error {
  readonly status: number;
  readonly errors: { errorCode: string; message: string }[];

  constructor(status: number, errors: { errorCode: string; message: string }[]) {
    const first = errors[0];
    super(
      `Salesforce request failed with ${status}`
      + (first ? ` (${first.errorCode}): ${first.message}` : ''),
    );
    this.name = 'SalesforceApiError';
    this.status = status;
    this.errors = errors;
  }
}

export class TreeLimitError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'TreeLimitError';
  }
}

export class TreeWriteError extends Error {
  readonly errors: TreeError[];

  constructor(errors: TreeError[]) {
    super(
      `sObject tree rolled back: ${errors.length} record(s) failed`
      + (errors[0] ? ` (${errors[0].referenceId}: ${errors[0].statusCode})` : ''),
    );
    this.name = 'TreeWriteError';
    this.errors = errors;
  }
}

export class SalesforceOrg {
  readonly baseUrl: string;
  readonly apiVersion: string;
  private readonly accessToken: string;

  constructor(baseUrl: string, apiVersion: string, accessToken: string) {
    this.baseUrl = baseUrl.replace(/\/+$/, '');
    this.apiVersion = apiVersion;
    this.accessToken = accessToken;
  }

  restPath(suffix: string): string {
    return `/services/data/${this.apiVersion}/${suffix.replace(/^\/+/, '')}`;
  }

  async request(method: string, path: string, body?: unknown): Promise<{
    status: number;
    body: unknown;
  }> {
    const res = await fetch(this.baseUrl + path, {
      method,
      headers: {
        Authorization: `Bearer ${this.accessToken}`,
        Accept: 'application/json',
        ...(body === undefined ? {} : { 'Content-Type': 'application/json' }),
      },
      body: body === undefined ? undefined : JSON.stringify(body),
      redirect: 'manual',
    });
    const text = await res.text();
    const parsed: unknown = text.length > 0 ? JSON.parse(text) : null;
    return { status: res.status, body: parsed };
  }
}

function countRecords(records: TreeRecord[]): number {
  let total = 0;
  for (const record of records) {
    total += 1;
    for (const group of Object.values(record.children ?? {})) {
      total += countRecords(group);
    }
  }
  return total;
}

function depthOf(records: TreeRecord[]): number {
  let deepest = 0;
  for (const record of records) {
    let below = 0;
    for (const group of Object.values(record.children ?? {})) {
      below = Math.max(below, depthOf(group));
    }
    deepest = Math.max(deepest, 1 + below);
  }
  return deepest;
}

function toWire(record: TreeRecord): Record<string, unknown> {
  const wire: Record<string, unknown> = {
    attributes: { type: record.type, referenceId: record.referenceId },
    ...record.fields,
  };
  for (const [relation, group] of Object.entries(record.children ?? {})) {
    wire[relation] = { records: group.map(toWire) };
  }
  return wire;
}

interface TreeResponse {
  hasErrors: boolean;
  results: {
    referenceId: string;
    id?: string;
    errors?: { statusCode: string; message: string; fields?: string[] }[];
  }[];
}

/**
 * Create one or more record hierarchies in a single sObject Tree call.
 * Returns referenceId -> created record id. Rolls back as a whole on any
 * record failure (TreeWriteError).
 */
export async function insertTree(
  org: SalesforceOrg,
  sObjectName: string,
  roots: TreeRecord[],
): Promise<Record<string, string>> {
  const total = countRecords(roots);
  if (total > 200) {
    throw new TreeLimitError(
      `sObject tree requests allow up to 200 records total, got ${total}`,
    );
  }
  const depth = depthOf(roots);
  if (depth > 5) {
    throw new TreeLimitError(
      `sObject trees may be up to five levels deep, got ${depth}`,
    );
  }

  const path = org.restPath(`composite/tree/${sObjectName}`);
  const { status, body } = await org.request('POST', path, {
    records: roots.map(toWire),
  });

  if (Array.isArray(body)) {
    // top-level REST error envelope (401, 404, ...)
    throw new SalesforceApiError(
      status,
      body as { errorCode: string; message: string }[],
    );
  }

  const tree = body as TreeResponse;
  if (tree.hasErrors) {
    const failures: TreeError[] = [];
    for (const result of tree.results) {
      for (const err of result.errors ?? []) {
        failures.push({
          referenceId: result.referenceId,
          statusCode: err.statusCode,
          message: err.message,
          fields: err.fields ?? [],
        });
      }
    }
    throw new TreeWriteError(failures);
  }

  const ids: Record<string, string> = {};
  for (const result of tree.results) {
    if (result.id !== undefined) {
      ids[result.referenceId] = result.id;
    }
  }
  return ids;
}
