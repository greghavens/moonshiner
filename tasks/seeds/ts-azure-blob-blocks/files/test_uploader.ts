// Protected acceptance tests for the block-blob uploader.
// Hermetic: a loopback node:http server plays the Blob service; nothing
// leaves 127.0.0.1 and the bearer token is a dummy.
import { test } from "node:test";
import assert from "node:assert/strict";
import { createServer, type IncomingMessage, type Server, type ServerResponse } from "node:http";

import {
  BlobHttpError,
  BlockBlobClient,
  PreconditionFailedError,
  makeBlockId,
} from "./uploader.ts";

const TOKEN = "dummy-storage-token";
const SERVICE_VERSION = "2026-04-06";
const CONTAINER = "vault-backups";
const BLOB = "nightly-2026-07-16.tar";
const BLOB_PATH = `/${CONTAINER}/${BLOB}`;

interface Recorded {
  method: string;
  rawUrl: string;
  path: string;
  query: URLSearchParams;
  headers: NodeJS.Dict<string | string[]>;
  body: Buffer;
}

function errorXml(code: string, message: string): string {
  return `<?xml version="1.0" encoding="utf-8"?><Error><Code>${code}</Code><Message>${message}</Message></Error>`;
}

class MockBlobService {
  server: Server;
  base = "";
  requests: Recorded[] = [];
  uncommitted = new Map<string, Buffer>();
  committed: Array<{ id: string; content: Buffer }> = [];
  etag: string | null = null;
  contentType: string | null = null;
  failBlock: { id: string; status: number; code: string } | null = null;
  private etagCounter = 0;

  constructor() {
    this.server = createServer((req, res) => this.dispatch(req, res));
  }

  async start(): Promise<void> {
    await new Promise<void>((resolve) => this.server.listen(0, "127.0.0.1", resolve));
    const address = this.server.address();
    if (address === null || typeof address === "string") throw new Error("no port");
    this.base = `http://127.0.0.1:${address.port}`;
  }

  async stop(): Promise<void> {
    await new Promise<void>((resolve, reject) =>
      this.server.close((err) => (err ? reject(err) : resolve())),
    );
  }

  seedCommitted(blocks: Array<{ id: string; content: Buffer }>): void {
    this.committed = blocks;
    this.etag = `"etag-${++this.etagCounter}"`;
  }

  get blobBytes(): Buffer {
    return Buffer.concat(this.committed.map((b) => b.content));
  }

  private dispatch(req: IncomingMessage, res: ServerResponse): void {
    const chunks: Buffer[] = [];
    req.on("data", (chunk: Buffer) => chunks.push(chunk));
    req.on("end", () => {
      const url = new URL(req.url ?? "/", "http://127.0.0.1");
      const recorded: Recorded = {
        method: req.method ?? "",
        rawUrl: req.url ?? "",
        path: url.pathname,
        query: url.searchParams,
        headers: req.headers,
        body: Buffer.concat(chunks),
      };
      this.requests.push(recorded);
      this.route(recorded, res);
    });
  }

  private route(r: Recorded, res: ServerResponse): void {
    if (r.path !== BLOB_PATH) {
      this.answer(res, 404, errorXml("BlobNotFound", r.path));
      return;
    }
    const comp = r.query.get("comp");
    if (r.method === "PUT" && comp === "block") {
      this.putBlock(r, res);
    } else if (r.method === "PUT" && comp === "blocklist") {
      this.putBlockList(r, res);
    } else if (r.method === "GET" && comp === "blocklist") {
      this.getBlockList(r, res);
    } else {
      this.answer(res, 400, errorXml("InvalidQueryParameterValue", String(comp)));
    }
  }

  private putBlock(r: Recorded, res: ServerResponse): void {
    const blockId = r.query.get("blockid") ?? "";
    if (this.failBlock !== null && this.failBlock.id === blockId) {
      this.answer(res, this.failBlock.status, errorXml(this.failBlock.code, "scripted failure"));
      return;
    }
    this.uncommitted.set(blockId, r.body);
    this.answer(res, 201, "");
  }

  private putBlockList(r: Recorded, res: ServerResponse): void {
    const ifMatch = r.headers["if-match"];
    if (typeof ifMatch === "string" && (this.etag === null || ifMatch !== this.etag)) {
      this.answer(res, 412, errorXml("ConditionNotMet", "The condition specified using HTTP conditional header(s) is not met."));
      return;
    }
    const ifNoneMatch = r.headers["if-none-match"];
    if (ifNoneMatch === "*" && this.etag !== null) {
      this.answer(res, 412, errorXml("ConditionNotMet", "The condition specified using HTTP conditional header(s) is not met."));
      return;
    }
    const ids = [...r.body.toString("utf8").matchAll(/<Latest>([^<]+)<\/Latest>/g)].map((m) => m[1]);
    const previous = new Map(this.committed.map((b) => [b.id, b.content]));
    const next: Array<{ id: string; content: Buffer }> = [];
    for (const id of ids) {
      // Latest: uncommitted list first, committed list second.
      const content = this.uncommitted.get(id) ?? previous.get(id);
      if (content === undefined) {
        this.answer(res, 400, errorXml("InvalidBlockList", id));
        return;
      }
      next.push({ id, content });
    }
    this.committed = next;
    this.uncommitted.clear();
    this.etag = `"etag-${++this.etagCounter}"`;
    const declaredType = r.headers["x-ms-blob-content-type"];
    this.contentType = typeof declaredType === "string" ? declaredType : null;
    this.answer(res, 201, "", { ETag: this.etag });
  }

  private getBlockList(r: Recorded, res: ServerResponse): void {
    const kind = r.query.get("blocklisttype") ?? "committed";
    const blockXml = (id: string, content: Buffer) =>
      `<Block><Name>${id}</Name><Size>${content.length}</Size></Block>`;
    let inner = "";
    if (kind === "committed" || kind === "all") {
      inner += `<CommittedBlocks>${this.committed.map((b) => blockXml(b.id, b.content)).join("")}</CommittedBlocks>`;
    }
    if (kind === "uncommitted" || kind === "all") {
      const ids = [...this.uncommitted.keys()].sort();
      inner += `<UncommittedBlocks>${ids.map((id) => blockXml(id, this.uncommitted.get(id)!)).join("")}</UncommittedBlocks>`;
    }
    const headers: Record<string, string> = {};
    if (this.etag !== null) headers.ETag = this.etag;
    this.answer(res, 200, `<?xml version="1.0" encoding="utf-8"?><BlockList>${inner}</BlockList>`, headers);
  }

  private answer(res: ServerResponse, status: number, body: string, headers: Record<string, string> = {}): void {
    res.writeHead(status, {
      "Content-Type": "application/xml",
      "x-ms-request-id": "00000000-0000-0000-0000-000000000000",
      "x-ms-version": SERVICE_VERSION,
      ...headers,
    });
    res.end(body);
  }
}

function clientFor(mock: MockBlobService): BlockBlobClient {
  return new BlockBlobClient({
    endpoint: mock.base,
    container: CONTAINER,
    blob: BLOB,
    token: TOKEN,
  });
}

function payload(size: number): Uint8Array {
  const data = new Uint8Array(size);
  for (let i = 0; i < size; i++) data[i] = (i * 31 + 7) % 256;
  return data;
}

function decodeBlockId(encoded: string): string {
  return Buffer.from(encoded, "base64").toString("utf8");
}

function assertServiceHeaders(r: Recorded): void {
  assert.equal(r.headers["x-ms-version"], SERVICE_VERSION, `${r.method} ${r.rawUrl} x-ms-version`);
  assert.equal(r.headers["authorization"], `Bearer ${TOKEN}`, `${r.method} ${r.rawUrl} auth`);
  const date = r.headers["x-ms-date"] ?? r.headers["date"];
  assert.ok(typeof date === "string" && date.endsWith("GMT"),
    `${r.method} ${r.rawUrl} needs an RFC 1123 x-ms-date/Date header, got ${String(date)}`);
}

async function withMock(fn: (mock: MockBlobService) => Promise<void>): Promise<void> {
  const mock = new MockBlobService();
  await mock.start();
  try {
    await fn(mock);
  } finally {
    await mock.stop();
  }
}

test("block ids are stable, same-length, valid base64", () => {
  assert.equal(decodeBlockId(makeBlockId(0)), "block-00000000");
  assert.equal(decodeBlockId(makeBlockId(42)), "block-00000042");
  assert.equal(decodeBlockId(makeBlockId(7)), "block-00000007");
  const lengths = new Set([0, 1, 7, 42, 999, 12345678].map((i) => makeBlockId(i).length));
  assert.equal(lengths.size, 1, "every encoded block id must have the same length");
  const preEncoded = decodeBlockId(makeBlockId(12345678));
  assert.ok(preEncoded.length <= 64, "pre-encoded id must stay within 64 bytes");
});

test("fresh upload stages ordered blocks and commits the block list", async () => {
  await withMock(async (mock) => {
    const data = payload(2500);
    const result = await clientFor(mock).upload(data, {
      blockSize: 1024,
      contentType: "application/x-tar",
    });

    assert.equal(mock.requests.length, 4, "3 Put Block calls + 1 Put Block List");
    assert.ok(mock.requests.every((r) => r.method === "PUT"), "a fresh upload never lists blocks");
    for (const r of mock.requests) assertServiceHeaders(r);

    const stages = mock.requests.slice(0, 3);
    const expectedIds = [makeBlockId(0), makeBlockId(1), makeBlockId(2)];
    stages.forEach((r, i) => {
      assert.equal(r.query.get("comp"), "block");
      const blockId = r.query.get("blockid") ?? "";
      assert.equal(blockId, expectedIds[i], "blocks staged in ascending order");
      assert.equal(decodeBlockId(blockId), `block-0000000${i}`);
      assert.ok(r.rawUrl.includes("%3D"), "base64 padding must be URL-encoded in the query");
      const expectedChunk = Buffer.from(data.slice(i * 1024, (i + 1) * 1024));
      assert.deepEqual(r.body, expectedChunk, `block ${i} body`);
      assert.equal(r.headers["content-length"], String(expectedChunk.length));
      assert.equal(r.headers["if-match"], undefined, "Put Block supports no conditional headers");
      assert.equal(r.headers["if-none-match"], undefined, "Put Block supports no conditional headers");
    });

    const commit = mock.requests[3];
    assert.equal(commit.query.get("comp"), "blocklist");
    const xml = commit.body.toString("utf8");
    assert.ok(xml.startsWith(`<?xml version="1.0" encoding="utf-8"?>`), "commit body needs the XML declaration");
    assert.ok(xml.includes("<BlockList>"), xml);
    const committedIds = [...xml.matchAll(/<Latest>([^<]+)<\/Latest>/g)].map((m) => m[1]);
    assert.deepEqual(committedIds, expectedIds, "commit order defines blob content");
    assert.ok(!/<(Committed|Uncommitted)>/.test(xml), "fresh uploads commit via Latest entries");
    assert.equal(commit.headers["x-ms-blob-content-type"], "application/x-tar");

    assert.deepEqual(mock.blobBytes, Buffer.from(data), "committed bytes match the payload");
    assert.equal(mock.contentType, "application/x-tar");
    assert.equal(mock.uncommitted.size, 0, "commit consumed every staged block");
    assert.equal(result.etag, mock.etag);
    assert.deepEqual(result.blockIds, expectedIds);
    assert.equal(result.blocksStaged, 3);
    assert.equal(result.blocksReused, 0);
  });
});

test("If-Match guards the commit and never leaks onto Put Block", async () => {
  await withMock(async (mock) => {
    mock.seedCommitted([{ id: makeBlockId(0), content: Buffer.from("old-contents") }]);
    const guard = mock.etag!;
    const data = payload(600);

    const result = await clientFor(mock).upload(data, { blockSize: 512, ifMatch: guard });

    const commit = mock.requests.at(-1)!;
    assert.equal(commit.query.get("comp"), "blocklist");
    assert.equal(commit.headers["if-match"], guard);
    for (const r of mock.requests.filter((x) => x.query.get("comp") === "block")) {
      assert.equal(r.headers["if-match"], undefined);
    }
    assert.notEqual(result.etag, guard, "successful commit returns the new ETag");
    assert.deepEqual(mock.blobBytes, Buffer.from(data));
  });
});

test("a changed blob fails the If-Match commit with ConditionNotMet and nothing is overwritten", async () => {
  await withMock(async (mock) => {
    mock.seedCommitted([{ id: makeBlockId(0), content: Buffer.from("existing-v2") }]);
    const staleGuard = '"etag-0-stale"';
    const before = mock.etag;

    const err = await clientFor(mock)
      .upload(payload(300), { blockSize: 256, ifMatch: staleGuard })
      .then(() => null, (e: unknown) => e);

    assert.ok(err instanceof PreconditionFailedError, String(err));
    assert.ok(err instanceof BlobHttpError, "PreconditionFailedError extends BlobHttpError");
    assert.equal(err.status, 412);
    assert.equal(err.code, "ConditionNotMet");
    assert.equal(mock.etag, before, "ETag unchanged");
    assert.deepEqual(mock.blobBytes, Buffer.from("existing-v2"), "blob content unchanged");
  });
});

test("create-only uploads use If-None-Match star", async () => {
  await withMock(async (mock) => {
    const data = payload(100);
    await clientFor(mock).upload(data, { blockSize: 256, ifNoneMatchStar: true });
    const commit = mock.requests.at(-1)!;
    assert.equal(commit.headers["if-none-match"], "*");
    assert.deepEqual(mock.blobBytes, Buffer.from(data));
  });

  await withMock(async (mock) => {
    mock.seedCommitted([{ id: makeBlockId(0), content: Buffer.from("already-there") }]);
    const err = await clientFor(mock)
      .upload(payload(100), { blockSize: 256, ifNoneMatchStar: true })
      .then(() => null, (e: unknown) => e);
    assert.ok(err instanceof PreconditionFailedError, String(err));
    assert.deepEqual(mock.blobBytes, Buffer.from("already-there"));
  });
});

test("resume stages only missing or mismatched blocks and commits the full list", async () => {
  await withMock(async (mock) => {
    const data = payload(4196); // blocks of 1024: 0..3 full, 4 short (100 bytes)
    const chunk = (i: number) => Buffer.from(data.slice(i * 1024, Math.min((i + 1) * 1024, data.length)));
    // A previous run staged blocks 0, 2 and 4 -- but block 2 died mid-transfer
    // and has the wrong size on the service.
    mock.uncommitted.set(makeBlockId(0), chunk(0));
    mock.uncommitted.set(makeBlockId(2), chunk(2).subarray(0, 100));
    mock.uncommitted.set(makeBlockId(4), chunk(4));

    const result = await clientFor(mock).upload(data, { blockSize: 1024, resume: true });

    const first = mock.requests[0];
    assert.equal(first.method, "GET", "resume starts by listing blocks");
    assert.equal(first.query.get("comp"), "blocklist");
    assert.equal(first.query.get("blocklisttype"), "uncommitted");
    assertServiceHeaders(first);

    const staged = mock.requests.filter((r) => r.method === "PUT" && r.query.get("comp") === "block");
    assert.deepEqual(
      staged.map((r) => decodeBlockId(r.query.get("blockid") ?? "")),
      ["block-00000001", "block-00000002", "block-00000003"],
      "reuses intact blocks 0 and 4, re-stages the size-mismatched block 2",
    );

    const commit = mock.requests.at(-1)!;
    const committedIds = [...commit.body.toString("utf8").matchAll(/<Latest>([^<]+)<\/Latest>/g)]
      .map((m) => m[1]);
    assert.deepEqual(committedIds, [0, 1, 2, 3, 4].map(makeBlockId));

    assert.deepEqual(mock.blobBytes, Buffer.from(data), "resumed blob is byte-identical");
    assert.equal(result.blocksStaged, 3);
    assert.equal(result.blocksReused, 2);
  });
});

test("a failed Put Block surfaces the storage error and aborts before commit", async () => {
  await withMock(async (mock) => {
    mock.failBlock = { id: makeBlockId(1), status: 413, code: "RequestBodyTooLarge" };
    const err = await clientFor(mock)
      .upload(payload(3000), { blockSize: 1024 })
      .then(() => null, (e: unknown) => e);

    assert.ok(err instanceof BlobHttpError, String(err));
    assert.ok(!(err instanceof PreconditionFailedError));
    assert.equal(err.status, 413);
    assert.equal(err.code, "RequestBodyTooLarge");
    assert.ok(
      mock.requests.every((r) => r.query.get("comp") !== "blocklist" || r.method === "GET"),
      "no commit after a failed stage",
    );
    assert.equal(mock.committed.length, 0);
  });
});
