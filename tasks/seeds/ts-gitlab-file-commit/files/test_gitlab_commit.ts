// Acceptance tests for the multi-action repository-commit feature. Runs
// against a local node:http mock speaking the GitLab REST v4 wire contract
// pinned in docs/contract.json — no real GitLab, no real credentials.
// Protected — do not modify. Run: node --test test_gitlab_commit.ts
import { test } from "node:test";
import assert from "node:assert/strict";
import { createServer } from "node:http";

import { GitLabClient, GitLabApiError } from "./gitlab/client.ts";
import {
  CommitBatch,
  GitLabCommitConflictError,
  GitLabValidationError,
} from "./gitlab/commits.ts";

const TOKEN = "glpat-dummy-c0mmit-4402";
const PROJECT = "acme/widget-app";
const ENCODED_PROJECT = "acme%2Fwidget-app";
const COMMITS_PATH = `/api/v4/projects/${ENCODED_PROJECT}/repository/commits`;
const OLD_HEAD = "6104942438c14ec7bd21c6cd5bd995272b3faff6";
const NEW_HEAD = "e83c5163316f89bfbde7d9ab23ca2e25604af290";

const CONFLICT_MESSAGE =
  "You are attempting to update a file that has changed since you started editing it.";

interface Captured {
  method: string;
  rawUrl: string;
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
        rawUrl: req.url ?? "",
        headers: req.headers,
        body: raw ? JSON.parse(raw) : null,
      });
      const s = script[Math.min(requests.length - 1, script.length - 1)];
      res.statusCode = s.status ?? 200;
      for (const [k, v] of Object.entries(s.headers ?? {})) res.setHeader(k, v);
      res.setHeader("content-type", "application/json");
      res.end(JSON.stringify(s.body ?? {}));
    });
  });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  t.after(() => new Promise((resolve) => server.close(resolve)));
  const addr = server.address();
  if (addr === null || typeof addr === "string") throw new Error("no port");
  return { base: `http://127.0.0.1:${addr.port}`, requests };
}

function commitDoc(id: string) {
  return {
    id,
    short_id: id.slice(0, 8),
    title: "chore: rotate service config",
    message: "chore: rotate service config",
    parent_ids: [OLD_HEAD],
    stats: { additions: 12, deletions: 3, total: 15 },
    status: null,
    web_url: `https://gitlab.example.com/acme/widget-app/-/commit/${id}`,
  };
}

// ---------------------------------------------------------------------------
// Existing behavior: repository file reads must keep working unchanged.
// ---------------------------------------------------------------------------

test("getFile URL-encodes project and file paths and authenticates", async (t) => {
  const fileDoc = {
    file_name: "app.yaml",
    file_path: "config/app.yaml",
    size: 48,
    encoding: "base64",
    content: Buffer.from("timeout: 15\n").toString("base64"),
    ref: "main",
    blob_id: "79f7bbd25901e8334750839545a9bd021f0e4c83",
    commit_id: NEW_HEAD,
    last_commit_id: OLD_HEAD,
  };
  const { base, requests } = await startMock(t, [{ status: 200, body: fileDoc }]);
  const client = new GitLabClient(base, TOKEN);
  const file = await client.getFile(PROJECT, "config/app.yaml", "main");

  assert.equal(requests.length, 1);
  assert.equal(requests[0].method, "GET");
  assert.equal(
    requests[0].rawUrl,
    `/api/v4/projects/${ENCODED_PROJECT}/repository/files/config%2Fapp.yaml?ref=main`,
  );
  assert.equal(requests[0].headers["private-token"], TOKEN);
  assert.equal(file.last_commit_id, OLD_HEAD);
  assert.equal(Buffer.from(file.content, "base64").toString("utf8"), "timeout: 15\n");
});

test("getFile surfaces GitLab's message document without the token", async (t) => {
  const { base } = await startMock(t, [
    { status: 404, body: { message: "404 File Not Found" } },
  ]);
  const client = new GitLabClient(base, TOKEN);
  await assert.rejects(
    () => client.getFile(PROJECT, "missing.txt", "main"),
    (err: unknown) => {
      assert.ok(err instanceof GitLabApiError);
      assert.equal(err.status, 404);
      assert.match(err.message, /404 File Not Found/);
      assert.ok(!err.message.includes(TOKEN), "token leaked into error");
      return true;
    },
  );
});

// ---------------------------------------------------------------------------
// New feature: staged multi-action commits.
// ---------------------------------------------------------------------------

test("commit posts one documented multi-action payload", async (t) => {
  const { base, requests } = await startMock(t, [
    { status: 201, body: commitDoc(NEW_HEAD) },
  ]);
  const client = new GitLabClient(base, TOKEN);
  const batch = new CommitBatch(client, PROJECT, "main");

  batch.trackHead("config/app.yaml", OLD_HEAD);
  batch.create("docs/runbook.md", "# Runbook\n");
  batch.update("config/app.yaml", "timeout: 30\n");
  batch.move("src/util.ts", "src/utils/index.ts");
  batch.remove("legacy/bootstrap.sh");
  batch.chmod("scripts/deploy.sh", true);
  batch.create("assets/logo.png", Buffer.from([0x89, 0x50, 0x4e, 0x47]).toString("base64"), {
    encoding: "base64",
  });

  const result = await batch.commit("chore: rotate service config");

  assert.equal(requests.length, 1);
  const req = requests[0];
  assert.equal(req.method, "POST");
  assert.equal(req.rawUrl, COMMITS_PATH);
  assert.equal(req.headers["private-token"], TOKEN);
  assert.match(String(req.headers["content-type"]), /application\/json/);

  assert.equal(req.body.branch, "main");
  assert.equal(req.body.commit_message, "chore: rotate service config");
  assert.ok(Array.isArray(req.body.actions));
  assert.equal(req.body.actions.length, 6);

  const [create, update, move, del, chmod, binary] = req.body.actions;
  assert.deepEqual(create, {
    action: "create",
    file_path: "docs/runbook.md",
    content: "# Runbook\n",
  });
  assert.deepEqual(update, {
    action: "update",
    file_path: "config/app.yaml",
    content: "timeout: 30\n",
    last_commit_id: OLD_HEAD,
  });
  assert.deepEqual(move, {
    action: "move",
    file_path: "src/utils/index.ts",
    previous_path: "src/util.ts",
  });
  assert.deepEqual(del, { action: "delete", file_path: "legacy/bootstrap.sh" });
  assert.deepEqual(chmod, {
    action: "chmod",
    file_path: "scripts/deploy.sh",
    execute_filemode: true,
  });
  assert.equal(binary.action, "create");
  assert.equal(binary.encoding, "base64");
  assert.equal(binary.file_path, "assets/logo.png");

  // file_path values inside the JSON body stay raw; only URL components
  // are percent-encoded.
  assert.ok(!JSON.stringify(req.body).includes("%2F"));

  assert.equal(result.id, NEW_HEAD);
  assert.equal(result.short_id, NEW_HEAD.slice(0, 8));
  assert.equal(result.stats.total, 15);

  // Success advances local state atomically.
  assert.equal(batch.pending().length, 0);
  assert.equal(batch.head("config/app.yaml"), NEW_HEAD);
  assert.equal(batch.head("docs/runbook.md"), NEW_HEAD);
});

test("commit can start the branch from another ref", async (t) => {
  const { base, requests } = await startMock(t, [
    { status: 201, body: commitDoc(NEW_HEAD) },
  ]);
  const client = new GitLabClient(base, TOKEN);
  const batch = new CommitBatch(client, PROJECT, "feature/rotate-config");
  batch.create("docs/runbook.md", "# Runbook\n");
  await batch.commit("chore: rotate service config", { startBranch: "main" });

  assert.equal(requests[0].body.branch, "feature/rotate-config");
  assert.equal(requests[0].body.start_branch, "main");
});

test("optimistic-concurrency conflict keeps local state intact for retry", async (t) => {
  const { base, requests } = await startMock(t, [
    { status: 400, body: { message: CONFLICT_MESSAGE } },
    { status: 201, body: commitDoc("f".repeat(40)) },
  ]);
  const client = new GitLabClient(base, TOKEN);
  const batch = new CommitBatch(client, PROJECT, "main");

  batch.trackHead("config/app.yaml", OLD_HEAD);
  batch.update("config/app.yaml", "timeout: 30\n");
  batch.remove("legacy/bootstrap.sh");

  await assert.rejects(
    () => batch.commit("chore: rotate service config"),
    (err: unknown) => {
      assert.ok(err instanceof GitLabCommitConflictError, `wrong error: ${err}`);
      assert.equal((err as GitLabCommitConflictError).status, 400);
      assert.match((err as Error).message, /changed since you started editing/);
      return true;
    },
  );

  // Nothing may be half-applied locally: same staged actions, same head.
  assert.equal(batch.pending().length, 2);
  assert.equal(batch.pending()[0].file_path, "config/app.yaml");
  assert.equal(batch.head("config/app.yaml"), OLD_HEAD);

  // Refresh the head (as a caller would after re-reading the file) and retry.
  batch.trackHead("config/app.yaml", NEW_HEAD);
  const result = await batch.commit("chore: rotate service config");

  assert.equal(requests.length, 2);
  const retry = requests[1].body;
  assert.equal(retry.actions.length, 2);
  assert.equal(retry.actions[0].last_commit_id, NEW_HEAD);
  assert.equal(result.id, "f".repeat(40));
  assert.equal(batch.pending().length, 0);
  assert.equal(batch.head("config/app.yaml"), "f".repeat(40));
});

test("other validation errors decode distinctly and preserve state", async (t) => {
  const { base } = await startMock(t, [
    { status: 400, body: { message: "A file with this name already exists" } },
  ]);
  const client = new GitLabClient(base, TOKEN);
  const batch = new CommitBatch(client, PROJECT, "main");
  batch.create("docs/runbook.md", "# Runbook\n");

  await assert.rejects(
    () => batch.commit("add runbook"),
    (err: unknown) => {
      assert.ok(err instanceof GitLabValidationError, `wrong error: ${err}`);
      assert.ok(!(err instanceof GitLabCommitConflictError));
      assert.equal((err as GitLabValidationError).status, 400);
      assert.match((err as Error).message, /already exists/);
      assert.ok(!(err as Error).message.includes(TOKEN));
      return true;
    },
  );
  assert.equal(batch.pending().length, 1);
});

test("an empty batch refuses to hit the API", async (t) => {
  const { base, requests } = await startMock(t, [{ status: 201, body: commitDoc(NEW_HEAD) }]);
  const client = new GitLabClient(base, TOKEN);
  const batch = new CommitBatch(client, PROJECT, "main");
  await assert.rejects(() => batch.commit("empty"), /no.*action|nothing.*staged/i);
  assert.equal(requests.length, 0);
});

test("update without a tracked head omits last_commit_id", async (t) => {
  const { base, requests } = await startMock(t, [
    { status: 201, body: commitDoc(NEW_HEAD) },
  ]);
  const client = new GitLabClient(base, TOKEN);
  const batch = new CommitBatch(client, PROJECT, "main");
  batch.update("README.md", "hello\n");
  await batch.commit("touch readme");

  const action = requests[0].body.actions[0];
  assert.deepEqual(action, {
    action: "update",
    file_path: "README.md",
    content: "hello\n",
  });
});
