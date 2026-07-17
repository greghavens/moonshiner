// Acceptance tests for the project IP access-list reconciler (MongoDB
// Atlas Administration API v2). A local node:http mock speaks the wire
// contract pinned in docs/contract.json — paginated GET, array-bodied POST
// with idempotent duplicate absorption, per-entry /status transitions, and
// 429 + Retry-After rate limiting. No real Atlas, no credentials, no
// wall-clock sleeps (waiting is injected and recorded).
// Run: node --test test_access_list.ts
// Protected — do not modify this file or anything under docs/.
import { test } from "node:test";
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { readFileSync } from "node:fs";

import { AtlasClient, AtlasApiError, ATLAS_MEDIA_TYPE } from "./atlas/client.ts";
import { reconcileAccessList } from "./atlas/reconcile.ts";

const GID = "64f00dfeedfacecafe123abc";
const TOKEN = "fixture-atlas-bearer-token-ts9";
const LIST = `/api/atlas/v2/groups/${GID}/accessList`;

interface Captured {
  method: string;
  url: string;
  headers: Record<string, string | string[] | undefined>;
  body: any;
}

interface Scripted {
  status?: number;
  body?: unknown;
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
        url: req.url ?? "",
        headers: req.headers,
        body: raw ? JSON.parse(raw) : null,
      });
      const s = script[Math.min(requests.length - 1, script.length - 1)];
      res.statusCode = s.status ?? 200;
      res.setHeader("content-type", (s.status ?? 200) >= 400 ? "application/json" : ATLAS_MEDIA_TYPE);
      for (const [k, v] of Object.entries(s.headers ?? {})) res.setHeader(k, v);
      res.end(s.body === undefined ? "" : JSON.stringify(s.body));
    });
  });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  t.after(() => new Promise((resolve) => server.close(resolve)));
  const addr = server.address() as { port: number };
  return { base: `http://127.0.0.1:${addr.port}`, requests };
}

function page(base: string, results: unknown[], opts: { next?: string; prev?: string; total: number }) {
  const links = [{ href: base + "#self", rel: "self" }];
  if (opts.prev) links.push({ href: base + opts.prev, rel: "previous" });
  if (opts.next) links.push({ href: base + opts.next, rel: "next" });
  return { links, results, totalCount: opts.total };
}

function apiError(status: number, errorCode: string, detail: string, reason: string) {
  return { detail, error: status, errorCode, parameters: [], reason };
}

function makeSleeper(): { calls: number[]; sleep: (ms: number) => Promise<void> } {
  const calls: number[] = [];
  return {
    calls,
    sleep: (ms: number) => {
      calls.push(ms);
      return Promise.resolve();
    },
  };
}

// ---------------------------------------------------------------------------
// Existing client behavior — must keep working.

test("client sends bearer auth, the dated media type, and encoded query", async (t) => {
  const mock = await startMock(t, [{ body: page("", [], { total: 0 }) }]);
  const client = new AtlasClient({ baseUrl: mock.base, token: TOKEN });
  await client.request("GET", LIST, {
    query: { pageNum: "1", itemsPerPage: "100", includeCount: "true" },
  });
  const r = mock.requests[0];
  assert.equal(r.method, "GET");
  assert.equal(r.url, `${LIST}?pageNum=1&itemsPerPage=100&includeCount=true`);
  assert.equal(r.headers.authorization, `Bearer ${TOKEN}`);
  assert.equal(r.headers.accept, ATLAS_MEDIA_TYPE);
  assert.equal(r.headers["content-type"], undefined, "GET must not send a Content-Type");
});

test("client decodes the documented ApiError envelope", async (t) => {
  const mock = await startMock(t, [
    {
      status: 404,
      body: apiError(404, "RESOURCE_NOT_FOUND", `Cannot find resource ${LIST}.`, "Not Found"),
    },
    {
      status: 429,
      headers: { "retry-after": "7" },
      body: apiError(429, "RATE_LIMITED_TOKEN_BUCKET", "Rate limit exceeded.", "Too Many Requests"),
    },
  ]);
  const client = new AtlasClient({ baseUrl: mock.base, token: TOKEN });

  const notFound = await client.request("GET", LIST).then(
    () => null,
    (e) => e,
  );
  assert.ok(notFound instanceof AtlasApiError);
  assert.equal(notFound.status, 404);
  assert.equal(notFound.errorCode, "RESOURCE_NOT_FOUND");
  assert.equal(notFound.detail, `Cannot find resource ${LIST}.`);
  assert.equal(notFound.reason, "Not Found");
  assert.equal(notFound.retryAfterSeconds, null);

  const limited = await client.request("GET", LIST).then(
    () => null,
    (e) => e,
  );
  assert.ok(limited instanceof AtlasApiError);
  assert.equal(limited.errorCode, "RATE_LIMITED_TOKEN_BUCKET");
  assert.equal(limited.retryAfterSeconds, 7);
  assert.ok(!String(limited.message).includes(TOKEN), "errors must not leak the token");
});

// ---------------------------------------------------------------------------
// New feature: reconcileAccessList.

test("reconcile pages existing entries and adds only the missing ones", async (t) => {
  let base = "";
  const script: Scripted[] = [];
  const mock = await startMock(t, script);
  base = mock.base;
  script.push(
    {
      body: page(
        base,
        [
          { cidrBlock: "10.20.0.0/16", comment: "site vpn", groupId: GID },
          { ipAddress: "198.51.100.9", cidrBlock: "198.51.100.9/32", groupId: GID },
        ],
        { next: `${LIST}?pageNum=2&itemsPerPage=100&includeCount=true`, total: 3 },
      ),
    },
    {
      body: page(base, [{ awsSecurityGroup: "sg-9dead0be", groupId: GID }], {
        prev: `${LIST}?pageNum=1&itemsPerPage=100&includeCount=true`,
        total: 3,
      }),
    },
    { body: page(base, [], { total: 5 }) }, // POST reply: current (merged) list
    { body: { STATUS: "PENDING" } },
    { body: { STATUS: "ACTIVE" } },
    { body: { STATUS: "ACTIVE" } },
  );

  const client = new AtlasClient({ baseUrl: mock.base, token: TOKEN });
  const { calls, sleep } = makeSleeper();
  const report = await reconcileAccessList(
    client,
    GID,
    [
      { cidrBlock: " 10.20.0.0/16 ", comment: "site vpn" },
      { ipAddress: "203.0.113.7", comment: "office egress" },
      { cidrBlock: "203.0.113.7/32" },
      { awsSecurityGroup: "SG-0ABC1234" },
      { cidrBlock: "10.20.0.0/16" },
    ],
    { sleep, pollIntervalMs: 500 },
  );

  const uris = mock.requests.map((r) => `${r.method} ${r.url}`);
  assert.deepEqual(uris, [
    `GET ${LIST}?pageNum=1&itemsPerPage=100&includeCount=true`,
    `GET ${LIST}?pageNum=2&itemsPerPage=100&includeCount=true`,
    `POST ${LIST}`,
    `GET ${LIST}/203.0.113.7/status`,
    `GET ${LIST}/203.0.113.7/status`,
    `GET ${LIST}/sg-0abc1234/status`,
  ]);

  const post = mock.requests[2];
  assert.equal(post.headers["content-type"], ATLAS_MEDIA_TYPE, "POST must send the dated Content-Type");
  assert.deepEqual(
    post.body,
    [
      { ipAddress: "203.0.113.7", comment: "office egress" },
      { awsSecurityGroup: "sg-0abc1234" },
    ],
    "one POST, an array of only the missing entries, original shapes, desired order",
  );

  assert.deepEqual(calls, [500], "one injected poll wait for the single PENDING observation");
  assert.deepEqual(report.added, ["203.0.113.7", "sg-0abc1234"]);
  assert.deepEqual(report.alreadyPresent, ["10.20.0.0/16"]);
  assert.deepEqual(report.statuses, { "203.0.113.7": "ACTIVE", "sg-0abc1234": "ACTIVE" });
});

test("a converged list means zero writes and zero polls", async (t) => {
  const script: Scripted[] = [];
  const mock = await startMock(t, script);
  script.push({
    body: page(
      mock.base,
      [
        { ipAddress: "192.0.2.44", cidrBlock: "192.0.2.44/32", groupId: GID },
        { awsSecurityGroup: "sg-9dead0be", groupId: GID },
        { ipAddress: "198.51.100.9", cidrBlock: "198.51.100.9/32", groupId: GID },
      ],
      { total: 3 },
    ),
  });
  const client = new AtlasClient({ baseUrl: mock.base, token: TOKEN });
  const { calls, sleep } = makeSleeper();
  const report = await reconcileAccessList(
    client,
    GID,
    [
      { cidrBlock: "192.0.2.44/32" },
      { awsSecurityGroup: "SG-9DEAD0BE" },
      { ipAddress: "198.51.100.9" },
    ],
    { sleep, pollIntervalMs: 500 },
  );

  assert.equal(mock.requests.length, 1, "only the page GET may hit the API");
  assert.equal(report.added.length, 0);
  assert.deepEqual(report.alreadyPresent, ["192.0.2.44/32", "sg-9dead0be", "198.51.100.9"]);
  assert.deepEqual(report.statuses, {});
  assert.deepEqual(calls, []);
});

test("CIDR status lookups percent-encode the slash in the path", async (t) => {
  const mock = await startMock(t, [
    { body: page("", [], { total: 0 }) },
    { body: page("", [], { total: 1 }) },
    { body: { STATUS: "PENDING" } },
    { body: { STATUS: "ACTIVE" } },
  ]);
  const client = new AtlasClient({ baseUrl: mock.base, token: TOKEN });
  const { calls, sleep } = makeSleeper();
  const report = await reconcileAccessList(client, GID, [{ cidrBlock: "198.51.100.0/24" }], {
    sleep,
    pollIntervalMs: 500,
  });
  assert.equal(mock.requests[3].url, `${LIST}/198.51.100.0%2F24/status`);
  assert.equal(mock.requests[2].url, `${LIST}/198.51.100.0%2F24/status`);
  assert.deepEqual(calls, [500]);
  assert.deepEqual(report.statuses, { "198.51.100.0/24": "ACTIVE" });
});

test("429 on POST waits Retry-After seconds and retries once", async (t) => {
  const mock = await startMock(t, [
    { body: page("", [], { total: 0 }) },
    {
      status: 429,
      headers: { "retry-after": "3" },
      body: apiError(
        429,
        "RATE_LIMITED_TOKEN_BUCKET",
        "Rate limit exceeded for api/atlas/v2/groups. Please retry after 3 seconds.",
        "Too Many Requests",
      ),
    },
    { body: page("", [], { total: 1 }) },
    { body: { STATUS: "ACTIVE" } },
  ]);
  const client = new AtlasClient({ baseUrl: mock.base, token: TOKEN });
  const { calls, sleep } = makeSleeper();
  const report = await reconcileAccessList(client, GID, [{ ipAddress: "192.0.2.99" }], {
    sleep,
    pollIntervalMs: 500,
  });
  const posts = mock.requests.filter((r) => r.method === "POST");
  assert.equal(posts.length, 2, "the rate-limited POST must be retried exactly once");
  assert.deepEqual(posts[0].body, posts[1].body, "the retry must resend the identical batch");
  assert.deepEqual(calls, [3000], "the wait must honor Retry-After (seconds -> ms) via the injected sleep");
  assert.deepEqual(report.added, ["192.0.2.99"]);
});

test("a FAILED status transition is terminal", async (t) => {
  const mock = await startMock(t, [
    { body: page("", [], { total: 0 }) },
    { body: page("", [], { total: 1 }) },
    { body: { STATUS: "PENDING" } },
    { body: { STATUS: "FAILED" } },
    { body: { STATUS: "ACTIVE" } }, // must never be requested
  ]);
  const client = new AtlasClient({ baseUrl: mock.base, token: TOKEN });
  const { calls, sleep } = makeSleeper();
  const err = await reconcileAccessList(client, GID, [{ cidrBlock: "198.18.0.0/15" }], {
    sleep,
    pollIntervalMs: 500,
  }).then(
    () => null,
    (e) => e,
  );
  assert.ok(err instanceof Error, "FAILED must reject");
  assert.ok(String(err.message).includes("198.18.0.0/15"), `error should name the entry: ${err.message}`);
  assert.equal(mock.requests.length, 4, "polling must stop at the FAILED observation");
  assert.deepEqual(calls, [500]);
});

test("protected docs fixtures parse and pin the researched contract", () => {
  const contract = JSON.parse(readFileSync("docs/contract.json", "utf8"));
  const sources = JSON.parse(readFileSync("docs/official_sources.json", "utf8"));
  assert.equal(contract.media_type, ATLAS_MEDIA_TYPE);
  assert.deepEqual(contract.status_endpoint.enum, ["PENDING", "FAILED", "ACTIVE"]);
  assert.equal(
    contract.duplicate_semantics.conflict_409_documented,
    false,
    "current official docs declare no 409 for POST accessList — duplicates upsert idempotently",
  );
  assert.ok(Array.isArray(sources.research.official_sources));
  assert.ok(sources.research.official_sources.length >= 2);
});
