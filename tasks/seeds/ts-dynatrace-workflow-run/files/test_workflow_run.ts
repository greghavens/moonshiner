// Acceptance tests for the Dynatrace workflow runner (src/index.ts).
//
// Runs two loopback fakes — the Dynatrace SSO token endpoint and the
// AutomationEngine platform API (/platform/automation/v1) — and drives the
// runner against them. No real Dynatrace, no real credentials, no
// wall-clock sleeps: the clock and the sleeper are injected and recorded.
// The wire contract the fakes enforce is pinned in docs/contract.json.
// This file and everything under docs/ are protected.

import { test } from "node:test";
import assert from "node:assert/strict";
import http from "node:http";
import { readFileSync } from "node:fs";
import {
  OAuthTokenProvider,
  WorkflowRunner,
  AutomationApiError,
  ExecutionTimeoutError,
} from "./src/index.ts";

const CONTRACT = JSON.parse(readFileSync(new URL("./docs/contract.json", import.meta.url), "utf8"));
const SOURCES = JSON.parse(readFileSync(new URL("./docs/official_sources.json", import.meta.url), "utf8"));

const CLIENT_ID: string = CONTRACT.oauth.fixture_client_id;
const CLIENT_SECRET: string = CONTRACT.oauth.fixture_client_secret; // dummy; must never leak
const TOKENS: string[] = CONTRACT.oauth.fixture_tokens;
const SCOPES: string[] = CONTRACT.oauth.scopes;
const WF_ID: string = CONTRACT.fixtures.workflow_id;
const EXECUTION = CONTRACT.fixtures.execution;
const EXEC_ID: string = EXECUTION.id;
const AUTOMATION = "/platform/automation/v1";

type Recorded = {
  method: string;
  url: URL;
  rawUrl: string;
  headers: http.IncomingHttpHeaders;
  body: string;
};

class FakeServer {
  requests: Recorded[] = [];
  script: Array<{ status: number; doc: unknown }> = [];
  server: http.Server;
  baseUrl = "";
  fallback: () => { status: number; doc: unknown };

  constructor(fallback: () => { status: number; doc: unknown }) {
    this.fallback = fallback;
    this.server = http.createServer((req, res) => {
      let body = "";
      req.on("data", (chunk) => (body += chunk));
      req.on("end", () => {
        this.requests.push({
          method: req.method ?? "",
          url: new URL(req.url ?? "/", this.baseUrl),
          rawUrl: req.url ?? "",
          headers: req.headers,
          body,
        });
        const step = this.script.length > 0 ? this.script.shift()! : this.fallback();
        const payload = JSON.stringify(step.doc);
        res.writeHead(step.status, { "content-type": "application/json" });
        res.end(payload);
      });
    });
  }

  queue(status: number, doc: unknown) {
    this.script.push({ status, doc });
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
  sso: FakeServer;
  dt: FakeServer;
  sleeps: number[];
  setNow: (ms: number) => void;
  provider: () => InstanceType<typeof OAuthTokenProvider>;
  runner: () => InstanceType<typeof WorkflowRunner>;
};

async function withHarness(fn: (h: Harness) => Promise<void>) {
  let tokenIndex = 0;
  const sso = new FakeServer(() => ({
    status: 200,
    doc: {
      access_token: TOKENS[Math.min(tokenIndex++, TOKENS.length - 1)],
      token_type: "Bearer",
      expires_in: CONTRACT.oauth.token_lifetime_seconds,
    },
  }));
  const dt = new FakeServer(() => ({ status: 200, doc: {} }));
  await sso.start();
  await dt.start();
  let now = 0;
  const sleeps: number[] = [];
  const h: Harness = {
    sso,
    dt,
    sleeps,
    setNow: (ms) => (now = ms),
    provider: () =>
      new OAuthTokenProvider({
        tokenUrl: `${sso.baseUrl}${CONTRACT.oauth.token_url_path}`,
        clientId: CLIENT_ID,
        clientSecret: CLIENT_SECRET,
        scopes: SCOPES,
        clock: () => now,
      }),
    runner: () => {
      return new WorkflowRunner({
        environmentUrl: dt.baseUrl,
        tokenProvider: h.provider(),
        sleeper: async (ms: number) => {
          sleeps.push(ms);
        },
      });
    },
  };
  try {
    await fn(h);
  } finally {
    await sso.stop();
    await dt.stop();
  }
}

function runningExecution(overrides: Record<string, unknown> = {}) {
  return { ...EXECUTION, ...overrides };
}

test("token request follows the documented client-credentials contract", async () => {
  await withHarness(async (h) => {
    h.dt.queue(201, runningExecution());
    await h.runner().runWorkflow(WF_ID, { input: { cache: "warm" } });

    assert.equal(h.sso.requests.length, 1);
    const req = h.sso.requests[0];
    assert.equal(req.method, "POST");
    assert.equal(req.url.pathname, CONTRACT.oauth.token_url_path);
    assert.match(String(req.headers["content-type"]), /^application\/x-www-form-urlencoded/);
    const form = new URLSearchParams(req.body);
    assert.equal(form.get("grant_type"), "client_credentials");
    assert.equal(form.get("client_id"), CLIENT_ID);
    assert.equal(form.get("client_secret"), CLIENT_SECRET);
    assert.equal(form.get("scope"), SCOPES.join(" "));
    assert.deepEqual(
      [...form.keys()].sort(),
      ["client_id", "client_secret", "grant_type", "scope"],
      "no undocumented token-request parameters",
    );
  });
});

test("bearer tokens are cached and refreshed near the 300s expiry", async () => {
  await withHarness(async (h) => {
    h.dt.queue(201, runningExecution());
    h.dt.queue(201, runningExecution());
    h.dt.queue(201, runningExecution());
    const runner = h.runner();

    await runner.runWorkflow(WF_ID, { input: { n: 1 } });
    h.setNow(120_000);
    await runner.runWorkflow(WF_ID, { input: { n: 2 } });
    assert.equal(h.sso.requests.length, 1, "a live token is reused, not re-fetched");
    assert.equal(h.dt.requests[0].headers.authorization, `Bearer ${TOKENS[0]}`);
    assert.equal(h.dt.requests[1].headers.authorization, `Bearer ${TOKENS[0]}`);

    const skewMs =
      (CONTRACT.oauth.token_lifetime_seconds - CONTRACT.oauth.refresh_skew_seconds) * 1000;
    h.setNow(skewMs);
    await runner.runWorkflow(WF_ID, { input: { n: 3 } });
    assert.equal(h.sso.requests.length, 2, "within the skew window a fresh token is fetched");
    assert.equal(h.dt.requests[2].headers.authorization, `Bearer ${TOKENS[1]}`);
  });
});

test("runWorkflow posts only the provided keys to the documented run path", async () => {
  await withHarness(async (h) => {
    h.dt.queue(201, runningExecution());
    const outcome = await h.runner().runWorkflow(WF_ID, {
      input: { cache: "warm", region: "emea" },
      uniqueQualifier: "release-2026-07-17",
    });

    assert.equal(h.dt.requests.length, 1);
    const req = h.dt.requests[0];
    assert.equal(req.method, "POST");
    assert.equal(req.url.pathname, `${AUTOMATION}/workflows/${WF_ID}/run`);
    assert.match(String(req.headers["content-type"]), /^application\/json/);
    assert.equal(req.headers.authorization, `Bearer ${TOKENS[0]}`);
    assert.deepEqual(JSON.parse(req.body), {
      input: { cache: "warm", region: "emea" },
      uniqueQualifier: "release-2026-07-17",
    }, "absent optional keys (params) must be omitted, not sent as null");
    assert.equal(outcome.duplicate, false);
    assert.equal(outcome.execution.id, EXEC_ID);
  });
});

test("a 202 means the uniqueQualifier was already used", async () => {
  await withHarness(async (h) => {
    h.dt.queue(202, runningExecution({ state: "SUCCESS" }));
    const outcome = await h.runner().runWorkflow(WF_ID, {
      input: {},
      uniqueQualifier: "release-2026-07-17",
    });
    assert.equal(h.dt.requests.length, 1, "202 is an outcome, not a retryable error");
    assert.equal(outcome.duplicate, true);
    assert.equal(outcome.execution.id, EXEC_ID, "the existing execution is returned");
  });
});

test("waitForExecution polls through every non-terminal state", async () => {
  await withHarness(async (h) => {
    for (const state of CONTRACT.endpoints.get_execution.non_terminal_states) {
      h.dt.queue(200, runningExecution({ state }));
    }
    h.dt.queue(200, runningExecution({ state: "SUCCESS", endedAt: "2026-07-17T11:42:00.000000Z" }));

    const execution = await h.runner().waitForExecution(EXEC_ID, {
      pollIntervalMs: 2500,
      maxPolls: 10,
    });

    assert.equal(execution.state, "SUCCESS");
    assert.equal(h.dt.requests.length, 4, "RUNNING, PAUSED and UNKNOWN all keep polling");
    for (const req of h.dt.requests) {
      assert.equal(req.method, "GET");
      assert.equal(req.url.pathname, `${AUTOMATION}/executions/${EXEC_ID}`);
    }
    assert.deepEqual(h.sleeps, [2500, 2500, 2500], "sleeps only between polls, via the injected sleeper");
  });
});

test("waitForExecution gives up after maxPolls with the last state", async () => {
  await withHarness(async (h) => {
    h.dt.queue(200, runningExecution({ state: "RUNNING" }));
    h.dt.queue(200, runningExecution({ state: "RUNNING" }));
    h.dt.queue(200, runningExecution({ state: "RUNNING" }));
    await assert.rejects(
      h.runner().waitForExecution(EXEC_ID, { pollIntervalMs: 1000, maxPolls: 3 }),
      (err: unknown) => {
        assert.ok(err instanceof ExecutionTimeoutError);
        assert.equal(err.executionId, EXEC_ID);
        assert.equal(err.lastState, "RUNNING");
        return true;
      },
    );
    assert.equal(h.dt.requests.length, 3, "exactly maxPolls polls");
    assert.deepEqual(h.sleeps, [1000, 1000], "no sleep after the final poll");
  });
});

test("a terminal ERROR resolves and keeps the platform error details", async () => {
  await withHarness(async (h) => {
    h.dt.queue(200, runningExecution({
      state: "ERROR",
      stateInfo: CONTRACT.fixtures.error_state_info,
      endedAt: "2026-07-17T11:43:11.000000Z",
    }));
    const execution = await h.runner().waitForExecution(EXEC_ID, {
      pollIntervalMs: 1000,
      maxPolls: 5,
    });
    assert.equal(execution.state, "ERROR", "ERROR is terminal, not retried");
    assert.equal(execution.stateInfo, CONTRACT.fixtures.error_state_info,
      "stateInfo must survive for the incident report");
    assert.deepEqual(h.sleeps, []);
  });
});

test("collectTaskResults fetches results only for result-bearing tasks", async () => {
  await withHarness(async (h) => {
    h.dt.queue(200, CONTRACT.fixtures.tasks);
    h.dt.queue(200, CONTRACT.fixtures.task_results.fetch_logs);
    h.dt.queue(200, CONTRACT.fixtures.task_results.notify);

    const results = await h.runner().collectTaskResults(EXEC_ID);

    assert.equal(h.dt.requests.length, 3, "one tasks call plus one result call per finished task");
    assert.equal(h.dt.requests[0].url.pathname, `${AUTOMATION}/executions/${EXEC_ID}/tasks`);
    assert.equal(h.dt.requests[1].url.pathname,
      `${AUTOMATION}/executions/${EXEC_ID}/tasks/fetch_logs/result`);
    assert.equal(h.dt.requests[2].url.pathname,
      `${AUTOMATION}/executions/${EXEC_ID}/tasks/notify/result`);
    assert.deepEqual(results, [
      { name: "fetch_logs", state: "SUCCESS", result: CONTRACT.fixtures.task_results.fetch_logs },
      { name: "notify", state: "ERROR", result: CONTRACT.fixtures.task_results.notify },
    ], "sorted by task name; SKIPPED tasks are excluded and never fetched");
  });
});

test("listExecutions pages with limit/offset and one comma-separated state param", async () => {
  await withHarness(async (h) => {
    const page = (ids: string[]) => ({
      count: 5,
      results: ids.map((id) => runningExecution({ id, state: "SUCCESS" })),
    });
    h.dt.queue(200, page(["exec-1", "exec-2"]));
    h.dt.queue(200, page(["exec-3", "exec-4"]));
    h.dt.queue(200, page(["exec-5"]));

    const executions = await h.runner().listExecutions({
      workflow: WF_ID,
      states: ["SUCCESS", "ERROR"],
      limit: 2,
    });

    assert.equal(h.dt.requests.length, 3);
    const offsets: string[] = [];
    for (const req of h.dt.requests) {
      assert.equal(req.url.pathname, `${AUTOMATION}/executions`);
      assert.equal(req.url.searchParams.get("workflow"), WF_ID);
      assert.deepEqual(req.url.searchParams.getAll("state"), ["SUCCESS,ERROR"],
        "multiple states travel comma-separated in a single parameter");
      assert.equal(req.url.searchParams.get("limit"), "2");
      offsets.push(req.url.searchParams.get("offset") ?? "");
    }
    assert.deepEqual(offsets, ["0", "2", "4"], "offset advances by the results received");
    assert.deepEqual(executions.map((e: { id: string }) => e.id),
      ["exec-1", "exec-2", "exec-3", "exec-4", "exec-5"]);
  });
});

test("platform errors keep status and detail but never the secrets", async () => {
  await withHarness(async (h) => {
    h.dt.queue(404, CONTRACT.fixtures.error_404);
    await assert.rejects(
      h.runner().runWorkflow(WF_ID, { input: {} }),
      (err: unknown) => {
        assert.ok(err instanceof AutomationApiError);
        assert.equal(err.status, 404);
        assert.equal(err.detail, "Not found.");
        const text = `${err.message} ${err.detail} ${err.body}`;
        assert.ok(!text.includes(CLIENT_SECRET), "client secret must never surface");
        assert.ok(!text.includes(TOKENS[0]), "bearer token must never surface");
        return true;
      },
    );
  });
});

test("a 401 invalidates the cached token and retries exactly once", async () => {
  await withHarness(async (h) => {
    h.dt.queue(401, { detail: "Token expired" });
    h.dt.queue(201, runningExecution());
    const outcome = await h.runner().runWorkflow(WF_ID, { input: {} });
    assert.equal(outcome.duplicate, false);
    assert.equal(h.dt.requests.length, 2);
    assert.equal(h.dt.requests[0].headers.authorization, `Bearer ${TOKENS[0]}`);
    assert.equal(h.dt.requests[1].headers.authorization, `Bearer ${TOKENS[1]}`,
      "the retry must carry a freshly fetched token");
    assert.equal(h.sso.requests.length, 2);
  });
});

test("a second 401 is surfaced, not retried forever", async () => {
  await withHarness(async (h) => {
    h.dt.queue(401, { detail: "Token expired" });
    h.dt.queue(401, { detail: "Token expired" });
    await assert.rejects(
      h.runner().runWorkflow(WF_ID, { input: {} }),
      (err: unknown) => err instanceof AutomationApiError && err.status === 401,
    );
    assert.equal(h.dt.requests.length, 2, "exactly one refresh-and-retry round");
  });
});

test("classic live.dynatrace.com environment URLs are rejected up front", async () => {
  await withHarness(async (h) => {
    assert.throws(
      () =>
        new WorkflowRunner({
          environmentUrl: "https://abc12345.live.dynatrace.com",
          tokenProvider: h.provider(),
          sleeper: async () => {},
        }),
      /apps\.dynatrace\.com/,
      "the error must point at the platform apps.dynatrace.com URL",
    );
    assert.equal(h.dt.requests.length, 0);
    assert.equal(h.sso.requests.length, 0);
  });
});

test("credentials never travel to the platform host or in URLs", async () => {
  await withHarness(async (h) => {
    h.dt.queue(201, runningExecution());
    h.dt.queue(200, runningExecution({ state: "SUCCESS" }));
    const runner = h.runner();
    await runner.runWorkflow(WF_ID, { input: {} });
    await runner.waitForExecution(EXEC_ID, { pollIntervalMs: 10, maxPolls: 2 });
    for (const req of h.dt.requests) {
      const flat = `${req.rawUrl}\n${req.body}\n${JSON.stringify(req.headers)}`;
      assert.ok(!flat.includes(CLIENT_SECRET), "client secret leaked to the platform API");
      assert.ok(!req.rawUrl.includes(TOKENS[0]) && !req.rawUrl.includes(TOKENS[1]),
        "bearer tokens must never be query parameters");
    }
  });
});

test("protected provenance fixtures are intact", () => {
  assert.equal(SOURCES.research.required, true);
  assert.ok(SOURCES.research.official_sources.length >= 2);
  assert.equal(CONTRACT.endpoints.run_workflow.path,
    "/platform/automation/v1/workflows/{workflowId}/run");
  assert.equal(CONTRACT.endpoints.get_execution.path,
    "/platform/automation/v1/executions/{executionId}");
  assert.deepEqual(CONTRACT.oauth.scopes,
    ["automation:workflows:run", "automation:workflows:read"]);
  assert.equal(CONTRACT.oauth.production_token_url,
    "https://sso.dynatrace.com/sso/oauth2/token");
  assert.deepEqual(CONTRACT.endpoints.get_execution.terminal_states,
    ["CANCELLED", "ERROR", "SUCCESS"]);
});
