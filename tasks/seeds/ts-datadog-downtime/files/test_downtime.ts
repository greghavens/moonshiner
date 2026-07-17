// Acceptance tests for the Datadog v2 downtime scheduler feature.
//
// Existing behavior (src/windows.ts planning/validation) must keep working;
// the new feature lives in src/downtime.ts. Tests run a loopback fake
// Datadog API — no real Datadog, no real credentials. The wire contract the
// fake enforces is pinned in docs/contract.json. This file and everything
// under docs/ are protected.

import test from "node:test";
import assert from "node:assert/strict";
import http from "node:http";
import { readFileSync } from "node:fs";

import {
  planWeeklyWindow,
  validateWindow,
  type MaintenanceWindow,
} from "./src/windows.ts";
import { DowntimeClient } from "./src/downtime.ts";

const CONTRACT = JSON.parse(readFileSync(new URL("./docs/contract.json", import.meta.url), "utf8"));
const SOURCES = JSON.parse(readFileSync(new URL("./docs/official_sources.json", import.meta.url), "utf8"));

const API_KEY: string = CONTRACT.auth.fixture_api_key;
const APP_KEY: string = CONTRACT.auth.fixture_app_key;
const DOWNTIME_PATH = "/api/v2/downtime";

type Recorded = {
  method: string;
  path: string;
  headers: http.IncomingHttpHeaders;
  body: unknown;
};

type Scripted = { status: number; body?: unknown };

class FakeDatadog {
  requests: Recorded[] = [];
  postResponses: Scripted[] = [];
  deleteResponses = new Map<string, Scripted[]>();
  server: http.Server;
  baseUrl = "";

  constructor() {
    this.server = http.createServer((req, res) => {
      let raw = "";
      req.on("data", (chunk) => (raw += chunk));
      req.on("end", () => {
        const rec: Recorded = {
          method: req.method ?? "",
          path: req.url ?? "",
          headers: req.headers,
          body: raw ? JSON.parse(raw) : undefined,
        };
        this.requests.push(rec);
        let scripted: Scripted | undefined;
        if (rec.method === "POST" && rec.path === DOWNTIME_PATH) {
          scripted = this.postResponses.shift();
        } else if (rec.method === "DELETE" && rec.path.startsWith(DOWNTIME_PATH + "/")) {
          const id = rec.path.slice(DOWNTIME_PATH.length + 1);
          scripted = this.deleteResponses.get(id)?.shift() ?? { status: 204 };
        }
        if (!scripted) {
          scripted = { status: 404, body: { errors: ["unexpected request"] } };
        }
        if (scripted.status === 204 || scripted.body === undefined) {
          res.writeHead(scripted.status).end();
        } else {
          res.writeHead(scripted.status, { "content-type": "application/json" });
          res.end(JSON.stringify(scripted.body));
        }
      });
    });
  }

  async start(): Promise<void> {
    await new Promise<void>((resolve) => this.server.listen(0, "127.0.0.1", resolve));
    const addr = this.server.address();
    if (addr === null || typeof addr === "string") throw new Error("no port");
    this.baseUrl = `http://127.0.0.1:${addr.port}`;
  }

  stop(): Promise<void> {
    return new Promise((resolve) => this.server.close(() => resolve()));
  }
}

function downtimeDoc(id: string, scope: string, monitorRelId: string | null) {
  return {
    data: {
      type: "downtime",
      id,
      attributes: { scope, status: "scheduled" },
      relationships: {
        created_by: { data: { id: "3d7a9c2e-0f41-11ee-a3aa-user0000", type: "users" } },
        monitor: { data: monitorRelId === null ? null : { id: monitorRelId, type: "monitors" } },
      },
    },
  };
}

function client(baseUrl: string): DowntimeClient {
  return new DowntimeClient({ baseUrl, apiKey: API_KEY, appKey: APP_KEY });
}

const oneTimeWindow: MaintenanceWindow = {
  name: "checkout-deploy-freeze",
  scope: "env:prod AND service:checkout",
  monitorId: 42,
  start: "2026-08-01T02:00:00+00:00",
  end: "2026-08-01T04:00:00+00:00",
  message: "Checkout deploy freeze @slack-checkout",
  muteFirstRecovery: true,
};

const recurringWindow: MaintenanceWindow = {
  name: "ingest-weekly-compaction",
  scope: "service:ingest",
  monitorTags: ["service:ingest", "team:data"],
  rrule: "FREQ=WEEKLY;INTERVAL=1;BYDAY=SA",
  start: "2026-08-01T02:00:00",
  duration: "2h",
  timezone: "America/New_York",
};

const minimalWindow: MaintenanceWindow = {
  name: "adhoc-mute",
  scope: "env:staging",
  monitorTags: ["team:data"],
};

test("existing planner behavior is unchanged", () => {
  const w = planWeeklyWindow({
    name: "ingest-weekly-compaction",
    scope: "service:ingest",
    monitorTags: ["service:ingest", "team:data"],
    day: "SA",
    firstDate: "2026-08-01",
    startTime: "02:00:00",
    duration: "2h",
    timezone: "America/New_York",
  });
  assert.equal(w.rrule, "FREQ=WEEKLY;INTERVAL=1;BYDAY=SA");
  assert.equal(w.start, "2026-08-01T02:00:00");
  assert.equal(w.duration, "2h");
  assert.deepEqual(validateWindow(w), []);
  assert.throws(() => planWeeklyWindow({ ...w, day: "XX" } as never));
});

test("existing validation rules are unchanged", () => {
  assert.deepEqual(validateWindow(oneTimeWindow), []);
  assert.deepEqual(validateWindow(recurringWindow), []);
  const both = { ...oneTimeWindow, monitorTags: ["a:b"] };
  assert.ok(validateWindow(both).some((p) => p.includes("exactly one")));
  const offsetInRecurring = { ...recurringWindow, start: "2026-08-01T02:00:00+00:00" };
  assert.ok(validateWindow(offsetInRecurring).some((p) => p.includes("offset")));
  const localOneTime = { ...oneTimeWindow, start: "2026-08-01T02:00:00" };
  assert.ok(validateWindow(localOneTime).some((p) => p.includes("offset")));
});

test("one-time create document matches the documented v2 resource shape", () => {
  const c = client("http://127.0.0.1:1");
  assert.deepEqual(c.buildCreateDocument(oneTimeWindow), {
    data: {
      type: "downtime",
      attributes: {
        scope: "env:prod AND service:checkout",
        monitor_identifier: { monitor_id: 42 },
        message: "Checkout deploy freeze @slack-checkout",
        mute_first_recovery_notification: true,
        schedule: {
          start: "2026-08-01T02:00:00+00:00",
          end: "2026-08-01T04:00:00+00:00",
        },
      },
    },
  });
});

test("recurring create document uses recurrences with offset-free start", () => {
  const c = client("http://127.0.0.1:1");
  assert.deepEqual(c.buildCreateDocument(recurringWindow), {
    data: {
      type: "downtime",
      attributes: {
        scope: "service:ingest",
        monitor_identifier: { monitor_tags: ["service:ingest", "team:data"] },
        schedule: {
          recurrences: [
            {
              rrule: "FREQ=WEEKLY;INTERVAL=1;BYDAY=SA",
              duration: "2h",
              start: "2026-08-01T02:00:00",
            },
          ],
          timezone: "America/New_York",
        },
      },
    },
  });
});

test("minimal window omits schedule entirely and rejects invalid windows", () => {
  const c = client("http://127.0.0.1:1");
  assert.deepEqual(c.buildCreateDocument(minimalWindow), {
    data: {
      type: "downtime",
      attributes: {
        scope: "env:staging",
        monitor_identifier: { monitor_tags: ["team:data"] },
      },
    },
  });
  assert.throws(
    () => c.buildCreateDocument({ ...oneTimeWindow, monitorTags: ["a:b"] }),
    /exactly one/
  );
});

test("scheduleDowntimes speaks the documented wire contract", async () => {
  const fake = new FakeDatadog();
  await fake.start();
  try {
    fake.postResponses.push(
      { status: 200, body: downtimeDoc("dt-0001-aaaa", oneTimeWindow.scope, "42") },
      { status: 200, body: downtimeDoc("dt-0002-bbbb", recurringWindow.scope, null) }
    );
    const result = await client(fake.baseUrl).scheduleDowntimes([
      oneTimeWindow,
      recurringWindow,
    ]);

    assert.equal(fake.requests.length, 2);
    for (const req of fake.requests) {
      assert.equal(req.method, "POST");
      const [path, query] = req.path.split("?");
      assert.equal(path, DOWNTIME_PATH);
      assert.equal(query, undefined); // keys travel as headers, never query params
      assert.equal(req.headers["dd-api-key"], API_KEY);
      assert.equal(req.headers["dd-application-key"], APP_KEY);
      assert.equal(req.headers["content-type"], "application/json");
      assert.equal(req.headers["accept"], "application/json");
    }
    assert.deepEqual(fake.requests[0].body, client("x").buildCreateDocument(oneTimeWindow));
    assert.deepEqual(fake.requests[1].body, client("x").buildCreateDocument(recurringWindow));

    assert.equal(result.failed.length, 0);
    assert.deepEqual(result.created, [
      {
        name: "checkout-deploy-freeze",
        id: "dt-0001-aaaa",
        scope: "env:prod AND service:checkout",
        status: "scheduled",
        monitorId: "42",
        createdBy: "3d7a9c2e-0f41-11ee-a3aa-user0000",
      },
      {
        name: "ingest-weekly-compaction",
        id: "dt-0002-bbbb",
        scope: "service:ingest",
        status: "scheduled",
        monitorId: null, // tag-scoped downtime: monitor relationship data is null
        createdBy: "3d7a9c2e-0f41-11ee-a3aa-user0000",
      },
    ]);
  } finally {
    await fake.stop();
  }
});

test("a mid-batch API failure retains earlier and later successes", async () => {
  const fake = new FakeDatadog();
  await fake.start();
  try {
    fake.postResponses.push(
      { status: 200, body: downtimeDoc("dt-0003-cccc", oneTimeWindow.scope, "42") },
      { status: 400, body: { errors: ["Downtime scope is invalid"] } },
      { status: 200, body: downtimeDoc("dt-0004-dddd", minimalWindow.scope, null) }
    );
    const result = await client(fake.baseUrl).scheduleDowntimes([
      oneTimeWindow,
      recurringWindow,
      minimalWindow,
    ]);
    assert.equal(fake.requests.length, 3, "a failure must not stop the batch");
    assert.deepEqual(
      result.created.map((c) => c.id),
      ["dt-0003-cccc", "dt-0004-dddd"]
    );
    assert.deepEqual(result.failed, [
      {
        name: "ingest-weekly-compaction",
        status: 400,
        errors: ["Downtime scope is invalid"],
      },
    ]);
  } finally {
    await fake.stop();
  }
});

test("locally invalid windows never reach the API", async () => {
  const fake = new FakeDatadog();
  await fake.start();
  try {
    fake.postResponses.push({
      status: 200,
      body: downtimeDoc("dt-0005-eeee", minimalWindow.scope, null),
    });
    const invalid: MaintenanceWindow = {
      ...oneTimeWindow,
      name: "double-identifier",
      monitorTags: ["a:b"],
    };
    const result = await client(fake.baseUrl).scheduleDowntimes([invalid, minimalWindow]);
    assert.equal(fake.requests.length, 1, "invalid windows must be rejected client-side");
    assert.deepEqual(result.created.map((c) => c.id), ["dt-0005-eeee"]);
    assert.equal(result.failed.length, 1);
    assert.equal(result.failed[0].name, "double-identifier");
    assert.equal(result.failed[0].status, null);
    assert.ok(result.failed[0].errors.some((e: string) => e.includes("exactly one")));
  } finally {
    await fake.stop();
  }
});

test("cancel is a DELETE returning 204, and 404 means already gone", async () => {
  const fake = new FakeDatadog();
  await fake.start();
  try {
    fake.deleteResponses.set("dt-0001-aaaa", [{ status: 204 }]);
    fake.deleteResponses.set("dt-gone", [
      { status: 404, body: { errors: ["Downtime not found"] } },
    ]);
    const c = client(fake.baseUrl);
    assert.equal(await c.cancelDowntime("dt-0001-aaaa"), "canceled");
    assert.equal(await c.cancelDowntime("dt-gone"), "already_gone");
    assert.equal(fake.requests.length, 2);
    assert.equal(fake.requests[0].method, "DELETE");
    assert.equal(fake.requests[0].path, `${DOWNTIME_PATH}/dt-0001-aaaa`);
    assert.equal(fake.requests[0].body, undefined);
    assert.equal(fake.requests[0].headers["dd-api-key"], API_KEY);
    assert.equal(fake.requests[0].headers["dd-application-key"], APP_KEY);
  } finally {
    await fake.stop();
  }
});

test("batch cancel keeps going and reports per-id outcomes", async () => {
  const fake = new FakeDatadog();
  await fake.start();
  try {
    fake.deleteResponses.set("dt-a", [{ status: 204 }]);
    fake.deleteResponses.set("dt-b", [
      { status: 403, body: { errors: ["Forbidden"] } },
    ]);
    fake.deleteResponses.set("dt-c", [
      { status: 404, body: { errors: ["Downtime not found"] } },
    ]);
    fake.deleteResponses.set("dt-d", [{ status: 204 }]);
    const result = await client(fake.baseUrl).cancelDowntimes([
      "dt-a",
      "dt-b",
      "dt-c",
      "dt-d",
    ]);
    assert.equal(fake.requests.length, 4);
    assert.deepEqual(result.canceled, ["dt-a", "dt-d"]);
    assert.deepEqual(result.alreadyGone, ["dt-c"]);
    assert.deepEqual(result.failed, [
      { id: "dt-b", status: 403, errors: ["Forbidden"] },
    ]);
    const flattened = JSON.stringify(result);
    assert.ok(!flattened.includes(API_KEY) && !flattened.includes(APP_KEY));
  } finally {
    await fake.stop();
  }
});

test("a hard cancel failure surfaces decoded errors without credentials", async () => {
  const fake = new FakeDatadog();
  await fake.start();
  try {
    fake.deleteResponses.set("dt-locked", [
      { status: 403, body: { errors: ["Forbidden: downtime is restricted"] } },
    ]);
    await assert.rejects(
      () => client(fake.baseUrl).cancelDowntime("dt-locked"),
      (err: Error) => {
        assert.ok(err.message.includes("Forbidden: downtime is restricted"));
        assert.ok(err.message.includes("403"));
        assert.ok(!err.message.includes(API_KEY));
        assert.ok(!err.message.includes(APP_KEY));
        return true;
      }
    );
  } finally {
    await fake.stop();
  }
});

test("protected research fixtures parse and carry provenance", () => {
  assert.equal(SOURCES.research.required, true);
  assert.ok(SOURCES.research.official_sources.length >= 2);
  assert.equal(CONTRACT.operations.create.path, DOWNTIME_PATH);
});
