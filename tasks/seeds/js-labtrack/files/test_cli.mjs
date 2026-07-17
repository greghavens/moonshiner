// Acceptance tests for the labtrack CLI — protected file.
// Drives the CLI exactly like the bench techs do: as a child process.
import test from "node:test";
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const CLI = path.join(HERE, "labtrack.mjs");

function freshDir() {
  return fs.mkdtempSync(path.join(HERE, "tmp-lab-"));
}

function run(cwd, ...args) {
  return spawnSync(process.execPath, [CLI, ...args], { cwd, encoding: "utf8" });
}

function norm(text) {
  return text.trim().split("\n").map((l) => l.trim().replace(/\s+/g, " "))
    .filter((l) => l !== "").join("\n");
}

function withDir(fn) {
  const dir = freshDir();
  try {
    return fn(dir);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
}

test("top-level --help prints the program summary and exits 0", () => {
  withDir((dir) => {
    const r = run(dir, "--help");
    assert.equal(r.status, 0);
    assert.equal(norm(r.stdout), norm(`
      Usage: labtrack [options] [command]

      track lab samples through intake and testing

      Options:
        -f, --file <path>              state file (default: "samples.json")
        -h, --help                     display help for command

      Commands:
        register [options] <sampleId>  register a new sample
        list [options]                 list registered samples
        status <sampleId> <newStatus>  update a sample's status
        help [command]                 display help for command
    `));
  });
});

test("register --help documents arguments, choices and defaults", () => {
  withDir((dir) => {
    const r = run(dir, "register", "--help");
    assert.equal(r.status, 0);
    assert.equal(norm(r.stdout), norm(`
      Usage: labtrack register [options] <sampleId>

      register a new sample

      Arguments:
        sampleId                sample identifier

      Options:
        -k, --kind <kind>       sample kind (choices: "water", "soil", "air")
        -d, --dilution <ratio>  dilution ratio (default: 1)
        -t, --tag <tags...>     free-form tags
        -h, --help              display help for command
    `));
    assert.equal(run(dir, "status", "--help").status, 0);
    assert.equal(run(dir, "list", "--help").status, 0);
  });
});

test("register persists the sample and reports it", () => {
  withDir((dir) => {
    const r = run(dir, "register", "S-001", "--kind", "soil",
      "--dilution", "2.5", "--tag", "intake", "--tag", "rush");
    assert.equal(r.status, 0);
    assert.equal(r.stdout, "registered S-001 kind=soil dilution=2.5\n");
    const state = JSON.parse(fs.readFileSync(path.join(dir, "samples.json"), "utf8"));
    assert.deepEqual(state, {
      "S-001": { kind: "soil", dilution: 2.5, tags: ["intake", "rush"], status: "received" },
    });
  });
});

test("tags and dilution have sane defaults", () => {
  withDir((dir) => {
    const r = run(dir, "register", "S-002", "--kind", "water");
    assert.equal(r.status, 0);
    assert.equal(r.stdout, "registered S-002 kind=water dilution=1\n");
    const state = JSON.parse(fs.readFileSync(path.join(dir, "samples.json"), "utf8"));
    assert.deepEqual(state["S-002"], {
      kind: "water", dilution: 1, tags: [], status: "received",
    });
  });
});

test("duplicate registration exits 3 and leaves the original intact", () => {
  withDir((dir) => {
    run(dir, "register", "S-001", "--kind", "soil", "--dilution", "2.5");
    const r = run(dir, "register", "S-001", "--kind", "air");
    assert.equal(r.status, 3);
    assert.equal(r.stderr, "error: sample S-001 already registered\n");
    const state = JSON.parse(fs.readFileSync(path.join(dir, "samples.json"), "utf8"));
    assert.equal(state["S-001"].kind, "soil");
    assert.equal(state["S-001"].dilution, 2.5);
  });
});

test("usage problems exit 2 with commander's message on stderr", () => {
  withDir((dir) => {
    let r = run(dir, "register", "S-003", "--kind", "plasma");
    assert.equal(r.status, 2);
    assert.match(r.stderr, /Allowed choices are water, soil, air/);

    for (const bad of ["zero", "0", "-1"]) {
      r = run(dir, "register", "S-003", "--kind", "air", "--dilution", bad);
      assert.equal(r.status, 2);
      assert.match(r.stderr, /dilution must be a positive number/);
    }

    r = run(dir, "register", "S-003");
    assert.equal(r.status, 2);
    assert.match(r.stderr, /required option '-k, --kind <kind>' not specified/);

    r = run(dir, "frobnicate");
    assert.equal(r.status, 2);
    assert.match(r.stderr, /unknown command/);

    r = run(dir, "list", "--nope");
    assert.equal(r.status, 2);
    assert.match(r.stderr, /unknown option/);

    assert.equal(fs.existsSync(path.join(dir, "samples.json")), false,
      "usage errors must not create the state file");
  });
});

test("list prints tab-separated rows sorted by id", () => {
  withDir((dir) => {
    run(dir, "register", "S-002", "--kind", "water");
    run(dir, "register", "S-001", "--kind", "soil", "--dilution", "2.5",
      "--tag", "intake", "--tag", "rush");
    const r = run(dir, "list");
    assert.equal(r.status, 0);
    assert.equal(r.stdout,
      "S-001\tsoil\tdilution=2.5\tstatus=received\ttags=intake,rush\n" +
      "S-002\twater\tdilution=1\tstatus=received\ttags=-\n");
  });
});

test("list --kind filters and --json emits parseable JSON", () => {
  withDir((dir) => {
    run(dir, "register", "S-002", "--kind", "water");
    run(dir, "register", "S-001", "--kind", "soil", "--tag", "rush");
    const plain = run(dir, "list", "--kind", "water");
    assert.equal(plain.stdout, "S-002\twater\tdilution=1\tstatus=received\ttags=-\n");
    const json = run(dir, "list", "--json");
    assert.equal(json.status, 0);
    assert.deepEqual(JSON.parse(json.stdout), [
      { id: "S-001", kind: "soil", dilution: 1, tags: ["rush"], status: "received" },
      { id: "S-002", kind: "water", dilution: 1, tags: [], status: "received" },
    ]);
  });
});

test("an empty ledger lists cleanly and does not create the state file", () => {
  withDir((dir) => {
    const plain = run(dir, "list");
    assert.equal(plain.status, 0);
    assert.equal(plain.stdout, "");
    const json = run(dir, "list", "--json");
    assert.equal(json.status, 0);
    assert.deepEqual(JSON.parse(json.stdout), []);
    assert.equal(fs.existsSync(path.join(dir, "samples.json")), false);
  });
});

test("status updates persist, and the argument is choice-checked", () => {
  withDir((dir) => {
    run(dir, "register", "S-001", "--kind", "soil");
    let r = run(dir, "status", "S-001", "testing");
    assert.equal(r.status, 0);
    assert.equal(r.stdout, "S-001 -> testing\n");
    assert.match(run(dir, "list").stdout, /status=testing/);

    r = run(dir, "status", "S-001", "bogus");
    assert.equal(r.status, 2);
    assert.match(r.stderr, /Allowed choices are received, testing, complete/);

    r = run(dir, "status", "S-777", "testing");
    assert.equal(r.status, 4);
    assert.equal(r.stderr, "error: no such sample: S-777\n");
  });
});

test("--file points every command at an alternate state file", () => {
  withDir((dir) => {
    const r = run(dir, "--file", "other.json", "register", "X-1", "--kind", "air");
    assert.equal(r.status, 0);
    assert.equal(fs.existsSync(path.join(dir, "other.json")), true);
    assert.equal(fs.existsSync(path.join(dir, "samples.json")), false);
    const list = run(dir, "--file", "other.json", "list");
    assert.equal(list.stdout, "X-1\tair\tdilution=1\tstatus=received\ttags=-\n");
    assert.equal(run(dir, "list").stdout, "");
  });
});
