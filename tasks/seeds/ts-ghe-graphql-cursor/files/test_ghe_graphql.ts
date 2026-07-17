// Acceptance tests for the GitHub Enterprise Cloud GraphQL repository
// inventory. Everything runs against a local node:http mock that speaks the
// GraphQL wire contract pinned in docs/contract.json — no real GitHub, no
// real credentials, no sleeps. Protected — do not modify.
// Run: node --test test_ghe_graphql.ts
import { test } from "node:test";
import assert from "node:assert/strict";
import { createServer } from "node:http";

import { GraphQLClient, GraphQLHttpError, GraphQLQueryError } from "./ghql/client.ts";
import { collectRepositories } from "./ghql/inventory.ts";

const TOKEN = "ghp_dummy0Token4Inventory9781";
const ORG = "machine-shop";

interface Captured {
  method: string;
  path: string;
  headers: Record<string, string | string[] | undefined>;
  body: any;
}

interface Scripted {
  status?: number;
  body?: unknown;
  rawBody?: string;
  headers?: Record<string, string>;
}

async function startMock(
  t: any,
  script: Scripted[],
): Promise<{ base: string; requests: Captured[] }> {
  const requests: Captured[] = [];
  const server = createServer((req, res) => {
    const chunks: Buffer[] = [];
    req.on("data", (c) => chunks.push(c));
    req.on("end", () => {
      const raw = Buffer.concat(chunks).toString("utf8");
      requests.push({
        method: req.method ?? "",
        path: new URL(req.url ?? "/", "http://localhost").pathname,
        headers: req.headers,
        body: raw ? JSON.parse(raw) : null,
      });
      const s = script[Math.min(requests.length - 1, script.length - 1)];
      res.statusCode = s.status ?? 200;
      for (const [k, v] of Object.entries(s.headers ?? {})) res.setHeader(k, v);
      if (s.rawBody !== undefined) {
        res.setHeader("content-type", "text/plain");
        res.end(s.rawBody);
      } else {
        res.setHeader("content-type", "application/json; charset=utf-8");
        res.end(JSON.stringify(s.body ?? {}));
      }
    });
  });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  t.after(() => new Promise((resolve) => server.close(resolve)));
  const addr = server.address();
  if (addr === null || typeof addr === "string") throw new Error("no port");
  return { base: `http://127.0.0.1:${addr.port}`, requests };
}

const REPOS = {
  rover: {
    id: "R_kgDOLx1a01",
    name: "mars-rover-fw",
    nameWithOwner: "machine-shop/mars-rover-fw",
    isArchived: false,
    visibility: "PRIVATE",
    pushedAt: "2026-07-01T08:15:00Z",
  },
  runbooks: {
    id: "R_kgDOLx1a02",
    name: "ops-runbooks",
    nameWithOwner: "machine-shop/ops-runbooks",
    isArchived: false,
    visibility: "INTERNAL",
    pushedAt: "2026-06-11T17:40:00Z",
  },
  gate: {
    id: "R_kgDOLx1a03",
    name: "quality-gate",
    nameWithOwner: "machine-shop/quality-gate",
    isArchived: true,
    visibility: "PRIVATE",
    pushedAt: null,
  },
  paint: {
    id: "R_kgDOLx1a04",
    name: "paint-batch-api",
    nameWithOwner: "machine-shop/paint-batch-api",
    isArchived: false,
    visibility: "PRIVATE",
    pushedAt: "2026-07-15T22:03:00Z",
  },
};

function rateLimit(cost: number, used: number) {
  return {
    limit: 12500,
    cost,
    remaining: 12500 - used,
    used,
    resetAt: "2026-07-17T13:00:00Z",
  };
}

function page(
  nodes: unknown[],
  totalCount: number,
  pageInfo: { hasNextPage: boolean; endCursor: string | null },
  used: number,
  extra?: Record<string, unknown>,
): Scripted {
  return {
    status: 200,
    headers: {
      "x-ratelimit-limit": "12500",
      "x-ratelimit-remaining": String(12500 - used),
      "x-ratelimit-used": String(used),
      "x-ratelimit-reset": "1784725200",
      "x-ratelimit-resource": "graphql",
    },
    body: {
      data: {
        organization: { repositories: { totalCount, pageInfo, nodes } },
        rateLimit: rateLimit(1, used),
      },
      ...(extra ?? {}),
    },
  };
}

const CUR1 = "Y3Vyc29yOnYyOpHOAAABsQ==";
const CUR2 = "Y3Vyc29yOnYyOpHOAAABsg==";

function threePages(): Scripted[] {
  return [
    page([REPOS.rover, REPOS.runbooks], 4, { hasNextPage: true, endCursor: CUR1 }, 1),
    page([REPOS.gate], 4, { hasNextPage: true, endCursor: CUR2 }, 2),
    page([REPOS.paint], 4, { hasNextPage: false, endCursor: null }, 3),
  ];
}

test("request contract: single POST /graphql with parameterized query", async (t) => {
  const { base, requests } = await startMock(t, [
    page([REPOS.rover], 1, { hasNextPage: false, endCursor: null }, 1),
  ]);
  const client = new GraphQLClient({ baseUrl: base, token: TOKEN });
  await collectRepositories(client, ORG);

  assert.equal(requests.length, 1);
  const r = requests[0];
  assert.equal(r.method, "POST", "GraphQL queries go over POST");
  assert.equal(r.path, "/graphql", "the GraphQL endpoint is a single /graphql resource");
  assert.equal(
    r.headers.authorization,
    `bearer ${TOKEN}`,
    "docs use the lowercase 'bearer' scheme for GraphQL",
  );
  assert.match(String(r.headers["content-type"]), /application\/json/);
  assert.ok(r.headers["user-agent"], "GitHub requires a User-Agent header");

  assert.equal(typeof r.body.query, "string", "body carries the query string");
  assert.ok(r.body.variables, "body carries a separate variables object");
  assert.equal(r.body.variables.login, ORG, "org login travels as a variable");
  assert.ok(
    r.body.query.includes("$login"),
    "query must declare a $login variable, not inline the org",
  );
  assert.ok(
    !r.body.query.includes(ORG),
    "org name must not be string-interpolated into the query text",
  );
  assert.ok(r.body.query.includes("pageInfo"), "query selects pageInfo for pagination");
  assert.ok(r.body.query.includes("hasNextPage"), "pageInfo needs hasNextPage");
  assert.ok(r.body.query.includes("endCursor"), "pageInfo needs endCursor");
  assert.ok(r.body.query.includes("rateLimit"), "query selects rateLimit metadata");
  assert.equal(r.body.variables.first, 100, "default page size is the documented max, 100");
  assert.ok(
    r.body.variables.after === null || r.body.variables.after === undefined,
    "first page sends no cursor (after is null/omitted)",
  );
});

test("page size is clamped to the documented 1-100 range", async (t) => {
  const { base, requests } = await startMock(t, [
    page([REPOS.rover], 1, { hasNextPage: false, endCursor: null }, 1),
  ]);
  const client = new GraphQLClient({ baseUrl: base, token: TOKEN });
  await collectRepositories(client, ORG, { pageSize: 250 });
  assert.equal(requests[0].body.variables.first, 100, "first/last accept at most 100");
});

test("cursor traversal follows endCursor until hasNextPage is false", async (t) => {
  const { base, requests } = await startMock(t, threePages());
  const client = new GraphQLClient({ baseUrl: base, token: TOKEN });
  const report = await collectRepositories(client, ORG);

  assert.equal(requests.length, 3, "exactly one request per page");
  assert.equal(requests[1].body.variables.after, CUR1, "page 2 passes page 1's endCursor verbatim");
  assert.equal(requests[2].body.variables.after, CUR2, "page 3 passes page 2's endCursor verbatim");

  assert.deepEqual(
    report.repositories.map((r: any) => r.name),
    ["mars-rover-fw", "ops-runbooks", "quality-gate", "paint-batch-api"],
    "repositories arrive in traversal order",
  );
  assert.equal(report.totalCount, 4, "connection totalCount is preserved");
  assert.equal(report.pagesFetched, 3);

  const gate = report.repositories[2];
  assert.equal(gate.id, REPOS.gate.id);
  assert.equal(gate.nameWithOwner, "machine-shop/quality-gate");
  assert.equal(gate.isArchived, true);
  assert.equal(gate.visibility, "PRIVATE");
  assert.equal(gate.pushedAt, null, "null pushedAt survives untouched");
});

test("rate-limit metadata: rateLimit field and x-ratelimit headers", async (t) => {
  const { base } = await startMock(t, threePages());
  const client = new GraphQLClient({ baseUrl: base, token: TOKEN });
  const report = await collectRepositories(client, ORG);

  assert.equal(report.rateLimit.limit, 12500, "GraphQL point limit from the rateLimit field");
  assert.equal(report.rateLimit.cost, 1);
  assert.equal(report.rateLimit.used, 3, "rateLimit reflects the LAST page's response");
  assert.equal(report.rateLimit.remaining, 12497);
  assert.equal(report.rateLimit.resetAt, "2026-07-17T13:00:00Z");

  assert.equal(report.rateHeaders.limit, 12500, "x-ratelimit-limit parsed as a number");
  assert.equal(report.rateHeaders.remaining, 12497);
  assert.equal(report.rateHeaders.used, 3);
  assert.equal(report.rateHeaders.reset, 1784725200, "x-ratelimit-reset is UTC epoch seconds");
  assert.equal(report.rateHeaders.resource, "graphql");
});

test("partial data plus errors keeps good nodes and records warnings", async (t) => {
  const partial = page(
    [REPOS.rover, null],
    2,
    { hasNextPage: false, endCursor: null },
    1,
    {
      errors: [
        {
          type: "FORBIDDEN",
          path: ["organization", "repositories", "nodes", 1],
          message: "Resource not accessible by integration",
        },
      ],
    },
  );
  const { base } = await startMock(t, [partial]);
  const client = new GraphQLClient({ baseUrl: base, token: TOKEN });
  const report = await collectRepositories(client, ORG);

  assert.equal(report.repositories.length, 1, "null nodes from partial errors are dropped");
  assert.equal(report.repositories[0].id, REPOS.rover.id);
  assert.equal(report.warnings.length, 1, "the GraphQL error is preserved as a warning");
  assert.equal(report.warnings[0].type, "FORBIDDEN");
  assert.equal(report.warnings[0].message, "Resource not accessible by integration");
  assert.deepEqual(report.warnings[0].path, ["organization", "repositories", "nodes", 1]);
});

test("errors with no data throw a typed GraphQL error", async (t) => {
  const { base } = await startMock(t, [
    {
      status: 200,
      body: {
        data: { organization: null, rateLimit: rateLimit(1, 1) },
        errors: [
          {
            type: "NOT_FOUND",
            path: ["organization"],
            message: "Could not resolve to an Organization with the login of 'machine-shop'.",
          },
        ],
      },
    },
  ]);
  const client = new GraphQLClient({ baseUrl: base, token: TOKEN });
  await assert.rejects(
    () => collectRepositories(client, ORG),
    (err: any) => {
      assert.ok(err instanceof GraphQLQueryError, `want GraphQLQueryError, got ${err}`);
      assert.equal(err.errors.length, 1);
      assert.equal(err.errors[0].type, "NOT_FOUND");
      assert.match(err.message, /Could not resolve to an Organization/);
      assert.ok(!err.message.includes(TOKEN), "token must never leak into error text");
      return true;
    },
  );
});

test("HTTP failures throw a typed error with the token redacted", async (t) => {
  const { base } = await startMock(t, [
    { status: 401, body: { message: "Bad credentials" } },
  ]);
  const client = new GraphQLClient({ baseUrl: base, token: TOKEN });
  await assert.rejects(
    () => collectRepositories(client, ORG),
    (err: any) => {
      assert.ok(err instanceof GraphQLHttpError, `want GraphQLHttpError, got ${err}`);
      assert.equal(err.status, 401);
      assert.match(err.message, /Bad credentials/);
      assert.ok(!err.message.includes(TOKEN), "token must never leak into error text");
      assert.ok(!err.message.includes("bearer"), "auth header must never leak into error text");
      return true;
    },
  );
});

test("a retried page is re-requested with the same cursor and deduplicated", async (t) => {
  const script: Scripted[] = [
    page([REPOS.rover, REPOS.runbooks], 4, { hasNextPage: true, endCursor: CUR1 }, 1),
    { status: 502, rawBody: "Bad gateway" },
    // The retried page overlaps: ops-runbooks shows up again after cursor drift.
    page([REPOS.runbooks, REPOS.gate], 4, { hasNextPage: true, endCursor: CUR2 }, 2),
    page([REPOS.paint], 4, { hasNextPage: false, endCursor: null }, 3),
  ];
  const { base, requests } = await startMock(t, script);
  const client = new GraphQLClient({ baseUrl: base, token: TOKEN });
  const report = await collectRepositories(client, ORG);

  assert.equal(requests.length, 4, "failed page is retried exactly once");
  assert.equal(requests[1].body.variables.after, CUR1);
  assert.equal(
    requests[2].body.variables.after,
    CUR1,
    "the retry re-sends the identical cursor, not a guessed one",
  );

  const ids = report.repositories.map((r: any) => r.id);
  assert.deepEqual(
    ids,
    [REPOS.rover.id, REPOS.runbooks.id, REPOS.gate.id, REPOS.paint.id],
    "duplicates from the retried page are dropped by node id, first-seen order kept",
  );
  assert.equal(report.duplicatesDropped, 1, "the overlap is counted");
});

test("two consecutive transport failures for one page give up", async (t) => {
  const script: Scripted[] = [
    page([REPOS.rover], 4, { hasNextPage: true, endCursor: CUR1 }, 1),
    { status: 502, rawBody: "Bad gateway" },
    { status: 503, rawBody: "Service unavailable" },
  ];
  const { base, requests } = await startMock(t, script);
  const client = new GraphQLClient({ baseUrl: base, token: TOKEN });
  await assert.rejects(
    () => collectRepositories(client, ORG),
    (err: any) => {
      assert.ok(err instanceof GraphQLHttpError, `want GraphQLHttpError, got ${err}`);
      assert.equal(err.status, 503, "the terminal failure is the one surfaced");
      return true;
    },
  );
  assert.equal(requests.length, 3, "one retry only — no unbounded retry loops");
});

test("client.execute exposes data, errors, and header metadata directly", async (t) => {
  const { base } = await startMock(t, [
    page([REPOS.rover], 1, { hasNextPage: false, endCursor: null }, 7),
  ]);
  const client = new GraphQLClient({ baseUrl: base, token: TOKEN });
  const res = await client.execute("query($login: String!) { placeholder }", {
    login: ORG,
  });
  assert.ok(res.data.organization, "execute returns the data envelope");
  assert.equal(res.rateHeaders.used, 7);
  assert.equal(res.rateHeaders.resource, "graphql");
});
