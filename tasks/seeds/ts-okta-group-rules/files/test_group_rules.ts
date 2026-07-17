// Acceptance tests for the Okta group-rule reconciler. A local node:http
// mock speaks the Group Rules wire contract pinned in docs/contract.json —
// no real Okta org, no real credentials, no sleeps.
// Run: node --test test_group_rules.ts
// Protected — do not modify this file or anything under docs/.
import { test } from "node:test";
import assert from "node:assert/strict";
import { createServer } from "node:http";

import { GroupRulesClient, OktaHttpError } from "./okta/client.ts";
import type { GroupRule } from "./okta/client.ts";
import { reconcileGroupRules } from "./okta/reconcile.ts";

const TOKEN = "00dummySSWSrulet0ken-fixture-7Pn2";

interface Captured {
  method: string;
  url: string;
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
        url: req.url ?? "",
        headers: req.headers,
        body: raw ? JSON.parse(raw) : null,
      });
      const s = script[Math.min(requests.length - 1, script.length - 1)];
      res.statusCode = s.status ?? 200;
      for (const [k, v] of Object.entries(s.headers ?? {})) res.setHeader(k, v);
      if (s.rawBody !== undefined) {
        res.end(s.rawBody);
      } else if (s.body !== undefined) {
        res.setHeader("content-type", "application/json");
        res.end(JSON.stringify(s.body));
      } else {
        res.end();
      }
    });
  });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  t.after(() => new Promise((resolve) => server.close(resolve)));
  const addr = server.address();
  if (addr === null || typeof addr === "string") throw new Error("no port");
  return { base: `http://127.0.0.1:${addr.port}`, requests };
}

function pathOf(url: string): string {
  const i = url.indexOf("?");
  return i < 0 ? url : url.slice(0, i);
}

function queryOf(url: string): Record<string, string> {
  const i = url.indexOf("?");
  const out: Record<string, string> = {};
  if (i < 0) return out;
  for (const pair of url.slice(i + 1).split("&")) {
    const eq = pair.indexOf("=");
    out[decodeURIComponent(pair.slice(0, eq))] = decodeURIComponent(pair.slice(eq + 1));
  }
  return out;
}

// ------------------------------------------------------------- fixtures

const EXPR_TYPE = "urn:okta:expression:1.0";

function rule(
  id: string,
  name: string,
  expr: string,
  groupIds: string[],
  status: "ACTIVE" | "INACTIVE",
): GroupRule {
  return {
    id,
    type: "group_rule",
    name,
    status,
    created: "2026-05-02T09:00:00.000Z",
    lastUpdated: "2026-06-20T11:30:00.000Z",
    conditions: {
      people: { users: { exclude: [] }, groups: { exclude: [] } },
      expression: { value: expr, type: EXPR_TYPE },
    },
    actions: { assignUserToGroups: { groupIds } },
  };
}

const ENG = rule("0pr1a2b3c4RULEaaaa01", "eng-core-access",
  'user.department=="Engineering"', ["00g1grpENGCORE0001"], "ACTIVE");
const SALES = rule("0pr1a2b3c4RULEbbbb02", "sales-crm-access",
  'user.department=="Sales"', ["00g1grpSALES000002"], "INACTIVE");
const VPN = rule("0pr1a2b3c4RULEcccc03", "contractor-vpn",
  'user.userType=="Contractor"', ["00g1grpVPN00000003"], "ACTIVE");

function desired(
  name: string,
  expr: string,
  groupIds: string[],
  status: "ACTIVE" | "INACTIVE",
  exprType: string = EXPR_TYPE,
): GroupRule {
  return {
    type: "group_rule",
    name,
    status,
    conditions: { expression: { value: expr, type: exprType } },
    actions: { assignUserToGroups: { groupIds } },
  };
}

function selfLink(base: string, query: string): string {
  return `<${base}/api/v1/groups/rules?${query}>; rel="self"`;
}

function nextLink(base: string, selfQuery: string, nextQuery: string): string {
  return `${selfLink(base, selfQuery)}, <${base}/api/v1/groups/rules?${nextQuery}>; rel="next"`;
}

// ------------------------------------------- existing client behavior

test("getRule keeps sending the documented request shape", async (t) => {
  const mock = await startMock(t, [{ body: ENG }]);
  const client = new GroupRulesClient(mock.base, TOKEN);
  const got = await client.getRule(ENG.id!);
  assert.equal(mock.requests.length, 1);
  const r = mock.requests[0];
  assert.equal(r.method, "GET");
  assert.equal(r.url, `/api/v1/groups/rules/${ENG.id}`);
  assert.equal(r.headers.authorization, `SSWS ${TOKEN}`);
  assert.match(String(r.headers.accept), /application\/json/);
  assert.equal(got.name, "eng-core-access");
  assert.equal(got.status, "ACTIVE");
});

test("okta error envelopes still decode into OktaHttpError", async (t) => {
  const mock = await startMock(t, [{
    status: 404,
    body: {
      errorCode: "E0000007",
      errorSummary: "Not found: Resource not found: 0prMissing (GroupRule)",
      errorLink: "E0000007",
      errorId: "oaeMlLvGUjYD5v16vkYWY007",
      errorCauses: [],
    },
  }]);
  const client = new GroupRulesClient(mock.base, TOKEN);
  await assert.rejects(client.getRule("0prMissing"), (e: unknown) => {
    assert.ok(e instanceof OktaHttpError);
    assert.equal(e.status, 404);
    assert.equal(e.errorCode, "E0000007");
    assert.ok(!e.message.includes(TOKEN), "token must never leak into errors");
    return true;
  });
});

// --------------------------------------------------- new: pagination

test("listAllRules pages with the documented cursor contract", async (t) => {
  // The script array is captured by reference, so the Link hrefs can point at
  // the mock's own base URL once it's known. Page 1 advertises a next link
  // whose URL reorders params and carries a server marker; page 2 has only a
  // self link.
  const script: Scripted[] = [{}, {}];
  const mock = await startMock(t, script);
  script[0] = {
    body: [ENG, SALES],
    headers: { Link: nextLink(mock.base, "limit=50", "after=0pr1a2b3c4RULEbbbb02&limit=50&srvMarker=hold") },
  };
  script[1] = {
    body: [VPN],
    headers: { Link: selfLink(mock.base, "after=0pr1a2b3c4RULEbbbb02&limit=50") },
  };

  const client = new GroupRulesClient(mock.base, TOKEN);
  const rules = await client.listAllRules();

  assert.equal(mock.requests.length, 2);
  const q1 = queryOf(mock.requests[0].url);
  assert.equal(pathOf(mock.requests[0].url), "/api/v1/groups/rules");
  assert.equal(q1.limit, "50");
  assert.equal(q1.after, undefined);
  const q2 = queryOf(mock.requests[1].url);
  assert.equal(q2.after, "0pr1a2b3c4RULEbbbb02");
  assert.equal(q2.srvMarker, "hold", "the rel=next URL must be requested verbatim");
  assert.deepEqual(rules.map((r: GroupRule) => r.name),
    ["eng-core-access", "sales-crm-access", "contractor-vpn"]);
});

// --------------------------------------------------- new: reconciler

test("missing rule is created without server-owned fields, then activated", async (t) => {
  const createdId = "0pr9NEWLYMINTED0009";
  const mock = await startMock(t, [
    { body: [], headers: {} },
    { body: { ...desired("eng-core-access", 'user.department=="Engineering"', ["00g1grpENGCORE0001"], "INACTIVE"), id: createdId, status: "INACTIVE" } },
    { status: 204, rawBody: "" },
  ]);
  const client = new GroupRulesClient(mock.base, TOKEN);
  const report = await reconcileGroupRules(client, [
    desired("eng-core-access", 'user.department=="Engineering"', ["00g1grpENGCORE0001"], "ACTIVE"),
  ]);

  assert.equal(mock.requests.length, 3);
  const create = mock.requests[1];
  assert.equal(create.method, "POST");
  assert.equal(pathOf(create.url), "/api/v1/groups/rules");
  assert.equal(create.body.type, "group_rule");
  assert.equal(create.body.name, "eng-core-access");
  assert.equal(create.body.conditions.expression.value, 'user.department=="Engineering"');
  assert.equal(create.body.conditions.expression.type, EXPR_TYPE);
  assert.deepEqual(create.body.actions.assignUserToGroups.groupIds, ["00g1grpENGCORE0001"]);
  assert.ok(!("status" in create.body), "rules are born INACTIVE; status is not writable on create");
  assert.ok(!("id" in create.body), "id is server-owned");

  const activate = mock.requests[2];
  assert.equal(activate.method, "POST");
  assert.equal(activate.url, `/api/v1/groups/rules/${createdId}/lifecycle/activate`);

  assert.deepEqual(report.created, ["eng-core-access"]);
  assert.deepEqual(report.updated, []);
  assert.deepEqual(report.rejected, []);
});

test("expression drift on an ACTIVE rule deactivates, replaces, reactivates", async (t) => {
  const newExpr = 'user.department=="Engineering" OR user.department=="SRE"';
  const mock = await startMock(t, [
    { body: [ENG], headers: {} },
    { status: 204, rawBody: "" },
    { body: { ...ENG, status: "INACTIVE", conditions: { ...ENG.conditions, expression: { value: newExpr, type: EXPR_TYPE } } } },
    { status: 204, rawBody: "" },
  ]);
  const client = new GroupRulesClient(mock.base, TOKEN);
  const report = await reconcileGroupRules(client, [
    desired("eng-core-access", newExpr, ["00g1grpENGCORE0001"], "ACTIVE"),
  ]);

  assert.equal(mock.requests.length, 4);
  assert.equal(mock.requests[1].method, "POST");
  assert.equal(mock.requests[1].url, `/api/v1/groups/rules/${ENG.id}/lifecycle/deactivate`);

  const put = mock.requests[2];
  assert.equal(put.method, "PUT");
  assert.equal(put.url, `/api/v1/groups/rules/${ENG.id}`);
  assert.equal(put.body.conditions.expression.value, newExpr);
  assert.equal(put.body.conditions.expression.type, EXPR_TYPE);
  assert.deepEqual(put.body.actions.assignUserToGroups.groupIds,
    ENG.actions.assignUserToGroups.groupIds,
    "actions are not updatable via PUT and must ride along unchanged");
  assert.ok(!("status" in put.body), "status transitions only via lifecycle endpoints");
  assert.ok(!("created" in put.body) && !("lastUpdated" in put.body), "server-owned timestamps stay out of the body");

  assert.equal(mock.requests[3].method, "POST");
  assert.equal(mock.requests[3].url, `/api/v1/groups/rules/${ENG.id}/lifecycle/activate`);

  assert.deepEqual(report.updated, ["eng-core-access"]);
  assert.deepEqual(report.created, []);
});

test("status-only drift uses the lifecycle endpoint, never PUT", async (t) => {
  const mock = await startMock(t, [
    { body: [ENG], headers: {} },
    { status: 204, rawBody: "" },
  ]);
  const client = new GroupRulesClient(mock.base, TOKEN);
  const report = await reconcileGroupRules(client, [
    desired("eng-core-access", 'user.department=="Engineering"', ["00g1grpENGCORE0001"], "INACTIVE"),
  ]);

  assert.equal(mock.requests.length, 2);
  assert.equal(mock.requests[1].method, "POST");
  assert.equal(mock.requests[1].url, `/api/v1/groups/rules/${ENG.id}/lifecycle/deactivate`);
  assert.ok(!mock.requests.some((r) => r.method === "PUT"),
    "a status flip must not go through replace");
  assert.deepEqual(report.statusChanged, ["eng-core-access"]);
  assert.deepEqual(report.updated, []);
});

test("action drift forces deactivate + delete + recreate + activate", async (t) => {
  const newId = "0pr9RECREATED00010";
  const mock = await startMock(t, [
    { body: [ENG], headers: {} },
    { status: 204, rawBody: "" },
    { status: 204, rawBody: "" },
    { body: { ...desired("eng-core-access", 'user.department=="Engineering"', ["00g1grpENGCORE0001", "00g1grpBUILDBOT004"], "INACTIVE"), id: newId, status: "INACTIVE" } },
    { status: 204, rawBody: "" },
  ]);
  const client = new GroupRulesClient(mock.base, TOKEN);
  const report = await reconcileGroupRules(client, [
    desired("eng-core-access", 'user.department=="Engineering"',
      ["00g1grpENGCORE0001", "00g1grpBUILDBOT004"], "ACTIVE"),
  ]);

  assert.equal(mock.requests.length, 5);
  assert.deepEqual(
    mock.requests.slice(1).map((r) => `${r.method} ${pathOf(r.url)}`),
    [
      `POST /api/v1/groups/rules/${ENG.id}/lifecycle/deactivate`,
      `DELETE /api/v1/groups/rules/${ENG.id}`,
      "POST /api/v1/groups/rules",
      `POST /api/v1/groups/rules/${newId}/lifecycle/activate`,
    ],
    "actions are immutable on the wire; changing groupIds means recreate");
  assert.deepEqual(mock.requests[3].body.actions.assignUserToGroups.groupIds,
    ["00g1grpENGCORE0001", "00g1grpBUILDBOT004"]);
  assert.deepEqual(report.recreated, ["eng-core-access"]);
});

test("matching rules produce zero write traffic, group order ignored", async (t) => {
  const two = rule("0pr1a2b3c4RULEdddd04", "dual-group", 'user.title=="Lead"',
    ["00g1grpAAA0000005", "00g1grpBBB0000006"], "ACTIVE");
  const mock = await startMock(t, [{ body: [two], headers: {} }]);
  const client = new GroupRulesClient(mock.base, TOKEN);
  const report = await reconcileGroupRules(client, [
    desired("dual-group", 'user.title=="Lead"',
      ["00g1grpBBB0000006", "00g1grpAAA0000005"], "ACTIVE"),
  ]);
  assert.equal(mock.requests.length, 1, "list only — no writes for a converged rule");
  assert.deepEqual(report.unchanged, ["dual-group"]);
  assert.deepEqual(report.updated, []);
  assert.deepEqual(report.statusChanged, []);
});

test("specs with non-current fields are rejected before any write", async (t) => {
  const mock = await startMock(t, [{ body: [], headers: {} }]);
  const client = new GroupRulesClient(mock.base, TOKEN);
  const legacyExpr = desired("legacy-expr", 'user.division=="Ops"', ["00g1grpOPS0000007"], "ACTIVE",
    "urn:okta:expression:2.0");
  const groupExclude = desired("group-exclude", 'user.division=="Ops"', ["00g1grpOPS0000008"], "ACTIVE");
  groupExclude.conditions.people = { groups: { exclude: ["00g1grpEXCLUDED09"] } };
  const badStatus = desired("bad-status", 'user.division=="Ops"', ["00g1grpOPS0000010"], "ACTIVE");
  (badStatus as any).status = "INVALID";

  const report = await reconcileGroupRules(client, [legacyExpr, groupExclude, badStatus]);

  assert.equal(mock.requests.length, 1, "rejected specs must not reach the API");
  assert.deepEqual(report.created, []);
  assert.equal(report.rejected.length, 3);
  const reasons = new Map(report.rejected.map((r) => [r.name, r.reason]));
  assert.match(reasons.get("legacy-expr")!, /conditions\.expression\.type/);
  assert.match(reasons.get("legacy-expr")!, /urn:okta:expression:1\.0/);
  assert.match(reasons.get("group-exclude")!, /conditions\.people\.groups\.exclude/);
  assert.match(reasons.get("bad-status")!, /status/);
});
