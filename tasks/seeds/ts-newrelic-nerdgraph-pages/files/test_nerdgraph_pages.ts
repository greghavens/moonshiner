// Acceptance tests for the NerdGraph entity inventory (src/index.ts).
//
// Runs a loopback fake NerdGraph endpoint (POST /graphql, API-Key auth,
// entitySearch cursor pages, partial data plus errors, 429 throttling) and
// drives the inventory against it. No real New Relic, no real credentials,
// no wall-clock sleeps: waiting goes through the injected sleeper and is
// recorded. The wire contract the fake enforces is pinned in
// docs/contract.json. This file and everything under docs/ are protected.

import { test } from "node:test";
import assert from "node:assert/strict";
import http from "node:http";
import { readFileSync } from "node:fs";
import {
  regionEndpoint,
  NerdGraphClient,
  EntityInventory,
  NerdGraphHttpError,
  NerdGraphQueryError,
} from "./src/index.ts";

const CONTRACT = JSON.parse(readFileSync(new URL("./docs/contract.json", import.meta.url), "utf8"));
const SOURCES = JSON.parse(readFileSync(new URL("./docs/official_sources.json", import.meta.url), "utf8"));

const API_KEY: string = CONTRACT.auth.fixture_api_key; // dummy; must never leak
const SEARCH: string = CONTRACT.fixtures.search_query;
const CURSORS: string[] = CONTRACT.fixtures.cursors;
const PAGES: Array<Array<Record<string, unknown>>> = CONTRACT.fixtures.entities_pages;
const ALL_GUIDS = PAGES.flat().map((e) => e.guid);

type Recorded = {
  method: string;
  rawUrl: string;
  headers: http.IncomingHttpHeaders;
  body: string;
  json: { query?: string; variables?: Record<string, unknown> } & Record<string, unknown>;
};

class FakeNerdGraph {
  requests: Recorded[] = [];
  script: Array<{ status: number; doc: unknown; raw?: string }> = [];
  server: http.Server;
  baseUrl = "";

  constructor() {
    this.server = http.createServer((req, res) => {
      let body = "";
      req.on("data", (chunk) => (body += chunk));
      req.on("end", () => {
        let json: Recorded["json"] = {};
        try {
          json = JSON.parse(body);
        } catch {
          json = {};
        }
        this.requests.push({
          method: req.method ?? "",
          rawUrl: req.url ?? "",
          headers: req.headers,
          body,
          json,
        });
        const step = this.script.length > 0
          ? this.script.shift()!
          : { status: 200, doc: { data: { actor: { entitySearch: { results: { entities: [], nextCursor: null } } } } } };
        res.writeHead(step.status, { "content-type": "application/json; charset=utf-8" });
        res.end(step.raw ?? JSON.stringify(step.doc));
      });
    });
  }

  queue(status: number, doc: unknown) {
    this.script.push({ status, doc });
  }

  queueRaw(status: number, raw: string) {
    this.script.push({ status, doc: null, raw });
  }

  queuePage(entities: unknown[], nextCursor: string | null, errors?: unknown[]) {
    const doc: Record<string, unknown> = {
      data: { actor: { entitySearch: { results: { entities, nextCursor } } } },
    };
    if (errors) doc.errors = errors;
    this.queue(200, doc);
  }

  async start() {
    await new Promise<void>((resolve) => this.server.listen(0, "127.0.0.1", resolve));
    const addr = this.server.address();
    if (addr === null || typeof addr === "string") throw new Error("no address");
    this.baseUrl = `http://127.0.0.1:${addr.port}`;
  }

  async stop() {
    await new Promise<void>((resolve, reject) =>
      this.server.close((err) => (err ? reject(err) : resolve())),
    );
  }
}

type Harness = {
  fake: FakeNerdGraph;
  sleeps: number[];
  client: (opts?: { maxAttempts?: number; baseDelayMs?: number }) => InstanceType<typeof NerdGraphClient>;
  inventory: (opts?: { maxAttempts?: number; baseDelayMs?: number }) => InstanceType<typeof EntityInventory>;
};

async function withHarness(fn: (h: Harness) => Promise<void>) {
  const fake = new FakeNerdGraph();
  await fake.start();
  const sleeps: number[] = [];
  const h: Harness = {
    fake,
    sleeps,
    client: (opts = {}) =>
      new NerdGraphClient({
        apiKey: API_KEY,
        endpointUrl: `${fake.baseUrl}/graphql`,
        sleeper: async (ms: number) => {
          sleeps.push(ms);
        },
        maxAttempts: opts.maxAttempts ?? 4,
        baseDelayMs: opts.baseDelayMs ?? 1000,
      }),
    inventory: (opts) => new EntityInventory({ client: h.client(opts) }),
  };
  try {
    await fn(h);
  } finally {
    await fake.stop();
  }
}

function queueCleanPages(fake: FakeNerdGraph) {
  fake.queuePage(PAGES[0], CURSORS[0]);
  fake.queuePage(PAGES[1], CURSORS[1]);
  fake.queuePage(PAGES[2], null);
}

test("regionEndpoint maps the documented regions and rejects unknown ones", () => {
  assert.equal(regionEndpoint("US"), CONTRACT.endpoints.US);
  assert.equal(regionEndpoint("EU"), CONTRACT.endpoints.EU);
  assert.throws(() => regionEndpoint("MARS"), (err: unknown) => {
    const msg = String((err as Error).message);
    assert.ok(msg.includes("US") && msg.includes("EU"),
      "the error must name the valid regions");
    return true;
  });
});

test("a region-configured client targets the documented endpoint", () => {
  const eu = new NerdGraphClient({
    apiKey: API_KEY,
    region: "EU",
    sleeper: async () => {},
  });
  assert.equal(eu.endpoint, CONTRACT.endpoints.EU);
  const us = new NerdGraphClient({
    apiKey: API_KEY,
    region: "US",
    sleeper: async () => {},
  });
  assert.equal(us.endpoint, CONTRACT.endpoints.US);
});

test("requests follow the documented NerdGraph transport", async () => {
  await withHarness(async (h) => {
    queueCleanPages(h.fake);
    await h.inventory().collect(SEARCH);

    assert.ok(h.fake.requests.length >= 1);
    const req = h.fake.requests[0];
    assert.equal(req.method, "POST");
    assert.equal(req.rawUrl, "/graphql");
    assert.match(String(req.headers["content-type"]), /^application\/json/);
    assert.equal(req.headers["api-key"], API_KEY, "the user key travels in the API-Key header");
    assert.equal(req.headers.authorization, undefined,
      "NerdGraph auth is the API-Key header, not a Bearer Authorization header");
    assert.deepEqual(Object.keys(req.json).sort(), ["query", "variables"],
      "the POST body is exactly {query, variables}");
    assert.equal(typeof req.json.query, "string");
  });
});

test("cursor traversal walks every page through variables", async () => {
  await withHarness(async (h) => {
    queueCleanPages(h.fake);
    const result = await h.inventory().collect(SEARCH);

    assert.equal(h.fake.requests.length, 3, "three pages, three requests");
    const [r1, r2, r3] = h.fake.requests;
    const v1 = r1.json.variables ?? {};
    assert.equal(v1.query, SEARCH, "the search string is a variable");
    assert.equal((v1 as Record<string, unknown>).cursor ?? null, null,
      "the first page sends no cursor");
    assert.equal((r2.json.variables ?? {}).cursor, CURSORS[0]);
    assert.equal((r3.json.variables ?? {}).cursor, CURSORS[1]);
    assert.equal((r2.json.variables ?? {}).query, SEARCH,
      "every page repeats the search variable");

    assert.equal(r2.json.query, r1.json.query,
      "one GraphQL document is reused verbatim for every page");
    assert.equal(r3.json.query, r1.json.query);
    const doc = String(r1.json.query);
    assert.ok(doc.includes("entitySearch"), "the document queries actor.entitySearch");
    assert.ok(doc.includes("nextCursor"), "the document selects nextCursor");
    assert.match(doc, /cursor:\s*\$cursor/, "the cursor is passed as a GraphQL variable");
    assert.ok(!doc.includes(CURSORS[0]) && !doc.includes(CURSORS[1]),
      "cursor values are never spliced into the document");

    assert.deepEqual(result.entities.map((e: { guid: string }) => e.guid), ALL_GUIDS,
      "entities accumulate in page order");
    assert.deepEqual(result.entities[0], PAGES[0][0],
      "entity fields survive the traversal untouched");
    assert.deepEqual(result.warnings, [], "clean pages produce no warnings");
  });
});

test("search strings with quotes are never interpolated into the document", async () => {
  await withHarness(async (h) => {
    h.fake.queuePage([PAGES[0][0]], null);
    const quoted: string = CONTRACT.fixtures.quoted_search_query;
    await h.inventory().collect(quoted);

    const req = h.fake.requests[0];
    assert.equal((req.json.variables ?? {}).query, quoted);
    assert.ok(!String(req.json.query).includes(quoted),
      "the raw search text must not appear inside the GraphQL document");
  });
});

test("partial data plus errors keeps the entities and records warnings", async () => {
  await withHarness(async (h) => {
    h.fake.queuePage(PAGES[0], CURSORS[0]);
    h.fake.queuePage(PAGES[1], CURSORS[1], [CONTRACT.fixtures.partial_error]);
    h.fake.queuePage(PAGES[2], null);

    const result = await h.inventory().collect(SEARCH);

    assert.equal(h.fake.requests.length, 3,
      "an error alongside usable data must not stop the traversal");
    assert.deepEqual(result.entities.map((e: { guid: string }) => e.guid), ALL_GUIDS);
    assert.deepEqual(result.warnings, [{
      message: CONTRACT.fixtures.partial_error.message,
      errorClass: "SERVER_ERROR",
    }], "each GraphQL error becomes one warning with its errorClass");
  });
});

test("a TIMEOUT with no usable data is a typed failure", async () => {
  await withHarness(async (h) => {
    h.fake.queue(200, {
      data: { actor: { entitySearch: null } },
      errors: [CONTRACT.fixtures.timeout_error],
    });
    await assert.rejects(
      h.inventory().collect(SEARCH),
      (err: unknown) => {
        assert.ok(err instanceof NerdGraphQueryError);
        assert.deepEqual(err.errorClasses, ["TIMEOUT"],
          "the documented errorClass extension must be surfaced");
        assert.deepEqual(err.errors, [CONTRACT.fixtures.timeout_error],
          "the raw GraphQL errors are preserved for diagnostics");
        assert.ok(String(err.message).includes(CONTRACT.fixtures.timeout_error.message),
          "the failure must carry the server's message");
        assert.ok(!String(err.message).includes(API_KEY));
        return true;
      },
    );
    assert.equal(h.fake.requests.length, 1, "a query timeout is not blindly retried");
  });
});

test("429 concurrency rejections back off through the sleeper and retry", async () => {
  await withHarness(async (h) => {
    h.fake.queue(429, CONTRACT.fixtures.throttle_body);
    h.fake.queue(429, CONTRACT.fixtures.throttle_body);
    h.fake.queuePage(PAGES[2], null);

    const result = await h.inventory({ maxAttempts: 3, baseDelayMs: 500 }).collect(SEARCH);

    assert.equal(h.fake.requests.length, 3, "two throttled attempts plus the success");
    assert.deepEqual(h.sleeps, [500, 1000],
      "backoff doubles from baseDelayMs and only sleeps between attempts");
    assert.deepEqual(result.entities.map((e: { guid: string }) => e.guid),
      PAGES[2].map((e) => e.guid));
  });
});

test("429 exhaustion surfaces the status after maxAttempts attempts", async () => {
  await withHarness(async (h) => {
    h.fake.queue(429, CONTRACT.fixtures.throttle_body);
    h.fake.queue(429, CONTRACT.fixtures.throttle_body);
    h.fake.queue(429, CONTRACT.fixtures.throttle_body);
    await assert.rejects(
      h.inventory({ maxAttempts: 3, baseDelayMs: 500 }).collect(SEARCH),
      (err: unknown) => {
        assert.ok(err instanceof NerdGraphHttpError);
        assert.equal(err.status, 429);
        return true;
      },
    );
    assert.equal(h.fake.requests.length, 3, "exactly maxAttempts attempts");
    assert.deepEqual(h.sleeps, [500, 1000], "no trailing sleep after the last attempt");
  });
});

test("other HTTP failures keep status and body but never the key", async () => {
  await withHarness(async (h) => {
    h.fake.queueRaw(502, CONTRACT.fixtures.server_error_body);
    await assert.rejects(
      h.inventory().collect(SEARCH),
      (err: unknown) => {
        assert.ok(err instanceof NerdGraphHttpError);
        assert.equal(err.status, 502);
        assert.ok(String(err.body).includes("upstream connect error"),
          "the raw body is kept for diagnostics");
        assert.ok(!String(err.message).includes(API_KEY), "the API key must never surface");
        return true;
      },
    );
    assert.equal(h.fake.requests.length, 1, "a 502 is not a documented retry case");
    assert.deepEqual(h.sleeps, []);
  });
});

test("the API key never travels in the URL or the request body", async () => {
  await withHarness(async (h) => {
    queueCleanPages(h.fake);
    await h.inventory().collect(SEARCH);
    for (const req of h.fake.requests) {
      assert.ok(!req.rawUrl.includes(API_KEY), "key leaked into the URL");
      assert.ok(!req.body.includes(API_KEY), "key leaked into the GraphQL body");
    }
  });
});

test("protected provenance fixtures are intact", () => {
  assert.equal(SOURCES.research.required, true);
  assert.ok(SOURCES.research.official_sources.length >= 2);
  assert.ok(Array.isArray(SOURCES.verified_facts) && SOURCES.verified_facts.length >= 4);
  assert.equal(CONTRACT.endpoints.US, "https://api.newrelic.com/graphql");
  assert.equal(CONTRACT.endpoints.EU, "https://api.eu.newrelic.com/graphql");
  assert.equal(CONTRACT.auth.header, "API-Key");
  assert.equal(CONTRACT.entity_search.page_cap_entities, 200);
  assert.equal(CONTRACT.limits.concurrent_requests_per_user, 25);
  assert.equal(CONTRACT.limits.over_limit_status, 429);
});
