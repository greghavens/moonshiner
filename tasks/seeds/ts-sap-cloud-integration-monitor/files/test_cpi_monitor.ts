// Acceptance tests for the SAP Cloud Integration message-processing-log
// monitor. A loopback fake tenant serves the OData API v2 wire contract
// pinned in docs/contract.json (d-envelope JSON, __next server paging,
// deferred navigation, OData error documents). No real tenant, no real
// credentials, no sleeps. Protected — do not modify this file, sap/client.ts's
// existing behavior contract, or anything under docs/.
import { test } from "node:test";
import assert from "node:assert/strict";
import http from "node:http";
import type { AddressInfo } from "node:net";

import { CpiClient, CpiError } from "./sap/client.ts";
import {
  buildLogsQuery,
  listAllLogs,
  normalizeLog,
  fetchErrorText,
  AuthorizationError,
  MonitorError,
} from "./sap/monitor.ts";

const USER = "cpi-api-client";
const PASS = "dummy-fixture-secret-9f2";
const AUTH = "Basic " + Buffer.from(`${USER}:${PASS}`).toString("base64");

interface Recorded {
  method: string;
  url: string;
  headers: http.IncomingHttpHeaders;
}

interface Scripted {
  status: number;
  body?: string;
  headers?: Record<string, string>;
}

class MockTenant {
  requests: Recorded[] = [];
  baseUrl = "";
  private server: http.Server;
  private serve: (n: number, req: Recorded) => Scripted;

  constructor(serve: (n: number, req: Recorded) => Scripted) {
    this.serve = serve;
    this.server = http.createServer((req, res) => {
      const rec: Recorded = { method: req.method ?? "", url: req.url ?? "", headers: req.headers };
      const n = this.requests.length;
      this.requests.push(rec);
      let s: Scripted;
      try {
        s = this.serve(n, rec);
      } catch (e) {
        s = { status: 599, body: String(e) };
      }
      res.writeHead(s.status, { "content-type": "application/json", ...(s.headers ?? {}) });
      res.end(s.body ?? "");
    });
  }

  start(): Promise<void> {
    return new Promise((resolve) => {
      this.server.listen(0, "127.0.0.1", () => {
        const { port } = this.server.address() as AddressInfo;
        this.baseUrl = `http://127.0.0.1:${port}`;
        resolve();
      });
    });
  }

  stop(): Promise<void> {
    return new Promise((resolve) => this.server.close(() => resolve()));
  }
}

function log(over: Record<string, unknown>): Record<string, unknown> {
  return {
    __metadata: {
      id: "MessageProcessingLogs('AGc1zdCsxbFcJgAvqB2nS9k_3s-U')",
      type: "com.sap.hci.api.MessageProcessingLog",
    },
    MessageGuid: "AGc1zdCsxbFcJgAvqB2nS9k_3s-U",
    CorrelationId: "AGc1zdA6qA5cD4wF0pXlHhY_2r-Q",
    ApplicationMessageId: null,
    ApplicationMessageType: null,
    IntegrationFlowName: "Replicate_Cost_Centers",
    Status: "COMPLETED",
    CustomStatus: "COMPLETED",
    LogLevel: "INFO",
    LogStart: "/Date(1767225600000)/",
    LogEnd: "/Date(1767225604250)/",
    Sender: "S4_QAS",
    Receiver: "WMS_EU",
    AlternateWebLink: "https://tenant.example.invalid/itspaces?messageGuid=AGc1zdCsxbFcJgAvqB2nS9k_3s-U",
    ErrorInformation: {
      __deferred: {
        uri: "PLACEHOLDER-SET-PER-TEST",
      },
    },
    ...over,
  };
}

function feed(results: unknown[], extra: Record<string, unknown> = {}): string {
  return JSON.stringify({ d: { results, ...extra } });
}

// ---------------------------------------------------------------- query building

test("buildLogsQuery pins the documented serialization exactly", () => {
  const q = buildLogsQuery({
    status: "FAILED",
    logStartAfter: "2026-06-01T00:00:00Z",
    logEndBefore: "2026-07-01T00:00:00Z",
    orderBy: { field: "LogEnd", desc: true },
    top: 50,
    countAll: true,
  });
  assert.equal(
    q,
    "$format=json" +
      "&$filter=Status%20eq%20'FAILED'%20and%20LogStart%20gt%20datetime'2026-06-01T00%3A00%3A00'%20and%20LogEnd%20lt%20datetime'2026-07-01T00%3A00%3A00'" +
      "&$orderby=LogEnd%20desc&$top=50&$inlinecount=allpages",
  );
});

test("datetime literals are un-zoned UTC — the API rejects zone offsets", () => {
  // Docs: "parsing of time zone information in datetime literals is not
  // supported. All datetime literals are interpreted as UTC."
  const q = buildLogsQuery({ logEndBefore: "2026-07-01T02:30:00+02:00" });
  assert.ok(q.includes("datetime'2026-07-01T00%3A30%3A00'"), `zone offset must be folded into UTC: ${q}`);
  assert.ok(!/datetime'[^']*Z'/.test(decodeURIComponent(q)), "no trailing Z inside a datetime literal");
  assert.ok(!/%2B\d\d%3A\d\d'/.test(q), "no +hh:mm offset inside a datetime literal");
});

test("filters compose in documented property order with %20 spacing", () => {
  const q = buildLogsQuery({
    status: "RETRY",
    customStatus: "WAITING_ON_WMS",
    integrationFlowName: "Replicate_Cost_Centers",
  });
  assert.equal(
    q,
    "$format=json&$filter=Status%20eq%20'RETRY'" +
      "%20and%20CustomStatus%20eq%20'WAITING_ON_WMS'" +
      "%20and%20IntegrationFlowName%20eq%20'Replicate_Cost_Centers'",
  );
  assert.ok(!q.includes(" "), "query string must be fully percent-encoded");
});

test("no options yields just the json format pin", () => {
  assert.equal(buildLogsQuery({}), "$format=json");
});

test("skip combines with top for client-driven iteration", () => {
  const q = buildLogsQuery({ top: 200, skip: 400 });
  assert.equal(q, "$format=json&$top=200&$skip=400");
});

// ---------------------------------------------------------------- paging

test("listAllLogs follows __next verbatim across three pages", async () => {
  const guids = ["M-000", "M-001", "M-002", "M-003", "M-004"];
  const mock = new MockTenant((n, req) => {
    if (req.headers.authorization !== AUTH) return { status: 401, body: "" };
    if (n === 0) {
      assert.ok(req.url.startsWith("/api/v1/MessageProcessingLogs?"), req.url);
      return {
        status: 200,
        body: feed(
          [log({ MessageGuid: guids[0] }), log({ MessageGuid: guids[1] })],
          {
            __count: "5",
            __next: `${mock.baseUrl}/api/v1/MessageProcessingLogs?$format=json&$skiptoken=2&srvMarker=keep-p1`,
          },
        ),
      };
    }
    if (n === 1) {
      // The next link must be used verbatim; a rebuilt URL loses srvMarker.
      if (!req.url.includes("srvMarker=keep-p1")) return { status: 500, body: "{}" };
      return {
        status: 200,
        body: feed(
          [log({ MessageGuid: guids[2] }), log({ MessageGuid: guids[3] })],
          { __next: `${mock.baseUrl}/api/v1/MessageProcessingLogs?$format=json&$skiptoken=4&srvMarker=keep-p2` },
        ),
      };
    }
    if (n === 2) {
      if (!req.url.includes("srvMarker=keep-p2")) return { status: 500, body: "{}" };
      return { status: 200, body: feed([log({ MessageGuid: guids[4] })]) };
    }
    return { status: 500, body: "{}" };
  });
  await mock.start();
  try {
    const client = new CpiClient(mock.baseUrl, USER, PASS);
    const { logs, totalCount } = await listAllLogs(client, { status: "COMPLETED", countAll: true });
    assert.equal(mock.requests.length, 3, "exactly one request per page");
    assert.deepEqual(logs.map((l) => l.messageGuid), guids);
    assert.equal(totalCount, 5, "__count from the first page, as a number");
    for (const r of mock.requests) {
      assert.equal(r.headers.authorization, AUTH, "basic auth on every page request");
    }
    assert.ok(mock.requests[1].url.includes("$skiptoken=2"), "skiptoken carried through");
  } finally {
    await mock.stop();
  }
});

test("a single page without __next makes exactly one request", async () => {
  const mock = new MockTenant(() => ({ status: 200, body: feed([log({})]) }));
  await mock.start();
  try {
    const client = new CpiClient(mock.baseUrl, USER, PASS);
    const { logs, totalCount } = await listAllLogs(client, {});
    assert.equal(logs.length, 1);
    assert.equal(totalCount, null, "no $inlinecount requested, no count");
    assert.equal(mock.requests.length, 1);
  } finally {
    await mock.stop();
  }
});

// ---------------------------------------------------------------- normalization

test("normalizeLog decodes /Date(ms)/ to ISO UTC and keeps identity fields", () => {
  const n = normalizeLog(log({}));
  assert.equal(n.messageGuid, "AGc1zdCsxbFcJgAvqB2nS9k_3s-U");
  assert.equal(n.correlationId, "AGc1zdA6qA5cD4wF0pXlHhY_2r-Q");
  assert.equal(n.flowName, "Replicate_Cost_Centers");
  assert.equal(n.status, "COMPLETED");
  assert.equal(n.customStatus, "COMPLETED");
  assert.equal(n.logStart, "2026-01-01T00:00:00.000Z");
  assert.equal(n.logEnd, "2026-01-01T00:00:04.250Z");
  assert.equal(n.sender, "S4_QAS");
  assert.equal(n.receiver, "WMS_EU");
});

test("a still-running log has no LogEnd yet", () => {
  const n = normalizeLog(log({ Status: "PROCESSING", LogEnd: null }));
  assert.equal(n.status, "PROCESSING");
  assert.equal(n.logEnd, null);
});

test("every documented message status is accepted", () => {
  for (const s of [
    "COMPLETED",
    "PROCESSING",
    "RETRY",
    "ESCALATED",
    "FAILED",
    "CANCELLED",
    "DISCARDED",
    "ABANDONED",
  ]) {
    assert.equal(normalizeLog(log({ Status: s, LogEnd: null })).status, s);
  }
});

test("an undocumented status is rejected, naming the value", () => {
  assert.throws(
    () => normalizeLog(log({ Status: "SHINY" })),
    (e: unknown) => e instanceof MonitorError && /SHINY/.test((e as Error).message),
  );
});

test("CustomStatus differs from Status and both are preserved", () => {
  const n = normalizeLog(log({ Status: "FAILED", CustomStatus: "REJECTED_BY_WMS" }));
  assert.equal(n.status, "FAILED");
  assert.equal(n.customStatus, "REJECTED_BY_WMS");
});

// ---------------------------------------------------------------- deferred navigation

test("fetchErrorText follows the deferred link and reads its $value", async () => {
  const mock = new MockTenant((n, req) => {
    if (n === 0) {
      assert.equal(
        req.url,
        "/api/v1/MessageProcessingLogs('AGc1FAIL')/ErrorInformation/$value",
        "deferred uri + /$value, used verbatim",
      );
      assert.equal(req.headers.authorization, AUTH);
      return {
        status: 200,
        headers: { "content-type": "text/plain" },
        body: "com.sap.it.rt error: Receiver WMS_EU returned HTTP 503",
      };
    }
    return { status: 500, body: "{}" };
  });
  await mock.start();
  try {
    const client = new CpiClient(mock.baseUrl, USER, PASS);
    const raw = log({
      MessageGuid: "AGc1FAIL",
      Status: "FAILED",
      ErrorInformation: {
        __deferred: { uri: `${mock.baseUrl}/api/v1/MessageProcessingLogs('AGc1FAIL')/ErrorInformation` },
      },
    });
    const text = await fetchErrorText(client, raw);
    assert.equal(text, "com.sap.it.rt error: Receiver WMS_EU returned HTTP 503");
    assert.equal(mock.requests.length, 1, "one call: the $value of the deferred link");
  } finally {
    await mock.stop();
  }
});

test("fetchErrorText returns null when the entry carries no deferred link", async () => {
  const mock = new MockTenant(() => ({ status: 500, body: "{}" }));
  await mock.start();
  try {
    const client = new CpiClient(mock.baseUrl, USER, PASS);
    const raw = log({ ErrorInformation: null });
    assert.equal(await fetchErrorText(client, raw), null);
    assert.equal(mock.requests.length, 0, "no API traffic without a link");
  } finally {
    await mock.stop();
  }
});

// ---------------------------------------------------------------- authorization errors

test("401 without a body maps to AuthorizationError(unauthorized)", async () => {
  const mock = new MockTenant(() => ({
    status: 401,
    headers: { "www-authenticate": 'Basic realm="SAP Cloud Integration"' },
    body: "",
  }));
  await mock.start();
  try {
    const client = new CpiClient(mock.baseUrl, "wrong-user", "wrong-pass-x7");
    await assert.rejects(
      () => listAllLogs(client, {}),
      (e: unknown) => {
        assert.ok(e instanceof AuthorizationError, "AuthorizationError expected");
        const a = e as AuthorizationError;
        assert.equal(a.status, 401);
        assert.equal(a.kind, "unauthorized");
        assert.equal(a.code, null);
        assert.ok(!a.message.includes("wrong-pass-x7"), "never leak the password");
        assert.ok(!a.message.includes("Basic "), "never leak the auth header");
        return true;
      },
    );
  } finally {
    await mock.stop();
  }
});

test("403 with an OData error document maps to forbidden and keeps the code", async () => {
  const mock = new MockTenant(() => ({
    status: 403,
    body: JSON.stringify({
      error: {
        code: "InsufficientRole",
        message: { lang: "en", value: "Role MonitoringDataRead required" },
      },
    }),
  }));
  await mock.start();
  try {
    const client = new CpiClient(mock.baseUrl, USER, PASS);
    await assert.rejects(
      () => listAllLogs(client, {}),
      (e: unknown) => {
        assert.ok(e instanceof AuthorizationError, "AuthorizationError expected");
        const a = e as AuthorizationError;
        assert.equal(a.status, 403);
        assert.equal(a.kind, "forbidden");
        assert.equal(a.code, "InsufficientRole");
        assert.ok(/MonitoringDataRead/.test(a.message), "surface the missing role hint");
        return true;
      },
    );
  } finally {
    await mock.stop();
  }
});

// ---------------------------------------------------------------- legacy client contract

test("existing client behavior: getLogsPage returns the raw d-envelope pieces", async () => {
  const mock = new MockTenant((n, req) => {
    assert.equal(req.url, "/api/v1/MessageProcessingLogs?$format=json&$top=1");
    return { status: 200, body: feed([log({})], { __count: "812" }) };
  });
  await mock.start();
  try {
    const client = new CpiClient(mock.baseUrl, USER, PASS);
    const page = await client.getLogsPage("$format=json&$top=1");
    assert.equal(page.logs.length, 1);
    assert.equal(page.logs[0].MessageGuid, "AGc1zdCsxbFcJgAvqB2nS9k_3s-U");
    assert.equal(page.nextUrl, null);
    assert.equal(page.count, "812", "raw __count stays a string on the client");
  } finally {
    await mock.stop();
  }
});

test("existing client behavior: OData error documents still raise CpiError", async () => {
  const mock = new MockTenant(() => ({
    status: 400,
    body: JSON.stringify({
      error: {
        code: "InvalidQueryOption",
        message: { lang: "en", value: "The query option $frobnicate is not supported" },
        innererror: { transactionid: "9A1B2C3D4E5F" },
      },
    }),
  }));
  await mock.start();
  try {
    const client = new CpiClient(mock.baseUrl, USER, PASS);
    await assert.rejects(
      () => client.request("/MessageProcessingLogs?$frobnicate=1"),
      (e: unknown) => {
        assert.ok(e instanceof CpiError);
        const c = e as CpiError;
        assert.equal(c.status, 400);
        assert.equal(c.code, "InvalidQueryOption");
        assert.equal(c.errorBody.innererror.transactionid, "9A1B2C3D4E5F", "SAP innererror preserved verbatim");
        return true;
      },
    );
  } finally {
    await mock.stop();
  }
});
