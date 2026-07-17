// Acceptance harness: loopback fake Oracle Fusion Cloud HCM pod exercising
// the work-relationship child-collection walker against the wire contract
// pinned in docs/contract.json (finder/q serialization, items/count/hasMore/
// limit/offset paging with server-clamped limits and unstable totals, link
// normalization, durable checkpoint resume after a page failure, structured
// application/vnd.oracle.adf.error+json errors).
// No real tenant, no real credentials, no sleeps.
// Run with: node --test test_worker_children.ts
// Protected — do not modify this file or anything under docs/.

import { after, before, test } from "node:test";
import assert from "node:assert/strict";
import http from "node:http";
import fs from "node:fs";
import path from "node:path";

import { buildChildQuery } from "./hcm/query.ts";
import { HcmClient, OraclePageError } from "./hcm/client.ts";
import { FileCheckpointStore } from "./hcm/checkpoint.ts";
import { walkChildCollection } from "./hcm/walker.ts";

const BASE = "/hcmRestApi/resources/11.13.18.05";
const WUID = "00020000000EACED0005TSW77";
const COLL = `${BASE}/workers/${WUID}/child/workRelationships`;
const USER = "HCM_RPT_SVC";
const PASS = "dummy-hcm-secret-55";
const AUTH = "Basic " + Buffer.from(`${USER}:${PASS}`).toString("base64");

const FIELDS = "PeriodOfServiceId,LegalEmployerName,WorkerType,StartDate";
const SERVER_CLAMP = 3;
const BOGUS_TOTAL = 999;

const CHECKPOINT_FILE = path.join("checkpoints_tmp", "walker-state.json");

type Row = { PeriodOfServiceId: number; LegalEmployerName: string; WorkerType: string; StartDate: string };

const ROWS: Row[] = [
  { PeriodOfServiceId: 4001, LegalEmployerName: "Vertex Staffing", WorkerType: "E", StartDate: "2012-05-01" },
  { PeriodOfServiceId: 4002, LegalEmployerName: "Vertex Staffing", WorkerType: "E", StartDate: "2013-02-11" },
  { PeriodOfServiceId: 4003, LegalEmployerName: "Vertex Global Services", WorkerType: "E", StartDate: "2014-09-01" },
  { PeriodOfServiceId: 4004, LegalEmployerName: "Vertex Global Services", WorkerType: "E", StartDate: "2016-01-15" },
  { PeriodOfServiceId: 4005, LegalEmployerName: "Vertex Field Ops", WorkerType: "E", StartDate: "2017-06-01" },
  { PeriodOfServiceId: 4006, LegalEmployerName: "Vertex Field Ops", WorkerType: "E", StartDate: "2019-03-18" },
  { PeriodOfServiceId: 4007, LegalEmployerName: "Vertex Global Services", WorkerType: "E", StartDate: "2021-11-01" },
  { PeriodOfServiceId: 4008, LegalEmployerName: "Vertex Global Services", WorkerType: "E", StartDate: "2024-04-07" },
];
const INSERTED: Row = { PeriodOfServiceId: 4000, LegalEmployerName: "Vertex Staffing", WorkerType: "E", StartDate: "2011-01-10" };

const Q1 = `q=WorkerType%3D%27E%27&fields=${FIELDS}&totalResults=true&limit=10`;
const Q2 = `finder=findByPeriodOfServiceId;PeriodOfServiceId=4005&q=WorkerType%3D%27E%27&fields=${FIELDS}&totalResults=true&limit=10`;
const Q3 = `q=StartDate%3E%272015-01-01%27&fields=${FIELDS}&totalResults=true&limit=10`;

type Seen = { method: string; url: string; auth?: string; framework?: string; accept?: string };
const LOG: Seen[] = [];

const STATE = {
  s1Requests: 0, // after the first S1 page the pod "inserts" a row at the front
  s3FailOnceLeft: 1,
};

let origin = "";
let server: http.Server;

function itemJson(row: Row): Record<string, unknown> {
  return {
    ...row,
    links: [
      { rel: "self", href: `${origin}${COLL}/${row.PeriodOfServiceId}`, name: "workRelationships", kind: "item" },
      { rel: "canonical", href: `${origin}${COLL}/${row.PeriodOfServiceId}`, name: "workRelationships", kind: "item" },
    ],
  };
}

function page(rows: Row[], offset: number, clamp = SERVER_CLAMP) {
  const slice = rows.slice(offset, offset + clamp);
  return {
    items: slice.map(itemJson),
    count: slice.length,
    hasMore: offset + slice.length < rows.length,
    limit: clamp,
    offset,
    totalResults: BOGUS_TOTAL,
    links: [{ rel: "self", href: `${origin}${COLL}`, name: "workRelationships", kind: "collection" }],
  };
}

const SERVER_ERROR = {
  title: "Internal Server Error",
  status: "500",
  "o:errorDetails": [
    {
      detail: "ORA-04021: timeout occurred while waiting to lock object PER_PERIODS_OF_SERVICE.",
      "o:errorCode": "ORA-04021",
      "o:errorPath": "",
    },
  ],
};

function respond(res: http.ServerResponse, status: number, body: unknown, contentType = "application/json") {
  const raw = JSON.stringify(body);
  res.writeHead(status, {
    "content-type": contentType,
    "content-length": Buffer.byteLength(raw),
    "rest-framework-version": "4",
  });
  res.end(raw);
}

function route(url: string, res: http.ServerResponse) {
  const sep = url.indexOf("?");
  const reqPath = sep < 0 ? url : url.slice(0, sep);
  const query = sep < 0 ? "" : url.slice(sep + 1);
  if (reqPath !== COLL) {
    respond(res, 404, { title: "Not Found", status: "404", "o:errorDetails": [] },
      "application/vnd.oracle.adf.error+json");
    return;
  }
  // ---- S1: q-filtered walk; the pod gains a row after the first page
  for (const offset of [0, 3, 6]) {
    if (query === `${Q1}&offset=${offset}`) {
      STATE.s1Requests += 1;
      const rows = STATE.s1Requests === 1 ? ROWS : [INSERTED, ...ROWS];
      respond(res, 200, page(rows, offset));
      return;
    }
  }
  // ---- S2: finder combined with q
  if (query === `${Q2}&offset=0`) {
    const rows = ROWS.filter((r) => r.PeriodOfServiceId === 4005);
    respond(res, 200, {
      items: rows.map(itemJson),
      count: 1,
      hasMore: false,
      limit: SERVER_CLAMP,
      offset: 0,
      totalResults: BOGUS_TOTAL,
    });
    return;
  }
  // ---- S3: stable walk whose third page fails once, then drifts by one row
  for (const offset of [0, 3]) {
    if (query === `${Q3}&offset=${offset}`) {
      respond(res, 200, page(ROWS, offset));
      return;
    }
  }
  if (query === `${Q3}&offset=6`) {
    if (STATE.s3FailOnceLeft > 0) {
      STATE.s3FailOnceLeft -= 1;
      respond(res, 500, SERVER_ERROR, "application/vnd.oracle.adf.error+json");
      return;
    }
    // resumed page re-serves an already-delivered row (4006) before the tail
    const drifted = ROWS.slice(5, 8);
    respond(res, 200, {
      items: drifted.map(itemJson),
      count: drifted.length,
      hasMore: false,
      limit: SERVER_CLAMP,
      offset: 6,
      totalResults: BOGUS_TOTAL,
    });
    return;
  }
  respond(res, 404, {
    title: "Not Found",
    status: "404",
    "o:errorDetails": [{ detail: `unpinned request: ${url}`, "o:errorCode": "TEST-0000", "o:errorPath": "" }],
  }, "application/vnd.oracle.adf.error+json");
}

before(async () => {
  fs.rmSync("checkpoints_tmp", { recursive: true, force: true });
  server = http.createServer((req, res) => {
    LOG.push({
      method: req.method ?? "",
      url: req.url ?? "",
      auth: req.headers["authorization"],
      framework: req.headers["rest-framework-version"] as string | undefined,
      accept: req.headers["accept"],
    });
    route(req.url ?? "", res);
  });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const addr = server.address();
  if (addr === null || typeof addr === "string") throw new Error("no port");
  origin = `http://127.0.0.1:${addr.port}`;
});

after(() => {
  server.close();
});

function checkHeaders(window: Seen[], label: string) {
  for (const seen of window) {
    assert.equal(seen.auth, AUTH, `${label}: Authorization on every request`);
    assert.equal(seen.framework, "4", `${label}: REST-Framework-Version on every request`);
    assert.equal(seen.accept, "application/json", `${label}: Accept on every request`);
  }
}

function readState(): Record<string, { offset: number; seenIds: number[] }> {
  return JSON.parse(fs.readFileSync(CHECKPOINT_FILE, "utf8"));
}

// --------------------------------------------------------------- U. builder

test("U: query serialization", () => {
  assert.equal(
    buildChildQuery({ q: ["WorkerType='E'"], fields: FIELDS.split(","), totalResults: true, limit: 10, offset: 0 }),
    `${Q1}&offset=0`,
  );
  assert.equal(
    buildChildQuery({ finder: { name: "findByPeriodOfServiceId", vars: { PeriodOfServiceId: 4005 } } }),
    "finder=findByPeriodOfServiceId;PeriodOfServiceId=4005",
  );
  assert.equal(
    buildChildQuery({ finder: { name: "findReports", vars: { AssignmentName: "Senior Analyst", DirectReportsFlag: true } } }),
    "finder=findReports;AssignmentName=Senior%20Analyst,DirectReportsFlag=true",
  );
  assert.equal(
    buildChildQuery({
      finder: { name: "findByPeriodOfServiceId", vars: { PeriodOfServiceId: 4005 } },
      q: ["WorkerType='E'"],
      fields: FIELDS.split(","),
      totalResults: true,
      limit: 10,
      offset: 0,
    }),
    `${Q2}&offset=0`,
  );
  assert.equal(
    buildChildQuery({ q: ["WorkerType='E'", "PrimaryFlag=true"] }),
    "q=WorkerType%3D%27E%27%3BPrimaryFlag%3Dtrue",
  );
  assert.equal(buildChildQuery({ onlyData: true, orderBy: "StartDate:desc" }), "onlyData=true&orderBy=StartDate:desc");
  assert.equal(buildChildQuery({}), "");
});

// ------------------------------------------------- S1. walk with moving rows

test("S1: offset walk dedupes when the collection grows mid-walk", async () => {
  LOG.length = 0;
  const client = new HcmClient(origin, USER, PASS);
  const store = new FileCheckpointStore(CHECKPOINT_FILE);
  const result = await walkChildCollection(client, {
    path: COLL,
    query: { q: ["WorkerType='E'"], fields: FIELDS.split(","), totalResults: true, limit: 10 },
    store,
  });

  assert.equal(LOG.length, 3, "exactly three page requests");
  assert.deepEqual(
    LOG.map((s) => s.url),
    [`${COLL}?${Q1}&offset=0`, `${COLL}?${Q1}&offset=3`, `${COLL}?${Q1}&offset=6`],
    "offset advances by the SERVER-returned count (clamped to 3), not the requested limit",
  );
  checkHeaders(LOG, "S1");

  assert.equal(result.items.length, 8, "eight distinct rows despite totalResults=999 and a mid-walk insert");
  assert.deepEqual(
    result.items.map((it: any) => it.id),
    [4001, 4002, 4003, 4004, 4005, 4006, 4007, 4008],
  );
  assert.equal(result.duplicatesSkipped, 1, "the re-served row is skipped exactly once");
  assert.equal(result.pages, 3);
  assert.equal(result.resumedFromOffset, null, "fresh walk starts without a checkpoint");
  assert.equal(result.estimatedTotalResults, BOGUS_TOTAL, "estimate surfaced verbatim, never used as a bound");

  const first: any = result.items[0];
  assert.equal(first.data.LegalEmployerName, "Vertex Staffing");
  assert.equal(first.data.StartDate, "2012-05-01");
  assert.equal(first.data.links, undefined, "data carries attributes only — links are normalized away");
  assert.equal(first.self, `${COLL}/4001`, "absolute self href normalized to a path");
  assert.equal(first.canonical, `${COLL}/4001`, "absolute canonical href normalized to a path");

  const state = fs.existsSync(CHECKPOINT_FILE) ? readState() : {};
  assert.deepEqual(Object.keys(state), [], "completed walk leaves no checkpoint behind");
});

// ------------------------------------------------------ S2. finder + q walk

test("S2: finder and q combine in one request", async () => {
  LOG.length = 0;
  const client = new HcmClient(origin, USER, PASS);
  const store = new FileCheckpointStore(CHECKPOINT_FILE);
  const result = await walkChildCollection(client, {
    path: COLL,
    query: {
      finder: { name: "findByPeriodOfServiceId", vars: { PeriodOfServiceId: 4005 } },
      q: ["WorkerType='E'"],
      fields: FIELDS.split(","),
      totalResults: true,
      limit: 10,
    },
    store,
  });

  assert.equal(LOG.length, 1);
  assert.equal(LOG[0].url, `${COLL}?${Q2}&offset=0`);
  checkHeaders(LOG, "S2");
  assert.equal(result.items.length, 1);
  assert.equal((result.items[0] as any).id, 4005);
  assert.equal((result.items[0] as any).data.LegalEmployerName, "Vertex Field Ops");
});

// ------------------------------------------- S3. page failure and resumption

test("S3: page failure checkpoints durably, resume completes without re-emitting", async () => {
  LOG.length = 0;
  const client = new HcmClient(origin, USER, PASS);
  const store = new FileCheckpointStore(CHECKPOINT_FILE);
  const opts = {
    path: COLL,
    query: { q: ["StartDate>'2015-01-01'"], fields: FIELDS.split(","), totalResults: true, limit: 10 },
    store,
  };

  let failure: OraclePageError | undefined;
  try {
    await walkChildCollection(client, opts);
  } catch (err) {
    failure = err as OraclePageError;
  }
  assert.ok(failure instanceof OraclePageError, "third page's 500 surfaces as OraclePageError");
  assert.equal(failure!.httpStatus, 500);
  assert.equal(failure!.title, "Internal Server Error");
  assert.equal(failure!.errorStatus, "500");
  assert.equal(failure!.details.length, 1, "o:errorDetails preserved");
  assert.equal(failure!.details[0].errorCode, "ORA-04021");
  assert.match(failure!.details[0].detail, /timeout occurred/);
  assert.ok(!String(failure!.message).includes(PASS), "no credentials in error text");
  assert.deepEqual(
    LOG.map((s) => s.url),
    [`${COLL}?${Q3}&offset=0`, `${COLL}?${Q3}&offset=3`, `${COLL}?${Q3}&offset=6`],
  );

  const key = `${COLL}?${Q3}`;
  const state = readState();
  assert.deepEqual(Object.keys(state), [key], "checkpoint keyed by collection path + full query");
  assert.equal(state[key].offset, 6, "checkpoint holds the offset of the FAILED page");
  assert.deepEqual(
    [...state[key].seenIds].sort((a, b) => a - b),
    [4001, 4002, 4003, 4004, 4005, 4006],
    "checkpoint remembers every id already delivered",
  );

  // ---- second run: resume from the durable checkpoint
  LOG.length = 0;
  const resumedStore = new FileCheckpointStore(CHECKPOINT_FILE); // fresh process simulation
  const result = await walkChildCollection(client, { ...opts, store: resumedStore });

  assert.deepEqual(
    LOG.map((s) => s.url),
    [`${COLL}?${Q3}&offset=6`],
    "resume continues at the checkpointed offset — no page is refetched from zero",
  );
  checkHeaders(LOG, "S3-resume");
  assert.equal(result.resumedFromOffset, 6);
  assert.deepEqual(
    result.items.map((it: any) => it.id),
    [4007, 4008],
    "rows delivered before the failure are not re-emitted even though the pod re-served one",
  );
  assert.equal(result.duplicatesSkipped, 1);
  assert.equal(result.pages, 1);

  const finalState = fs.existsSync(CHECKPOINT_FILE) ? readState() : {};
  assert.deepEqual(Object.keys(finalState), [], "completed resume clears its checkpoint");
});
