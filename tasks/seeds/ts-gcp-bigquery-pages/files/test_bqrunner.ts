// Acceptance tests for the bq query runner. Everything runs against a local
// node:http mock that speaks the BigQuery REST v2 wire contract pinned in
// docs/contract.json — no real project, no real credentials, no sleeps
// (jobs.query/getQueryResults long-polling is server-side via timeoutMs).
import { test } from "node:test";
import assert from "node:assert/strict";
import { createServer } from "node:http";

import { runQuery, BigQueryJobError, BigQueryHttpError } from "./bq/client.ts";
import { encodeParam } from "./bq/params.ts";

const TOKEN = "dummy-bq-token-4471";
const PROJECT = "citrus-lab";

interface Captured {
  method: string;
  path: string;
  search: URLSearchParams;
  headers: Record<string, string | string[] | undefined>;
  body: any;
}

interface Scripted {
  status?: number;
  body: unknown;
}

async function startMock(
  t: any,
  serve: (n: number, req: Captured) => Scripted,
): Promise<{ base: string; requests: Captured[] }> {
  const requests: Captured[] = [];
  const server = createServer((req, res) => {
    const chunks: Buffer[] = [];
    req.on("data", (c) => chunks.push(c));
    req.on("end", () => {
      const raw = Buffer.concat(chunks).toString("utf8");
      const u = new URL(req.url ?? "/", "http://localhost");
      const captured: Captured = {
        method: req.method ?? "",
        path: u.pathname,
        search: u.searchParams,
        headers: req.headers,
        body: raw ? JSON.parse(raw) : null,
      };
      const n = requests.length;
      requests.push(captured);
      const scripted = serve(n, captured);
      res.statusCode = scripted.status ?? 200;
      res.setHeader("content-type", "application/json; charset=UTF-8");
      res.end(JSON.stringify(scripted.body));
    });
  });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", () => resolve()));
  const addr = server.address() as { port: number };
  t.after(() => server.close());
  return { base: `http://127.0.0.1:${addr.port}`, requests };
}

const JOB_REF = { projectId: PROJECT, jobId: "job_abc", location: "EU" };

const SCHEMA = {
  fields: [
    { name: "user_id", type: "INTEGER", mode: "REQUIRED" },
    { name: "score", type: "FLOAT", mode: "NULLABLE" },
    { name: "tags", type: "STRING", mode: "REPEATED" },
    { name: "active", type: "BOOLEAN", mode: "NULLABLE" },
    { name: "note", type: "STRING", mode: "NULLABLE" },
  ],
};

const PAGE1_ROWS = [
  {
    f: [
      { v: "9007199254740993" },
      { v: "12.5" },
      { v: [{ v: "a" }, { v: "b" }] },
      { v: "true" },
      { v: null },
    ],
  },
  {
    f: [{ v: "2" }, { v: null }, { v: [] }, { v: "false" }, { v: "ok" }],
  },
];

const PAGE2_ROWS = [
  {
    f: [{ v: "3" }, { v: "0.25" }, { v: [{ v: "z" }] }, { v: null }, { v: "third" }],
  },
];

function cfgFor(base: string) {
  return {
    baseUrl: base,
    projectId: PROJECT,
    token: TOKEN,
    location: "EU",
    pollTimeoutMs: 250,
    maxPolls: 5,
  };
}

test("parameterized query polls the incomplete job and pages rows by pageToken", async (t) => {
  const { base, requests } = await startMock(t, (n) => {
    switch (n) {
      case 0:
        return { body: { kind: "bigquery#queryResponse", jobReference: JOB_REF, jobComplete: false } };
      case 1:
        return { body: { kind: "bigquery#getQueryResultsResponse", jobReference: JOB_REF, jobComplete: false } };
      case 2:
        return {
          body: {
            kind: "bigquery#getQueryResultsResponse",
            jobReference: JOB_REF,
            jobComplete: true,
            schema: SCHEMA,
            rows: PAGE1_ROWS,
            pageToken: "PT1",
            totalRows: "3",
            cacheHit: false,
          },
        };
      case 3:
        return {
          body: {
            kind: "bigquery#getQueryResultsResponse",
            jobReference: JOB_REF,
            jobComplete: true,
            rows: PAGE2_ROWS,
            totalRows: "3",
          },
        };
      default:
        return { status: 500, body: { error: { message: "unexpected request" } } };
    }
  });

  const sql =
    "SELECT user_id, score, tags, active, note FROM telemetry.users WHERE score >= @min_score AND team = @team AND user_id IN UNNEST(@ids) AND active = @flag";
  const result = await runQuery(
    cfgFor(base),
    sql,
    { min_score: 4.5, team: "blue", ids: [1n, 2n], flag: true },
    { pageSize: 2 },
  );

  assert.equal(requests.length, 4);

  const q = requests[0];
  assert.equal(q.method, "POST");
  assert.equal(q.path, `/bigquery/v2/projects/${PROJECT}/queries`);
  assert.equal(q.headers.authorization, `Bearer ${TOKEN}`);
  assert.match(String(q.headers["content-type"]), /^application\/json/);
  assert.equal(q.body.query, sql);
  assert.equal(q.body.useLegacySql, false);
  assert.equal(q.body.parameterMode, "NAMED");
  assert.equal(q.body.location, "EU");
  assert.equal(q.body.maxResults, 2);
  assert.equal(q.body.timeoutMs, 250);
  assert.deepEqual(q.body.queryParameters, [
    { name: "min_score", parameterType: { type: "FLOAT64" }, parameterValue: { value: "4.5" } },
    { name: "team", parameterType: { type: "STRING" }, parameterValue: { value: "blue" } },
    {
      name: "ids",
      parameterType: { type: "ARRAY", arrayType: { type: "INT64" } },
      parameterValue: { arrayValues: [{ value: "1" }, { value: "2" }] },
    },
    { name: "flag", parameterType: { type: "BOOL" }, parameterValue: { value: "true" } },
  ]);

  for (const i of [1, 2]) {
    const poll = requests[i];
    assert.equal(poll.method, "GET");
    assert.equal(poll.path, `/bigquery/v2/projects/${PROJECT}/queries/job_abc`);
    assert.equal(poll.headers.authorization, `Bearer ${TOKEN}`);
    assert.equal(poll.search.get("location"), "EU");
    assert.equal(poll.search.get("timeoutMs"), "250");
    assert.equal(poll.search.get("pageToken"), null);
  }

  const page2 = requests[3];
  assert.equal(page2.method, "GET");
  assert.equal(page2.path, `/bigquery/v2/projects/${PROJECT}/queries/job_abc`);
  assert.equal(page2.headers.authorization, `Bearer ${TOKEN}`);
  assert.equal(page2.search.get("location"), "EU");
  assert.equal(page2.search.get("pageToken"), "PT1");
  assert.equal(page2.search.get("maxResults"), "2");

  assert.equal(result.jobId, "job_abc");
  assert.equal(result.pageCount, 2);
  assert.equal(typeof result.totalRows, "bigint");
  assert.equal(result.totalRows, 3n);
  assert.deepEqual(result.schema, SCHEMA.fields);
  assert.equal(result.cacheHit, false);

  assert.equal(result.rows.length, 3);
  const [r1, r2, r3] = result.rows;
  assert.equal(r1.user_id, 9007199254740993n);
  assert.equal(r1.score, 12.5);
  assert.deepEqual(r1.tags, ["a", "b"]);
  assert.equal(r1.active, true);
  assert.equal(r1.note, null);

  assert.equal(r2.user_id, 2n);
  assert.equal(r2.score, null);
  assert.deepEqual(r2.tags, []);
  assert.equal(r2.active, false);
  assert.equal(r2.note, "ok");

  assert.equal(r3.user_id, 3n);
  assert.equal(r3.score, 0.25);
  assert.deepEqual(r3.tags, ["z"]);
  assert.equal(r3.active, null);
  assert.equal(r3.note, "third");
});

test("job-level errors are reported before any rows are surfaced", async (t) => {
  const { base, requests } = await startMock(t, (n) => {
    switch (n) {
      case 0:
        return { body: { kind: "bigquery#queryResponse", jobReference: JOB_REF, jobComplete: false } };
      default:
        return {
          body: {
            kind: "bigquery#getQueryResultsResponse",
            jobReference: JOB_REF,
            jobComplete: true,
            schema: SCHEMA,
            rows: PAGE1_ROWS,
            pageToken: "PT9",
            totalRows: "3",
            errors: [
              {
                reason: "invalidQuery",
                location: "query",
                message: "Unrecognized name: usr_id at [1:8]",
              },
            ],
          },
        };
    }
  });

  await assert.rejects(
    runQuery(cfgFor(base), "SELECT usr_id FROM telemetry.users"),
    (err: any) => {
      assert.ok(err instanceof BigQueryJobError, `expected BigQueryJobError, got ${err}`);
      assert.equal(err.jobId, "job_abc");
      assert.equal(err.errors.length, 1);
      assert.equal(err.errors[0].reason, "invalidQuery");
      assert.equal(err.errors[0].location, "query");
      assert.match(err.message, /Unrecognized name/);
      return true;
    },
  );
  // The failed job's pageToken must not be followed.
  assert.equal(requests.length, 2);
});

test("HTTP errors surface the API message and status without leaking the token", async (t) => {
  const { base, requests } = await startMock(t, () => ({
    status: 400,
    body: {
      error: {
        code: 400,
        message: "Syntax error: Unexpected keyword SELCT at [1:1]",
        status: "INVALID_ARGUMENT",
      },
    },
  }));

  await assert.rejects(
    runQuery(cfgFor(base), "SELCT 1"),
    (err: any) => {
      assert.ok(err instanceof BigQueryHttpError, `expected BigQueryHttpError, got ${err}`);
      assert.equal(err.status, 400);
      assert.match(err.message, /Syntax error/);
      assert.ok(!err.message.includes(TOKEN), "error text leaks the bearer token");
      return true;
    },
  );
  assert.equal(requests.length, 1);
});

test("polling an incomplete job is bounded by maxPolls", async (t) => {
  const { base, requests } = await startMock(t, () => ({
    body: { kind: "bigquery#getQueryResultsResponse", jobReference: JOB_REF, jobComplete: false },
  }));

  const cfg = { ...cfgFor(base), maxPolls: 3 };
  await assert.rejects(runQuery(cfg, "SELECT 1"), (err: any) => {
    assert.match(String(err.message), /incomplete/i);
    return true;
  });
  // one jobs.query plus exactly maxPolls jobs.getQueryResults probes
  assert.equal(requests.length, 4);
});

test("a query without parameters omits parameterMode and finishes in one round trip", async (t) => {
  const { base, requests } = await startMock(t, () => ({
    body: {
      kind: "bigquery#queryResponse",
      jobReference: JOB_REF,
      jobComplete: true,
      schema: { fields: [{ name: "n", type: "INTEGER", mode: "NULLABLE" }] },
      rows: [{ f: [{ v: "42" }] }],
      totalRows: "1",
    },
  }));

  const result = await runQuery(cfgFor(base), "SELECT 42 AS n");
  assert.equal(requests.length, 1);
  const q = requests[0];
  assert.ok(!("parameterMode" in q.body), "parameterMode must be omitted without parameters");
  assert.ok(!("queryParameters" in q.body), "queryParameters must be omitted without parameters");
  assert.equal(q.body.useLegacySql, false);
  assert.equal(result.pageCount, 1);
  assert.equal(result.totalRows, 1n);
  assert.equal(result.rows.length, 1);
  assert.equal(result.rows[0].n, 42n);
});

test("named parameter encoding pins the documented shapes", () => {
  assert.deepEqual(encodeParam("team", "blue"), {
    name: "team",
    parameterType: { type: "STRING" },
    parameterValue: { value: "blue" },
  });
  assert.deepEqual(encodeParam("n", 7n), {
    name: "n",
    parameterType: { type: "INT64" },
    parameterValue: { value: "7" },
  });
  assert.deepEqual(encodeParam("f", 4.5), {
    name: "f",
    parameterType: { type: "FLOAT64" },
    parameterValue: { value: "4.5" },
  });
  assert.deepEqual(encodeParam("whole", 3), {
    name: "whole",
    parameterType: { type: "FLOAT64" },
    parameterValue: { value: "3" },
  });
  assert.deepEqual(encodeParam("off", false), {
    name: "off",
    parameterType: { type: "BOOL" },
    parameterValue: { value: "false" },
  });
  assert.deepEqual(encodeParam("states", ["WA", "WI"]), {
    name: "states",
    parameterType: { type: "ARRAY", arrayType: { type: "STRING" } },
    parameterValue: { arrayValues: [{ value: "WA" }, { value: "WI" }] },
  });
  assert.throws(() => encodeParam("bad", null as any), /NULL/);
  assert.throws(() => encodeParam("empty", [] as any), /empty array/i);
});
