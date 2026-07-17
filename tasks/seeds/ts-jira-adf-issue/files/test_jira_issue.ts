// Acceptance tests for the Jira issue writer. Everything runs against a local
// node:http mock that speaks the Jira Cloud platform REST API v3 wire contract
// pinned in docs/contract.json — no real site, no real credentials.
import { test } from "node:test";
import assert from "node:assert/strict";
import { createServer } from "node:http";

import { JiraIssueClient, JiraHttpError, JiraFieldError } from "./jira/client.ts";
import {
  doc,
  paragraph,
  text,
  link,
  codeBlock,
  bulletList,
  descriptionFromText,
} from "./jira/adf.ts";

const EMAIL = "issuebot@example.com";
const TOKEN = "dummy-jira-api-token-9315";
const BASIC = "Basic " + Buffer.from(`${EMAIL}:${TOKEN}`).toString("base64");

interface Captured {
  method: string;
  path: string;
  search: URLSearchParams;
  headers: Record<string, string | string[] | undefined>;
  body: any;
}

interface Scripted {
  status?: number;
  body?: unknown;
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
      if (scripted.body === undefined) {
        res.end();
      } else {
        res.setHeader("content-type", "application/json;charset=UTF-8");
        res.end(JSON.stringify(scripted.body));
      }
    });
  });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", () => resolve()));
  const addr = server.address() as { port: number };
  t.after(() => server.close());
  return { base: `http://127.0.0.1:${addr.port}`, requests };
}

function clientFor(base: string): any {
  return new JiraIssueClient({ baseUrl: base, email: EMAIL, apiToken: TOKEN });
}

test("ADF builders produce the documented document structure", () => {
  assert.deepEqual(descriptionFromText(""), { type: "doc", version: 1, content: [] });

  assert.deepEqual(descriptionFromText("Deploy failed.\nRollback completed."), {
    type: "doc",
    version: 1,
    content: [
      { type: "paragraph", content: [{ type: "text", text: "Deploy failed." }] },
      { type: "paragraph", content: [{ type: "text", text: "Rollback completed." }] },
    ],
  });

  const d = doc(
    paragraph(
      text("See "),
      link("the runbook", "https://ops.example.com/runbook/search"),
      text(" before retrying — this is "),
      text("urgent", ["strong"]),
      text("."),
    ),
    bulletList("drain traffic", "restart indexer"),
    codeBlock("bash", "systemctl restart search-indexer"),
  );
  assert.deepEqual(d, {
    type: "doc",
    version: 1,
    content: [
      {
        type: "paragraph",
        content: [
          { type: "text", text: "See " },
          {
            type: "text",
            text: "the runbook",
            marks: [{ type: "link", attrs: { href: "https://ops.example.com/runbook/search" } }],
          },
          { type: "text", text: " before retrying — this is " },
          { type: "text", text: "urgent", marks: [{ type: "strong" }] },
          { type: "text", text: "." },
        ],
      },
      {
        type: "bulletList",
        content: [
          {
            type: "listItem",
            content: [
              { type: "paragraph", content: [{ type: "text", text: "drain traffic" }] },
            ],
          },
          {
            type: "listItem",
            content: [
              { type: "paragraph", content: [{ type: "text", text: "restart indexer" }] },
            ],
          },
        ],
      },
      {
        type: "codeBlock",
        attrs: { language: "bash" },
        content: [{ type: "text", text: "systemctl restart search-indexer" }],
      },
    ],
  });
});

test("createIssue posts the documented v3 body with an ADF description", async (t) => {
  const { base, requests } = await startMock(t, () => ({
    status: 201,
    body: {
      id: "10230",
      key: "OPS-341",
      self: `${base}/rest/api/3/issue/10230`,
    },
  }));

  const created = await clientFor(base).createIssue({
    projectKey: "OPS",
    issueTypeId: "10004",
    summary: "Search cluster deploy failed",
    description: "Deploy failed.\nRollback completed.",
    assigneeAccountId: "5b109f2e9729b51b54dc274d",
    reporterAccountId: "70121:reporter-acct-42",
    labels: ["deploy", "sev2"],
    dueDate: "2026-07-31",
  });

  assert.equal(requests.length, 1);
  const q = requests[0];
  assert.equal(q.method, "POST");
  assert.equal(q.path, "/rest/api/3/issue");
  assert.equal(q.headers.authorization, BASIC);
  assert.match(String(q.headers["content-type"]), /^application\/json/);

  const fields = q.body.fields;
  assert.deepEqual(fields.project, { key: "OPS" });
  assert.deepEqual(fields.issuetype, { id: "10004" });
  assert.equal(fields.summary, "Search cluster deploy failed");
  // v3 takes Atlassian Document Format here — never the legacy v2 plain string.
  assert.deepEqual(fields.description, {
    type: "doc",
    version: 1,
    content: [
      { type: "paragraph", content: [{ type: "text", text: "Deploy failed." }] },
      { type: "paragraph", content: [{ type: "text", text: "Rollback completed." }] },
    ],
  });
  // People are identified by account ID under "id" — no name/username fields.
  assert.deepEqual(fields.assignee, { id: "5b109f2e9729b51b54dc274d" });
  assert.deepEqual(fields.reporter, { id: "70121:reporter-acct-42" });
  assert.deepEqual(fields.labels, ["deploy", "sev2"]);
  assert.equal(fields.duedate, "2026-07-31");
  assert.ok(!("dueDate" in fields), "the wire field is duedate, not dueDate");

  assert.deepEqual(created, {
    id: "10230",
    key: "OPS-341",
    self: `${base}/rest/api/3/issue/10230`,
  });
});

test("optional fields are omitted entirely and prebuilt ADF passes through unchanged", async (t) => {
  const { base, requests } = await startMock(t, () => ({
    status: 201,
    body: { id: "10231", key: "OPS-342", self: `${base}/rest/api/3/issue/10231` },
  }));

  const description = doc(paragraph(text("Prebuilt body.")));
  await clientFor(base).createIssue({
    projectKey: "OPS",
    issueTypeId: "10004",
    summary: "Minimal issue",
    description,
  });

  const fields = requests[0].body.fields;
  assert.deepEqual(fields.description, {
    type: "doc",
    version: 1,
    content: [{ type: "paragraph", content: [{ type: "text", text: "Prebuilt body." }] }],
  });
  for (const absent of ["assignee", "reporter", "labels", "duedate", "priority"]) {
    assert.ok(!(absent in fields), `unset field ${absent} must be omitted, not null`);
  }
});

test("a description that is not a valid ADF root document is rejected before any request", async (t) => {
  const { base, requests } = await startMock(t, () => ({
    status: 500,
    body: { errorMessages: ["should never be reached"], errors: {} },
  }));

  const client = clientFor(base);
  const bad = [
    { version: 1, content: [] }, // missing type: "doc"
    { type: "doc", content: [] }, // missing version
    { type: "doc", version: 2, content: [] }, // wrong version
    { type: "paragraph", content: [] }, // not a root doc node
  ];
  for (const description of bad) {
    await assert.rejects(
      client.createIssue({
        projectKey: "OPS",
        issueTypeId: "10004",
        summary: "x",
        description,
      }),
      (err: any) => {
        assert.match(String(err.message), /ADF|Atlassian Document/i);
        return true;
      },
    );
  }
  assert.equal(requests.length, 0, "invalid ADF must be rejected locally, before any HTTP call");
});

test("field errors from the v3 error collection are decoded per field", async (t) => {
  const { base } = await startMock(t, () => ({
    status: 400,
    body: {
      errorMessages: ["Field 'priority' is required"],
      errors: {
        assignee: "User 'deadbeef' does not exist.",
        customfield_10042: "Team is required.",
      },
    },
  }));

  await assert.rejects(
    clientFor(base).createIssue({
      projectKey: "OPS",
      issueTypeId: "10004",
      summary: "Broken create",
    }),
    (err: any) => {
      assert.ok(err instanceof JiraFieldError, `expected JiraFieldError, got ${err}`);
      assert.ok(err instanceof JiraHttpError, "JiraFieldError must extend JiraHttpError");
      assert.equal(err.status, 400);
      assert.deepEqual(err.errorMessages, ["Field 'priority' is required"]);
      assert.deepEqual(err.fieldErrors, {
        assignee: "User 'deadbeef' does not exist.",
        customfield_10042: "Team is required.",
      });
      assert.match(err.message, /priority/);
      assert.ok(!err.message.includes(TOKEN), "error text leaks the API token");
      assert.ok(!err.message.includes(EMAIL), "error text leaks the account email");
      return true;
    },
  );
});

test("non-field HTTP failures raise JiraHttpError with the API's message", async (t) => {
  const { base } = await startMock(t, () => ({
    status: 401,
    body: { errorMessages: ["Authentication credentials are incorrect or missing."], errors: {} },
  }));

  await assert.rejects(
    clientFor(base).createIssue({ projectKey: "OPS", issueTypeId: "10004", summary: "x" }),
    (err: any) => {
      assert.ok(err instanceof JiraHttpError, `expected JiraHttpError, got ${err}`);
      assert.ok(!(err instanceof JiraFieldError), "401 carries no field errors");
      assert.equal(err.status, 401);
      assert.match(err.message, /credentials are incorrect/);
      assert.ok(!err.message.includes(TOKEN), "error text leaks the API token");
      assert.ok(
        !err.message.includes(Buffer.from(`${EMAIL}:${TOKEN}`).toString("base64")),
        "error text leaks the basic-auth blob",
      );
      return true;
    },
  );
});

test("updateIssue puts fields and update operations to the issue resource", async (t) => {
  const { base, requests } = await startMock(t, () => ({ status: 204 }));

  const result = await clientFor(base).updateIssue(
    "OPS-341",
    {
      summary: "Search cluster deploy failed (rolled back)",
      description: "Postmortem drafted.",
      addLabels: ["postmortem"],
      removeLabels: ["sev2"],
    },
    { notifyUsers: false },
  );

  assert.equal(result, null, "a 204 edit returns no issue");
  assert.equal(requests.length, 1);
  const q = requests[0];
  assert.equal(q.method, "PUT");
  assert.equal(q.path, "/rest/api/3/issue/OPS-341");
  assert.equal(q.headers.authorization, BASIC);
  assert.equal(q.search.get("notifyUsers"), "false");
  assert.equal(q.search.get("returnIssue"), null);

  assert.equal(q.body.fields.summary, "Search cluster deploy failed (rolled back)");
  assert.deepEqual(q.body.fields.description, {
    type: "doc",
    version: 1,
    content: [{ type: "paragraph", content: [{ type: "text", text: "Postmortem drafted." }] }],
  });
  assert.ok(!("labels" in q.body.fields), "label deltas belong in update, not fields");
  assert.deepEqual(q.body.update, {
    labels: [{ add: "postmortem" }, { remove: "sev2" }],
  });
});

test("updateIssue with returnIssue=true returns the edited issue from the 200 body", async (t) => {
  const issueBody = {
    id: "10230",
    key: "OPS-341",
    fields: { summary: "Renamed", labels: ["postmortem"] },
  };
  const { base, requests } = await startMock(t, () => ({ status: 200, body: issueBody }));

  const result = await clientFor(base).updateIssue(
    "OPS-341",
    { summary: "Renamed" },
    { returnIssue: true },
  );

  const q = requests[0];
  assert.equal(q.search.get("returnIssue"), "true");
  assert.ok(!("update" in q.body), "no update operations were requested");
  assert.deepEqual(result, issueBody);
});

test("editing an unknown issue surfaces the 404 error collection", async (t) => {
  const { base } = await startMock(t, () => ({
    status: 404,
    body: {
      errorMessages: ["Issue does not exist or you do not have permission to see it."],
      errors: {},
    },
  }));

  await assert.rejects(
    clientFor(base).updateIssue("OPS-999", { summary: "nope" }),
    (err: any) => {
      assert.ok(err instanceof JiraHttpError);
      assert.equal(err.status, 404);
      assert.match(err.message, /does not exist/);
      return true;
    },
  );
});
