// Acceptance tests for the Splunk KV Store client (src/index.ts).
//
// Runs a loopback fake splunkd serving the KV Store collection-data wire
// contract pinned in docs/contract.json (servicesNS data paths, query/
// sort/skip/limit/fields encoding, key-aware updates, batch_save with the
// documented batch limits, messages error envelopes). No real Splunk, no
// real credentials, no sleeps. This file and everything under docs/ are
// protected.

import { test } from "node:test";
import assert from "node:assert/strict";
import http from "node:http";
import { readFileSync } from "node:fs";
import { KvStoreClient, SplunkKvError, BatchSaveError } from "./src/index.ts";

const CONTRACT = JSON.parse(readFileSync(new URL("./docs/contract.json", import.meta.url), "utf8"));
const SOURCES = JSON.parse(readFileSync(new URL("./docs/official_sources.json", import.meta.url), "utf8"));

const TOKEN: string = CONTRACT.auth.fixture_token; // dummy; must never leak
const APP = "floor_ops";
const DATA_ROOT = `/servicesNS/nobody/${APP}/storage/collections/data`;

type Recorded = {
  method: string;
  url: URL;
  rawUrl: string;
  headers: http.IncomingHttpHeaders;
  body: string;
};

type Scripted =
  | { kind: "json"; status: number; doc: unknown }
  | { kind: "auto" };

class FakeKvStore {
  requests: Recorded[] = [];
  script: Scripted[] = [];
  server: http.Server;
  baseUrl = "";
  private generated = 0;

  constructor() {
    this.server = http.createServer((req, res) => {
      let raw = "";
      req.on("data", (chunk) => (raw += chunk));
      req.on("end", () => {
        const url = new URL(req.url ?? "/", this.baseUrl);
        this.requests.push({
          method: req.method ?? "",
          url,
          rawUrl: req.url ?? "",
          headers: req.headers,
          body: raw,
        });
        const step = this.script.shift() ?? { kind: "auto" as const };
        let status = 200;
        let doc: unknown;
        if (step.kind === "json") {
          status = step.status;
          doc = step.doc;
        } else if (url.pathname.endsWith("/batch_save")) {
          // default: echo one key per posted document, like splunkd
          const docs = JSON.parse(raw) as Array<Record<string, unknown>>;
          doc = docs.map((d) =>
            typeof d._key === "string" ? d._key : `gen-${this.generated++}`);
        } else if (req.method === "POST") {
          doc = { _key: `gen-${this.generated++}` };
        } else {
          doc = [];
        }
        const payload = JSON.stringify(doc);
        res.writeHead(status, { "content-type": "application/json" });
        res.end(payload);
      });
    });
  }

  listen(): Promise<void> {
    return new Promise((resolve) => {
      this.server.listen(0, "127.0.0.1", () => {
        const addr = this.server.address();
        if (addr === null || typeof addr === "string") throw new Error("no port");
        this.baseUrl = `http://127.0.0.1:${addr.port}`;
        resolve();
      });
    });
  }

  close(): Promise<void> {
    return new Promise((resolve) => this.server.close(() => resolve()));
  }
}

async function withFake(
  fn: (fake: FakeKvStore, client: KvStoreClient) => Promise<void>,
): Promise<void> {
  const fake = new FakeKvStore();
  await fake.listen();
  try {
    const client = new KvStoreClient({
      baseUrl: fake.baseUrl,
      token: TOKEN,
      app: APP,
    });
    await fn(fake, client);
  } finally {
    await fake.close();
  }
}

test("query encodes the documented query options", async () => {
  await withFake(async (fake, client) => {
    fake.script.push({
      kind: "json",
      status: 200,
      doc: [
        { _key: "b7", _user: "nobody", bay: 7, site: "osl" },
        { _key: "b6", _user: "nobody", bay: 6, site: "osl" },
      ],
    });
    const rows = await client.query("asset_checkpoints", {
      query: { bay: { $gt: 5 }, site: "osl" },
      sort: "bay:-1,site",
      skip: 10,
      limit: 5,
      fields: "bay,site,_key",
    });
    assert.equal(rows.length, 2);
    assert.equal(rows[0]._key, "b7");
    assert.equal(fake.requests.length, 1);
    const req = fake.requests[0];
    assert.equal(req.method, "GET");
    assert.equal(req.url.pathname, `${DATA_ROOT}/asset_checkpoints`);
    assert.equal(req.headers.authorization, `Bearer ${TOKEN}`);
    assert.equal(req.url.searchParams.get("query"), '{"bay":{"$gt":5},"site":"osl"}');
    assert.equal(req.url.searchParams.get("sort"), "bay:-1,site");
    assert.equal(req.url.searchParams.get("skip"), "10");
    assert.equal(req.url.searchParams.get("limit"), "5");
    assert.equal(req.url.searchParams.get("fields"), "bay,site,_key");
    assert.ok(!req.rawUrl.includes(TOKEN), "token must never be in a URL");
  });
});

test("query omits options that were not given", async () => {
  await withFake(async (fake, client) => {
    await client.query("asset_checkpoints", {});
    const params = fake.requests[0].url.searchParams;
    for (const name of ["query", "sort", "skip", "limit", "fields"]) {
      assert.ok(!params.has(name), `${name} must be omitted, not sent empty`);
    }
  });
});

test("insert posts JSON and returns the generated _key", async () => {
  await withFake(async (fake, client) => {
    fake.script.push({ kind: "json", status: 200, doc: { _key: "665f1c2e" } });
    const key = await client.insert("asset_checkpoints", { bay: 4, site: "trd" });
    assert.equal(key, "665f1c2e");
    const req = fake.requests[0];
    assert.equal(req.method, "POST");
    assert.equal(req.url.pathname, `${DATA_ROOT}/asset_checkpoints`);
    assert.match(String(req.headers["content-type"]), /^application\/json/);
    assert.deepEqual(JSON.parse(req.body), { bay: 4, site: "trd" });
  });
});

test("update replaces the whole record at its key-aware URL", async () => {
  await withFake(async (fake, client) => {
    fake.script.push({ kind: "json", status: 200, doc: { _key: "orders/2026-07#9" } });
    const key = await client.update("asset_checkpoints", "orders/2026-07#9", {
      bay: 4,
      site: "trd",
      swept: true,
    });
    assert.equal(key, "orders/2026-07#9");
    const req = fake.requests[0];
    assert.equal(req.method, "POST");
    assert.equal(
      req.url.pathname,
      `${DATA_ROOT}/asset_checkpoints/orders%2F2026-07%239`,
      "record keys must be URL-encoded into the path",
    );
    assert.deepEqual(JSON.parse(req.body), { bay: 4, site: "trd", swept: true },
      "update sends the full replacement document");
  });
});

test("remove issues a DELETE for the key", async () => {
  await withFake(async (fake, client) => {
    fake.script.push({ kind: "json", status: 200, doc: {} });
    await client.remove("asset_checkpoints", "b7");
    const req = fake.requests[0];
    assert.equal(req.method, "DELETE");
    assert.equal(req.url.pathname, `${DATA_ROOT}/asset_checkpoints/b7`);
  });
});

test("splunkd error envelopes become SplunkKvError", async () => {
  await withFake(async (fake, client) => {
    fake.script.push({
      kind: "json",
      status: 409,
      doc: { messages: [{ type: "ERROR", text: "An object with the same _key already exists in collection" }] },
    });
    await assert.rejects(
      client.insert("asset_checkpoints", { _key: "b7", bay: 7 }),
      (err: unknown) => {
        assert.ok(err instanceof SplunkKvError);
        assert.equal(err.status, 409);
        assert.equal(err.type, "ERROR");
        assert.equal(err.text, "An object with the same _key already exists in collection");
        assert.ok(!String(err.message).includes(TOKEN), "errors must not leak the token");
        return true;
      },
    );
  });
});

test("batchSave splits by the documented 1000-document default", async () => {
  await withFake(async (fake, client) => {
    const docs = Array.from({ length: 1001 }, (_, i) => ({ n: i }));
    const keys = await client.batchSave("asset_checkpoints", docs);
    assert.equal(keys.length, 1001);
    assert.equal(fake.requests.length, 2, "1001 docs = one full chunk + one remainder");
    const first = JSON.parse(fake.requests[0].body) as unknown[];
    const second = JSON.parse(fake.requests[1].body) as unknown[];
    assert.equal(first.length, 1000);
    assert.equal(second.length, 1);
    assert.equal(fake.requests[0].url.pathname, `${DATA_ROOT}/asset_checkpoints/batch_save`);
    assert.equal(fake.requests[0].method, "POST");
    assert.match(String(fake.requests[0].headers["content-type"]), /^application\/json/);
  });
});

test("batchSave also splits when a chunk would exceed the byte budget", async () => {
  await withFake(async (fake, client) => {
    const docs = [
      { _key: "a", note: "x".repeat(80) },
      { _key: "b", note: "y".repeat(80) },
      { _key: "c", note: "z".repeat(80) },
    ];
    const keys = await client.batchSave("asset_checkpoints", docs, { maxBytes: 250 });
    assert.deepEqual(keys, ["a", "b", "c"], "keys come back in submission order");
    assert.equal(fake.requests.length, 2, "third doc would push the JSON body over maxBytes");
    assert.equal((JSON.parse(fake.requests[0].body) as unknown[]).length, 2);
    assert.equal((JSON.parse(fake.requests[1].body) as unknown[]).length, 1);
    for (const req of fake.requests) {
      assert.ok(Buffer.byteLength(req.body, "utf8") <= 250,
        `chunk of ${Buffer.byteLength(req.body, "utf8")} bytes exceeds maxBytes`);
    }
  });
});

test("batchSave surfaces partial failure without abandoning earlier chunks", async () => {
  await withFake(async (fake, client) => {
    fake.script.push({ kind: "auto" });
    fake.script.push({
      kind: "json",
      status: 400,
      doc: { messages: [{ type: "ERROR", text: "Document field names cannot start with $" }] },
    });
    const docs = [
      { _key: "a1", n: 0 },
      { _key: "a2", n: 1 },
      { _key: "a3", n: 2 },
      { _key: "a4", n: 3 },
      { _key: "a5", n: 4 },
    ];
    await assert.rejects(
      client.batchSave("asset_checkpoints", docs, { maxDocs: 2 }),
      (err: unknown) => {
        assert.ok(err instanceof BatchSaveError);
        assert.deepEqual(err.savedKeys, ["a1", "a2"],
          "keys from chunks that splunkd accepted are preserved");
        assert.equal(err.failedAt, 2, "index of the first document in the failed chunk");
        assert.equal(err.remaining, 3, "failed-chunk plus never-attempted documents");
        assert.equal(err.status, 400);
        assert.equal(err.text, "Document field names cannot start with $");
        return true;
      },
    );
    assert.equal(fake.requests.length, 2, "no chunk is attempted after a failure");
  });
});

test("batchSave of nothing does nothing", async () => {
  await withFake(async (fake, client) => {
    const keys = await client.batchSave("asset_checkpoints", []);
    assert.deepEqual(keys, []);
    assert.equal(fake.requests.length, 0);
  });
});

test("exportAll pages by _key order and reports a resumable checkpoint", async () => {
  await withFake(async (fake, client) => {
    fake.script.push({
      kind: "json",
      status: 200,
      doc: [
        { _key: "k1", bay: 1 },
        { _key: "k2", bay: 2 },
      ],
    });
    fake.script.push({
      kind: "json",
      status: 200,
      doc: [{ _key: "k3", bay: 3 }],
    });
    const out = await client.exportAll("asset_checkpoints", { pageSize: 2 });
    assert.deepEqual(out.docs.map((d) => d._key), ["k1", "k2", "k3"]);
    assert.equal(out.checkpoint, "k3", "checkpoint is the last delivered _key");
    assert.equal(fake.requests.length, 2, "a short page ends the export");

    const first = fake.requests[0].url.searchParams;
    assert.equal(first.get("sort"), "_key",
      "stable exports must sort by _key ascending");
    assert.equal(first.get("limit"), "2");
    assert.ok(!first.has("query"), "no checkpoint yet, so no query filter");
    assert.ok(!first.has("skip"),
      "checkpointed export must not use skip-based paging (unstable under writes)");

    const second = fake.requests[1].url.searchParams;
    assert.equal(second.get("query"), '{"_key":{"$gt":"k2"}}',
      "later pages continue strictly after the last delivered key");
    assert.equal(second.get("sort"), "_key");
  });
});

test("exportAll resumes from a supplied checkpoint after a failure", async () => {
  await withFake(async (fake, client) => {
    fake.script.push({
      kind: "json",
      status: 200,
      doc: [
        { _key: "k1", bay: 1 },
        { _key: "k2", bay: 2 },
      ],
    });
    fake.script.push({
      kind: "json",
      status: 503,
      doc: { messages: [{ type: "ERROR", text: "KV Store is initializing. Please try again later." }] },
    });
    await assert.rejects(
      client.exportAll("asset_checkpoints", { pageSize: 2 }),
      (err: unknown) => {
        assert.ok(err instanceof SplunkKvError);
        assert.equal(err.status, 503);
        assert.equal(err.checkpoint, "k2",
          "a failed export still reports how far it verifiably got");
        return true;
      },
    );

    fake.requests.length = 0;
    fake.script.push({
      kind: "json",
      status: 200,
      doc: [{ _key: "k3", bay: 3 }],
    });
    const resumed = await client.exportAll("asset_checkpoints", {
      pageSize: 2,
      checkpoint: "k2",
    });
    assert.deepEqual(resumed.docs.map((d) => d._key), ["k3"]);
    assert.equal(resumed.checkpoint, "k3");
    assert.equal(
      fake.requests[0].url.searchParams.get("query"),
      '{"_key":{"$gt":"k2"}}',
      "resume starts strictly after the supplied checkpoint",
    );
  });
});

test("research provenance fixtures are present and coherent", () => {
  assert.equal(SOURCES.research.required, true);
  assert.ok(Array.isArray(SOURCES.research.official_sources));
  assert.ok(SOURCES.research.official_sources.length >= 2);
  assert.equal(typeof CONTRACT.operations, "object");
});
